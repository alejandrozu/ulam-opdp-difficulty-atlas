#!/usr/bin/env python3
"""Build a provenance-only canonical-record manifest for five collections.

The MathDB public-list snapshot has no collection-membership field, so this
tool deliberately does *not* attach a MathDB item to a named collection.  It
instead turns the pre-existing, exact frozen-Ulam collection overlaps into a
record-level source-lead manifest.  That gives later recovery work stable
identifiers, source locators, retrieval/reuse facts, and an explicit gate
without copying a statement, title, background, PDF page, or source excerpt.

The only optional network action is a single, pinned Erdős YAML request.  Its
body is held in memory only long enough to verify the pinned SHA-256 and map
the already-public numeric identifiers to YAML line spans; it is never written
to disk or emitted by this script.  Every other collection source is retained
as a frozen-Ulam document lead until a separately reviewed, record-level
artifact acquisition creates a passage locator.

No command in this module changes an OPDP release payload, assigns a recovery
outcome, asserts MathDB membership, assesses whether a problem is open, or
recalculates a score.
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
import urllib.error
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


TOOL_NAME = "OPDP MathDB collection canonical-record manifest builder"
TOOL_VERSION = "1.0.0"
REGISTRY_SCHEMA = "opdp.mathdb.collection-source-registry.v1"
EVIDENCE_SCHEMA = "opdp.mathdb.collection-external-evidence.v1"
INVENTORY_SCHEMA = "opdp.mathdb.collection-source-inventory.v1"
OVERLAP_SCHEMA = "opdp.mathdb.collection-ulam-overlap.v1"
LOCATOR_INDEX_SCHEMA = "opdp.mathdb.erdos-pinned-record-locators.v1"
ROW_SCHEMA = "opdp.mathdb.collection-canonical-record.v1"
SUMMARY_SCHEMA = "opdp.mathdb.collection-canonical-record-manifest.v1"
DEFAULT_USER_AGENT = (
    "OPDP-Collection-Canonical-Record-Manifest/1.0 "
    "(+https://github.com/alejandrozu/ulam-opdp-difficulty-atlas)"
)
ERDOS_NUMBER_LINE = re.compile(r'''(?m)^- number:\s+["']?([0-9]+)["']?\s*$''')


class ManifestError(RuntimeError):
    """An immutable-input or provenance-gate failure."""


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


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ManifestError(f"cannot read {path}: {exc}") from exc
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


def load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManifestError(f"{label} {path}: expected a JSON object")
    return value


def require_string(mapping: dict[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{context}: {key} must be a nonempty string")
    return value.strip()


def require_int(value: Any, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ManifestError(f"{context}: expected an integer")
    return value


def relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path).replace("\\", "/")


def parse_registry(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    registry = load_json_object(path, "registry")
    if registry.get("schema") != REGISTRY_SCHEMA:
        raise ManifestError(f"{path}: unsupported registry schema {registry.get('schema')!r}")
    collections_value = registry.get("collections")
    if not isinstance(collections_value, list) or len(collections_value) != 5:
        raise ManifestError(f"{path}: expected five collection specifications")
    collections: dict[str, dict[str, Any]] = {}
    for index, collection in enumerate(collections_value):
        context = f"registry.collections[{index}]"
        if not isinstance(collection, dict):
            raise ManifestError(f"{context}: expected object")
        key = require_string(collection, "key", context)
        if key in collections:
            raise ManifestError(f"{context}: duplicate collection key {key!r}")
        require_string(collection, "display_name", context)
        rule = collection.get("ulam_overlap_rule")
        if not isinstance(rule, dict):
            raise ManifestError(f"{context}: missing ulam_overlap_rule")
        for field in ("source_collection_label", "problem_number_regex"):
            require_string(rule, field, f"{context}.ulam_overlap_rule")
        require_int(rule.get("source_collection_id"), f"{context}.ulam_overlap_rule.source_collection_id")
        expected_count = require_int(rule.get("expected_count"), f"{context}.ulam_overlap_rule.expected_count")
        if expected_count < 1:
            raise ManifestError(f"{context}: expected_count must be positive")
        sources = collection.get("sources")
        if not isinstance(sources, list) or not sources:
            raise ManifestError(f"{context}: expected sources")
        collections[key] = collection
    expected_keys = {"erdos", "aim", "amr", "kourovka", "millennium"}
    if set(collections) != expected_keys:
        raise ManifestError(f"{path}: unexpected collection key set {sorted(collections)!r}")
    return registry, collections


def parse_evidence(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    evidence = load_json_object(path, "external evidence")
    if evidence.get("schema") != EVIDENCE_SCHEMA:
        raise ManifestError(f"{path}: unsupported evidence schema {evidence.get('schema')!r}")
    entries = evidence.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ManifestError(f"{path}: entries must be a nonempty list")
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        context = f"external evidence entry {index}"
        if not isinstance(entry, dict):
            raise ManifestError(f"{context}: expected object")
        collection_key = require_string(entry, "collection_key", context)
        source_key = require_string(entry, "source_key", context)
        content_sha256 = require_string(entry, "content_sha256", context)
        if not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
            raise ManifestError(f"{context}: invalid content SHA-256")
        identity = (collection_key, source_key)
        if identity in result:
            raise ManifestError(f"{context}: duplicate evidence identity {identity!r}")
        result[identity] = entry
    return result


def parse_inventory(path: Path) -> dict[str, Any]:
    inventory = load_json_object(path, "collection inventory")
    if inventory.get("schema") != INVENTORY_SCHEMA:
        raise ManifestError(f"{path}: unsupported inventory schema {inventory.get('schema')!r}")
    contract = inventory.get("non_destructive_contract")
    if not isinstance(contract, dict):
        raise ManifestError(f"{path}: missing non_destructive_contract")
    if contract.get("full_statements_copied_into_inventory") is not False:
        raise ManifestError(f"{path}: inventory statement-copy guard failed")
    if contract.get("mathdb_membership_claimed_for_any_collection") is not False:
        raise ManifestError(f"{path}: inventory MathDB-membership guard failed")
    if contract.get("opdp_dimensions_recalculated") is not False:
        raise ManifestError(f"{path}: inventory rescore guard failed")
    return inventory


def parse_overlap_rows(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    rows: dict[tuple[str, int], dict[str, Any]] = {}
    try:
        handle = path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise ManifestError(f"cannot read overlap artifact {path}: {exc}") from exc
    with handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise ManifestError(f"{path} line {line_number}: blank line")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ManifestError(f"{path} line {line_number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict) or row.get("schema") != OVERLAP_SCHEMA:
                raise ManifestError(f"{path} line {line_number}: wrong row schema")
            collection_key = require_string(row, "collection_key", f"{path} line {line_number}")
            ulam = row.get("ulam")
            if not isinstance(ulam, dict):
                raise ManifestError(f"{path} line {line_number}: missing Ulam object")
            problem_id = require_int(ulam.get("problem_id"), f"{path} line {line_number} Ulam problem_id")
            identity = (collection_key, problem_id)
            if identity in rows:
                raise ManifestError(f"{path} line {line_number}: duplicate overlap identity {identity!r}")
            source_text = ulam.get("source_text")
            if not isinstance(source_text, dict):
                raise ManifestError(f"{path} line {line_number}: missing source-text evidence")
            if source_text.get("statement_copied_into_this_inventory") is not False:
                raise ManifestError(f"{path} line {line_number}: statement-copy guard failed")
            if row.get("mathdb_membership") != "not_asserted_no_native_collection_field":
                raise ManifestError(f"{path} line {line_number}: illegal MathDB membership claim")
            if row.get("opdp_recalculation_allowed") is not False:
                raise ManifestError(f"{path} line {line_number}: illegal recalculation permission")
            rows[identity] = row
    return rows


def load_ulam_records(path: Path) -> list[dict[str, Any]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read frozen Ulam v1.6 {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ManifestError(f"{path}: expected a top-level records list")
    records = payload["records"]
    if len(records) != 15458:
        raise ManifestError(f"{path}: expected 15,458 frozen records, found {len(records)}")
    if any(not isinstance(record, dict) for record in records):
        raise ManifestError(f"{path}: records must all be objects")
    return records


def ulam_member(record: dict[str, Any], collection: dict[str, Any]) -> bool:
    rule = collection["ulam_overlap_rule"]
    provenance = record.get("provenance")
    if not isinstance(provenance, dict):
        return False
    if provenance.get("source_collection_id") != rule["source_collection_id"]:
        return False
    if provenance.get("source_collection_label") != rule["source_collection_label"]:
        return False
    problem_number = record.get("problem_number")
    return isinstance(problem_number, str) and re.fullmatch(rule["problem_number_regex"], problem_number) is not None


def selected_records(
    records: Iterable[dict[str, Any]], collections: dict[str, dict[str, Any]]
) -> list[tuple[str, int, dict[str, Any]]]:
    selected: list[tuple[str, int, dict[str, Any]]] = []
    counts: Counter[str] = Counter()
    for index, record in enumerate(records):
        for collection_key, collection in collections.items():
            if ulam_member(record, collection):
                selected.append((collection_key, index, record))
                counts[collection_key] += 1
                break
    for key, collection in collections.items():
        expected = collection["ulam_overlap_rule"]["expected_count"]
        if counts[key] != expected:
            raise ManifestError(
                f"collection {key!r}: expected {expected} frozen Ulam records, found {counts[key]}"
            )
    if len(selected) != 7489:
        raise ManifestError(f"expected 7,489 frozen Ulam overlap records, found {len(selected)}")
    return selected


def erdos_source(collection: dict[str, Any]) -> dict[str, Any]:
    for source in collection["sources"]:
        if isinstance(source, dict) and source.get("key") == "erdosproblems_ground_truth_yaml":
            return source
    raise ManifestError("registry: missing erdosproblems_ground_truth_yaml source")


def make_erdos_locator_index(
    *,
    registry_path: Path,
    collection: dict[str, Any],
    timeout_seconds: float,
    max_bytes: int,
    user_agent: str,
) -> dict[str, Any]:
    """Fetch exactly one pinned YAML source and emit only record locators/hashes."""
    source = erdos_source(collection)
    retrieval_url = require_string(source, "retrieval_url", "Erdős registry source")
    canonical_url = require_string(source, "canonical_url", "Erdős registry source")
    expected_sha256 = require_string(source, "content_sha256_expected", "Erdős registry source")
    pinned_revision = require_string(source, "pinned_revision", "Erdős registry source")
    count_extractor = source.get("count_extractor")
    if not isinstance(count_extractor, dict):
        raise ManifestError("Erdős registry source: missing count extractor")
    expected_record_count = require_int(
        count_extractor.get("expected_count"), "Erdős registry source count extractor"
    )
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ManifestError("Erdős registry source: malformed expected SHA-256")
    request = urllib.request.Request(
        retrieval_url,
        headers={"User-Agent": user_agent, "Accept": "text/plain"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", None)
            final_url = response.geturl()
            body = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        raise ManifestError(f"Erdős locator fetch: HTTP {exc.code} from {retrieval_url}") from exc
    except urllib.error.URLError as exc:
        raise ManifestError(f"Erdős locator fetch: network error for {retrieval_url}: {exc}") from exc
    if status is not None and not 200 <= int(status) < 300:
        raise ManifestError(f"Erdős locator fetch: unexpected HTTP status {status}")
    if len(body) > max_bytes:
        raise ManifestError(
            f"Erdős locator fetch: response exceeds --max-response-bytes={max_bytes}"
        )
    observed_sha256 = sha256_bytes(body)
    if observed_sha256 != expected_sha256:
        raise ManifestError(
            "Erdős locator fetch: pinned content hash mismatch; "
            f"expected {expected_sha256}, observed {observed_sha256}"
        )
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError("Erdős locator fetch: pinned YAML is not UTF-8") from exc
    matches = list(ERDOS_NUMBER_LINE.finditer(text))
    if len(matches) != expected_record_count:
        raise ManifestError(
            "Erdős locator fetch: expected "
            f"{expected_record_count:,} top-level records, found {len(matches):,}"
        )
    records: list[dict[str, Any]] = []
    seen_numbers: set[int] = set()
    for position, match in enumerate(matches):
        number = int(match.group(1))
        if number in seen_numbers:
            raise ManifestError(f"Erdős locator fetch: duplicate source number {number}")
        seen_numbers.add(number)
        line_start = text.count("\n", 0, match.start()) + 1
        if position + 1 < len(matches):
            line_end = text.count("\n", 0, matches[position + 1].start())
        else:
            line_end = text.count("\n") + 1
        if line_end < line_start:
            raise ManifestError(f"Erdős locator fetch: invalid line span for source number {number}")
        records.append(
            {
                "upstream_number": number,
                "stable_source_id": (
                    f"erdosproblems:{pinned_revision}:{number}"
                ),
                "locator": {
                    "kind": "pinned_yaml_line_span",
                    "url": f"{canonical_url}#L{line_start}-L{line_end}",
                    "line_start": line_start,
                    "line_end": line_end,
                    "artifact_sha256": observed_sha256,
                },
            }
        )
    return {
        "schema": LOCATOR_INDEX_SCHEMA,
        "generated_at_utc": utc_now(),
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "non_destructive_contract": {
            "raw_yaml_retained": False,
            "statement_text_retained": False,
            "mathdb_membership_claimed": False,
            "opdp_dimensions_recalculated": False,
        },
        "registry": {"path": relative_path(registry_path), "sha256": sha256_file(registry_path)},
        "source_artifact": {
            "source_key": source.get("key"),
            "canonical_url": canonical_url,
            "requested_url": retrieval_url,
            "final_url": final_url,
            "pinned_revision": pinned_revision,
            "content_sha256": observed_sha256,
            "content_length_bytes": len(body),
            "retrieval_status": "verified_transient_retrieval_body_discarded",
            "reuse": source.get("reuse"),
        },
        "records": records,
    }


def validate_erdos_locator_index(
    value: dict[str, Any], registry_path: Path, collection: dict[str, Any]
) -> dict[int, dict[str, Any]]:
    if value.get("schema") != LOCATOR_INDEX_SCHEMA:
        raise ManifestError("Erdős locator index: wrong schema")
    contract = value.get("non_destructive_contract")
    if not isinstance(contract, dict) or contract.get("raw_yaml_retained") is not False:
        raise ManifestError("Erdős locator index: raw-source retention guard failed")
    if contract.get("statement_text_retained") is not False:
        raise ManifestError("Erdős locator index: statement retention guard failed")
    if contract.get("mathdb_membership_claimed") is not False:
        raise ManifestError("Erdős locator index: MathDB-membership guard failed")
    if contract.get("opdp_dimensions_recalculated") is not False:
        raise ManifestError("Erdős locator index: rescore guard failed")
    registry = value.get("registry")
    if not isinstance(registry, dict) or registry.get("sha256") != sha256_file(registry_path):
        raise ManifestError("Erdős locator index: registry hash mismatch")
    source = erdos_source(collection)
    pinned_revision = require_string(source, "pinned_revision", "Erdős registry source")
    count_extractor = source.get("count_extractor")
    if not isinstance(count_extractor, dict):
        raise ManifestError("Erdős locator index: registry count extractor missing")
    expected_record_count = require_int(
        count_extractor.get("expected_count"), "Erdős locator index registry count extractor"
    )
    artifact = value.get("source_artifact")
    if not isinstance(artifact, dict):
        raise ManifestError("Erdős locator index: missing source artifact")
    if artifact.get("content_sha256") != source.get("content_sha256_expected"):
        raise ManifestError("Erdős locator index: pinned source hash mismatch")
    if artifact.get("canonical_url") != source.get("canonical_url"):
        raise ManifestError("Erdős locator index: canonical source URL mismatch")
    records = value.get("records")
    if not isinstance(records, list) or len(records) != expected_record_count:
        raise ManifestError(
            f"Erdős locator index: expected {expected_record_count:,} records"
        )
    result: dict[int, dict[str, Any]] = {}
    for index, row in enumerate(records):
        context = f"Erdős locator index record {index}"
        if not isinstance(row, dict):
            raise ManifestError(f"{context}: expected object")
        number = require_int(row.get("upstream_number"), context)
        if number < 1 or number in result:
            raise ManifestError(f"{context}: duplicate/invalid upstream number {number}")
        stable_id = require_string(row, "stable_source_id", context)
        expected_id = f"erdosproblems:{pinned_revision}:{number}"
        if stable_id != expected_id:
            raise ManifestError(f"{context}: stable source ID mismatch")
        locator = row.get("locator")
        if not isinstance(locator, dict):
            raise ManifestError(f"{context}: missing locator")
        if locator.get("kind") != "pinned_yaml_line_span":
            raise ManifestError(f"{context}: wrong locator kind")
        line_start = require_int(locator.get("line_start"), context)
        line_end = require_int(locator.get("line_end"), context)
        if line_start < 1 or line_end < line_start:
            raise ManifestError(f"{context}: invalid line range")
        if locator.get("artifact_sha256") != source.get("content_sha256_expected"):
            raise ManifestError(f"{context}: artifact hash mismatch")
        expected_fragment = f"#L{line_start}-L{line_end}"
        if not require_string(locator, "url", context).endswith(expected_fragment):
            raise ManifestError(f"{context}: locator URL does not bind its line range")
        result[number] = row
    return result


def write_erdos_locator_index(path: Path, value: dict[str, Any]) -> None:
    atomic_write(path, canonical_json_bytes(value) + b"\n")


def load_erdos_locator_index(
    path: Path, registry_path: Path, collection: dict[str, Any]
) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    value = load_json_object(path, "Erdős locator index")
    return value, validate_erdos_locator_index(value, registry_path, collection)


def source_for_key(collection: dict[str, Any], source_key: str) -> dict[str, Any]:
    for source in collection["sources"]:
        if isinstance(source, dict) and source.get("key") == source_key:
            return source
    raise ManifestError(f"collection {collection.get('key')!r}: missing source key {source_key!r}")


def evidence_descriptor(evidence: dict[str, Any]) -> dict[str, Any]:
    """Return non-body facts about a captured collection index."""
    return {
        "source_key": evidence.get("source_key"),
        "canonical_url": evidence.get("canonical_url"),
        "content_sha256": evidence.get("content_sha256"),
        "content_length_bytes": evidence.get("content_length_bytes"),
        "retrieved_at_utc": evidence.get("retrieved_at_utc"),
        "raw_response_bodies_retained": evidence.get("local_raw_copy_retained"),
        "retrieval_status": "hash_only_collection_evidence",
    }


def canonical_url_from_record(record: dict[str, Any]) -> str | None:
    provenance = record.get("provenance")
    source_record = record.get("source_record")
    if not isinstance(provenance, dict):
        return None
    candidate = provenance.get("canonical_source_url")
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    if isinstance(source_record, dict):
        candidate = source_record.get("source_url")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    candidate = provenance.get("source_url_used")
    if isinstance(candidate, str) and candidate.strip() and provenance.get("source_url_kind") != "ulam_fallback":
        return candidate.strip()
    return None


def stable_url_id(prefix: str, url: str) -> str:
    return f"{prefix}:url-sha256:{sha256_text(url)}"


def make_record_row(
    *,
    collection_key: str,
    collection: dict[str, Any],
    record_index: int,
    record: dict[str, Any],
    overlap: dict[str, Any],
    evidence: dict[tuple[str, str], dict[str, Any]],
    erdos_locator_rows: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    problem_id = require_int(record.get("problem_id"), f"frozen Ulam record {record_index}")
    problem_number = require_string(record, "problem_number", f"frozen Ulam record {record_index}")
    ulam = overlap.get("ulam")
    if not isinstance(ulam, dict):
        raise ManifestError(f"overlap {collection_key}/{problem_id}: missing Ulam evidence")
    if ulam.get("record_index_zero_based") != record_index:
        raise ManifestError(f"overlap {collection_key}/{problem_id}: record index mismatch")
    if ulam.get("problem_number") != problem_number:
        raise ManifestError(f"overlap {collection_key}/{problem_id}: display identifier mismatch")
    if ulam.get("record_canonical_sha256") != sha256_bytes(canonical_json_bytes(record)):
        raise ManifestError(f"overlap {collection_key}/{problem_id}: frozen record hash mismatch")
    source_text = record.get("source_text")
    if not isinstance(source_text, dict) or not isinstance(source_text.get("statement"), str):
        raise ManifestError(f"frozen Ulam record {record_index}: missing frozen statement")
    statement = source_text["statement"]
    overlap_source_text = ulam.get("source_text")
    if not isinstance(overlap_source_text, dict):
        raise ManifestError(f"overlap {collection_key}/{problem_id}: missing statement binding")
    if overlap_source_text.get("statement_sha256") != sha256_text(statement):
        raise ManifestError(f"overlap {collection_key}/{problem_id}: statement hash mismatch")
    source_record = record.get("source_record")
    source_record_hash = (
        sha256_bytes(canonical_json_bytes(source_record)) if isinstance(source_record, dict) else None
    )
    if ulam.get("source_record_canonical_sha256") != source_record_hash:
        raise ManifestError(f"overlap {collection_key}/{problem_id}: source-record hash mismatch")
    frozen_binding = {
        "record_key": f"frozen-ulam:{problem_id}",
        "problem_id": problem_id,
        "problem_number": problem_number,
        "record_json_pointer": ulam.get("record_json_pointer"),
        "record_canonical_sha256": ulam.get("record_canonical_sha256"),
        "source_record_canonical_sha256": source_record_hash,
        "statement_json_pointer": overlap_source_text.get("statement_json_pointer"),
        "statement_sha256": overlap_source_text.get("statement_sha256"),
        "statement_utf8_bytes": overlap_source_text.get("statement_utf8_bytes"),
        "statement_text_copied_here": False,
    }

    claims_not_made = {
        "mathdb_membership": "not_asserted_no_native_collection_field",
        "source_statement_recovery_outcome": "not_assigned_by_manifest",
        "current_open_status": "not_assessed_by_manifest",
        "opdp_recalculation_allowed": False,
    }
    if collection_key == "erdos":
        match = re.fullmatch(r"EP-([0-9]+)", problem_number)
        if match is None:
            raise ManifestError(f"Erdős frozen identifier {problem_number!r} has wrong syntax")
        number = int(match.group(1))
        locator = erdos_locator_rows.get(number)
        if locator is None:
            raise ManifestError(
                f"Erdős frozen identifier {problem_number!r} has no pinned upstream YAML locator"
            )
        source = source_for_key(collection, "erdosproblems_ground_truth_yaml")
        source_evidence = evidence.get(("erdos", "erdosproblems_ground_truth_yaml"))
        if source_evidence is None:
            raise ManifestError("missing captured Erdős upstream evidence")
        return {
            "schema": ROW_SCHEMA,
            "collection_key": collection_key,
            "frozen_ulam": frozen_binding,
            "upstream_identity": {
                "status": "exact_pinned_upstream_numeric_identifier",
                "stable_source_id": locator["stable_source_id"],
                "upstream_record_number": number,
                "identity_binding": "EP-n maps exactly to pinned erdosproblems YAML top-level number n.",
            },
            "source_locator": {
                "quality": "exact_record_locator_not_semantic_comparison",
                "locator": locator["locator"],
                "source_artifact": evidence_descriptor(source_evidence),
                "source_retrieval_status": "pinned_yaml_hash_verified_and_line_locator_derived",
                "reuse": source.get("reuse"),
            },
            "recovery_eligibility": {
                "statement_comparison_eligible": True,
                "automatic_recovery_outcome_allowed": False,
                "reason": (
                    "The manifest has a pinned, hash-bound record locator, but no semantic comparison, "
                    "self-contained restatement review, rights review for cited original material, or "
                    "independent current-open verification has occurred."
                ),
                "opdp_recalculation_allowed": False,
            },
            "claims_not_made": claims_not_made,
        }

    if collection_key in {"aim", "amr"}:
        url = canonical_url_from_record(record)
        if url is None:
            raise ManifestError(f"{collection_key}/{problem_id}: missing frozen document URL")
        index_source_key = "aim_problem_lists_index" if collection_key == "aim" else "amr_resources_index"
        index_source = source_for_key(collection, index_source_key)
        index_evidence = evidence.get((collection_key, index_source_key))
        if index_evidence is None:
            raise ManifestError(f"missing captured {collection_key} collection-index evidence")
        return {
            "schema": ROW_SCHEMA,
            "collection_key": collection_key,
            "frozen_ulam": frozen_binding,
            "upstream_identity": {
                "status": "frozen_declared_document_url_only",
                "stable_source_id": stable_url_id(collection_key, url),
                "document_url_sha256": sha256_text(url),
                "identity_binding": (
                    "The exact frozen Ulam record declares this document URL. It is a source lead, "
                    "not an independently verified upstream record identifier."
                ),
            },
            "source_locator": {
                "quality": "exact_document_url_not_exact_passage_locator",
                "locator": {"kind": "document_url", "url": url},
                "source_artifact": evidence_descriptor(index_evidence),
                "source_retrieval_status": "document_not_retrieved_by_this_manifest",
                "reuse": index_source.get("reuse"),
            },
            "recovery_eligibility": {
                "statement_comparison_eligible": False,
                "automatic_recovery_outcome_allowed": False,
                "reason": (
                    "A frozen document URL lacks an acquired artifact hash and an exact passage/page/line "
                    "locator. Fetch and review the individual source document before comparing text."
                ),
                "opdp_recalculation_allowed": False,
            },
            "claims_not_made": claims_not_made,
        }

    if collection_key == "kourovka":
        match = re.fullmatch(r"KOU-21\.([0-9]+)", problem_number)
        if match is None:
            raise ManifestError(f"Kourovka frozen identifier {problem_number!r} has wrong syntax")
        original_number = f"21.{int(match.group(1))}"
        source = source_for_key(collection, "kourovka_issue_21_announcement")
        source_evidence = evidence.get(("kourovka", "kourovka_issue_21_announcement"))
        if source_evidence is None:
            raise ManifestError("missing captured Kourovka announcement evidence")
        statement_artifact_url = require_string(source, "statement_artifact_url", "Kourovka registry source")
        return {
            "schema": ROW_SCHEMA,
            "collection_key": collection_key,
            "frozen_ulam": frozen_binding,
            "upstream_identity": {
                "status": "edition_and_display_number_candidate_only",
                "stable_source_id": f"kourovka:issue-21:{original_number}",
                "source_display_identifier": original_number,
                "identity_binding": (
                    "KOU-21.n is a frozen display-ID translation of issue-21 number 21.n. "
                    "No issue-PDF passage has been acquired or inspected here."
                ),
            },
            "source_locator": {
                "quality": "statement_artifact_url_without_exact_passage_locator",
                "locator": {"kind": "statement_artifact_url", "url": statement_artifact_url},
                "source_artifact": evidence_descriptor(source_evidence),
                "source_retrieval_status": "statement_artifact_not_retrieved_by_this_manifest",
                "reuse": source.get("reuse"),
            },
            "recovery_eligibility": {
                "statement_comparison_eligible": False,
                "automatic_recovery_outcome_allowed": False,
                "reason": (
                    "The official edition announcement supports the edition-level count, not a source-page "
                    "binding for this problem. Acquire and rights-review the issue PDF before comparison."
                ),
                "opdp_recalculation_allowed": False,
            },
            "claims_not_made": claims_not_made,
        }

    if collection_key == "millennium":
        source = source_for_key(collection, "clay_millennium_landing_page")
        source_evidence = evidence.get(("millennium", "clay_millennium_landing_page"))
        if source_evidence is None:
            raise ManifestError("missing captured Clay Millennium evidence")
        statement_artifact_url = require_string(source, "statement_artifact_url", "Clay registry source")
        return {
            "schema": ROW_SCHEMA,
            "collection_key": collection_key,
            "frozen_ulam": frozen_binding,
            "upstream_identity": {
                "status": "frozen_collection_display_identifier_only",
                "stable_source_id": f"clay:millennium:{problem_number}",
                "source_display_identifier": problem_number,
                "identity_binding": (
                    "The frozen MPP display identifier is preserved as a source lead. The official Clay "
                    "landing page has not supplied an exact monograph page or record identifier here."
                ),
            },
            "source_locator": {
                "quality": "statement_artifact_url_without_exact_passage_locator",
                "locator": {"kind": "statement_artifact_url", "url": statement_artifact_url},
                "source_artifact": evidence_descriptor(source_evidence),
                "source_retrieval_status": "statement_artifact_not_retrieved_by_this_manifest",
                "reuse": source.get("reuse"),
            },
            "recovery_eligibility": {
                "statement_comparison_eligible": False,
                "automatic_recovery_outcome_allowed": False,
                "reason": (
                    "The collection landing page is evidence for collection framing, not an exact source passage. "
                    "Acquire a source-specific official artifact and page/section locator before comparison."
                ),
                "opdp_recalculation_allowed": False,
            },
            "claims_not_made": claims_not_made,
        }
    raise ManifestError(f"unsupported collection key {collection_key!r}")


def output_bytes(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(canonical_json_bytes(row) + b"\n" for row in rows)


def build_summary(
    *,
    registry_path: Path,
    evidence_path: Path,
    inventory_path: Path,
    overlap_path: Path,
    ulam_path: Path,
    erdos_index_path: Path,
    rows_path: Path,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    counts = Counter(row["collection_key"] for row in rows)
    quality = Counter(row["source_locator"]["quality"] for row in rows)
    comparison = Counter(
        "eligible" if row["recovery_eligibility"]["statement_comparison_eligible"] else "not_eligible"
        for row in rows
    )
    automatic = Counter(
        "allowed" if row["recovery_eligibility"]["automatic_recovery_outcome_allowed"] else "not_allowed"
        for row in rows
    )
    if any(row["claims_not_made"]["opdp_recalculation_allowed"] is not False for row in rows):
        raise ManifestError("attempted to write an OPDP recalculation permission")
    return {
        "schema": SUMMARY_SCHEMA,
        "generated_at_utc": utc_now(),
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "non_destructive_contract": {
            "full_source_statements_copied": False,
            "mathdb_membership_claimed": False,
            "recovery_outcomes_assigned": False,
            "open_statuses_assessed": False,
            "opdp_dimensions_recalculated": False,
        },
        "scope": {
            "basis": (
                "The 7,489 exact frozen-Ulam overlaps for the five named collection candidates. "
                "This is not a MathDB collection-membership export."
            ),
            "record_count": len(rows),
            "collection_record_counts": dict(sorted(counts.items())),
        },
        "inputs": {
            "registry": {"path": relative_path(registry_path), "sha256": sha256_file(registry_path)},
            "external_evidence": {
                "path": relative_path(evidence_path),
                "sha256": sha256_file(evidence_path),
            },
            "collection_inventory": {
                "path": relative_path(inventory_path),
                "sha256": sha256_file(inventory_path),
            },
            "ulam_overlap_sidecar": {
                "path": relative_path(overlap_path),
                "sha256": sha256_file(overlap_path),
            },
            "frozen_ulam_v1_6": {"path": relative_path(ulam_path), "sha256": sha256_file(ulam_path)},
            "erdos_locator_index": {
                "path": relative_path(erdos_index_path),
                "sha256": sha256_file(erdos_index_path),
            },
        },
        "recovery_gate_summary": {
            "source_locator_quality_counts": dict(sorted(quality.items())),
            "statement_comparison_eligibility": dict(sorted(comparison.items())),
            "automatic_recovery_outcome": dict(sorted(automatic.items())),
            "opdp_recalculation_allowed_count": 0,
            "interpretation": (
                "Only exact, hash-bound upstream record locators are comparison-eligible. Even those "
                "cannot receive a recovery label or a rescore without semantic comparison, a self-contained "
                "reviewed restatement, source-specific rights review, and independent current-open evidence."
            ),
        },
        "output": {
            "path": relative_path(rows_path),
            "sha256": sha256_file(rows_path),
            "record_count": len(rows),
            "contains_statement_text": False,
        },
    }


def reject_embedded_source_text(value: Any, context: str) -> None:
    """Reject literal source-text fields while permitting hashes/pointers/caveats."""
    forbidden_keys = {
        "statement",
        "title",
        "background",
        "excerpt",
        "source_quote",
        "source_text",
        "original_statement",
        "clean_statement",
    }
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in forbidden_keys:
                raise ManifestError(f"{context}: literal source-text key {key!r} is forbidden")
            reject_embedded_source_text(nested, context)
    elif isinstance(value, list):
        for nested in value:
            reject_embedded_source_text(nested, context)


def verify_output(
    *, summary_path: Path, rows_path: Path, registry_path: Path, evidence_path: Path, inventory_path: Path,
    overlap_path: Path, ulam_path: Path, erdos_index_path: Path
) -> dict[str, Any]:
    _, collections = parse_registry(registry_path)
    parse_evidence(evidence_path)
    parse_inventory(inventory_path)
    _, erdos_locator_rows = load_erdos_locator_index(
        erdos_index_path, registry_path, collections["erdos"]
    )
    summary = load_json_object(summary_path, "canonical-record manifest summary")
    if summary.get("schema") != SUMMARY_SCHEMA:
        raise ManifestError(f"{summary_path}: wrong summary schema")
    contract = summary.get("non_destructive_contract")
    expected_contract = {
        "full_source_statements_copied": False,
        "mathdb_membership_claimed": False,
        "recovery_outcomes_assigned": False,
        "open_statuses_assessed": False,
        "opdp_dimensions_recalculated": False,
    }
    if contract != expected_contract:
        raise ManifestError(f"{summary_path}: non-destructive contract mismatch")
    output = summary.get("output")
    if not isinstance(output, dict):
        raise ManifestError(f"{summary_path}: missing output binding")
    if output.get("sha256") != sha256_file(rows_path):
        raise ManifestError(f"{summary_path}: row-file SHA-256 mismatch")
    inputs = summary.get("inputs")
    if not isinstance(inputs, dict):
        raise ManifestError(f"{summary_path}: missing input bindings")
    expected_inputs = {
        "registry": registry_path,
        "external_evidence": evidence_path,
        "collection_inventory": inventory_path,
        "ulam_overlap_sidecar": overlap_path,
        "frozen_ulam_v1_6": ulam_path,
        "erdos_locator_index": erdos_index_path,
    }
    for key, path in expected_inputs.items():
        detail = inputs.get(key)
        if not isinstance(detail, dict) or detail.get("sha256") != sha256_file(path):
            raise ManifestError(f"{summary_path}: input hash mismatch for {key}")
    expected_counts = {"erdos": 632, "aim": 3359, "amr": 3342, "kourovka": 150, "millennium": 6}
    counts: Counter[str] = Counter()
    seen_keys: set[str] = set()
    try:
        handle = rows_path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise ManifestError(f"cannot read {rows_path}: {exc}") from exc
    with handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise ManifestError(f"{rows_path} line {line_number}: blank line")
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ManifestError(f"{rows_path} line {line_number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict) or row.get("schema") != ROW_SCHEMA:
                raise ManifestError(f"{rows_path} line {line_number}: wrong row schema")
            reject_embedded_source_text(row, f"{rows_path} line {line_number}")
            collection_key = require_string(row, "collection_key", f"{rows_path} line {line_number}")
            if collection_key not in expected_counts:
                raise ManifestError(f"{rows_path} line {line_number}: unexpected collection key")
            frozen = row.get("frozen_ulam")
            if not isinstance(frozen, dict):
                raise ManifestError(f"{rows_path} line {line_number}: missing frozen Ulam binding")
            record_key = require_string(frozen, "record_key", f"{rows_path} line {line_number}")
            if record_key in seen_keys:
                raise ManifestError(f"{rows_path} line {line_number}: duplicate frozen record key")
            seen_keys.add(record_key)
            if frozen.get("statement_text_copied_here") is not False:
                raise ManifestError(f"{rows_path} line {line_number}: statement-copy guard failed")
            claims = row.get("claims_not_made")
            if not isinstance(claims, dict):
                raise ManifestError(f"{rows_path} line {line_number}: missing claim guard")
            if claims.get("mathdb_membership") != "not_asserted_no_native_collection_field":
                raise ManifestError(f"{rows_path} line {line_number}: illegal MathDB membership claim")
            if claims.get("source_statement_recovery_outcome") != "not_assigned_by_manifest":
                raise ManifestError(f"{rows_path} line {line_number}: illegal recovery outcome")
            if claims.get("current_open_status") != "not_assessed_by_manifest":
                raise ManifestError(f"{rows_path} line {line_number}: illegal open-status outcome")
            if claims.get("opdp_recalculation_allowed") is not False:
                raise ManifestError(f"{rows_path} line {line_number}: illegal rescoring permission")
            eligibility = row.get("recovery_eligibility")
            if not isinstance(eligibility, dict):
                raise ManifestError(f"{rows_path} line {line_number}: missing recovery gate")
            if eligibility.get("automatic_recovery_outcome_allowed") is not False:
                raise ManifestError(f"{rows_path} line {line_number}: illegal automatic recovery outcome")
            if eligibility.get("opdp_recalculation_allowed") is not False:
                raise ManifestError(f"{rows_path} line {line_number}: illegal rescoring gate")
            locator = row.get("source_locator")
            if not isinstance(locator, dict) or not isinstance(locator.get("locator"), dict):
                raise ManifestError(f"{rows_path} line {line_number}: missing source locator")
            if collection_key == "erdos":
                if locator.get("quality") != "exact_record_locator_not_semantic_comparison":
                    raise ManifestError(f"{rows_path} line {line_number}: Erdős locator quality mismatch")
                if eligibility.get("statement_comparison_eligible") is not True:
                    raise ManifestError(f"{rows_path} line {line_number}: Erdős comparison gate mismatch")
                upstream_identity = row.get("upstream_identity")
                if not isinstance(upstream_identity, dict):
                    raise ManifestError(f"{rows_path} line {line_number}: missing Erdős upstream identity")
                source_id = require_string(
                    upstream_identity,
                    "stable_source_id",
                    f"{rows_path} line {line_number} Erdős upstream identity",
                )
                problem_number = require_string(
                    frozen, "problem_number", f"{rows_path} line {line_number} Erdős binding"
                )
                matched = re.fullmatch(r"EP-([0-9]+)", problem_number)
                if matched is None or int(matched.group(1)) not in erdos_locator_rows:
                    raise ManifestError(f"{rows_path} line {line_number}: invalid Erdős display identifier")
                expected_locator = erdos_locator_rows[int(matched.group(1))]["locator"]
                if row["source_locator"]["locator"] != expected_locator:
                    raise ManifestError(f"{rows_path} line {line_number}: Erdős locator/index mismatch")
                if source_id != erdos_locator_rows[int(matched.group(1))]["stable_source_id"]:
                    raise ManifestError(f"{rows_path} line {line_number}: Erdős stable-ID/index mismatch")
            elif eligibility.get("statement_comparison_eligible") is not False:
                raise ManifestError(f"{rows_path} line {line_number}: unsupported comparison eligibility")
            counts[collection_key] += 1
    if dict(counts) != expected_counts:
        raise ManifestError(f"{rows_path}: collection counts mismatch: {dict(counts)!r}")
    if output.get("record_count") != sum(counts.values()):
        raise ManifestError(f"{summary_path}: output count mismatch")
    gate = summary.get("recovery_gate_summary")
    if not isinstance(gate, dict) or gate.get("opdp_recalculation_allowed_count") != 0:
        raise ManifestError(f"{summary_path}: invalid recovery-gate summary")
    return summary


def build(arguments: argparse.Namespace) -> dict[str, Any]:
    registry, collections = parse_registry(arguments.registry)
    evidence = parse_evidence(arguments.external_evidence)
    parse_inventory(arguments.inventory)
    overlap_rows = parse_overlap_rows(arguments.overlap)
    erdos_collection = collections["erdos"]
    if arguments.refresh_erdos_locator_index:
        if not arguments.allow_network:
            raise ManifestError("--refresh-erdos-locator-index requires --allow-network")
        if arguments.max_response_bytes < 401189:
            raise ManifestError("--max-response-bytes is too small for the pinned Erdős YAML")
        index = make_erdos_locator_index(
            registry_path=arguments.registry,
            collection=erdos_collection,
            timeout_seconds=arguments.timeout_seconds,
            max_bytes=arguments.max_response_bytes,
            user_agent=arguments.user_agent,
        )
        write_erdos_locator_index(arguments.erdos_locator_index, index)
        log(
            "refreshed hash-only Erdős locator index; raw source was discarded after line spans were derived"
        )
    index, erdos_locator_rows = load_erdos_locator_index(
        arguments.erdos_locator_index, arguments.registry, erdos_collection
    )
    records = load_ulam_records(arguments.ulam_v16)
    selected = selected_records(records, collections)
    rows: list[dict[str, Any]] = []
    for collection_key, record_index, record in selected:
        problem_id = require_int(record.get("problem_id"), f"frozen Ulam record {record_index}")
        overlap = overlap_rows.get((collection_key, problem_id))
        if overlap is None:
            raise ManifestError(f"frozen Ulam {collection_key}/{problem_id}: missing overlap row")
        rows.append(
            make_record_row(
                collection_key=collection_key,
                collection=collections[collection_key],
                record_index=record_index,
                record=record,
                overlap=overlap,
                evidence=evidence,
                erdos_locator_rows=erdos_locator_rows,
            )
        )
    expected_overlap_keys = set(overlap_rows)
    actual_overlap_keys = {(row["collection_key"], row["frozen_ulam"]["problem_id"]) for row in rows}
    if actual_overlap_keys != expected_overlap_keys:
        raise ManifestError("selected frozen Ulam records and overlap sidecar have different identities")
    rows.sort(key=lambda row: (row["collection_key"], row["frozen_ulam"]["problem_id"]))
    atomic_write(arguments.rows_output, output_bytes(rows))
    summary = build_summary(
        registry_path=arguments.registry,
        evidence_path=arguments.external_evidence,
        inventory_path=arguments.inventory,
        overlap_path=arguments.overlap,
        ulam_path=arguments.ulam_v16,
        erdos_index_path=arguments.erdos_locator_index,
        rows_path=arguments.rows_output,
        rows=rows,
    )
    atomic_write(arguments.summary_output, canonical_json_bytes(summary) + b"\n")
    verify_output(
        summary_path=arguments.summary_output,
        rows_path=arguments.rows_output,
        registry_path=arguments.registry,
        evidence_path=arguments.external_evidence,
        inventory_path=arguments.inventory,
        overlap_path=arguments.overlap,
        ulam_path=arguments.ulam_v16,
        erdos_index_path=arguments.erdos_locator_index,
    )
    return summary


def self_test() -> None:
    """Pure, no-network guards for the line-locator and identifier logic."""
    fixture = '- number: "1"\n  field: x\n- number: "3"\n  field: y\n'
    matches = list(ERDOS_NUMBER_LINE.finditer(fixture))
    if [match.group(1) for match in matches] != ["1", "3"]:
        raise ManifestError("self-test: quoted Erdős source-number parsing failed")
    first_start = fixture.count("\n", 0, matches[0].start()) + 1
    first_end = fixture.count("\n", 0, matches[1].start())
    if (first_start, first_end) != (1, 2):
        raise ManifestError("self-test: Erdős line-span calculation failed")
    if stable_url_id("aim", "https://example.test/a") != stable_url_id(
        "aim", "https://example.test/a"
    ):
        raise ManifestError("self-test: URL ID must be deterministic")
    if stable_url_id("aim", "https://example.test/a") == stable_url_id(
        "amr", "https://example.test/a"
    ):
        raise ManifestError("self-test: URL IDs must preserve collection namespace")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry", type=Path, default=Path("scripts/mathdb_recovery/collection_source_registry.v1.json")
    )
    parser.add_argument(
        "--external-evidence", type=Path, default=Path("data/MATHDB_COLLECTION_EXTERNAL_EVIDENCE_v1.json")
    )
    parser.add_argument(
        "--inventory", type=Path, default=Path("data/MATHDB_COLLECTION_SOURCE_INVENTORY_v1.json")
    )
    parser.add_argument(
        "--overlap", type=Path, default=Path("data/MATHDB_COLLECTION_ULAM_OVERLAP_v1.jsonl")
    )
    parser.add_argument(
        "--ulam-v16", type=Path, default=Path("data/Ulam_UnsolvedMath_OPDP_Assessments_v1.6.json.gz")
    )
    parser.add_argument(
        "--erdos-locator-index",
        type=Path,
        default=Path("data/MATHDB_COLLECTION_ERDOS_RECORD_LOCATORS_v1.json"),
    )
    parser.add_argument(
        "--rows-output",
        type=Path,
        default=Path("data/MATHDB_COLLECTION_CANONICAL_RECORD_MANIFEST_v1.jsonl"),
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("data/MATHDB_COLLECTION_CANONICAL_RECORD_MANIFEST_v1.json"),
    )
    parser.add_argument(
        "--refresh-erdos-locator-index",
        action="store_true",
        help="rate-limited one-URL refresh of the pinned Erdős YAML locator index",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="explicit acknowledgement required with --refresh-erdos-locator-index",
    )
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--max-response-bytes", type=int, default=1_000_000)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="validate existing outputs and all hash/claim gates without reading statements or using network",
    )
    parser.add_argument("--self-test", action="store_true", help="run pure deterministic tests and exit")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    try:
        self_test()
        if arguments.self_test:
            print("self-test passed")
            return 0
        if arguments.verify_only:
            verify_output(
                summary_path=arguments.summary_output,
                rows_path=arguments.rows_output,
                registry_path=arguments.registry,
                evidence_path=arguments.external_evidence,
                inventory_path=arguments.inventory,
                overlap_path=arguments.overlap,
                ulam_path=arguments.ulam_v16,
                erdos_index_path=arguments.erdos_locator_index,
            )
            print("canonical-record manifest verification passed")
            return 0
        summary = build(arguments)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    print(
        "wrote {count:,} provenance-only collection record rows; {eligible:,} have an exact "
        "upstream record locator for later human statement comparison; 0 are score-eligible".format(
            count=summary["scope"]["record_count"],
            eligible=summary["recovery_gate_summary"]["statement_comparison_eligibility"].get(
                "eligible", 0
            ),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
