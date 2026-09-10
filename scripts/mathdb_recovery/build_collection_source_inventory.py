#!/usr/bin/env python3
"""Build a non-destructive source/provenance inventory for five collections.

The MathDB v1.7 public-list snapshot has no native collection-membership
field.  This builder therefore keeps two deliberately separate facts:

* exact membership in the frozen, statement-bearing UnsolvedMath v1.6
  collections, identified by the preserved provenance fields; and
* optional *discovery-only* title-pattern counts in the frozen MathDB public
  catalogue.

It never asserts that a title-pattern candidate is a MathDB collection member,
never copies a statement into a new output, and never opens or rewrites the
v1.7 OPDP payload.  The JSONL overlap sidecar holds hashes and JSON pointers
back into v1.6 so a later curator can retrieve the already-frozen statement
without duplicating third-party text.
"""

from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator


TOOL_NAME = "OPDP MathDB collection source-inventory builder"
TOOL_VERSION = "1.0.0"
REGISTRY_SCHEMA = "opdp.mathdb.collection-source-registry.v1"
EXTERNAL_EVIDENCE_SCHEMA = "opdp.mathdb.collection-external-evidence.v1"
INVENTORY_SCHEMA = "opdp.mathdb.collection-source-inventory.v1"
OVERLAP_SCHEMA = "opdp.mathdb.collection-ulam-overlap.v1"
MATHDB_SUMMARY_SCHEMA = "opdp.mathdb.problem-summary.v1"


class InventoryError(RuntimeError):
    """An immutable-input or output-integrity failure."""


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary_path.unlink()


def parse_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InventoryError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InventoryError(f"{path}: expected JSON object")
    return value


def require_string(mapping: dict[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InventoryError(f"{context}: {key} must be a nonempty string")
    return value.strip()


def require_nonnegative_int(value: Any, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise InventoryError(f"{context}: expected a nonnegative integer")
    return value


def relative_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def load_registry(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    registry = parse_json_object(path)
    if registry.get("schema") != REGISTRY_SCHEMA:
        raise InventoryError(
            f"registry schema must be {REGISTRY_SCHEMA!r}, found {registry.get('schema')!r}"
        )
    collections = registry.get("collections")
    if not isinstance(collections, list) or not collections:
        raise InventoryError("registry.collections must be a nonempty array")
    expected_keys = {"erdos", "aim", "amr", "kourovka", "millennium"}
    keys: list[str] = []
    for index, collection in enumerate(collections):
        context = f"registry.collections[{index}]"
        if not isinstance(collection, dict):
            raise InventoryError(f"{context} must be an object")
        key = require_string(collection, "key", context)
        keys.append(key)
        target = collection.get("project_requested_mathdb_target_count")
        require_nonnegative_int(target, f"{context}.project_requested_mathdb_target_count")
        if collection.get("mathdb_membership_status") != "not_asserted_no_native_collection_field":
            raise InventoryError(
                f"{context}.mathdb_membership_status must preserve the no-native-field limitation"
            )
        rule = collection.get("ulam_overlap_rule")
        if not isinstance(rule, dict):
            raise InventoryError(f"{context}.ulam_overlap_rule must be an object")
        source_collection_id = rule.get("source_collection_id")
        if not isinstance(source_collection_id, int) or isinstance(source_collection_id, bool):
            raise InventoryError(f"{context}.ulam_overlap_rule.source_collection_id must be an integer")
        require_string(rule, "source_collection_label", f"{context}.ulam_overlap_rule")
        pattern = require_string(rule, "problem_number_regex", f"{context}.ulam_overlap_rule")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise InventoryError(f"{context}: invalid problem_number_regex: {exc}") from exc
        require_nonnegative_int(rule.get("expected_count"), f"{context}.ulam_overlap_rule.expected_count")
        sources = collection.get("sources")
        if not isinstance(sources, list) or not sources:
            raise InventoryError(f"{context}.sources must be a nonempty array")
    if set(keys) != expected_keys or len(keys) != len(expected_keys):
        raise InventoryError(
            "registry must contain exactly the five collection keys "
            f"{sorted(expected_keys)!r}, found {keys!r}"
        )
    return registry, collections


def load_ulam_records(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InventoryError(f"cannot read v1.6 payload {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise InventoryError("v1.6 payload must be an object")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise InventoryError("v1.6 payload.records must be a nonempty array")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise InventoryError(f"v1.6 record {index} must be an object")
    return payload, records


def iter_mathdb_summaries(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    try:
        handle = path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise InventoryError(f"cannot open MathDB summary file {path}: {exc}") from exc
    with handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                raise InventoryError(f"MathDB summary line {line_number} is blank")
            try:
                envelope = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise InventoryError(f"MathDB summary line {line_number}: invalid JSON: {exc}") from exc
            if not isinstance(envelope, dict) or envelope.get("schema") != MATHDB_SUMMARY_SCHEMA:
                raise InventoryError(
                    f"MathDB summary line {line_number}: expected {MATHDB_SUMMARY_SCHEMA!r} envelope"
                )
            problem = envelope.get("problem")
            if not isinstance(problem, dict):
                raise InventoryError(f"MathDB summary line {line_number}: problem must be an object")
            yield line_number, problem


def validate_mathdb_input(summary_path: Path, manifest_path: Path) -> tuple[dict[str, Any], int]:
    manifest = parse_json_object(manifest_path)
    catalog = manifest.get("catalog")
    if not isinstance(catalog, dict):
        raise InventoryError("MathDB catalog manifest lacks catalog object")
    expected_sha256 = require_string(catalog, "sha256", "MathDB catalog manifest.catalog")
    observed_sha256 = sha256_file(summary_path)
    if observed_sha256 != expected_sha256:
        raise InventoryError(
            f"MathDB summary SHA-256 mismatch: expected {expected_sha256}, observed {observed_sha256}"
        )
    expected_count = require_nonnegative_int(
        catalog.get("completed_count"), "MathDB catalog manifest.catalog.completed_count"
    )
    return manifest, expected_count


def load_external_evidence(path: Path | None, registry_path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    if path is None:
        return {}
    payload = parse_json_object(path)
    if payload.get("schema") != EXTERNAL_EVIDENCE_SCHEMA:
        raise InventoryError(
            f"external evidence schema must be {EXTERNAL_EVIDENCE_SCHEMA!r}, found {payload.get('schema')!r}"
        )
    registry = payload.get("registry")
    if not isinstance(registry, dict):
        raise InventoryError("external evidence lacks registry binding")
    expected_sha = sha256_file(registry_path)
    if registry.get("sha256") != expected_sha:
        raise InventoryError(
            "external evidence was captured against a different registry; recapture it or use the matching registry"
        )
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise InventoryError("external evidence entries must be an array")
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise InventoryError("external evidence entry must be an object")
        key = (require_string(entry, "collection_key", "external evidence"), require_string(entry, "source_key", "external evidence"))
        if key in index:
            raise InventoryError(f"duplicate external evidence entry {key!r}")
        index[key] = entry
    return index


def validate_external_evidence_coverage(
    collections: list[dict[str, Any]],
    evidence_path: Path | None,
    evidence: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """Reject partial or internally failed evidence when a capture is supplied."""

    if evidence_path is None:
        return
    expected: set[tuple[str, str]] = set()
    source_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for collection in collections:
        collection_key = collection["key"]
        for source in collection["sources"]:
            key = (collection_key, source["key"])
            expected.add(key)
            source_by_key[key] = source
    missing = sorted(expected - set(evidence))
    extra = sorted(set(evidence) - expected)
    if missing or extra:
        raise InventoryError(
            "external evidence must cover exactly the registry sources; "
            f"missing={missing!r}, extra={extra!r}"
        )
    for key, entry in evidence.items():
        source = source_by_key[key]
        if entry.get("canonical_url") != source.get("canonical_url"):
            raise InventoryError(f"external evidence {key!r}: canonical URL differs from registry")
        if entry.get("local_raw_copy_retained") is not False:
            raise InventoryError(f"external evidence {key!r}: raw-content retention guard failed")
        observation = entry.get("count_observation")
        if isinstance(observation, dict) and observation.get("matches_expected") is False:
            raise InventoryError(f"external evidence {key!r}: failed count assertion")


def compact_external_source(source: dict[str, Any], evidence: dict[str, Any] | None) -> dict[str, Any]:
    output: dict[str, Any] = {
        "source_key": require_string(source, "key", "registry source"),
        "role": source.get("role"),
        "canonical_url": require_string(source, "canonical_url", "registry source"),
        "format": source.get("format"),
        "reuse": source.get("reuse"),
    }
    if isinstance(source.get("statement_artifact_url"), str):
        output["statement_artifact_url_not_fetched"] = source["statement_artifact_url"]
    if evidence is None:
        output["retrieval_evidence"] = "not_supplied"
    else:
        output["retrieval_evidence"] = {
            key: evidence.get(key)
            for key in (
                "retrieved_at_utc",
                "requested_url",
                "final_url",
                "http_status",
                "content_type",
                "content_length_bytes",
                "content_sha256",
                "pinned_revision",
                "count_observation",
            )
            if key in evidence
        }
    return output


def ulam_member(record: dict[str, Any], rule: dict[str, Any]) -> bool:
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        return False
    if provenance.get("source_collection_id") != rule["source_collection_id"]:
        return False
    if provenance.get("source_collection_label") != rule["source_collection_label"]:
        return False
    problem_number = record.get("problem_number")
    return isinstance(problem_number, str) and re.fullmatch(rule["problem_number_regex"], problem_number) is not None


def validate_near_membership(record: dict[str, Any], rule: dict[str, Any], record_index: int) -> None:
    """Fail if a fixed collection ID has silently changed its label/prefix."""

    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        return
    if provenance.get("source_collection_id") != rule["source_collection_id"]:
        return
    label = provenance.get("source_collection_label")
    problem_number = record.get("problem_number")
    expected_label = rule["source_collection_label"]
    if label != expected_label:
        raise InventoryError(
            f"v1.6 record {record_index}: collection id {rule['source_collection_id']} has "
            f"label {label!r}, expected {expected_label!r}"
        )
    if not isinstance(problem_number, str) or re.fullmatch(rule["problem_number_regex"], problem_number) is None:
        raise InventoryError(
            f"v1.6 record {record_index}: collection {expected_label!r} has unexpected "
            f"problem number {problem_number!r}"
        )


def make_overlap_row(
    *,
    collection: dict[str, Any],
    record: dict[str, Any],
    record_index: int,
) -> dict[str, Any]:
    source_text = record.get("source_text")
    provenance = record.get("provenance")
    source_record = record.get("source_record")
    if not isinstance(source_text, dict) or not isinstance(provenance, dict):
        raise InventoryError(f"v1.6 record {record_index}: expected source_text and provenance objects")
    statement = source_text.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        raise InventoryError(f"v1.6 record {record_index}: selected overlap has no statement snapshot")
    if not isinstance(record.get("problem_id"), int) or isinstance(record["problem_id"], bool):
        raise InventoryError(f"v1.6 record {record_index}: problem_id must be an integer")
    problem_number = require_string(record, "problem_number", f"v1.6 record {record_index}")
    title = require_string(record, "title", f"v1.6 record {record_index}")
    row: dict[str, Any] = {
        "schema": OVERLAP_SCHEMA,
        "collection_key": collection["key"],
        "membership_method": "frozen_ulam_provenance_exact_match_v1",
        "ulam": {
            "record_index_zero_based": record_index,
            "record_json_pointer": f"/records/{record_index}",
            "problem_id": record["problem_id"],
            "problem_number": problem_number,
            "title": title,
            "record_canonical_sha256": sha256_bytes(canonical_json_bytes(record)),
            "source_record_canonical_sha256": (
                sha256_bytes(canonical_json_bytes(source_record)) if isinstance(source_record, dict) else None
            ),
            "source_text": {
                "statement_json_pointer": f"/records/{record_index}/source_text/statement",
                "statement_sha256": sha256_bytes(statement.encode("utf-8")),
                "statement_utf8_bytes": len(statement.encode("utf-8")),
                "text_mode": source_text.get("text_mode"),
                "statement_copied_into_this_inventory": False,
            },
            "provenance": {
                "source_collection_id": provenance.get("source_collection_id"),
                "source_collection_label": provenance.get("source_collection_label"),
                "ulam_url": provenance.get("ulam_url"),
                "canonical_source_url": provenance.get("canonical_source_url"),
                "source_url_used": provenance.get("source_url_used"),
                "source_url_kind": provenance.get("source_url_kind"),
                "declared_dataset_license": provenance.get("declared_dataset_license"),
            },
        },
        "recovery_status": "source_inventory_only",
        "mathdb_membership": "not_asserted_no_native_collection_field",
        "opdp_recalculation_allowed": False,
    }
    return row


def mathdb_discovery_counts(
    summary_path: Path, collections: list[dict[str, Any]]
) -> tuple[int, dict[str, dict[str, Any]]]:
    rules: list[tuple[str, re.Pattern[str]]] = []
    result: dict[str, dict[str, Any]] = {}
    for collection in collections:
        signal = collection.get("mathdb_discovery_signal")
        if signal is None:
            result[collection["key"]] = {
                "status": "not_run_no_safe_collection_signal_registered",
                "membership_interpretation": "not_asserted_no_native_collection_field",
            }
            continue
        if not isinstance(signal, dict) or signal.get("kind") != "strict_title_prefix":
            raise InventoryError(f"collection {collection['key']!r}: unsupported MathDB discovery signal")
        pattern = require_string(signal, "regex", f"collection {collection['key']!r} discovery signal")
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise InventoryError(f"collection {collection['key']!r}: invalid discovery regex: {exc}") from exc
        rules.append((collection["key"], compiled))
        result[collection["key"]] = {
            "status": "discovery_only_title_pattern",
            "regex": pattern,
            "candidate_record_count": 0,
            "candidate_numbers_sha256": None,
            "membership_interpretation": signal.get("interpretation"),
        }
    candidates: dict[str, list[int]] = {key: [] for key, _ in rules}
    count = 0
    for line_number, problem in iter_mathdb_summaries(summary_path):
        count += 1
        title = problem.get("title")
        number = problem.get("number")
        if not isinstance(title, str) or not isinstance(number, int) or isinstance(number, bool):
            raise InventoryError(f"MathDB summary line {line_number}: expected integer number and string title")
        for key, pattern in rules:
            if pattern.search(title):
                candidates[key].append(number)
    for key, numbers in candidates.items():
        result[key]["candidate_record_count"] = len(numbers)
        result[key]["candidate_numbers_sha256"] = sha256_bytes(
            canonical_json_bytes(numbers)
        )
    return count, result


def make_inventory(
    *,
    registry: dict[str, Any],
    registry_path: Path,
    ulam_path: Path,
    ulam_payload: dict[str, Any],
    mathdb_path: Path,
    mathdb_manifest_path: Path,
    mathdb_manifest: dict[str, Any],
    mathdb_count: int,
    discovery: dict[str, dict[str, Any]],
    collections: list[dict[str, Any]],
    overlap_rows: list[dict[str, Any]],
    external_evidence_path: Path | None,
    external_evidence: dict[tuple[str, str], dict[str, Any]],
    overlap_path: Path,
    generated_at_utc: str,
) -> dict[str, Any]:
    rows_by_collection: dict[str, list[dict[str, Any]]] = {collection["key"]: [] for collection in collections}
    for row in overlap_rows:
        rows_by_collection[row["collection_key"]].append(row)
    catalog = mathdb_manifest["catalog"]
    collection_entries: list[dict[str, Any]] = []
    for collection in collections:
        key = collection["key"]
        rule = collection["ulam_overlap_rule"]
        rows = rows_by_collection[key]
        expected = rule["expected_count"]
        if len(rows) != expected:
            raise InventoryError(
                f"collection {key!r}: found {len(rows)} frozen-Ulam records, expected {expected}"
            )
        source_entries = [
            compact_external_source(source, external_evidence.get((key, source["key"])))
            for source in collection["sources"]
        ]
        project_target = collection["project_requested_mathdb_target_count"]
        collection_entries.append(
            {
                "collection_key": key,
                "display_name": collection["display_name"],
                "project_requested_mathdb_target_count": project_target,
                "mathdb_membership": {
                    "status": collection["mathdb_membership_status"],
                    "reason": "The frozen opdp.mathdb.problem-summary.v1 envelope has no native collection-id or collection-label field. A title, tag, URL lead, or overlap with Ulam cannot be silently promoted to membership.",
                    "discovery_signal": discovery[key],
                },
                "frozen_ulam_overlap": {
                    "membership_method": "frozen_ulam_provenance_exact_match_v1",
                    "rule": rule,
                    "count": len(rows),
                    "target_minus_ulam_overlap": project_target - len(rows),
                    "statement_coverage": {
                        "records_with_nonempty_frozen_statement": len(rows),
                        "statement_text_repeated_here": False,
                        "statement_access": "Use the JSON pointer and source_text.statement SHA-256 in the overlap JSONL to retrieve the frozen v1.6 snapshot.",
                    },
                },
                "external_source_provenance": source_entries,
                "source_reuse_caveat": "The UnsolvedMath declared CC-BY-4.0 dataset license applies to the frozen dataset snapshot; it does not establish rights in each underlying original collection document. Follow the per-source caveats above before publishing recovered statement text.",
                "recovery_state": "inventory_complete_statement_recovery_not_started",
                "opdp_recalculation_allowed": False,
            }
        )
    overlap_bytes = b"".join(canonical_json_bytes(row) + b"\n" for row in overlap_rows)
    payload: dict[str, Any] = {
        "schema": INVENTORY_SCHEMA,
        "generated_at_utc": generated_at_utc,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "non_destructive_contract": {
            "v17_payload_modified": False,
            "full_statements_copied_into_inventory": False,
            "mathdb_membership_claimed_for_any_collection": False,
            "opdp_dimensions_recalculated": False,
        },
        "matching_policy": {
            "ulam_overlap": "Exact match of frozen Ulam collection id, collection label, and display identifier against a versioned registry rule.",
            "mathdb": "No membership assertion without an independently auditable statement/source match. Registered title patterns are discovery-only diagnostics.",
            "statement_recovery": "A later recovery event must bind a MathDB-declared source or otherwise retain an explicit contextual/unresolved label; this inventory does not create recovery events.",
        },
        "inputs": {
            "registry": {
                "path": relative_path(registry_path),
                "sha256": sha256_file(registry_path),
                "schema": registry["schema"],
                "registry_version": registry.get("registry_version"),
            },
            "frozen_ulam_v1_6": {
                "path": relative_path(ulam_path),
                "sha256": sha256_file(ulam_path),
                "record_count": len(ulam_payload["records"]),
                "assessment_release": {
                    key: ulam_payload.get("assessment_release", {}).get(key)
                    for key in ("framework", "framework_version", "rule_version", "assessment_date")
                },
                "declared_dataset_license": "CC-BY-4.0",
            },
            "frozen_mathdb_public_catalog": {
                "path": relative_path(mathdb_path),
                "sha256": sha256_file(mathdb_path),
                "record_count": mathdb_count,
                "manifest_path": relative_path(mathdb_manifest_path),
                "manifest_sha256": sha256_file(mathdb_manifest_path),
                "snapshot_status": catalog.get("status"),
                "text_basis": catalog.get("text_basis"),
                "source_record_semantics": catalog.get("source_record_semantics"),
            },
            "external_evidence": (
                {
                    "path": relative_path(external_evidence_path),
                    "sha256": sha256_file(external_evidence_path),
                    "entry_count": len(external_evidence),
                    "raw_response_bodies_retained": False,
                }
                if external_evidence_path is not None
                else None
            ),
        },
        "overlap_artifact": {
            "path": relative_path(overlap_path),
            "schema": OVERLAP_SCHEMA,
            "record_count": len(overlap_rows),
            "sha256": sha256_bytes(overlap_bytes),
            "contains_full_statements": False,
        },
        "collections": collection_entries,
    }
    return payload


def verify_outputs(inventory_path: Path, overlap_path: Path) -> None:
    inventory = parse_json_object(inventory_path)
    if inventory.get("schema") != INVENTORY_SCHEMA:
        raise InventoryError(f"{inventory_path}: wrong inventory schema")
    artifact = inventory.get("overlap_artifact")
    if not isinstance(artifact, dict):
        raise InventoryError(f"{inventory_path}: missing overlap_artifact")
    observed_sha = sha256_file(overlap_path)
    if artifact.get("sha256") != observed_sha:
        raise InventoryError(
            f"{overlap_path}: SHA-256 differs from inventory binding; expected {artifact.get('sha256')}, observed {observed_sha}"
        )
    rows = 0
    seen: set[tuple[str, int]] = set()
    try:
        handle = overlap_path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise InventoryError(f"cannot read overlap file {overlap_path}: {exc}") from exc
    with handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise InventoryError(f"{overlap_path} line {line_number}: blank line")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise InventoryError(f"{overlap_path} line {line_number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict) or row.get("schema") != OVERLAP_SCHEMA:
                raise InventoryError(f"{overlap_path} line {line_number}: wrong overlap schema")
            if row.get("mathdb_membership") != "not_asserted_no_native_collection_field":
                raise InventoryError(f"{overlap_path} line {line_number}: illegal MathDB membership claim")
            ulam = row.get("ulam")
            if not isinstance(ulam, dict) or not isinstance(ulam.get("problem_id"), int):
                raise InventoryError(f"{overlap_path} line {line_number}: invalid Ulam pointer")
            key = (str(row.get("collection_key")), ulam["problem_id"])
            if key in seen:
                raise InventoryError(f"{overlap_path} line {line_number}: duplicate overlap row {key!r}")
            seen.add(key)
            if ulam.get("source_text", {}).get("statement_copied_into_this_inventory") is not False:
                raise InventoryError(f"{overlap_path} line {line_number}: statement-copy guard failed")
            rows += 1
    if artifact.get("record_count") != rows:
        raise InventoryError(
            f"{overlap_path}: expected {artifact.get('record_count')} rows, observed {rows}"
        )
    collections = inventory.get("collections")
    if not isinstance(collections, list) or len(collections) != 5:
        raise InventoryError(f"{inventory_path}: expected five collection entries")
    if inventory.get("non_destructive_contract", {}).get("v17_payload_modified") is not False:
        raise InventoryError(f"{inventory_path}: non-destructive v1.7 guard failed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("scripts/mathdb_recovery/collection_source_registry.v1.json"),
    )
    parser.add_argument(
        "--ulam-v16",
        type=Path,
        default=Path("data/Ulam_UnsolvedMath_OPDP_Assessments_v1.6.json.gz"),
    )
    parser.add_argument(
        "--mathdb-catalog",
        type=Path,
        default=Path("../opdp-v1_7-mathdb-build/mathdb-snapshot-2026-09-08/mathdb_problem_summaries.jsonl"),
    )
    parser.add_argument(
        "--mathdb-manifest",
        type=Path,
        default=Path("../opdp-v1_7-mathdb-build/mathdb-snapshot-2026-09-08/mathdb_catalog_manifest.json"),
    )
    parser.add_argument(
        "--external-evidence", type=Path, default=Path("data/MATHDB_COLLECTION_EXTERNAL_EVIDENCE_v1.json")
    )
    parser.add_argument(
        "--inventory-output",
        type=Path,
        default=Path("data/MATHDB_COLLECTION_SOURCE_INVENTORY_v1.json"),
    )
    parser.add_argument(
        "--overlap-output",
        type=Path,
        default=Path("data/MATHDB_COLLECTION_ULAM_OVERLAP_v1.jsonl"),
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="verify existing inventory/overlap outputs without loading corpus sources",
    )
    parser.add_argument(
        "--generated-at-utc",
        default=None,
        help="optional ISO-8601 UTC timestamp for reproducible output metadata",
    )
    arguments = parser.parse_args(argv)
    try:
        if arguments.verify_only:
            verify_outputs(arguments.inventory_output, arguments.overlap_output)
            log("collection source inventory verification passed")
            return 0
        registry, collections = load_registry(arguments.registry)
        ulam_payload, ulam_records = load_ulam_records(arguments.ulam_v16)
        mathdb_manifest, expected_mathdb_count = validate_mathdb_input(
            arguments.mathdb_catalog, arguments.mathdb_manifest
        )
        evidence_path = arguments.external_evidence if arguments.external_evidence.exists() else None
        external_evidence = load_external_evidence(evidence_path, arguments.registry)
        validate_external_evidence_coverage(collections, evidence_path, external_evidence)

        overlap_rows: list[dict[str, Any]] = []
        for collection in collections:
            rule = collection["ulam_overlap_rule"]
            for index, record in enumerate(ulam_records):
                validate_near_membership(record, rule, index)
                if ulam_member(record, rule):
                    overlap_rows.append(
                        make_overlap_row(collection=collection, record=record, record_index=index)
                    )
        order = {collection["key"]: index for index, collection in enumerate(collections)}
        overlap_rows.sort(key=lambda row: (order[row["collection_key"]], row["ulam"]["problem_id"]))

        mathdb_count, discovery = mathdb_discovery_counts(arguments.mathdb_catalog, collections)
        if mathdb_count != expected_mathdb_count:
            raise InventoryError(
                f"MathDB summary count mismatch: manifest says {expected_mathdb_count}, observed {mathdb_count}"
            )
        timestamp = arguments.generated_at_utc or utc_now()
        inventory = make_inventory(
            registry=registry,
            registry_path=arguments.registry,
            ulam_path=arguments.ulam_v16,
            ulam_payload=ulam_payload,
            mathdb_path=arguments.mathdb_catalog,
            mathdb_manifest_path=arguments.mathdb_manifest,
            mathdb_manifest=mathdb_manifest,
            mathdb_count=mathdb_count,
            discovery=discovery,
            collections=collections,
            overlap_rows=overlap_rows,
            external_evidence_path=evidence_path,
            external_evidence=external_evidence,
            overlap_path=arguments.overlap_output,
            generated_at_utc=timestamp,
        )
        overlap_bytes = b"".join(canonical_json_bytes(row) + b"\n" for row in overlap_rows)
        atomic_write(arguments.overlap_output, overlap_bytes)
        atomic_write(arguments.inventory_output, canonical_json_bytes(inventory) + b"\n")
        verify_outputs(arguments.inventory_output, arguments.overlap_output)
        log(
            f"wrote {arguments.inventory_output} and {arguments.overlap_output} "
            f"({len(overlap_rows)} frozen-Ulam overlap records)"
        )
        return 0
    except InventoryError as exc:
        log(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
