#!/usr/bin/env python3
"""Validate and append immutable MathDB statement-recovery events.

Events are deliberately separated from canonical tasks.  The source task
shards are write-once; any revised match, statement reconstruction, openness
check, or rescore decision is a new line in recovery_events.jsonl.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Iterable, Iterator


TASK_SCHEMA = "opdp.mathdb.recovery-task.v1"
TASK_MANIFEST_SCHEMA = "opdp.mathdb.recovery-task-manifest.v1"
EVENT_SCHEMA = "opdp.mathdb.recovery-event.v1"
EVENT_TYPES = {
    "source_asset_registered",
    "source_matched",
    "recovery_outcome",
    "openness_check",
    "rescore_decision",
    "manual_review",
}
RECOVERY_CLASSES = {
    "exact_source",
    "faithful_normalization",
    "contextual_reconstruction",
    "unresolved",
}
OPENNESS_STATUSES = {
    "verified_open",
    "source_claimed_open",
    "possibly_resolved",
    "confirmed_solved_or_refuted",
    "uncertain",
    "not_assessed",
}
LOCATOR_TYPES = {
    "theorem_label",
    "conjecture_label",
    "question_label",
    "section",
    "page",
    "page_range",
    "line_range",
    "anchor",
    "other",
}


class EventError(RuntimeError):
    """A ledger input, task binding, or append-only integrity failure."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EventError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EventError(f"{path} must contain a JSON object")
    return value


def require_object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EventError(f"{context} must be an object")
    return value


def require_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventError(f"{context} must be a nonempty string")
    return value


def require_sha256(value: Any, context: str) -> str:
    value = require_string(value, context)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value.lower()):
        raise EventError(f"{context} must be a lowercase/uppercase SHA-256 hex digest")
    return value.lower()


def parse_timestamp(value: Any, context: str) -> str:
    text = require_string(value, context)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EventError(f"{context} must be an ISO-8601 timestamp: {text!r}") from exc
    if parsed.tzinfo is None:
        raise EventError(f"{context} must include a timezone")
    return text


def valid_http_url(value: Any, context: str) -> str:
    value = require_string(value, context)
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise EventError(f"{context} must be an http(s) URL")
    return value


def ensure_keys(value: dict[str, Any], allowed: set[str], required: set[str], context: str) -> None:
    missing = required - value.keys()
    unexpected = value.keys() - allowed
    if missing:
        raise EventError(f"{context} missing required keys: {', '.join(sorted(missing))}")
    if unexpected:
        raise EventError(f"{context} has unsupported keys: {', '.join(sorted(unexpected))}")


def validate_uuid(value: Any, context: str) -> str:
    text = require_string(value, context)
    try:
        return str(uuid.UUID(text))
    except ValueError as exc:
        raise EventError(f"{context} must be a UUID") from exc


def validate_locator(value: Any, context: str) -> None:
    locator = require_object(value, context)
    ensure_keys(
        locator,
        {
            "source_url", "asset_id", "source_version", "locator_type", "locator", "source_sha256",
            "quote_sha256", "locator_scheme", "line_start", "line_end", "byte_start", "byte_end_exclusive",
        },
        {"source_url", "asset_id", "locator_type", "locator", "source_sha256", "quote_sha256"},
        context,
    )
    valid_http_url(locator["source_url"], f"{context}.source_url")
    require_string(locator["asset_id"], f"{context}.asset_id")
    if "source_version" in locator and locator["source_version"] is not None:
        require_string(locator["source_version"], f"{context}.source_version")
    if locator["locator_type"] not in LOCATOR_TYPES:
        raise EventError(f"{context}.locator_type is not an allowed locator type")
    require_string(locator["locator"], f"{context}.locator")
    require_sha256(locator["source_sha256"], f"{context}.source_sha256")
    require_sha256(locator["quote_sha256"], f"{context}.quote_sha256")
    if "locator_scheme" in locator:
        require_string(locator["locator_scheme"], f"{context}.locator_scheme")
    for key in ("line_start", "line_end"):
        if key in locator and (not isinstance(locator[key], int) or isinstance(locator[key], bool) or locator[key] < 1):
            raise EventError(f"{context}.{key} must be a positive integer")
    for key in ("byte_start", "byte_end_exclusive"):
        if key in locator and (not isinstance(locator[key], int) or isinstance(locator[key], bool) or locator[key] < 0):
            raise EventError(f"{context}.{key} must be a nonnegative integer")
    if "line_start" in locator and "line_end" in locator and locator["line_end"] < locator["line_start"]:
        raise EventError(f"{context}.line_end must not precede line_start")
    if "byte_start" in locator and "byte_end_exclusive" in locator and locator["byte_end_exclusive"] <= locator["byte_start"]:
        raise EventError(f"{context}.byte_end_exclusive must exceed byte_start")


def validate_actor(value: Any) -> None:
    actor = require_object(value, "actor")
    ensure_keys(actor, {"kind", "id"}, {"kind", "id"}, "actor")
    if actor["kind"] not in {"human", "agent", "pipeline"}:
        raise EventError("actor.kind must be human, agent, or pipeline")
    require_string(actor["id"], "actor.id")


def validate_asset_payload(payload: dict[str, Any]) -> None:
    ensure_keys(payload, {"asset"}, {"asset"}, "source_asset_registered.payload")
    asset = require_object(payload["asset"], "source_asset_registered.payload.asset")
    ensure_keys(
        asset,
        {
            "asset_id",
            "channel",
            "source_url",
            "retrieved_at_utc",
            "sha256",
            "content_type",
            "license_or_terms",
            "local_access",
            "notes",
        },
        {
            "asset_id",
            "channel",
            "source_url",
            "retrieved_at_utc",
            "sha256",
            "content_type",
            "license_or_terms",
            "local_access",
        },
        "source_asset_registered.payload.asset",
    )
    require_string(asset["asset_id"], "asset.asset_id")
    if asset["channel"] not in {"arxiv_s3", "arxiv_kaggle", "arxiv_oai_pmh", "collection_native", "direct_source", "authorized_bulk", "other"}:
        raise EventError("asset.channel is not an approved/reviewable channel label")
    valid_http_url(asset["source_url"], "asset.source_url")
    parse_timestamp(asset["retrieved_at_utc"], "asset.retrieved_at_utc")
    require_sha256(asset["sha256"], "asset.sha256")
    require_string(asset["content_type"], "asset.content_type")
    require_string(asset["license_or_terms"], "asset.license_or_terms")
    if not isinstance(asset["local_access"], bool):
        raise EventError("asset.local_access must be boolean")
    if "notes" in asset:
        require_string(asset["notes"], "asset.notes")


def validate_match_payload(payload: dict[str, Any]) -> None:
    ensure_keys(payload, {"match"}, {"match"}, "source_matched.payload")
    match = require_object(payload["match"], "source_matched.payload.match")
    ensure_keys(
        match,
        {"source_locator", "method", "confidence", "title_overlap", "excerpt_overlap", "decision", "notes"},
        {"source_locator", "method", "confidence", "decision"},
        "source_matched.payload.match",
    )
    validate_locator(match["source_locator"], "match.source_locator")
    require_string(match["method"], "match.method")
    confidence = match["confidence"]
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        raise EventError("match.confidence must be a number in [0, 1]")
    if match["decision"] not in {"accepted", "rejected", "needs_review"}:
        raise EventError("match.decision must be accepted, rejected, or needs_review")
    for key in ("title_overlap", "excerpt_overlap"):
        if key in match:
            value = match[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
                raise EventError(f"match.{key} must be a number in [0, 1]")
    if "notes" in match:
        require_string(match["notes"], "match.notes")


def validate_recovery_payload(payload: dict[str, Any]) -> None:
    ensure_keys(
        payload,
        {"classification", "publishable_statement", "local_evidence", "limitations"},
        {"classification", "limitations"},
        "recovery_outcome.payload",
    )
    classification = payload["classification"]
    if classification not in RECOVERY_CLASSES:
        raise EventError("recovery_outcome.classification is invalid")
    limitations = payload["limitations"]
    if not isinstance(limitations, list) or not all(isinstance(item, str) and item.strip() for item in limitations):
        raise EventError("recovery_outcome.limitations must be an array of nonempty strings")
    statement = payload.get("publishable_statement")
    local_evidence = payload.get("local_evidence")
    if classification == "unresolved":
        if statement is not None or local_evidence is not None:
            raise EventError("an unresolved outcome must not claim a publishable statement or local recovery evidence")
        return
    local_evidence = require_object(local_evidence, "recovery_outcome.local_evidence")
    ensure_keys(
        local_evidence,
        {"source_locators", "method", "confidence", "supporting_event_ids", "transformation_ledger"},
        {"source_locators", "method", "confidence"},
        "recovery_outcome.local_evidence",
    )
    locators = local_evidence["source_locators"]
    if not isinstance(locators, list) or not locators:
        raise EventError("a recovered outcome requires at least one local exact source locator")
    for index, locator in enumerate(locators):
        validate_locator(locator, f"recovery_outcome.local_evidence.source_locators[{index}]")
    require_string(local_evidence["method"], "recovery_outcome.local_evidence.method")
    confidence = local_evidence["confidence"]
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
        raise EventError("recovery_outcome.local_evidence.confidence must be in [0, 1]")
    if "supporting_event_ids" in local_evidence:
        if not isinstance(local_evidence["supporting_event_ids"], list):
            raise EventError("recovery_outcome.local_evidence.supporting_event_ids must be an array")
        for event_id in local_evidence["supporting_event_ids"]:
            validate_uuid(event_id, "recovery_outcome.local_evidence.supporting_event_ids[]")
    if "transformation_ledger" in local_evidence:
        if not isinstance(local_evidence["transformation_ledger"], list):
            raise EventError("recovery_outcome.local_evidence.transformation_ledger must be an array")
        for item in local_evidence["transformation_ledger"]:
            require_string(item, "recovery_outcome.local_evidence.transformation_ledger[]")
    statement = require_object(statement, "recovery_outcome.publishable_statement")
    ensure_keys(
        statement,
        {"text", "statement_sha256", "representation", "self_contained"},
        {"text", "statement_sha256", "representation", "self_contained"},
        "recovery_outcome.publishable_statement",
    )
    text = require_string(statement["text"], "recovery_outcome.publishable_statement.text")
    if sha256_bytes(text.encode("utf-8")) != require_sha256(
        statement["statement_sha256"], "recovery_outcome.publishable_statement.statement_sha256"
    ):
        raise EventError("recovery_outcome.publishable_statement.statement_sha256 does not match UTF-8 text")
    if statement["representation"] not in {
        "faithful_restatement",
        "normalized_notation",
        "contextual_reconstruction",
    }:
        raise EventError("recovery_outcome.publishable_statement.representation is invalid")
    if not isinstance(statement["self_contained"], bool):
        raise EventError("recovery_outcome.publishable_statement.self_contained must be boolean")
    if classification == "exact_source" and statement["representation"] not in {"faithful_restatement", "normalized_notation"}:
        raise EventError("exact_source requires a faithful publishable restatement or notation normalization")
    if classification == "faithful_normalization" and statement["representation"] not in {"normalized_notation", "faithful_restatement"}:
        raise EventError("faithful_normalization requires normalized_notation or faithful_restatement")
    if classification == "contextual_reconstruction" and statement["representation"] != "contextual_reconstruction":
        raise EventError("contextual_reconstruction requires its matching representation")


def validate_openness_payload(payload: dict[str, Any]) -> None:
    ensure_keys(
        payload,
        {"status", "checked_at_utc", "method", "evidence", "notes"},
        {"status", "checked_at_utc", "method", "evidence"},
        "openness_check.payload",
    )
    if payload["status"] not in OPENNESS_STATUSES:
        raise EventError("openness_check.status is invalid")
    parse_timestamp(payload["checked_at_utc"], "openness_check.checked_at_utc")
    require_string(payload["method"], "openness_check.method")
    evidence = payload["evidence"]
    if not isinstance(evidence, list):
        raise EventError("openness_check.evidence must be an array")
    urls: set[str] = set()
    evidence_kinds: set[str] = set()
    for index, item in enumerate(evidence):
        record = require_object(item, f"openness_check.evidence[{index}]")
        ensure_keys(
            record,
            {"kind", "url", "locator", "accessed_at_utc", "source_date", "claim"},
            {"kind", "url", "accessed_at_utc", "claim"},
            f"openness_check.evidence[{index}]",
        )
        if record["kind"] not in {"primary_source", "independent_current_source", "active_curator_attestation", "literature_review", "counterevidence"}:
            raise EventError(f"openness_check.evidence[{index}].kind is invalid")
        evidence_kinds.add(record["kind"])
        urls.add(valid_http_url(record["url"], f"openness_check.evidence[{index}].url"))
        parse_timestamp(record["accessed_at_utc"], f"openness_check.evidence[{index}].accessed_at_utc")
        require_string(record["claim"], f"openness_check.evidence[{index}].claim")
        if "locator" in record:
            require_string(record["locator"], f"openness_check.evidence[{index}].locator")
        if "source_date" in record:
            require_string(record["source_date"], f"openness_check.evidence[{index}].source_date")
    if payload["status"] == "verified_open":
        if len(evidence) < 2 or len(urls) < 2:
            raise EventError("verified_open requires at least two evidence items from distinct URLs")
        if not ({"independent_current_source", "active_curator_attestation"} & evidence_kinds):
            raise EventError(
                "verified_open requires independent_current_source or active_curator_attestation evidence"
            )
    if "notes" in payload:
        require_string(payload["notes"], "openness_check.notes")


def validate_rescore_payload(payload: dict[str, Any]) -> None:
    ensure_keys(
        payload,
        {"eligible", "basis_event_ids", "reasons"},
        {"eligible", "basis_event_ids", "reasons"},
        "rescore_decision.payload",
    )
    if not isinstance(payload["eligible"], bool):
        raise EventError("rescore_decision.eligible must be boolean")
    if not isinstance(payload["basis_event_ids"], list) or not payload["basis_event_ids"]:
        raise EventError("rescore_decision.basis_event_ids must be a nonempty array")
    for event_id in payload["basis_event_ids"]:
        validate_uuid(event_id, "rescore_decision.basis_event_ids[]")
    if not isinstance(payload["reasons"], list) or not payload["reasons"]:
        raise EventError("rescore_decision.reasons must be a nonempty array")
    for item in payload["reasons"]:
        require_string(item, "rescore_decision.reasons[]")


def validate_manual_review_payload(payload: dict[str, Any]) -> None:
    ensure_keys(payload, {"issue", "recommended_action", "notes"}, {"issue", "recommended_action"}, "manual_review.payload")
    require_string(payload["issue"], "manual_review.issue")
    require_string(payload["recommended_action"], "manual_review.recommended_action")
    if "notes" in payload:
        require_string(payload["notes"], "manual_review.notes")


def validate_event(event: Any) -> dict[str, Any]:
    value = require_object(event, "event")
    ensure_keys(
        value,
        {
            "schema",
            "event_id",
            "occurred_at_utc",
            "task_key",
            "task_content_sha256",
            "event_type",
            "actor",
            "supersedes_event_id",
            "payload",
        },
        {
            "schema",
            "event_id",
            "occurred_at_utc",
            "task_key",
            "task_content_sha256",
            "event_type",
            "actor",
            "payload",
        },
        "event",
    )
    if value["schema"] != EVENT_SCHEMA:
        raise EventError(f"event.schema must equal {EVENT_SCHEMA!r}")
    value["event_id"] = validate_uuid(value["event_id"], "event.event_id")
    parse_timestamp(value["occurred_at_utc"], "event.occurred_at_utc")
    task_key = require_string(value["task_key"], "event.task_key")
    if not task_key.startswith("mathdb:") or not task_key[7:].isdigit() or int(task_key[7:]) < 1:
        raise EventError("event.task_key must be mathdb:<positive native number>")
    value["task_content_sha256"] = require_sha256(value["task_content_sha256"], "event.task_content_sha256")
    if value["event_type"] not in EVENT_TYPES:
        raise EventError("event.event_type is invalid")
    validate_actor(value["actor"])
    if "supersedes_event_id" in value:
        value["supersedes_event_id"] = validate_uuid(value["supersedes_event_id"], "event.supersedes_event_id")
    payload = require_object(value["payload"], "event.payload")
    if value["event_type"] == "source_asset_registered":
        validate_asset_payload(payload)
    elif value["event_type"] == "source_matched":
        validate_match_payload(payload)
    elif value["event_type"] == "recovery_outcome":
        validate_recovery_payload(payload)
    elif value["event_type"] == "openness_check":
        validate_openness_payload(payload)
    elif value["event_type"] == "rescore_decision":
        validate_rescore_payload(payload)
    elif value["event_type"] == "manual_review":
        validate_manual_review_payload(payload)
    return value


def task_hash_without_self(task: dict[str, Any]) -> str:
    value = {key: item for key, item in task.items() if key != "task_content_sha256"}
    return sha256_bytes(canonical_json_bytes(value))


def safe_shard_path(tasks_dir: Path, name: Any) -> Path:
    if not isinstance(name, str) or not name.endswith(".jsonl"):
        raise EventError("task manifest has invalid shard path")
    candidate = (tasks_dir / name).resolve()
    if candidate.parent != tasks_dir.resolve():
        raise EventError("task manifest shard path escapes canonical_tasks")
    return candidate


def ensure_task_index(run_dir: Path) -> Path:
    tasks_dir = run_dir / "canonical_tasks"
    manifest_path = tasks_dir / "canonical_tasks_manifest.json"
    if not manifest_path.is_file():
        raise EventError(f"missing canonical task manifest: {manifest_path}")
    manifest = read_json(manifest_path)
    if manifest.get("schema") != TASK_MANIFEST_SCHEMA:
        raise EventError("canonical task manifest has wrong schema")
    task_manifest_sha = sha256_file(manifest_path)
    index_path = run_dir / "task_lookup.sqlite"
    if index_path.exists():
        try:
            connection = sqlite3.connect(index_path)
            found = connection.execute("SELECT value FROM meta WHERE key='task_manifest_sha256'").fetchone()
            connection.close()
            if found and found[0] == task_manifest_sha:
                return index_path
        except sqlite3.DatabaseError:
            pass
    descriptor, temp_name = tempfile.mkstemp(prefix=".task_lookup.", suffix=".sqlite", dir=run_dir)
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        connection = sqlite3.connect(temp_path)
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("CREATE TABLE tasks (task_key TEXT PRIMARY KEY, task_hash TEXT NOT NULL)")
        shards = manifest.get("shards")
        if not isinstance(shards, list) or not shards:
            raise EventError("canonical task manifest must contain nonempty shards")
        expected_count = 0
        for shard in shards:
            if not isinstance(shard, dict):
                raise EventError("canonical task manifest has a non-object shard")
            shard_path = safe_shard_path(tasks_dir, shard.get("path"))
            if not shard_path.is_file():
                raise EventError(f"canonical task shard is missing: {shard_path}")
            if sha256_file(shard_path) != shard.get("sha256"):
                raise EventError(f"canonical task shard hash mismatch: {shard_path}")
            actual_shard_count = 0
            with shard_path.open("r", encoding="utf-8", newline="") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    try:
                        task = json.loads(raw_line)
                    except json.JSONDecodeError as exc:
                        raise EventError(f"{shard_path}:{line_number}: invalid task JSON") from exc
                    if not isinstance(task, dict) or task.get("schema") != TASK_SCHEMA:
                        raise EventError(f"{shard_path}:{line_number}: invalid task schema")
                    task_key = require_string(task.get("task_key"), f"{shard_path}:{line_number}.task_key")
                    task_hash = require_sha256(task.get("task_content_sha256"), f"{shard_path}:{line_number}.task_content_sha256")
                    if task_hash_without_self(task) != task_hash:
                        raise EventError(f"{shard_path}:{line_number}: task content hash mismatch")
                    try:
                        connection.execute("INSERT INTO tasks(task_key, task_hash) VALUES (?, ?)", (task_key, task_hash))
                    except sqlite3.IntegrityError as exc:
                        raise EventError(f"duplicate task key in canonical shards: {task_key}") from exc
                    actual_shard_count += 1
            if actual_shard_count != shard.get("task_count"):
                raise EventError(f"canonical task shard count mismatch: {shard_path}")
            expected_count += actual_shard_count
        if expected_count != manifest.get("record_count"):
            raise EventError("canonical task manifest record_count does not equal shard total")
        connection.execute("INSERT INTO meta(key, value) VALUES (?, ?)", ("task_manifest_sha256", task_manifest_sha))
        connection.commit()
        connection.close()
        os.replace(temp_path, index_path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()
    return index_path


def parse_event_file(path: Path, json_lines: bool) -> Iterator[dict[str, Any]]:
    if json_lines:
        with path.open("r", encoding="utf-8", newline="") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                if not raw_line.strip():
                    raise EventError(f"{path}:{line_number}: blank event lines are not allowed")
                try:
                    value = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise EventError(f"{path}:{line_number}: invalid JSON") from exc
                yield require_object(value, f"{path}:{line_number}")
    else:
        yield read_json(path)


def add_missing_event_id(event: dict[str, Any]) -> dict[str, Any]:
    if "event_id" in event:
        return event
    copied = dict(event)
    copied["event_id"] = str(uuid.uuid4())
    return copied


@contextlib.contextmanager
def advisory_lock(path: Path) -> Iterator[None]:
    """Use a small OS lock file so two local processes cannot interleave lines."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0)
        if not handle.read(1):
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                with contextlib.suppress(OSError):
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                with contextlib.suppress(OSError):
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def existing_event_ids(ledger: Path) -> set[str]:
    if not ledger.exists():
        return set()
    identifiers: set[str] = set()
    with ledger.open("r", encoding="utf-8", newline="") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                raise EventError(f"{ledger}:{line_number}: blank ledger line")
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise EventError(f"{ledger}:{line_number}: invalid ledger JSON") from exc
            event = validate_event(event)
            identifier = event["event_id"]
            if identifier in identifiers:
                raise EventError(f"{ledger}:{line_number}: duplicate existing event_id {identifier}")
            identifiers.add(identifier)
    return identifiers


def append_events(run_dir: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    index_path = ensure_task_index(run_dir)
    connection = sqlite3.connect(index_path)
    try:
        for event in events:
            found = connection.execute(
                "SELECT task_hash FROM tasks WHERE task_key=?", (event["task_key"],)
            ).fetchone()
            if not found:
                raise EventError(f"event task_key is not in canonical task collection: {event['task_key']}")
            if found[0] != event["task_content_sha256"]:
                raise EventError(
                    f"event task_content_sha256 does not bind to canonical task {event['task_key']}"
                )
    finally:
        connection.close()
    ledger = run_dir / "recovery_events.jsonl"
    lock_path = run_dir / ".recovery_events.lock"
    encoded = [canonical_json_bytes(event) + b"\n" for event in events]
    with advisory_lock(lock_path):
        identifiers = existing_event_ids(ledger)
        incoming = set()
        for event in events:
            identifier = event["event_id"]
            if identifier in identifiers or identifier in incoming:
                raise EventError(f"event_id already exists in ledger or supplied batch: {identifier}")
            incoming.add(identifier)
        with ledger.open("ab") as handle:
            for line in encoded:
                handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    return {
        "status": "appended",
        "run_dir": str(run_dir),
        "ledger": str(ledger),
        "events_appended": len(events),
        "event_ids": [event["event_id"] for event in events],
        "ledger_sha256": sha256_file(ledger),
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Append validated MathDB recovery-ledger event(s).")
    value.add_argument("--run-dir", type=Path, required=True)
    inputs = value.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--event", type=Path, help="one JSON event object")
    inputs.add_argument("--events-jsonl", type=Path, help="one JSON event object per line")
    return value


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        run_dir = args.run_dir.resolve()
        if not run_dir.is_dir():
            raise EventError(f"run directory does not exist: {run_dir}")
        event_file = (args.event or args.events_jsonl).resolve()
        if not event_file.is_file():
            raise EventError(f"event input does not exist: {event_file}")
        raw_events = parse_event_file(event_file, bool(args.events_jsonl))
        events = [validate_event(add_missing_event_id(event)) for event in raw_events]
        if not events:
            raise EventError("event input contained no events")
        report = append_events(run_dir, events)
    except (EventError, OSError, sqlite3.DatabaseError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
