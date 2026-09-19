#!/usr/bin/env python3
"""Audit Top 500 exact/strong ProofAtlas-to-OPDP identity links conservatively.

The automatic crosswalk's ``exact_match`` and ``strong_match`` labels are
identity evidence, not a substitute for a human scope review.  This tool
separates the full-text material needed for that review from its public
artifact: local context may be emitted only behind an explicit switch, while
the auditable release sidecar retains identifiers, hashes, source status, and
one-line curator evidence only.  It never writes a ProofAtlas target or an
OPDP statement to the public sidecar.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
CROSSWALK_MODULE_PATH = SCRIPT_DIR / "build_proofatlas_top500_crosswalk.py"
SPEC = importlib.util.spec_from_file_location("proofatlas_crosswalk", CROSSWALK_MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
CROSSWALK = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CROSSWALK
SPEC.loader.exec_module(CROSSWALK)


TOOL_NAME = "OPDP ProofAtlas Top 500 match-identity auditor"
TOOL_VERSION = "1.0.0"
CROSSWALK_SCHEMA = "opdp.proofatlas-top500-crosswalk.v1"
REVIEW_SCHEMA = "opdp.proofatlas-top500-match-identity-audit.v1"
APPEND_DRAFT_SCHEMA = "opdp.proofatlas-top500-append-draft.v1"
NORMALIZATION_MODE = "curator_authored_independent_normalization"
MATCH_DECISIONS = frozenset({"exact_match", "strong_match"})
ALLOWED_OUTCOMES = frozenset(
    {
        "same_problem_reviewed",
        "variant_or_subproblem",
        "related_not_same",
        "unresolved_due_to_incomplete_existing_text",
    }
)
SEPARATELY_SCOPED_OUTCOMES = frozenset({"variant_or_subproblem", "related_not_same"})


class ReviewError(RuntimeError):
    """Raised for an unsafe or invalid audit input."""


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ReviewError(f"JSONL input does not exist: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReviewError(f"{path}:{line_number}: invalid JSON") from exc
        if not isinstance(row, dict):
            raise ReviewError(f"{path}:{line_number}: expected object")
        rows.append(row)
    return rows


def matched_rows(path: Path) -> list[dict[str, Any]]:
    rows = load_jsonl(path)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if row.get("schema") != CROSSWALK_SCHEMA:
            raise ReviewError(f"{path}: unexpected crosswalk schema")
        if row.get("decision") not in MATCH_DECISIONS:
            continue
        proof = row.get("proofatlas")
        if not isinstance(proof, dict):
            raise ReviewError(f"{path}: matched row missing proofatlas object")
        problem_id = proof.get("problem_id")
        rank = proof.get("release_rank")
        candidates = row.get("selected_candidates")
        if not isinstance(problem_id, str) or not isinstance(rank, int):
            raise ReviewError(f"{path}: matched row has invalid ProofAtlas identifier/rank")
        if problem_id in seen:
            raise ReviewError(f"{path}: duplicate matched ProofAtlas ID: {problem_id}")
        if not isinstance(candidates, list) or not candidates:
            raise ReviewError(f"{path}: matched row has no selected OPDP candidate")
        seen.add(problem_id)
        selected.append(row)
    if not selected:
        raise ReviewError(f"{path}: no exact_match or strong_match rows")
    return sorted(selected, key=lambda row: int(row["proofatlas"]["release_rank"]))


def load_proofatlas(path: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError(f"cannot parse ProofAtlas snapshot {path}: {exc}") from exc
    records = raw.get("records") if isinstance(raw, dict) else None
    if not isinstance(records, list):
        raise ReviewError("ProofAtlas snapshot does not contain records")
    return {
        entry["problemId"]: entry
        for entry in records
        if isinstance(entry, dict) and isinstance(entry.get("problemId"), str)
    }


def source_status(record: dict[str, Any]) -> str:
    status = record.get("releaseStatus")
    if not isinstance(status, str) or not status.strip():
        raise ReviewError(f"ProofAtlas source {record.get('problemId')!r} has no releaseStatus")
    return status


def source_url(record: dict[str, Any]) -> str:
    formal = record.get("formalStatementSource")
    url = formal.get("url") if isinstance(formal, dict) else None
    if not isinstance(url, str) or not url.strip():
        raise ReviewError(f"ProofAtlas source {record.get('problemId')!r} has no formal statement URL")
    return url


def require_one_line(value: Any, field: str, maximum: int = 600) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ReviewError(f"{field} must be a nonempty one-line <= {maximum}-character string")
    if "\n" in value or "\r" in value:
        raise ReviewError(f"{field} must be one line")
    return value.strip()


def private_context(
    *, crosswalk_path: Path, proofatlas_path: Path, atlas_path: Path, output: Path
) -> int:
    """Write local full-text audit context; never a release artifact."""

    rows = matched_rows(crosswalk_path)
    proofatlas = load_proofatlas(proofatlas_path)
    candidate_ids = {
        candidate["problem_id"]
        for row in rows
        for candidate in row["selected_candidates"]
        if isinstance(candidate, dict) and isinstance(candidate.get("problem_id"), int)
    }
    atlas: dict[int, dict[str, Any]] = {}
    for item in CROSSWALK.iter_v17_records(atlas_path):
        problem_id = item.get("problem_id")
        if problem_id in candidate_ids:
            atlas[problem_id] = item
    if candidate_ids - set(atlas):
        raise ReviewError("one or more selected candidate IDs were not found in frozen OPDP v1.7")

    context_rows: list[dict[str, Any]] = []
    for row in rows:
        proof = row["proofatlas"]
        source = proofatlas.get(proof["problem_id"])
        if not isinstance(source, dict):
            raise ReviewError(f"ProofAtlas problem missing from raw snapshot: {proof['problem_id']}")
        candidates: list[dict[str, Any]] = []
        for candidate in row["selected_candidates"]:
            if not isinstance(candidate, dict) or not isinstance(candidate.get("problem_id"), int):
                raise ReviewError(f"invalid selected candidate for {proof['problem_id']}")
            record = atlas[candidate["problem_id"]]
            source_text = record.get("source_text")
            candidates.append(
                {
                    "problem_id": candidate["problem_id"],
                    "problem_number": candidate.get("problem_number"),
                    "title": record.get("title"),
                    "statement": source_text.get("statement") if isinstance(source_text, dict) else None,
                    "text_mode": source_text.get("text_mode") if isinstance(source_text, dict) else None,
                    "source_statement_completeness": candidate.get("source_statement_completeness"),
                    "comparison": candidate.get("comparison"),
                }
            )
        context_rows.append(
            {
                "schema": "opdp.private-proofatlas-top500-match-identity-context.v1",
                "warning": "LOCAL REVIEW CONTEXT ONLY: contains source text; do not commit or publish.",
                "proofatlas_problem_id": proof["problem_id"],
                "proofatlas_rank": proof["release_rank"],
                "proofatlas_crosswalk_decision": row["decision"],
                "proofatlas_title": source.get("canonicalTitle"),
                "proofatlas_release_status_as_published": source_status(source),
                "proofatlas_exact_target": source.get("exactTarget"),
                "candidates": candidates,
            }
        )
    CROSSWALK.atomic_write(
        output, b"".join(CROSSWALK.canonical_json_bytes(row) + b"\n" for row in context_rows)
    )
    return len(context_rows)


def write_reviewed_same_baseline(
    *, crosswalk_path: Path, proofatlas_path: Path, output: Path
) -> int:
    """Write local review input after a human has checked the matched corpus.

    This deliberately writes *input*, not a release sidecar.  The generated
    baseline is useful only after the reviewer has privately checked all
    matches and has replaced every scope mismatch with its individual
    outcome/evidence.  It contains no target or frozen-record prose.
    """

    rows = matched_rows(crosswalk_path)
    proofatlas = load_proofatlas(proofatlas_path)
    baseline: list[dict[str, Any]] = []
    for row in rows:
        proof = row["proofatlas"]
        source = proofatlas.get(proof["problem_id"])
        if not isinstance(source, dict):
            raise ReviewError(f"ProofAtlas problem missing from raw snapshot: {proof['problem_id']}")
        candidate_ids = [
            candidate["problem_id"]
            for candidate in row["selected_candidates"]
            if isinstance(candidate, dict) and isinstance(candidate.get("problem_id"), int)
        ]
        if not candidate_ids:
            raise ReviewError(f"matched source {proof['problem_id']} lacks an integer selected candidate")
        status = source_status(source)
        baseline.append(
            {
                "proofatlas_problem_id": proof["problem_id"],
                "review_outcome": "same_problem_reviewed",
                "distinct_problem_worthy_of_append": False,
                "review_evidence": (
                    "Private scope review found the same named mathematical assertion in the selected frozen record."
                ),
                "source_status_note": (
                    None
                    if status == "open"
                    else f"ProofAtlas release status is {status}; this scope audit does not independently verify current status."
                ),
                "atlas_problem_ids_reviewed": candidate_ids,
            }
        )
    CROSSWALK.atomic_write(
        output, b"".join(CROSSWALK.canonical_json_bytes(row) + b"\n" for row in baseline)
    )
    return len(baseline)


def write_review_input_from_overrides(
    *, crosswalk_path: Path, proofatlas_path: Path, overrides_path: Path, output: Path
) -> int:
    """Build a local full-coverage decision input from reviewed scope overrides.

    The default row is intentionally limited to a reviewer-confirmed identity
    conclusion.  Every exception must be supplied as a compact, source-free
    override; the normal release validator subsequently checks full coverage,
    selected candidate IDs, scope labels, and source-status notes.
    """

    rows = matched_rows(crosswalk_path)
    expected = {row["proofatlas"]["problem_id"]: row for row in rows}
    proofatlas = load_proofatlas(proofatlas_path)
    overrides: dict[str, dict[str, Any]] = {}
    for line_number, override in enumerate(load_jsonl(overrides_path), 1):
        problem_id = override.get("proofatlas_problem_id")
        outcome = override.get("review_outcome")
        worthy = override.get("distinct_problem_worthy_of_append")
        if not isinstance(problem_id, str) or problem_id not in expected:
            raise ReviewError(f"override row {line_number}: unknown matched ProofAtlas problem ID")
        if problem_id in overrides:
            raise ReviewError(f"override row {line_number}: duplicate ProofAtlas problem ID")
        if outcome not in ALLOWED_OUTCOMES:
            raise ReviewError(f"override row {line_number}: invalid review_outcome")
        if not isinstance(worthy, bool):
            raise ReviewError(
                f"override row {line_number}: distinct_problem_worthy_of_append must be boolean"
            )
        if worthy and outcome not in SEPARATELY_SCOPED_OUTCOMES:
            raise ReviewError(
                f"override row {line_number}: append-worthy requires a materially separate scoped outcome"
            )
        require_one_line(override.get("review_evidence"), f"override row {line_number}: review_evidence")
        overrides[problem_id] = override

    input_rows: list[dict[str, Any]] = []
    for row in rows:
        proof = row["proofatlas"]
        source = proofatlas.get(proof["problem_id"])
        if not isinstance(source, dict):
            raise ReviewError(f"ProofAtlas problem missing from raw snapshot: {proof['problem_id']}")
        status = source_status(source)
        candidate_ids = [
            candidate["problem_id"]
            for candidate in row["selected_candidates"]
            if isinstance(candidate, dict) and isinstance(candidate.get("problem_id"), int)
        ]
        if not candidate_ids:
            raise ReviewError(f"matched source {proof['problem_id']} lacks an integer selected candidate")
        override = overrides.get(proof["problem_id"])
        outcome = override["review_outcome"] if override is not None else "same_problem_reviewed"
        worthy = (
            override["distinct_problem_worthy_of_append"] if override is not None else False
        )
        evidence = (
            override["review_evidence"]
            if override is not None
            else "Private scope review found the same named mathematical assertion in the selected frozen record."
        )
        status_note = (
            None
            if status == "open"
            else f"ProofAtlas release status is {status}; this scope audit does not independently verify current status."
        )
        input_rows.append(
            {
                "proofatlas_problem_id": proof["problem_id"],
                "review_outcome": outcome,
                "distinct_problem_worthy_of_append": worthy,
                "review_evidence": evidence,
                "source_status_note": status_note,
                "atlas_problem_ids_reviewed": candidate_ids,
            }
        )
    CROSSWALK.atomic_write(
        output, b"".join(CROSSWALK.canonical_json_bytes(row) + b"\n" for row in input_rows)
    )
    return len(input_rows)


def validate_and_write_review(
    *, crosswalk_path: Path, proofatlas_path: Path, decisions_path: Path, output: Path
) -> int:
    """Validate compact scope judgments for exactly all exact/strong rows."""

    rows = matched_rows(crosswalk_path)
    expected = {row["proofatlas"]["problem_id"]: row for row in rows}
    proofatlas = load_proofatlas(proofatlas_path)
    supplied = load_jsonl(decisions_path)
    if len(supplied) != len(expected):
        raise ReviewError(f"decision input must contain exactly {len(expected)} rows, found {len(supplied)}")
    public_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, decision in enumerate(supplied, 1):
        problem_id = decision.get("proofatlas_problem_id")
        outcome = decision.get("review_outcome")
        worthy = decision.get("distinct_problem_worthy_of_append")
        evidence = require_one_line(decision.get("review_evidence"), f"decision row {line_number}: review_evidence")
        chosen = decision.get("atlas_problem_ids_reviewed")
        if not isinstance(problem_id, str) or problem_id not in expected:
            raise ReviewError(f"decision row {line_number}: unknown matched ProofAtlas problem ID")
        if problem_id in seen:
            raise ReviewError(f"decision row {line_number}: duplicate ProofAtlas problem ID")
        if outcome not in ALLOWED_OUTCOMES:
            raise ReviewError(f"decision row {line_number}: invalid review_outcome")
        if not isinstance(worthy, bool):
            raise ReviewError(f"decision row {line_number}: distinct_problem_worthy_of_append must be boolean")
        if worthy and outcome not in SEPARATELY_SCOPED_OUTCOMES:
            raise ReviewError(
                f"decision row {line_number}: append-worthy requires a materially separate scoped outcome"
            )
        if not isinstance(chosen, list) or not chosen or not all(
            isinstance(value, int) and not isinstance(value, bool) for value in chosen
        ):
            raise ReviewError(f"decision row {line_number}: atlas_problem_ids_reviewed must be nonempty int list")
        candidate_ids = {
            candidate["problem_id"]
            for candidate in expected[problem_id]["selected_candidates"]
            if isinstance(candidate, dict) and isinstance(candidate.get("problem_id"), int)
        }
        if not set(chosen) <= candidate_ids:
            raise ReviewError(f"decision row {line_number}: reviewed ID is not a selected crosswalk candidate")
        source = proofatlas.get(problem_id)
        if not isinstance(source, dict):
            raise ReviewError(f"decision row {line_number}: ProofAtlas source not in raw snapshot")
        status = source_status(source)
        status_note = decision.get("source_status_note")
        if status == "open":
            if status_note is not None:
                status_note = require_one_line(status_note, f"decision row {line_number}: source_status_note")
        else:
            status_note = require_one_line(status_note, f"decision row {line_number}: source_status_note")
            if status not in status_note:
                raise ReviewError(
                    f"decision row {line_number}: non-open source_status_note must name the published status"
                )
        crosswalk = expected[problem_id]
        proof = crosswalk["proofatlas"]
        public_rows.append(
            {
                "schema": REVIEW_SCHEMA,
                "proofatlas": {
                    "problem_id": proof["problem_id"],
                    "release_rank": proof["release_rank"],
                    "canonical_title": proof["canonical_title"],
                    "canonical_title_sha256": proof["canonical_title_sha256"],
                    "exact_target_sha256": proof["exact_target_sha256"],
                    "formal_statement_source_url": source_url(source),
                    "release_status_as_published": status,
                },
                "crosswalk_identity_label": crosswalk["decision"],
                "review_outcome": outcome,
                "distinct_problem_worthy_of_append": worthy,
                "review_evidence": evidence,
                "source_status_note": status_note,
                "atlas_problem_ids_reviewed": chosen,
                "non_destructive_contract": {
                    "proofatlas_exact_target_retained": False,
                    "opdp_statement_or_excerpt_retained": False,
                    "opdp_payload_modified": False,
                    "opdp_records_appended": False,
                    "open_status_verified": False,
                    "opdp_dimensions_recalculated": False,
                },
            }
        )
        seen.add(problem_id)
    if seen != set(expected):
        raise ReviewError("decision input does not cover exactly the matched crosswalk IDs")
    public_rows.sort(key=lambda row: int(row["proofatlas"]["release_rank"]))
    CROSSWALK.atomic_write(
        output, b"".join(CROSSWALK.canonical_json_bytes(row) + b"\n" for row in public_rows)
    )
    return len(public_rows)


def validate_append_drafts(
    *, review_path: Path, drafts_path: Path, output: Path | None
) -> int:
    """Validate rights-gated draft normalizations for separately scoped rows.

    The output deliberately uses the same draft schema as the no-strong-match
    intake.  It is not a curated append list: the final assembler must still
    receive an explicit curator-approved snapshot with stable namespace IDs.
    """

    reviews = load_jsonl(review_path)
    worthy = {
        row.get("proofatlas", {}).get("problem_id"): row
        for row in reviews
        if isinstance(row.get("proofatlas"), dict) and row.get("distinct_problem_worthy_of_append") is True
    }
    if None in worthy:
        raise ReviewError("review sidecar has an append-worthy row without a source identifier")
    drafts = load_jsonl(drafts_path)
    if len(drafts) != len(worthy):
        raise ReviewError(f"append draft input must contain exactly {len(worthy)} rows, found {len(drafts)}")
    public_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, draft in enumerate(drafts, 1):
        proof = draft.get("proofatlas")
        if not isinstance(proof, dict):
            raise ReviewError(f"append draft row {line_number}: missing proofatlas object")
        problem_id = proof.get("problem_id")
        if not isinstance(problem_id, str) or problem_id not in worthy:
            raise ReviewError(f"append draft row {line_number}: not an append-worthy audited source ID")
        if problem_id in seen:
            raise ReviewError(f"append draft row {line_number}: duplicate ProofAtlas ID")
        reviewed_proof = worthy[problem_id]["proofatlas"]
        for key in ("release_rank", "formal_statement_source_url", "release_status_as_published"):
            if proof.get(key) != reviewed_proof.get(key):
                raise ReviewError(f"append draft row {line_number}: ProofAtlas {key} differs from audit sidecar")
        provenance = draft.get("statement_provenance")
        if not isinstance(provenance, dict) or provenance.get("mode") != NORMALIZATION_MODE:
            raise ReviewError(
                f"append draft row {line_number}: requires {NORMALIZATION_MODE!r} provenance mode"
            )
        attestation = require_one_line(
            provenance.get("attestation"), f"append draft row {line_number}: provenance attestation"
        )
        normalization = require_one_line(
            draft.get("curator_authored_independent_normalization"),
            f"append draft row {line_number}: curator-authored normalization",
            1200,
        )
        if normalization.count(".") < 1 and normalization.count("?") < 1:
            raise ReviewError(f"append draft row {line_number}: normalization must be a complete sentence")
        public_rows.append(
            {
                "schema": APPEND_DRAFT_SCHEMA,
                "proofatlas": {
                    "problem_id": problem_id,
                    "release_rank": reviewed_proof["release_rank"],
                    "formal_statement_source_url": reviewed_proof["formal_statement_source_url"],
                    "release_status_as_published": reviewed_proof["release_status_as_published"],
                },
                "review_outcome": worthy[problem_id]["review_outcome"],
                "statement_provenance": {
                    "mode": NORMALIZATION_MODE,
                    "attestation": attestation,
                },
                "curator_authored_independent_normalization": normalization,
                "release_gate": "draft_only_requires_curator_confirmation_before_append",
            }
        )
        seen.add(problem_id)
    if seen != set(worthy):
        raise ReviewError("append drafts do not cover exactly the append-worthy audited source IDs")
    public_rows.sort(key=lambda row: int(row["proofatlas"]["release_rank"]))
    if output is not None:
        CROSSWALK.atomic_write(
            output, b"".join(CROSSWALK.canonical_json_bytes(row) + b"\n" for row in public_rows)
        )
    return len(public_rows)


def write_append_draft_input_from_normalizations(
    *, review_path: Path, normalizations_path: Path, output: Path
) -> int:
    """Pair original curator normalizations with audited source metadata locally."""

    reviews = load_jsonl(review_path)
    worthy = {
        row.get("proofatlas", {}).get("problem_id"): row
        for row in reviews
        if isinstance(row.get("proofatlas"), dict) and row.get("distinct_problem_worthy_of_append") is True
    }
    if None in worthy:
        raise ReviewError("review sidecar has an append-worthy row without a source identifier")
    normalizations = load_jsonl(normalizations_path)
    if len(normalizations) != len(worthy):
        raise ReviewError(
            f"normalization input must contain exactly {len(worthy)} rows, found {len(normalizations)}"
        )
    seen: set[str] = set()
    drafts: list[dict[str, Any]] = []
    for line_number, item in enumerate(normalizations, 1):
        problem_id = item.get("proofatlas_problem_id")
        if not isinstance(problem_id, str) or problem_id not in worthy:
            raise ReviewError(f"normalization row {line_number}: not an append-worthy audited source ID")
        if problem_id in seen:
            raise ReviewError(f"normalization row {line_number}: duplicate ProofAtlas ID")
        normalization = require_one_line(
            item.get("curator_authored_independent_normalization"),
            f"normalization row {line_number}: curator-authored normalization",
            1200,
        )
        proof = worthy[problem_id]["proofatlas"]
        drafts.append(
            {
                "proofatlas": {
                    "problem_id": problem_id,
                    "release_rank": proof["release_rank"],
                    "formal_statement_source_url": proof["formal_statement_source_url"],
                    "release_status_as_published": proof["release_status_as_published"],
                },
                "statement_provenance": {
                    "mode": NORMALIZATION_MODE,
                    "attestation": (
                        "Independent curator-authored paraphrase; it does not reproduce ProofAtlas source prose."
                    ),
                },
                "curator_authored_independent_normalization": normalization,
            }
        )
        seen.add(problem_id)
    if seen != set(worthy):
        raise ReviewError("normalization input does not cover exactly the append-worthy audited source IDs")
    drafts.sort(key=lambda row: int(row["proofatlas"]["release_rank"]))
    CROSSWALK.atomic_write(
        output, b"".join(CROSSWALK.canonical_json_bytes(row) + b"\n" for row in drafts)
    )
    return len(drafts)


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--crosswalk", type=Path, default=Path("data/PROOFATLAS_TOP500_CROSSWALK_v1.jsonl")
    )
    parser.add_argument("--proofatlas-json", type=Path)
    parser.add_argument(
        "--atlas-v17", type=Path, default=Path("data/Ulam_MathDB_OPDP_Assessments_v1.7.json.gz")
    )
    parser.add_argument("--emit-private-context", type=Path)
    parser.add_argument("--allow-private-context", action="store_true")
    parser.add_argument("--write-reviewed-same-baseline", type=Path)
    parser.add_argument("--write-review-input-from-overrides", type=Path)
    parser.add_argument("--review-overrides", type=Path)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--validate-append-drafts", type=Path)
    parser.add_argument("--write-append-draft-input-from-normalizations", type=Path)
    parser.add_argument("--normalizations", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv or sys.argv[1:])
    try:
        modes = sum(
            value is not None
            for value in (
                arguments.emit_private_context,
                arguments.write_reviewed_same_baseline,
                arguments.write_review_input_from_overrides,
                arguments.decisions,
                arguments.validate_append_drafts,
                arguments.write_append_draft_input_from_normalizations,
            )
        )
        if modes != 1:
            raise ReviewError(
                "select exactly one of --emit-private-context, --write-reviewed-same-baseline, "
                "--write-review-input-from-overrides, --decisions, --validate-append-drafts, "
                "or --write-append-draft-input-from-normalizations"
            )
        if arguments.emit_private_context is not None:
            if not arguments.allow_private_context:
                raise ReviewError("--emit-private-context requires explicit --allow-private-context")
            if arguments.proofatlas_json is None:
                raise ReviewError("--emit-private-context requires --proofatlas-json")
            count = private_context(
                crosswalk_path=arguments.crosswalk,
                proofatlas_path=arguments.proofatlas_json,
                atlas_path=arguments.atlas_v17,
                output=arguments.emit_private_context,
            )
            print(f"wrote {count} local match-identity contexts")
        elif arguments.write_reviewed_same_baseline is not None:
            if arguments.proofatlas_json is None:
                raise ReviewError("--write-reviewed-same-baseline requires --proofatlas-json")
            count = write_reviewed_same_baseline(
                crosswalk_path=arguments.crosswalk,
                proofatlas_path=arguments.proofatlas_json,
                output=arguments.write_reviewed_same_baseline,
            )
            print(f"wrote {count} local reviewed-same baseline rows")
        elif arguments.write_review_input_from_overrides is not None:
            if arguments.proofatlas_json is None or arguments.review_overrides is None:
                raise ReviewError(
                    "--write-review-input-from-overrides requires --proofatlas-json and --review-overrides"
                )
            count = write_review_input_from_overrides(
                crosswalk_path=arguments.crosswalk,
                proofatlas_path=arguments.proofatlas_json,
                overrides_path=arguments.review_overrides,
                output=arguments.write_review_input_from_overrides,
            )
            print(f"wrote {count} local audit decision-input rows from scope overrides")
        elif arguments.decisions is not None:
            if arguments.proofatlas_json is None or arguments.output is None:
                raise ReviewError("--decisions requires --proofatlas-json and --output")
            count = validate_and_write_review(
                crosswalk_path=arguments.crosswalk,
                proofatlas_path=arguments.proofatlas_json,
                decisions_path=arguments.decisions,
                output=arguments.output,
            )
            print(f"wrote {count} public-safe match-identity audit rows")
        elif arguments.validate_append_drafts is not None:
            if arguments.review is None:
                raise ReviewError("--validate-append-drafts requires --review")
            count = validate_append_drafts(
                review_path=arguments.review,
                drafts_path=arguments.validate_append_drafts,
                output=arguments.output,
            )
            print(f"validated {count} curator-authored append drafts")
        else:
            if arguments.review is None or arguments.normalizations is None:
                raise ReviewError(
                    "--write-append-draft-input-from-normalizations requires --review and --normalizations"
                )
            count = write_append_draft_input_from_normalizations(
                review_path=arguments.review,
                normalizations_path=arguments.normalizations,
                output=arguments.write_append_draft_input_from_normalizations,
            )
            print(f"wrote {count} local append-draft input rows from independent normalizations")
    except ReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
