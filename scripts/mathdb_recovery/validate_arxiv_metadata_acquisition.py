#!/usr/bin/env python3
"""Read-only integrity audit for an OPDP arXiv metadata acquisition run.

The acquisition worker deliberately separates immutable evidence
(`request_plan.json` and `responses/*.json`) from replaceable convenience
artifacts (`summary.json` and the derived metadata index).  This validator
audits only the former plus the append-only error ledger.  It never repairs,
rewrites, deletes, or creates a file, so it is suitable for checking a copied
or quiescent resumable run before it feeds a later S3 source-extraction stage.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from acquire_arxiv_metadata import (
    API_ENDPOINT,
    DEFAULT_MIN_INTERVAL_SECONDS,
    ERROR_SCHEMA,
    MAX_BATCH_SIZE,
    PLAN_SCHEMA,
    RUN_SCHEMA,
    AcquisitionError,
    build_request_url,
    canonical_json_bytes,
    check_final_url,
    load_response_record,
    make_response_record,
    normalize_arxiv_identifier,
    parse_utc,
    read_json_object,
    read_source_rows,
    result_response_path,
    sha256_bytes,
    sha256_file,
)


VALIDATOR_NAME = "OPDP arXiv metadata acquisition validator"
VALIDATOR_VERSION = "0.1.0"
REPORT_SCHEMA = "opdp.mathdb.arxiv-api-metadata-validation-report.v1"
ALLOWED_SELECTION_KINDS = {
    "all_manifest_sources",
    "manifest_prefix",
    "manifest_prefix_complete",
    "explicit_ids",
    "explicit_ids_complete",
}


class ValidationError(RuntimeError):
    """A malformed validator invocation or inaccessible immutable input."""


class Findings:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.error_count = 0
        self.warning_count = 0
        self.examples: list[dict[str, Any]] = []

    def add(self, severity: str, code: str, message: str, **context: Any) -> None:
        if severity == "error":
            self.error_count += 1
        elif severity == "warning":
            self.warning_count += 1
        else:
            raise AssertionError(f"unknown finding severity: {severity}")
        if len(self.examples) < self.limit:
            self.examples.append({"severity": severity, "code": code, "message": message, **context})


def iso_timestamp(value: Any) -> bool:
    return parse_utc(value) is not None


def require_exact_keys(
    value: Any,
    expected: set[str],
    context: str,
    findings: Findings,
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        findings.add("error", "OBJECT_REQUIRED", f"{context} must be an object")
        return None
    actual = set(value)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        findings.add(
            "error",
            "OBJECT_KEYS_MISMATCH",
            f"{context} has unexpected/missing keys",
            missing=missing,
            unexpected=unexpected,
        )
        return None
    return value


def read_metadata_run(metadata_dir: Path, run_dir: Path, findings: Findings) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    metadata_run_path = metadata_dir / "run.json"
    plan_path = metadata_dir / "request_plan.json"
    if not metadata_run_path.is_file():
        findings.add("error", "MISSING_METADATA_RUN", "metadata run manifest is missing", path=str(metadata_run_path))
        return None, None
    if not plan_path.is_file():
        findings.add("error", "MISSING_REQUEST_PLAN", "immutable request plan is missing", path=str(plan_path))
        return None, None
    try:
        metadata_run = read_json_object(metadata_run_path, "metadata run manifest")
        plan = read_json_object(plan_path, "metadata request plan")
    except AcquisitionError as exc:
        findings.add("error", "INVALID_JSON", str(exc))
        return None, None
    if require_exact_keys(metadata_run, {"schema", "tool", "source", "plan", "safety", "storage"}, "metadata run manifest", findings) is None:
        return None, None
    if metadata_run.get("schema") != RUN_SCHEMA:
        findings.add("error", "RUN_SCHEMA", "metadata run manifest has an unexpected schema")
        return None, None
    if plan.get("schema") != PLAN_SCHEMA:
        findings.add("error", "PLAN_SCHEMA", "request plan has an unexpected schema")
        return None, None
    tool = require_exact_keys(metadata_run.get("tool"), {"name", "version"}, "metadata run tool", findings)
    run_source = require_exact_keys(
        metadata_run.get("source"),
        {"recovery_run_manifest_sha256", "source_manifest_filename", "source_manifest_sha256", "arxiv_sources_sha256"},
        "metadata run source",
        findings,
    )
    plan_reference = require_exact_keys(metadata_run.get("plan"), {"filename", "sha256", "selection_scope", "source_count", "batch_count"}, "metadata run plan", findings)
    safety = require_exact_keys(
        metadata_run.get("safety"),
        {"default_mode", "approved_endpoint_only", "maximum_ids_per_request", "minimum_inter_request_seconds", "single_connection_policy", "raw_full_text_downloaded", "release_mutation"},
        "metadata run safety",
        findings,
    )
    storage = require_exact_keys(
        metadata_run.get("storage"),
        {"immutable_response_files", "append_only_error_ledger", "replaceable_derived_index"},
        "metadata run storage",
        findings,
    )
    if tool is None or run_source is None or plan_reference is None or safety is None or storage is None:
        return metadata_run, plan
    if tool.get("name") != "OPDP arXiv metadata acquisition" or not isinstance(tool.get("version"), str):
        findings.add("error", "RUN_TOOL", "metadata run manifest has an unexpected tool identity")
    recovery_run = run_dir / "run.json"
    if not recovery_run.is_file():
        findings.add("error", "MISSING_RECOVERY_RUN", "canonical recovery run manifest is missing", path=str(recovery_run))
    elif run_source.get("recovery_run_manifest_sha256") != sha256_file(recovery_run):
        findings.add("error", "RECOVERY_RUN_HASH", "metadata run is not bound to this recovery run manifest")
    if plan_reference.get("filename") != "request_plan.json":
        findings.add("error", "PLAN_FILENAME", "metadata run references an unexpected plan filename")
    if plan_reference.get("sha256") != sha256_file(plan_path):
        findings.add("error", "PLAN_HASH", "request plan hash does not match immutable run binding")
    expected_safety = {
        "approved_endpoint_only": API_ENDPOINT,
        "maximum_ids_per_request": MAX_BATCH_SIZE,
        "minimum_inter_request_seconds": DEFAULT_MIN_INTERVAL_SECONDS,
        "raw_full_text_downloaded": False,
        "release_mutation": "prohibited by this tool",
    }
    for key, expected in expected_safety.items():
        if safety.get(key) != expected:
            findings.add("error", "RUN_SAFETY_POLICY", "metadata run safety policy differs from the required value", key=key, expected=expected, observed=safety.get(key))
    if safety.get("single_connection_policy") != "single sequential worker with Connection: close":
        findings.add("error", "RUN_CONNECTION_POLICY", "metadata run does not declare the required single-worker policy")
    if storage.get("immutable_response_files") != "responses/*.json" or storage.get("append_only_error_ledger") != "error_ledger.jsonl":
        findings.add("error", "RUN_STORAGE_POLICY", "metadata run does not declare the immutable evidence locations")
    return metadata_run, plan


def validate_plan(
    plan: dict[str, Any],
    metadata_run: dict[str, Any],
    source_manifest_path: Path,
    source_manifest_sha: str,
    source_rows_sha: str,
    source_rows: list[dict[str, Any]],
    findings: Findings,
) -> list[dict[str, Any]]:
    expected_plan_keys = {"schema", "tool", "source", "api", "selection", "batches"}
    if require_exact_keys(plan, expected_plan_keys, "request plan", findings) is None:
        return []
    source = require_exact_keys(plan.get("source"), {"source_manifest_filename", "source_manifest_sha256", "arxiv_sources_sha256"}, "request plan source", findings)
    api = require_exact_keys(
        plan.get("api"),
        {"endpoint", "request_shape", "maximum_ids_per_request", "single_connection_policy", "minimum_inter_request_seconds", "full_text_requested"},
        "request plan API policy",
        findings,
    )
    selection = require_exact_keys(plan.get("selection"), {"kind", "source_count", "arxiv_ids_sha256", "scope"}, "request plan selection", findings)
    if source is None or api is None or selection is None:
        return []
    if source.get("source_manifest_filename") != source_manifest_path.name:
        findings.add("error", "SOURCE_MANIFEST_FILENAME", "plan source-manifest filename does not match validator input")
    if source.get("source_manifest_sha256") != source_manifest_sha:
        findings.add("error", "SOURCE_MANIFEST_HASH", "plan source-manifest hash does not match validator input")
    if source.get("arxiv_sources_sha256") != source_rows_sha:
        findings.add("error", "SOURCE_ROWS_HASH", "plan arXiv-source rows hash does not match validator input")
    run_source = metadata_run.get("source", {})
    for key in ("source_manifest_filename", "source_manifest_sha256", "arxiv_sources_sha256"):
        if run_source.get(key) != source.get(key):
            findings.add("error", "RUN_PLAN_SOURCE_BINDING", "run manifest and request plan disagree on source binding", key=key)
    expected_api = {
        "endpoint": API_ENDPOINT,
        "request_shape": "GET /api/query?id_list=<comma-separated canonical IDs>&max_results=<exact batch size>",
        "maximum_ids_per_request": MAX_BATCH_SIZE,
        "single_connection_policy": True,
        "minimum_inter_request_seconds": DEFAULT_MIN_INTERVAL_SECONDS,
        "full_text_requested": False,
    }
    for key, expected in expected_api.items():
        if api.get(key) != expected:
            findings.add("error", "PLAN_API_POLICY", "request plan API policy differs from the required value", key=key, expected=expected, observed=api.get(key))
    batches = plan.get("batches")
    if not isinstance(batches, list) or not batches:
        findings.add("error", "PLAN_BATCHES", "request plan must contain a nonempty batch list")
        return []
    if selection.get("kind") not in ALLOWED_SELECTION_KINDS:
        findings.add("error", "SELECTION_KIND", "request plan has an unrecognized selection kind", kind=selection.get("kind"))
    manifest_by_id = {row["arxiv_id"]: row for row in source_rows}
    all_ids: list[str] = []
    expected_response_files: set[str] = set()
    validated_batches: list[dict[str, Any]] = []
    for ordinal, batch in enumerate(batches, start=1):
        if require_exact_keys(batch, {"index", "batch_key", "arxiv_ids", "observed_versions", "request_url", "response_file"}, f"batch {ordinal}", findings) is None:
            continue
        index = batch.get("index")
        ids = batch.get("arxiv_ids")
        observed_versions = batch.get("observed_versions")
        if index != ordinal:
            findings.add("error", "BATCH_INDEX", "batch indices must be one-based and contiguous", expected=ordinal, observed=index)
        if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_BATCH_SIZE or any(not isinstance(identifier, str) for identifier in ids):
            findings.add("error", "BATCH_IDS", "batch must have 1..50 string identifiers", batch=ordinal)
            continue
        normalized_ids: list[str] = []
        malformed = False
        for identifier in ids:
            normalized = normalize_arxiv_identifier(identifier)
            if normalized is None or normalized[1] is not None or normalized[0] != identifier:
                findings.add("error", "BATCH_IDENTIFIER", "batch has a noncanonical arXiv identifier", batch=ordinal, arxiv_id=identifier)
                malformed = True
                break
            normalized_ids.append(identifier)
        if malformed:
            continue
        if len(set(ids)) != len(ids):
            findings.add("error", "BATCH_DUPLICATE_IDS", "a batch repeats an arXiv identifier", batch=ordinal)
        if not isinstance(observed_versions, dict):
            findings.add("error", "BATCH_OBSERVED_VERSIONS", "batch observed_versions must be an object", batch=ordinal)
            continue
        expected_versions: dict[str, list[str]] = {}
        for identifier in ids:
            source_row = manifest_by_id.get(identifier)
            if source_row is None:
                findings.add("error", "BATCH_UNKNOWN_ID", "batch identifier is absent from frozen source manifest", batch=ordinal, arxiv_id=identifier)
                expected_versions[identifier] = []
            else:
                expected_versions[identifier] = source_row["observed_versions"]
        if observed_versions != expected_versions:
            findings.add("error", "BATCH_VERSION_BINDING", "batch observed-version map differs from frozen source manifest", batch=ordinal)
        try:
            expected_url = build_request_url(ids)
        except AcquisitionError as exc:
            findings.add("error", "BATCH_URL_BUILD", str(exc), batch=ordinal)
            continue
        if batch.get("request_url") != expected_url:
            findings.add("error", "BATCH_REQUEST_URL", "batch request URL is not the exact official API URL for its identifiers", batch=ordinal, expected=expected_url, observed=batch.get("request_url"))
        expected_key = sha256_bytes(
            canonical_json_bytes(
                {
                    "index": ordinal,
                    "arxiv_ids": ids,
                    "request_url": expected_url,
                    "observed_versions": expected_versions,
                }
            )
        )
        if batch.get("batch_key") != expected_key:
            findings.add("error", "BATCH_KEY", "batch key does not bind exact IDs, URL, and observed versions", batch=ordinal)
        expected_file = f"responses/batch-{ordinal:05d}-{expected_key[:16]}.json"
        if batch.get("response_file") != expected_file:
            findings.add("error", "BATCH_RESPONSE_PATH", "batch response path is not deterministic", batch=ordinal, expected=expected_file, observed=batch.get("response_file"))
        if expected_file in expected_response_files:
            findings.add("error", "DUPLICATE_RESPONSE_PATH", "two batches share a response path", path=expected_file)
        expected_response_files.add(expected_file)
        all_ids.extend(ids)
        validated_batches.append(batch)
    if len(set(all_ids)) != len(all_ids):
        findings.add("error", "PLAN_DUPLICATE_IDS", "the request plan repeats one or more identifiers across batches")
    if selection.get("source_count") != len(all_ids):
        findings.add("error", "SELECTION_COUNT", "selection source count differs from IDs in batches", expected=len(all_ids), observed=selection.get("source_count"))
    if selection.get("arxiv_ids_sha256") != sha256_bytes(canonical_json_bytes(all_ids)):
        findings.add("error", "SELECTION_HASH", "selection ID hash differs from batched identifiers")
    complete_selection = len(all_ids) == len(source_rows) and set(all_ids) == set(manifest_by_id)
    expected_scope = "complete" if complete_selection else "sample"
    if selection.get("scope") != expected_scope:
        findings.add("error", "SELECTION_SCOPE", "selection scope does not match source-manifest coverage", expected=expected_scope, observed=selection.get("scope"))
    run_plan = metadata_run.get("plan", {})
    if run_plan.get("source_count") != len(all_ids) or run_plan.get("batch_count") != len(batches) or run_plan.get("selection_scope") != selection.get("scope"):
        findings.add("error", "RUN_PLAN_COUNTS", "metadata run manifest and request plan disagree on coverage")
    return validated_batches


def validate_response_envelopes(metadata_dir: Path, batches: list[dict[str, Any]], findings: Findings) -> dict[str, int]:
    expected_files = {batch["response_file"] for batch in batches if isinstance(batch.get("response_file"), str)}
    responses_root = metadata_dir / "responses"
    completed = 0
    if responses_root.is_symlink():
        findings.add("error", "SYMLINK_RESPONSES_DIRECTORY", "responses directory may not be a symlink")
        return {"completed_batches": 0, "pending_batches": len(batches), "unexpected_response_files": 0}
    if responses_root.exists() and not responses_root.is_dir():
        findings.add("error", "RESPONSES_NOT_DIRECTORY", "responses path exists but is not a directory")
        return {"completed_batches": 0, "pending_batches": len(batches), "unexpected_response_files": 0}
    actual_files: set[str] = set()
    if responses_root.is_dir():
        for path in responses_root.rglob("*"):
            if path.is_dir():
                continue
            relative = path.relative_to(metadata_dir).as_posix()
            if path.name == ".DS_Store":
                continue
            actual_files.add(relative)
    unexpected = sorted(actual_files - expected_files)
    for relative in unexpected:
        findings.add("error", "UNEXPECTED_RESPONSE_FILE", "responses directory contains a file not bound by the immutable plan", path=relative)
    for batch in batches:
        try:
            response_path = result_response_path(metadata_dir, batch)
        except AcquisitionError as exc:
            findings.add("error", "UNSAFE_RESPONSE_PATH", str(exc), batch=batch.get("index"))
            continue
        if not response_path.exists():
            continue
        if response_path.is_symlink():
            findings.add("error", "SYMLINK_RESPONSE_FILE", "response envelope may not be a symlink", path=str(response_path))
            continue
        if not response_path.is_file():
            findings.add("error", "RESPONSE_NOT_FILE", "bound response path is not a regular file", path=str(response_path))
            continue
        try:
            record = load_response_record(response_path, batch)
            request = record.get("request")
            response = record.get("response")
            if not isinstance(request, dict) or not isinstance(response, dict):
                raise AcquisitionError("response envelope has invalid request/response objects")
            request_started = request.get("request_started_at_utc")
            status = response.get("status")
            final_url = response.get("final_url")
            headers = response.get("headers")
            encoded = response.get("atom_xml_base64")
            if request.get("method") != "GET" or not iso_timestamp(request_started):
                raise AcquisitionError("response envelope has an invalid request method or timestamp")
            if request.get("connection_policy") != "one sequential request at a time; Connection: close":
                raise AcquisitionError("response envelope does not declare the required connection policy")
            if status != 200 or not isinstance(final_url, str) or not isinstance(headers, dict) or not isinstance(encoded, str):
                raise AcquisitionError("response envelope has invalid HTTP response fields")
            check_final_url(final_url)
            if final_url != batch["request_url"]:
                raise AcquisitionError("response final URL does not exactly match the planned API request URL")
            if any(key not in {"content-type", "content-length", "etag", "last-modified"} or not isinstance(value, str) for key, value in headers.items()):
                raise AcquisitionError("response envelope has unsupported response headers")
            try:
                body = base64.b64decode(encoded.encode("ascii"), validate=True)
            except (ValueError, UnicodeError) as exc:
                raise AcquisitionError("response envelope has invalid base64 Atom XML") from exc
            # Reconstructing the canonical envelope both re-parses the Atom
            # bytes and rejects extras/altered parsed metadata or scope flags.
            expected = make_response_record(batch, request_started, status, final_url, headers, body)
            if record != expected:
                raise AcquisitionError("response envelope differs from its canonical reconstruction")
            scope = record.get("content_scope")
            if scope != {"raw_full_text_requested": False, "contains_only_legacy_api_metadata_response": True}:
                raise AcquisitionError("response envelope does not assert metadata-only content scope")
            completed += 1
        except (AcquisitionError, ValueError, TypeError) as exc:
            findings.add("error", "RESPONSE_ENVELOPE", str(exc), batch=batch.get("index"), path=str(response_path))
    return {
        "completed_batches": completed,
        "pending_batches": len(batches) - completed,
        "unexpected_response_files": len(unexpected),
    }


def validate_error_ledger(metadata_dir: Path, batches: list[dict[str, Any]], findings: Findings) -> int:
    ledger = metadata_dir / "error_ledger.jsonl"
    if not ledger.exists():
        return 0
    if not ledger.is_file() or ledger.is_symlink():
        findings.add("error", "ERROR_LEDGER_TYPE", "error ledger is not a regular file")
        return 0
    batch_by_key = {batch["batch_key"]: batch for batch in batches if isinstance(batch.get("batch_key"), str)}
    try:
        raw = ledger.read_bytes()
    except OSError as exc:
        findings.add("error", "ERROR_LEDGER_READ", str(exc))
        return 0
    lines = raw.splitlines(keepends=True)
    valid_events = 0
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            findings.add("error", "ERROR_LEDGER_BLANK", "error ledger contains a blank line", line=number)
            continue
        if not line.endswith((b"\n", b"\r")) and number == len(lines):
            # A concurrent append can expose an incomplete final line.  Do not
            # call the run valid in that state, but distinguish it from a
            # durable corrupt historic line.
            findings.add("warning", "ERROR_LEDGER_IN_FLIGHT", "error ledger has a non-newline-terminated final line; retry after writer exits", line=number)
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            findings.add("error", "ERROR_LEDGER_JSON", f"invalid JSON: {exc}", line=number)
            continue
        if require_exact_keys(
            event,
            {"schema", "event_id", "occurred_at_utc", "phase", "batch_index", "batch_key", "attempt", "request_url", "arxiv_ids", "request_started_at_utc", "error"},
            f"error ledger line {number}",
            findings,
        ) is None:
            continue
        if event.get("schema") != ERROR_SCHEMA or not iso_timestamp(event.get("occurred_at_utc")):
            findings.add("error", "ERROR_LEDGER_SCHEMA", "error event has schema or timestamp mismatch", line=number)
            continue
        batch = batch_by_key.get(event.get("batch_key"))
        if batch is None:
            findings.add("error", "ERROR_LEDGER_BATCH", "error event refers to an unknown batch", line=number)
            continue
        if event.get("batch_index") != batch["index"] or event.get("request_url") != batch["request_url"] or event.get("arxiv_ids") != batch["arxiv_ids"]:
            findings.add("error", "ERROR_LEDGER_BINDING", "error event does not exactly bind to its planned batch", line=number)
        if not isinstance(event.get("attempt"), int) or event["attempt"] < 1:
            findings.add("error", "ERROR_LEDGER_ATTEMPT", "error event attempt is invalid", line=number)
        started = event.get("request_started_at_utc")
        if started is not None and not iso_timestamp(started):
            findings.add("error", "ERROR_LEDGER_REQUEST_TIME", "error event request timestamp is invalid", line=number)
        error = event.get("error")
        if require_exact_keys(error, {"class", "message", "http_status"}, f"error ledger line {number}.error", findings) is None:
            continue
        if not isinstance(error.get("class"), str) or not isinstance(error.get("message"), str):
            findings.add("error", "ERROR_LEDGER_ERROR", "error event payload has invalid class/message", line=number)
        if error.get("http_status") is not None and not isinstance(error.get("http_status"), int):
            findings.add("error", "ERROR_LEDGER_HTTP_STATUS", "error event HTTP status is invalid", line=number)
        valid_events += 1
    return valid_events


def validate_acquisition(
    run_dir: Path,
    source_manifest_path: Path,
    metadata_dir: Path,
    finding_limit: int,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    source_manifest_path = source_manifest_path.resolve()
    metadata_dir = metadata_dir.resolve()
    if not run_dir.is_dir():
        raise ValidationError(f"run directory does not exist: {run_dir}")
    if not metadata_dir.is_dir():
        raise ValidationError(f"metadata acquisition directory does not exist: {metadata_dir}")
    try:
        metadata_dir.relative_to(run_dir)
    except ValueError:
        raise ValidationError("metadata acquisition directory must be below the recovery run directory")
    findings = Findings(finding_limit)
    try:
        _, source_manifest_sha, source_rows_sha, source_rows = read_source_rows(source_manifest_path)
    except AcquisitionError as exc:
        raise ValidationError(f"cannot validate source manifest: {exc}") from exc
    metadata_run, plan = read_metadata_run(metadata_dir, run_dir, findings)
    batches: list[dict[str, Any]] = []
    if metadata_run is not None and plan is not None:
        batches = validate_plan(
            plan,
            metadata_run,
            source_manifest_path,
            source_manifest_sha,
            source_rows_sha,
            source_rows,
            findings,
        )
    response_metrics = validate_response_envelopes(metadata_dir, batches, findings) if batches else {
        "completed_batches": 0,
        "pending_batches": 0,
        "unexpected_response_files": 0,
    }
    error_events = validate_error_ledger(metadata_dir, batches, findings) if batches else 0
    complete = bool(batches) and response_metrics["completed_batches"] == len(batches)
    valid = findings.error_count == 0
    return {
        "schema": REPORT_SCHEMA,
        "validator": {"name": VALIDATOR_NAME, "version": VALIDATOR_VERSION},
        "read_only": True,
        "inputs": {
            "run_dir": str(run_dir),
            "metadata_dir": str(metadata_dir),
            "source_manifest": str(source_manifest_path),
            "source_manifest_sha256": source_manifest_sha,
        },
        "status": "valid_complete" if valid and complete else "valid_incomplete" if valid else "invalid",
        "coverage": {
            "planned_batch_count": len(batches),
            **response_metrics,
            "error_ledger_event_count": error_events,
        },
        "findings": {
            "error_count": findings.error_count,
            "warning_count": findings.warning_count,
            "examples": findings.examples,
        },
        "scope": {
            "validated": ["immutable request plan", "immutable response envelopes", "Atom-body and parsed-metadata hashes", "exact request-ID binding", "metadata-only content assertions", "append-only error ledger"],
            "not_validated": ["replaceable summary/index", "source-text rights", "S3 source archive mapping", "statement reconstruction", "openness status", "OPDP scores"],
        },
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Read-only integrity validator for a MathDB arXiv metadata acquisition directory."
    )
    value.add_argument("--run-dir", type=Path, required=True, help="canonical MathDB recovery run directory")
    value.add_argument("--source-manifest", type=Path, required=True, help="frozen arXiv source manifest used by acquisition")
    value.add_argument("--metadata-dir", type=Path, required=True, help="metadata acquisition output directory under --run-dir")
    value.add_argument("--max-findings", type=int, default=100, help="maximum representative findings to print")
    return value


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    if args.max_findings < 1:
        print("error: --max-findings must be positive", file=sys.stderr)
        return 2
    try:
        report = validate_acquisition(args.run_dir, args.source_manifest, args.metadata_dir, args.max_findings)
    except (ValidationError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["findings"]["error_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
