#!/usr/bin/env python3
"""Emit a compact, content-free public summary of an arXiv metadata run.

This is deliberately a *publication summary*, not a second metadata index.
It reads the immutable request plan, response envelopes, and append-only error
ledger produced by :mod:`acquire_arxiv_metadata`, then emits only hashes,
counts, fixed status labels, and field-presence aggregates.  It never emits
an arXiv identifier, title, abstract, author name, Atom/XML byte, source
passage, response header, request URL, or error message.

The writer must have exited before this tool is used.  A final artifact can
only be written for a ``valid_complete`` run; an explicitly requested
``--allow-incomplete-preview`` may print a non-publishable aggregate to
stdout, but cannot write an artifact.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import acquire_arxiv_metadata as acquisition
import validate_arxiv_metadata_acquisition as validator


TOOL_NAME = "OPDP arXiv metadata public acquisition summary"
TOOL_VERSION = "0.1.0"
PUBLIC_SUMMARY_SCHEMA = "opdp.mathdb.arxiv-api-public-aggregate-summary.v1"


class PublicSummaryError(RuntimeError):
    """The requested public aggregate cannot safely be emitted."""


def canonical_json_bytes(value: Any) -> bytes:
    """Use the same stable JSON representation as immutable run evidence."""

    return acquisition.canonical_json_bytes(value)


def sha256_file(path: Path) -> str:
    return acquisition.sha256_file(path)


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def writer_lock_present(metadata_dir: Path) -> bool:
    """Treat any lock as uncertain; never inspect dynamic evidence while present."""

    lock_path = metadata_dir / ".acquisition.lock"
    return lock_path.exists() or lock_path.is_symlink()


def hash_regular_file(path: Path, label: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise PublicSummaryError(f"{label} is not a regular file")
    return sha256_file(path)


def capture_evidence_snapshot(
    run_dir: Path,
    source_manifest: Path,
    metadata_dir: Path,
) -> tuple[str, dict[str, str]]:
    """Hash the immutable evidence set without exposing its file inventory.

    The digest binds the recovery run, frozen source manifest, metadata run,
    request plan, append-only error ledger (if present), and every response
    envelope.  The internal file paths never leave this function; only the
    resulting digest and the five fixed top-level manifest hashes are public.
    """

    recovery_run = run_dir / "run.json"
    metadata_run = metadata_dir / "run.json"
    request_plan = metadata_dir / "request_plan.json"
    error_ledger = metadata_dir / "error_ledger.jsonl"
    bindings = {
        "canonical_recovery_run_sha256": hash_regular_file(recovery_run, "canonical recovery run manifest"),
        "source_manifest_sha256": hash_regular_file(source_manifest, "frozen source manifest"),
        "metadata_run_sha256": hash_regular_file(metadata_run, "metadata run manifest"),
        "request_plan_sha256": hash_regular_file(request_plan, "metadata request plan"),
    }
    snapshot: dict[str, Any] = {
        "schema": "opdp.mathdb.arxiv-api-public-aggregate-snapshot.v1",
        "bindings": bindings,
        "error_ledger_sha256": None,
        "response_envelopes": [],
    }
    if error_ledger.exists() or error_ledger.is_symlink():
        snapshot["error_ledger_sha256"] = hash_regular_file(error_ledger, "metadata error ledger")

    responses_root = metadata_dir / "responses"
    if responses_root.exists() or responses_root.is_symlink():
        if responses_root.is_symlink() or not responses_root.is_dir():
            raise PublicSummaryError("responses root is not a regular directory")
        for path in sorted(responses_root.rglob("*")):
            if path.is_dir():
                continue
            relative = path.relative_to(metadata_dir).as_posix()
            snapshot["response_envelopes"].append(
                {"path": relative, "sha256": hash_regular_file(path, "metadata response envelope")}
            )
    return acquisition.sha256_bytes(canonical_json_bytes(snapshot)), bindings


def nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def nonempty_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value)


def fixed_counter(counter: Counter[str], keys: Iterable[str]) -> dict[str, int]:
    """Return only a fixed, content-free set of aggregate keys."""

    return {key: int(counter.get(key, 0)) for key in keys}


def summarize_error_ledger(error_path: Path) -> dict[str, Any]:
    """Aggregate errors without exposing messages, IDs, URLs, or batch keys."""

    phase_counts: Counter[str] = Counter()
    http_status_counts: Counter[str] = Counter()
    batch_keys: set[str] = set()
    event_count = 0

    if not error_path.exists():
        return {
            "present": False,
            "sha256": None,
            "recorded_event_count": 0,
            "batches_with_one_or_more_recorded_events": 0,
            "phase_counts": fixed_counter(
                phase_counts,
                ("http", "request_or_parse", "other"),
            ),
            "http_status_counts": {},
        }
    if not error_path.is_file() or error_path.is_symlink():
        raise PublicSummaryError("error ledger is not a regular file")

    with error_path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                raise PublicSummaryError(f"error ledger has a blank line at {line_number}")
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise PublicSummaryError(f"error ledger has invalid JSON at line {line_number}") from exc
            if not isinstance(event, dict):
                raise PublicSummaryError(f"error ledger has a non-object event at line {line_number}")
            # The validator has already checked the full binding and schema.
            # This function intentionally maps every variable string into a
            # fixed category so an error message/value can never leak here.
            phase = event.get("phase")
            phase_counts[phase if phase in {"http", "request_or_parse"} else "other"] += 1
            error = event.get("error")
            if isinstance(error, dict):
                status = error.get("http_status")
                if isinstance(status, int) and 100 <= status <= 599:
                    http_status_counts[f"http_{status}"] += 1
            batch_key = event.get("batch_key")
            if isinstance(batch_key, str):
                batch_keys.add(batch_key)
            event_count += 1

    return {
        "present": True,
        "sha256": sha256_file(error_path),
        "recorded_event_count": event_count,
        "batches_with_one_or_more_recorded_events": len(batch_keys),
        "phase_counts": fixed_counter(phase_counts, ("http", "request_or_parse", "other")),
        "http_status_counts": {
            key: int(http_status_counts[key]) for key in sorted(http_status_counts)
        },
    }


def summarize_completed_responses(metadata_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Read valid envelopes and retain only fixed field-presence aggregates."""

    field_counts: Counter[str] = Counter()
    license_counts: Counter[str] = Counter()
    withdrawal_counts: Counter[str] = Counter()
    withdrawal_field_counts: Counter[str] = Counter()
    response_status_counts: Counter[str] = Counter()
    validation_status_counts: Counter[str] = Counter()
    completed_batches = 0
    completed_entries = 0
    metadata_only_envelopes = 0

    batches = plan.get("batches")
    if not isinstance(batches, list):
        raise PublicSummaryError("validated request plan has no batch list")
    for batch in batches:
        if not isinstance(batch, dict):
            raise PublicSummaryError("validated request plan has a non-object batch")
        response_path = acquisition.result_response_path(metadata_dir, batch)
        if not response_path.exists():
            continue
        # load_response_record rechecks the body and parsed-entry hashes.  No
        # textual field is copied into the returned public aggregate.
        record = acquisition.load_response_record(response_path, batch)
        completed_batches += 1
        response = record["response"]
        status = response.get("status")
        response_status_counts[f"http_{status}" if isinstance(status, int) else "http_other"] += 1
        content_scope = record.get("content_scope")
        if content_scope == {
            "raw_full_text_requested": False,
            "contains_only_legacy_api_metadata_response": True,
        }:
            metadata_only_envelopes += 1
        response_validation = record.get("validation")
        validation_status = response_validation.get("status") if isinstance(response_validation, dict) else None
        validation_status_counts[
            "complete_exact_id_match" if validation_status == "complete_exact_id_match" else "other"
        ] += 1

        parsed = record.get("parsed_metadata")
        entries = parsed.get("entries") if isinstance(parsed, dict) else None
        if not isinstance(entries, list):
            raise PublicSummaryError("valid response envelope has no parsed entry list")
        for entry in entries:
            if not isinstance(entry, dict):
                raise PublicSummaryError("valid response envelope has a non-object metadata entry")
            completed_entries += 1

            # Counts only: no title, abstract, author, category, or comment
            # values leave this function.
            if nonempty_text(entry.get("title")):
                field_counts["title_field_present"] += 1
            if nonempty_text(entry.get("abstract")):
                field_counts["abstract_field_present"] += 1
            if nonempty_list(entry.get("authors")):
                field_counts["author_list_nonempty"] += 1
            if nonempty_text(entry.get("published_at")):
                field_counts["published_at_present"] += 1
            if nonempty_text(entry.get("updated_at")):
                field_counts["updated_at_present"] += 1
            if nonempty_list(entry.get("categories")):
                field_counts["category_list_nonempty"] += 1
            if nonempty_text(entry.get("primary_category")):
                field_counts["primary_category_present"] += 1
            if nonempty_text(entry.get("comment")):
                field_counts["comment_field_present"] += 1
            if nonempty_text(entry.get("journal_ref")):
                field_counts["journal_ref_present"] += 1
            if nonempty_text(entry.get("doi")):
                field_counts["doi_present"] += 1
            if nonempty_text(entry.get("report_no")):
                field_counts["report_no_present"] += 1

            version_info = entry.get("version_info")
            if isinstance(version_info, dict):
                if nonempty_text(version_info.get("api_returned_version")):
                    field_counts["api_returned_version_present"] += 1
                if nonempty_list(version_info.get("source_manifest_observed_versions")):
                    field_counts["source_manifest_observed_versions_nonempty"] += 1

            license_info = entry.get("license_info")
            if isinstance(license_info, dict):
                if nonempty_list(license_info.get("license_urls")):
                    license_counts["entries_with_one_or_more_api_license_urls"] += 1
                license_status = license_info.get("status")
                if license_status == "provided_by_api_response":
                    license_counts["provided_by_api_response"] += 1
                elif license_status == "not_provided_by_api_response":
                    license_counts["not_provided_by_api_response"] += 1
                else:
                    license_counts["other_or_missing_status"] += 1
            else:
                license_counts["other_or_missing_status"] += 1

            withdrawal_info = entry.get("withdrawal_info")
            if isinstance(withdrawal_info, dict):
                withdrawal_status = withdrawal_info.get("status")
                if withdrawal_status == "possible_withdrawal_indicator":
                    withdrawal_counts["possible_withdrawal_indicator"] += 1
                elif withdrawal_status == "not_indicated_by_api_response":
                    withdrawal_counts["not_indicated_by_api_response"] += 1
                else:
                    withdrawal_counts["other_or_missing_status"] += 1
                evidence_fields = withdrawal_info.get("evidence_fields")
                if nonempty_list(evidence_fields):
                    withdrawal_counts["entries_with_one_or_more_marker_fields"] += 1
                    if isinstance(evidence_fields, list):
                        for field_name in evidence_fields:
                            if field_name == "title":
                                withdrawal_field_counts["title"] += 1
                            elif field_name == "comment":
                                withdrawal_field_counts["comment"] += 1
                            elif field_name == "summary":
                                withdrawal_field_counts["abstract"] += 1
                            else:
                                withdrawal_field_counts["other"] += 1
            else:
                withdrawal_counts["other_or_missing_status"] += 1

    return {
        "response_aggregates": {
            "completed_response_envelope_count": completed_batches,
            "metadata_only_response_envelope_count": metadata_only_envelopes,
            "http_status_counts": {
                key: int(response_status_counts[key]) for key in sorted(response_status_counts)
            },
            "entry_validation_status_counts": fixed_counter(
                validation_status_counts,
                ("complete_exact_id_match", "other"),
            ),
        },
        "completed_metadata_entry_count": completed_entries,
        "metadata_field_presence_counts": {
            "title_field_present": int(field_counts["title_field_present"]),
            "abstract_field_present": int(field_counts["abstract_field_present"]),
            "author_list_nonempty": int(field_counts["author_list_nonempty"]),
            "published_at_present": int(field_counts["published_at_present"]),
            "updated_at_present": int(field_counts["updated_at_present"]),
            "category_list_nonempty": int(field_counts["category_list_nonempty"]),
            "primary_category_present": int(field_counts["primary_category_present"]),
            "comment_field_present": int(field_counts["comment_field_present"]),
            "journal_ref_present": int(field_counts["journal_ref_present"]),
            "doi_present": int(field_counts["doi_present"]),
            "report_no_present": int(field_counts["report_no_present"]),
            "api_returned_version_present": int(field_counts["api_returned_version_present"]),
            "source_manifest_observed_versions_nonempty": int(
                field_counts["source_manifest_observed_versions_nonempty"]
            ),
        },
        "license_field_presence_counts": {
            "entries_with_one_or_more_api_license_urls": int(
                license_counts["entries_with_one_or_more_api_license_urls"]
            ),
            "provided_by_api_response": int(license_counts["provided_by_api_response"]),
            "not_provided_by_api_response": int(
                license_counts["not_provided_by_api_response"]
            ),
            "other_or_missing_status": int(license_counts["other_or_missing_status"]),
        },
        "withdrawal_marker_aggregate": {
            "possible_withdrawal_indicator": int(
                withdrawal_counts["possible_withdrawal_indicator"]
            ),
            "not_indicated_by_api_response": int(
                withdrawal_counts["not_indicated_by_api_response"]
            ),
            "other_or_missing_status": int(withdrawal_counts["other_or_missing_status"]),
            "entries_with_one_or_more_marker_fields": int(
                withdrawal_counts["entries_with_one_or_more_marker_fields"]
            ),
            "marker_field_counts": fixed_counter(
                withdrawal_field_counts,
                ("title", "comment", "abstract", "other"),
            ),
            "interpretation": "A marker is a non-decisive text heuristic, not a withdrawal or open-status determination.",
        },
    }


def make_base_summary(
    snapshot_state: str,
    validation_status: str,
    verification_status: str,
    *,
    validation_error_count: int | None = None,
    validation_warning_count: int | None = None,
    evidence_snapshot_sha256: str | None = None,
) -> dict[str, Any]:
    """Create a fixed, content-free shape for complete and blocked states."""

    final_eligible = verification_status == "verified_complete"
    return {
        "schema": PUBLIC_SUMMARY_SCHEMA,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "read_only_inputs": True,
        "content_scope": {
            "contains_individual_arxiv_identifiers": False,
            "contains_request_urls": False,
            "contains_response_file_paths": False,
            "contains_titles": False,
            "contains_abstracts": False,
            "contains_author_names": False,
            "contains_atom_xml_or_other_response_bytes": False,
            "contains_error_messages": False,
            "contains_source_text": False,
            "contains_only_aggregate_counts_and_digests": True,
        },
        "snapshot": {
            "snapshot_state": snapshot_state,
            "evidence_snapshot_sha256": evidence_snapshot_sha256,
        },
        "validation": {
            "status": validation_status,
            "error_count": validation_error_count,
            "warning_count": validation_warning_count,
            "validator": {
                "name": validator.VALIDATOR_NAME,
                "version": validator.VALIDATOR_VERSION,
            },
        },
        "integrity": {
            "validator_error_count": validation_error_count,
            "validator_warning_count": validation_warning_count,
            "unexpected_response_file_count": None,
            "recorded_error_event_count": None,
        },
        "verification": {"status": verification_status},
        "publication_gate": {
            "final_artifact_eligible": final_eligible,
            "output_mode": "immutable_public_artifact" if final_eligible else "stdout_preview_only",
            "reason": (
                "A stable, quiescent snapshot passed validation with no warnings and every planned batch is complete."
                if final_eligible
                else "No final public artifact may be written for this snapshot state."
            ),
        },
        "bindings": None,
        "coverage": None,
        "response_aggregates": None,
        "error_ledger": None,
        "metadata_field_presence_counts": None,
        "license_field_presence_counts": None,
        "withdrawal_marker_aggregate": None,
        "not_claimed": [
            "statement reconstruction",
            "recovery-outcome label",
            "whether a proposition remains open",
            "rights to redistribute paper content",
            "OPDP scoring or recalculation",
        ],
    }


def make_public_summary(
    run_dir: Path,
    source_manifest: Path,
    metadata_dir: Path,
) -> dict[str, Any]:
    """Build a deterministic public aggregate from a stable, quiescent run.

    A missing lock is necessary but not sufficient: the tool hashes the full
    evidence set before and after validation/aggregation.  Any change
    suppresses all response-derived public metrics rather than risking a
    mixed-time snapshot.
    """

    run_dir = run_dir.resolve()
    source_manifest = source_manifest.resolve()
    metadata_dir = metadata_dir.resolve()
    if not run_dir.is_dir():
        raise PublicSummaryError(f"recovery run directory does not exist: {run_dir}")
    if not source_manifest.is_file():
        raise PublicSummaryError(f"source manifest does not exist: {source_manifest}")
    if not metadata_dir.is_dir() or not is_within(metadata_dir, run_dir):
        raise PublicSummaryError("metadata directory must be an existing directory below the recovery run")

    # Do not inspect plan, responses, error ledger, or lock contents while a
    # writer may be active.  A present lock may be stale, but treating it as
    # uncertain is safer than exposing a torn aggregate.
    if writer_lock_present(metadata_dir):
        return make_base_summary(
            "writer_lock_present",
            "not_run",
            "blocked_writer_lock_present",
        )

    try:
        pre_snapshot_sha, snapshot_bindings = capture_evidence_snapshot(
            run_dir, source_manifest, metadata_dir
        )
    except (OSError, PublicSummaryError):
        return make_base_summary(
            "input_unreadable",
            "not_run",
            "blocked_input_unreadable",
        )

    try:
        # This independent validator reconstructs every completed response
        # from immutable Atom bytes.  Representative findings can contain
        # dynamic context, so this exporter keeps only fixed status/counts.
        report = validator.validate_acquisition(run_dir, source_manifest, metadata_dir, 1)
        findings = report.get("findings")
        report_coverage = report.get("coverage")
        validation_status = report.get("status")
        if not isinstance(findings, dict) or validation_status not in {
            "valid_complete",
            "valid_incomplete",
            "invalid",
        }:
            raise PublicSummaryError("metadata validator returned an unknown report shape")
        if not isinstance(report_coverage, dict):
            raise PublicSummaryError("metadata validator returned no coverage object")
        validation_error_count = int(findings.get("error_count", 0))
        validation_warning_count = int(findings.get("warning_count", 0))

        response_data: dict[str, Any] | None = None
        error_summary: dict[str, Any] | None = None
        plan: dict[str, Any] | None = None
        # Never derive public counts when integrity validation has failed or
        # warned (for example, an unterminated in-flight error-ledger line).
        if validation_error_count == 0 and validation_warning_count == 0:
            plan = acquisition.read_json_object(
                metadata_dir / "request_plan.json", "metadata request plan"
            )
            response_data = summarize_completed_responses(metadata_dir, plan)
            error_summary = summarize_error_ledger(metadata_dir / "error_ledger.jsonl")
    except (
        OSError,
        ValueError,
        PublicSummaryError,
        acquisition.AcquisitionError,
        validator.ValidationError,
    ):
        # If an input changed in a way that prevents a coherent aggregate,
        # expose no metrics.  The post-snapshot lock check below is impossible
        # only when the input cannot safely be read at all.
        return make_base_summary(
            "input_unreadable",
            "not_run",
            "blocked_input_unreadable",
        )

    # The postflight check is deliberately after both validator and aggregate
    # reads; a writer or external modification during either phase invalidates
    # the entire public view.
    if writer_lock_present(metadata_dir):
        return make_base_summary(
            "changed_during_read",
            "not_run",
            "blocked_input_changed_during_read",
        )
    try:
        post_snapshot_sha, _ = capture_evidence_snapshot(run_dir, source_manifest, metadata_dir)
    except (OSError, PublicSummaryError):
        return make_base_summary(
            "input_unreadable",
            "not_run",
            "blocked_input_unreadable",
        )
    if post_snapshot_sha != pre_snapshot_sha:
        return make_base_summary(
            "changed_during_read",
            "not_run",
            "blocked_input_changed_during_read",
        )

    # Stable, quiescent snapshots can report the validator's state.  Any
    # warning is still non-publishable even if the legacy validator calls the
    # underlying evidence `valid_complete`.
    if validation_error_count > 0 or validation_status == "invalid":
        return make_base_summary(
            "stable_quiescent",
            "invalid",
            "blocked_integrity_error",
            validation_error_count=validation_error_count,
            validation_warning_count=validation_warning_count,
            evidence_snapshot_sha256=pre_snapshot_sha,
        )
    if validation_warning_count > 0:
        return make_base_summary(
            "stable_quiescent",
            validation_status,
            "blocked_validator_warning",
            validation_error_count=validation_error_count,
            validation_warning_count=validation_warning_count,
            evidence_snapshot_sha256=pre_snapshot_sha,
        )
    if response_data is None or error_summary is None or plan is None:
        return make_base_summary(
            "stable_quiescent",
            validation_status,
            "blocked_input_unreadable",
            validation_error_count=validation_error_count,
            validation_warning_count=validation_warning_count,
            evidence_snapshot_sha256=pre_snapshot_sha,
        )

    source = plan.get("source")
    selection = plan.get("selection")
    batches = plan.get("batches")
    if not isinstance(source, dict) or not isinstance(selection, dict) or not isinstance(batches, list):
        return make_base_summary(
            "stable_quiescent",
            validation_status,
            "blocked_input_unreadable",
            validation_error_count=validation_error_count,
            validation_warning_count=validation_warning_count,
            evidence_snapshot_sha256=pre_snapshot_sha,
        )
    source_rows_sha = source.get("arxiv_sources_sha256")
    requested_ids_sha = selection.get("arxiv_ids_sha256")
    requested_source_count = selection.get("source_count")
    selection_scope = selection.get("scope")
    if (
        not isinstance(source_rows_sha, str)
        or not isinstance(requested_ids_sha, str)
        or not isinstance(requested_source_count, int)
        or requested_source_count < 1
        or selection_scope not in {"complete", "sample"}
    ):
        return make_base_summary(
            "stable_quiescent",
            validation_status,
            "blocked_input_unreadable",
            validation_error_count=validation_error_count,
            validation_warning_count=validation_warning_count,
            evidence_snapshot_sha256=pre_snapshot_sha,
        )

    completed_entries = response_data["completed_metadata_entry_count"]
    completed_batches = response_data["response_aggregates"]["completed_response_envelope_count"]
    report_completed_batches = report_coverage.get("completed_batches")
    report_pending_batches = report_coverage.get("pending_batches")
    report_unexpected_response_files = report_coverage.get("unexpected_response_files")
    report_error_events = report_coverage.get("error_ledger_event_count")
    if (
        completed_entries > requested_source_count
        or completed_batches > len(batches)
        or report_completed_batches != completed_batches
        or report_pending_batches != len(batches) - completed_batches
        or not isinstance(report_unexpected_response_files, int)
        or not isinstance(report_error_events, int)
        or report_error_events != error_summary["recorded_event_count"]
    ):
        return make_base_summary(
            "stable_quiescent",
            validation_status,
            "blocked_integrity_error",
            validation_error_count=validation_error_count,
            validation_warning_count=validation_warning_count,
            evidence_snapshot_sha256=pre_snapshot_sha,
        )

    verification_status = (
        "verified_complete" if validation_status == "valid_complete" else "verified_incomplete"
    )
    summary = make_base_summary(
        "stable_quiescent",
        validation_status,
        verification_status,
        validation_error_count=validation_error_count,
        validation_warning_count=validation_warning_count,
        evidence_snapshot_sha256=pre_snapshot_sha,
    )
    summary.update(
        {
            "bindings": {
                **snapshot_bindings,
                "arxiv_source_rows_sha256": source_rows_sha,
                "requested_id_order_sha256": requested_ids_sha,
            },
            "coverage": {
                "selection_scope": selection_scope,
                "planned_batch_count": len(batches),
                "completed_response_batch_count": completed_batches,
                "pending_batch_count": len(batches) - completed_batches,
                "requested_id_count": requested_source_count,
                "completed_requested_id_count": completed_entries,
                "pending_requested_id_count": requested_source_count - completed_entries,
                "complete": validation_status == "valid_complete",
            },
            "integrity": {
                "validator_error_count": validation_error_count,
                "validator_warning_count": validation_warning_count,
                "unexpected_response_file_count": report_unexpected_response_files,
                "recorded_error_event_count": error_summary["recorded_event_count"],
            },
            "response_aggregates": response_data["response_aggregates"],
            "error_ledger": error_summary,
            "metadata_field_presence_counts": response_data["metadata_field_presence_counts"],
            "license_field_presence_counts": response_data["license_field_presence_counts"],
            "withdrawal_marker_aggregate": response_data["withdrawal_marker_aggregate"],
        }
    )
    return summary


def write_immutable_artifact(path: Path, summary: dict[str, Any], run_dir: Path) -> str:
    """Write a final public artifact only after the complete-run gate passes."""

    if is_within(path, run_dir):
        raise PublicSummaryError("public summary output must be outside the ignored recovery run directory")
    payload = canonical_json_bytes(summary) + b"\n"
    digest = acquisition.sha256_bytes(payload)
    if path.exists():
        if not path.is_file() or path.is_symlink():
            raise PublicSummaryError("public summary output exists but is not a regular file")
        if path.read_bytes() != payload:
            raise PublicSummaryError(
                "public summary output already exists with different content; "
                "choose a new versioned filename rather than overwriting it"
            )
        return digest
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
    return digest


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Emit a compact, content-free public summary of a quiescent arXiv metadata acquisition run."
    )
    value.add_argument("--run-dir", type=Path, required=True, help="canonical MathDB recovery run directory")
    value.add_argument("--source-manifest", type=Path, required=True, help="frozen arXiv source manifest")
    value.add_argument("--metadata-dir", type=Path, required=True, help="completed metadata acquisition directory under --run-dir")
    value.add_argument(
        "--output",
        type=Path,
        help="optional immutable public JSON artifact; only allowed for a valid_complete run",
    )
    value.add_argument(
        "--allow-incomplete-preview",
        action="store_true",
        help="allow an unpublishable stdout-only aggregate for a quiescent valid_incomplete run",
    )
    return value


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        summary = make_public_summary(args.run_dir, args.source_manifest, args.metadata_dir)
        status = summary["validation"]["status"]
        complete = bool(summary["publication_gate"]["final_artifact_eligible"])
        if not complete and not args.allow_incomplete_preview:
            raise PublicSummaryError(
                f"refusing to emit a public summary for validation status {status!r}; "
                "wait for valid_complete or use --allow-incomplete-preview for stdout only"
            )
        if args.output is not None:
            if not complete:
                raise PublicSummaryError("--output is forbidden until validation status is valid_complete")
            digest = write_immutable_artifact(args.output.resolve(), summary, args.run_dir.resolve())
            print(
                json.dumps(
                    {
                        "status": "written",
                        "public_summary_sha256": digest,
                        "validation_status": status,
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (PublicSummaryError, acquisition.AcquisitionError, validator.ValidationError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
