#!/usr/bin/env python3
"""Validate compact human review of a bounded ProofAtlas no-strong tranche.

The crosswalk intentionally treats insufficient lexical evidence as an
abstention.  This tool makes a later human scope judgment auditable without
publishing the ProofAtlas target or a frozen OPDP statement.  It can emit a
full-text *local* context only behind an explicit flag and into a caller
chosen ignored path.  Its normal output is a content-minimized decision
sidecar.  A separate append-draft validator accepts only curator-authored,
independent normalizations for rows that a reviewer has actually marked
append-worthy.
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


TOOL_NAME = "OPDP ProofAtlas Top 500 no-strong-match reviewer"
TOOL_VERSION = "1.0.0"
INDEX_SCHEMA = "opdp.proofatlas-top500-no-strong-review-workbench.v1"
REVIEW_SCHEMA = "opdp.proofatlas-top500-no-strong-highrank-review.v1"
APPEND_DRAFT_SCHEMA = "opdp.proofatlas-top500-append-draft.v1"
NORMALIZATION_MODE = "curator_authored_independent_normalization"
ALLOWED_OUTCOMES = frozenset(
    {
        "same_problem_reviewed",
        "variant_or_subproblem",
        "related_not_same",
        "appendable_no_strong_match",
        "unresolved_due_to_incomplete_existing_text",
        "distinct_problem_worthy_of_append",
    }
)
APPEND_WORTHY_OUTCOMES = frozenset(
    {
        "variant_or_subproblem",
        "related_not_same",
        "appendable_no_strong_match",
        "distinct_problem_worthy_of_append",
    }
)


class ReviewError(RuntimeError):
    """Raised for an invalid public decision record or unsafe invocation."""


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


def no_strong_rows(path: Path, rank_min: int, rank_max: int) -> list[dict[str, Any]]:
    if rank_min < 1 or rank_max < rank_min:
        raise ReviewError("rank range must be positive and nonempty")
    rows = load_jsonl(path)
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if row.get("schema") != INDEX_SCHEMA:
            raise ReviewError(f"{path}: unexpected no-strong workbench schema")
        proof = row.get("proofatlas")
        if not isinstance(proof, dict):
            raise ReviewError(f"{path}: workbench row missing proofatlas object")
        problem_id = proof.get("problem_id")
        rank = proof.get("release_rank")
        if not isinstance(problem_id, str) or not isinstance(rank, int):
            raise ReviewError(f"{path}: workbench row has invalid ProofAtlas identifier/rank")
        if rank_min <= rank <= rank_max:
            if problem_id in seen:
                raise ReviewError(f"{path}: duplicate ProofAtlas ID in requested range: {problem_id}")
            selected.append(row)
            seen.add(problem_id)
    if not selected:
        raise ReviewError(f"{path}: no no-strong rows in ranks {rank_min}-{rank_max}")
    return sorted(selected, key=lambda item: int(item["proofatlas"]["release_rank"]))


def load_proofatlas(path: Path) -> dict[str, dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError(f"cannot parse ProofAtlas snapshot {path}: {exc}") from exc
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ReviewError("ProofAtlas snapshot does not contain records")
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        if isinstance(record, dict) and isinstance(record.get("problemId"), str):
            result[record["problemId"]] = record
    return result


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


def private_context(
    *, index_path: Path, proofatlas_path: Path, atlas_path: Path, output: Path, rank_min: int, rank_max: int
) -> int:
    """Write local full-text reading context; never a release artifact."""

    rows = no_strong_rows(index_path, rank_min, rank_max)
    proofatlas = load_proofatlas(proofatlas_path)
    candidate_ids = {
        candidate["problem_id"]
        for row in rows
        for candidate in row.get("candidates", [])
        if isinstance(candidate, dict) and isinstance(candidate.get("problem_id"), int)
    }
    atlas: dict[int, dict[str, Any]] = {}
    for item in CROSSWALK.iter_v17_records(atlas_path):
        problem_id = item.get("problem_id")
        if problem_id in candidate_ids:
            atlas[problem_id] = item
    if candidate_ids - set(atlas):
        raise ReviewError("one or more reviewed candidate IDs were not found in frozen OPDP v1.7")

    context_rows: list[dict[str, Any]] = []
    for row in rows:
        proof = row["proofatlas"]
        source = proofatlas.get(proof["problem_id"])
        if not isinstance(source, dict):
            raise ReviewError(f"ProofAtlas problem missing from raw snapshot: {proof['problem_id']}")
        candidates = []
        for candidate in row.get("candidates", []):
            if not isinstance(candidate, dict) or not isinstance(candidate.get("problem_id"), int):
                continue
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
                    "title_relation": candidate.get("title_relation"),
                }
            )
        context_rows.append(
            {
                "schema": "opdp.private-proofatlas-top500-no-strong-context.v1",
                "warning": "LOCAL REVIEW CONTEXT ONLY: contains source text; do not commit or publish.",
                "proofatlas_problem_id": proof["problem_id"],
                "proofatlas_rank": proof["release_rank"],
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


def require_one_line(value: Any, field: str, maximum: int = 600) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ReviewError(f"{field} must be a nonempty one-line <= {maximum}-character string")
    if "\n" in value or "\r" in value:
        raise ReviewError(f"{field} must be one line")
    return value.strip()


def validate_and_write_review(
    *,
    index_path: Path,
    proofatlas_path: Path,
    decisions_path: Path,
    output: Path,
    rank_min: int,
    rank_max: int,
    atlas_path: Path | None = None,
) -> int:
    """Validate one compact human judgment per requested no-strong source ID."""

    expected_rows = no_strong_rows(index_path, rank_min, rank_max)
    expected = {row["proofatlas"]["problem_id"]: row for row in expected_rows}
    proofatlas = load_proofatlas(proofatlas_path)
    supplied = load_jsonl(decisions_path)
    if len(supplied) != len(expected):
        raise ReviewError(f"decision input must contain exactly {len(expected)} rows, found {len(supplied)}")

    # A candidate index is deliberately incomplete: it is a compact discovery
    # aid, and a reviewer may subsequently locate an exact frozen OPDP record
    # through a full local v1.7 read.  Permit that stronger direct review only
    # when its ID is verified against the supplied frozen corpus, and label it
    # separately in the public sidecar.
    all_atlas_ids: set[int] | None = None
    if atlas_path is not None:
        all_atlas_ids = {
            int(item["problem_id"])
            for item in CROSSWALK.iter_v17_records(atlas_path)
            if isinstance(item.get("problem_id"), int) and not isinstance(item.get("problem_id"), bool)
        }
        if not all_atlas_ids:
            raise ReviewError(f"no OPDP v1.7 records found in {atlas_path}")

    public_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, decision in enumerate(supplied, 1):
        problem_id = decision.get("proofatlas_problem_id")
        outcome = decision.get("review_outcome")
        worthy = decision.get("distinct_problem_worthy_of_append")
        evidence = require_one_line(decision.get("review_evidence"), f"decision row {line_number}: review_evidence")
        chosen = decision.get("atlas_problem_ids_reviewed")
        if not isinstance(problem_id, str) or problem_id not in expected:
            raise ReviewError(f"decision row {line_number}: unknown ProofAtlas no-strong problem ID")
        if problem_id in seen:
            raise ReviewError(f"decision row {line_number}: duplicate ProofAtlas problem ID")
        if outcome not in ALLOWED_OUTCOMES:
            raise ReviewError(f"decision row {line_number}: invalid review_outcome")
        if not isinstance(worthy, bool):
            raise ReviewError(
                f"decision row {line_number}: distinct_problem_worthy_of_append must be boolean"
            )
        if worthy and outcome not in APPEND_WORTHY_OUTCOMES:
            raise ReviewError(
                f"decision row {line_number}: only a separately scoped non-identical outcome may be append-worthy"
            )
        if outcome in {"same_problem_reviewed", "unresolved_due_to_incomplete_existing_text"} and worthy:
            raise ReviewError(f"decision row {line_number}: outcome cannot be append-worthy")
        if not isinstance(chosen, list) or not chosen or not all(
            isinstance(value, int) and not isinstance(value, bool) for value in chosen
        ):
            raise ReviewError(f"decision row {line_number}: atlas_problem_ids_reviewed must be nonempty int list")
        candidate_ids = {
            candidate["problem_id"]
            for candidate in expected[problem_id].get("candidates", [])
            if isinstance(candidate, dict) and isinstance(candidate.get("problem_id"), int)
        }
        direct_ids = set(chosen) - candidate_ids
        if direct_ids and (all_atlas_ids is None or not direct_ids <= all_atlas_ids):
            raise ReviewError(
                f"decision row {line_number}: reviewed ID is neither retained by the no-strong index "
                "nor verified in frozen OPDP v1.7"
            )
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
        proof = expected[problem_id]["proofatlas"]
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
                "review_outcome": outcome,
                "distinct_problem_worthy_of_append": worthy,
                "review_evidence": evidence,
                "source_status_note": status_note,
                "atlas_problem_ids_reviewed": chosen,
                "atlas_review_provenance": {
                    "retained_no_strong_candidate_ids": sorted(set(chosen) & candidate_ids),
                    "full_v17_direct_reviewed_ids": sorted(direct_ids),
                },
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
        raise ReviewError("decision input does not cover exactly the requested no-strong source IDs")
    public_rows.sort(key=lambda row: int(row["proofatlas"]["release_rank"]))
    CROSSWALK.atomic_write(
        output, b"".join(CROSSWALK.canonical_json_bytes(row) + b"\n" for row in public_rows)
    )
    return len(public_rows)


def validate_append_drafts(
    *, review_path: Path, drafts_path: Path, output: Path | None
) -> int:
    """Validate drafts for exactly the append-worthy decisions in a review sidecar."""

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
            raise ReviewError(f"append draft row {line_number}: not an append-worthy reviewed source ID")
        if problem_id in seen:
            raise ReviewError(f"append draft row {line_number}: duplicate ProofAtlas ID")
        reviewed_proof = worthy[problem_id]["proofatlas"]
        for key in ("release_rank", "formal_statement_source_url", "release_status_as_published"):
            if proof.get(key) != reviewed_proof.get(key):
                raise ReviewError(f"append draft row {line_number}: ProofAtlas {key} differs from review sidecar")
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
        raise ReviewError("append drafts do not cover exactly the append-worthy review rows")
    public_rows.sort(key=lambda row: int(row["proofatlas"]["release_rank"]))
    if output is not None:
        CROSSWALK.atomic_write(
            output, b"".join(CROSSWALK.canonical_json_bytes(row) + b"\n" for row in public_rows)
        )
    return len(public_rows)


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index",
        type=Path,
        default=Path("data/PROOFATLAS_TOP500_NO_STRONG_MATCH_CANDIDATE_INDEX_v1.jsonl"),
    )
    parser.add_argument("--proofatlas-json", type=Path)
    parser.add_argument(
        "--atlas-v17", type=Path, default=Path("data/Ulam_MathDB_OPDP_Assessments_v1.7.json.gz")
    )
    parser.add_argument("--rank-min", type=int, default=250)
    parser.add_argument("--rank-max", type=int, default=498)
    parser.add_argument("--emit-private-context", type=Path)
    parser.add_argument("--allow-private-context", action="store_true")
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--validate-append-drafts", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv or sys.argv[1:])
    try:
        modes = sum(
            value is not None
            for value in (arguments.emit_private_context, arguments.decisions, arguments.validate_append_drafts)
        )
        if modes != 1:
            raise ReviewError("select exactly one of --emit-private-context, --decisions, or --validate-append-drafts")
        if arguments.emit_private_context is not None:
            if not arguments.allow_private_context:
                raise ReviewError("--emit-private-context requires explicit --allow-private-context")
            if arguments.proofatlas_json is None:
                raise ReviewError("--emit-private-context requires --proofatlas-json")
            count = private_context(
                index_path=arguments.index,
                proofatlas_path=arguments.proofatlas_json,
                atlas_path=arguments.atlas_v17,
                output=arguments.emit_private_context,
                rank_min=arguments.rank_min,
                rank_max=arguments.rank_max,
            )
            print(f"wrote {count} local no-strong-review contexts")
        elif arguments.decisions is not None:
            if arguments.proofatlas_json is None or arguments.output is None:
                raise ReviewError("--decisions requires --proofatlas-json and --output")
            count = validate_and_write_review(
                index_path=arguments.index,
                proofatlas_path=arguments.proofatlas_json,
                decisions_path=arguments.decisions,
                output=arguments.output,
                rank_min=arguments.rank_min,
                rank_max=arguments.rank_max,
                atlas_path=arguments.atlas_v17,
            )
            print(f"wrote {count} public-safe no-strong review rows")
        else:
            if arguments.review is None:
                raise ReviewError("--validate-append-drafts requires --review")
            count = validate_append_drafts(
                review_path=arguments.review,
                drafts_path=arguments.validate_append_drafts,
                output=arguments.output,
            )
            print(f"validated {count} curator-authored append drafts")
    except ReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
