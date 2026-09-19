#!/usr/bin/env python3
"""Review one fixed partition of the ProofAtlas collaboration workbench safely.

The collaboration workbench deliberately omits source prose.  This companion
has two mutually exclusive modes:

* ``--emit-private-context --allow-private-context`` reconstructs local review
  material from the ignored frozen collaboration HTML plus frozen OPDP v1.7.
  It may contain statements and must never be committed or published.
* ``--decisions`` validates a human-curated, public-safe decision set and
  emits a compact sidecar containing identifiers, hashes, locators, source
  status, outcomes, candidate IDs, and one-line paraphrased evidence only.

Neither mode mutates the OPDP payload, treats source status as independently
verified, or authorizes an append.  A missing/weak match is especially not
novelty evidence when a frozen MathDB record has only an excerpt.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
TOP500_CROSSWALK_PATH = SCRIPT_DIR / "build_proofatlas_top500_crosswalk.py"
COLLAB_CROSSWALK_PATH = SCRIPT_DIR / "build_proofatlas_collaboration_crosswalk.py"


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CROSSWALK = load_module("proofatlas_top500_crosswalk_for_partition_review", TOP500_CROSSWALK_PATH)
COLLAB = load_module("proofatlas_collaboration_crosswalk_for_partition_review", COLLAB_CROSSWALK_PATH)


WORKBENCH_SCHEMA = "opdp.proofatlas-collaboration-review-workbench.v1"
CROSSWALK_SCHEMA = "opdp.proofatlas-collaboration-crosswalk.v1"
REVIEW_SCHEMA = "opdp.proofatlas-collaboration-semantic-review.v1"
APPEND_DRAFT_SCHEMA = "opdp.proofatlas.collaboration-append-curation-draft.v1"
NORMALIZATION_MODE = "curator_authored_independent_normalization"
ALLOWED_OUTCOMES = frozenset(
    {
        "same_problem_reviewed",
        "variant_or_subproblem",
        "related_not_same",
        "appendable_no_strong_match",
        "unresolved_due_to_incomplete_existing_text",
    }
)
APPENDABLE_OUTCOMES = frozenset(
    {"variant_or_subproblem", "related_not_same", "appendable_no_strong_match"}
)


class ReviewError(RuntimeError):
    pass


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ReviewError(f"JSONL input does not exist: {path}")
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    raise ReviewError(f"{path}:{line_number}: blank JSONL line")
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ReviewError(f"{path}:{line_number}: invalid JSON") from exc
                if not isinstance(row, dict):
                    raise ReviewError(f"{path}:{line_number}: expected object")
                rows.append(row)
    except OSError as exc:
        raise ReviewError(f"cannot read {path}: {exc}") from exc
    return rows


def partition_rows(workbench_path: Path, start_index: int, end_index: int) -> list[dict[str, Any]]:
    if start_index < 1 or end_index < start_index:
        raise ReviewError("partition indexes must be positive and ordered")
    rows = load_jsonl(workbench_path)
    if any(row.get("schema") != WORKBENCH_SCHEMA for row in rows):
        raise ReviewError(f"{workbench_path}: unexpected workbench schema")
    if end_index > len(rows):
        raise ReviewError(f"{workbench_path}: end index {end_index} exceeds {len(rows)} rows")
    selected = rows[start_index - 1 : end_index]
    keys = [row.get("review_key") for row in selected]
    if not all(isinstance(key, str) and key for key in keys) or len(set(keys)) != len(keys):
        raise ReviewError("partition lacks unique nonempty review keys")
    return selected


def crosswalk_metadata(path: Path) -> dict[str, dict[str, Any]]:
    rows = load_jsonl(path)
    result: dict[str, dict[str, Any]] = {}
    for line_number, row in enumerate(rows, 1):
        if row.get("schema") != CROSSWALK_SCHEMA:
            raise ReviewError(f"{path}:{line_number}: unexpected collaboration crosswalk schema")
        workspace = row.get("workspace")
        if not isinstance(workspace, dict):
            raise ReviewError(f"{path}:{line_number}: malformed workspace")
        slug = workspace.get("slug")
        route = workspace.get("source_route")
        status = workspace.get("source_status_as_published")
        if not all(isinstance(value, str) and value for value in (slug, route, status)):
            raise ReviewError(f"{path}:{line_number}: incomplete workspace public metadata")
        if slug in result:
            raise ReviewError(f"{path}:{line_number}: duplicate workspace slug")
        result[slug] = {
            "slug": slug,
            "source_route": route,
            "source_status_as_published": status,
            "title_sha256": workspace.get("title_sha256"),
            "statement_sha256": workspace.get("statement_sha256"),
        }
    if len(result) != 268:
        raise ReviewError(f"{path}: expected 268 distinct workspace rows, found {len(result)}")
    return result


def private_context(
    *,
    workbench_path: Path,
    collaboration_snapshot: Path,
    atlas_path: Path,
    start_index: int,
    end_index: int,
    output: Path,
) -> int:
    """Emit local-only full-text context for a fixed partition."""

    rows = partition_rows(workbench_path, start_index, end_index)
    workspaces, _metadata = COLLAB.load_workspaces(collaboration_snapshot)
    workspace_by_slug = {workspace.slug: workspace for workspace in workspaces}
    candidate_ids = {
        candidate["problem_id"]
        for row in rows
        for candidate in row.get("opdp_candidates", [])
        if isinstance(candidate, dict) and isinstance(candidate.get("problem_id"), int)
    }
    atlas: dict[int, dict[str, Any]] = {}
    for record in CROSSWALK.iter_v17_records(atlas_path):
        problem_id = record.get("problem_id")
        if problem_id in candidate_ids:
            atlas[problem_id] = record
    missing = candidate_ids - set(atlas)
    if missing:
        raise ReviewError(f"frozen OPDP v1.7 did not contain candidate IDs: {sorted(missing)[:5]}")

    context_rows: list[dict[str, Any]] = []
    for offset, row in enumerate(rows, start=start_index):
        workspace_info = row.get("workspace")
        if not isinstance(workspace_info, dict) or not isinstance(workspace_info.get("slug"), str):
            raise ReviewError(f"workbench row {offset}: malformed workspace")
        slug = workspace_info["slug"]
        workspace = workspace_by_slug.get(slug)
        if workspace is None:
            raise ReviewError(f"workbench row {offset}: source workspace missing from frozen HTML")
        candidates: list[dict[str, Any]] = []
        for candidate in row.get("opdp_candidates", []):
            if not isinstance(candidate, dict):
                raise ReviewError(f"workbench row {offset}: malformed candidate")
            problem_id = candidate.get("problem_id")
            if not isinstance(problem_id, int):
                raise ReviewError(f"workbench row {offset}: candidate ID missing")
            source = atlas[problem_id].get("source_text")
            candidates.append(
                {
                    "problem_id": problem_id,
                    "problem_number": candidate.get("problem_number"),
                    "title": atlas[problem_id].get("title"),
                    "statement": source.get("statement") if isinstance(source, dict) else None,
                    "text_mode": source.get("text_mode") if isinstance(source, dict) else None,
                    "source_statement_completeness": candidate.get("comparison", {})
                    .get("statement", {})
                    .get("source_text_complete_for_identity_evidence"),
                    "comparison": candidate.get("comparison"),
                }
            )
        context_rows.append(
            {
                "schema": "opdp.private-proofatlas-collaboration-partition-context.v1",
                "warning": "LOCAL REVIEW CONTEXT ONLY: contains source text; do not commit or publish.",
                "workbench_position": offset,
                "review_key": row.get("review_key"),
                "workspace": {
                    "slug": workspace.slug,
                    "route": workspace.route,
                    "source_status": workspace.source_status,
                    "title": workspace.title,
                    "statement": workspace.statement,
                },
                "top500_source_declared_links": row.get("top500_source_declared_links"),
                "opdp_candidates": candidates,
            }
        )
    CROSSWALK.atomic_write(
        output, b"".join(CROSSWALK.canonical_json_bytes(row) + b"\n" for row in context_rows)
    )
    return len(context_rows)


def ensure_one_line_evidence(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 600:
        raise ReviewError(f"{context}: review_evidence must be a nonempty <=600-character string")
    if "\n" in value or "\r" in value:
        raise ReviewError(f"{context}: review_evidence must be one line")
    return value


def validate_and_write(
    *,
    workbench_path: Path,
    crosswalk_path: Path,
    decisions_path: Path,
    start_index: int,
    end_index: int,
    output: Path,
) -> int:
    rows = partition_rows(workbench_path, start_index, end_index)
    expected = {row["review_key"]: row for row in rows}
    metadata = crosswalk_metadata(crosswalk_path)
    supplied = load_jsonl(decisions_path)
    if len(supplied) != len(expected):
        raise ReviewError(f"decision input must contain exactly {len(expected)} rows, found {len(supplied)}")
    public_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, decision in enumerate(supplied, 1):
        context = f"decision row {line_number}"
        review_key = decision.get("review_key")
        outcome = decision.get("review_outcome")
        append_worthy = decision.get("distinct_problem_worthy_of_append")
        reviewed_atlas = decision.get("reviewed_atlas_problem_ids")
        reviewed_top500 = decision.get("reviewed_top500_problem_ids")
        evidence = ensure_one_line_evidence(decision.get("review_evidence"), context)
        if not isinstance(review_key, str) or review_key not in expected:
            raise ReviewError(f"{context}: unknown review_key")
        if review_key in seen:
            raise ReviewError(f"{context}: duplicate review_key")
        if outcome not in ALLOWED_OUTCOMES:
            raise ReviewError(f"{context}: invalid review_outcome")
        if not isinstance(append_worthy, bool):
            raise ReviewError(f"{context}: distinct_problem_worthy_of_append must be boolean")
        workbench_row = expected[review_key]
        workspace = workbench_row.get("workspace")
        if not isinstance(workspace, dict) or not isinstance(workspace.get("slug"), str):
            raise ReviewError(f"{context}: malformed expected workspace")
        slug = workspace["slug"]
        public_workspace = metadata.get(slug)
        if public_workspace is None:
            raise ReviewError(f"{context}: workspace not present in public collaboration crosswalk")
        candidate_ids = {
            candidate.get("problem_id")
            for candidate in workbench_row.get("opdp_candidates", [])
            if isinstance(candidate, dict) and isinstance(candidate.get("problem_id"), int)
        }
        direct_ids = {
            link.get("top500_problem_id")
            for link in workbench_row.get("top500_source_declared_links", [])
            if isinstance(link, dict) and isinstance(link.get("top500_problem_id"), str)
        }
        if not isinstance(reviewed_atlas, list) or not all(
            isinstance(value, int) and not isinstance(value, bool) for value in reviewed_atlas
        ):
            raise ReviewError(f"{context}: reviewed_atlas_problem_ids must be an integer array")
        if len(set(reviewed_atlas)) != len(reviewed_atlas) or not set(reviewed_atlas) <= candidate_ids:
            raise ReviewError(f"{context}: reviewed atlas IDs must be unique selected candidates")
        if not isinstance(reviewed_top500, list) or not all(isinstance(value, str) for value in reviewed_top500):
            raise ReviewError(f"{context}: reviewed_top500_problem_ids must be a string array")
        if len(set(reviewed_top500)) != len(reviewed_top500) or not set(reviewed_top500) <= direct_ids:
            raise ReviewError(f"{context}: reviewed Top 500 IDs must be unique declared links")
        if not reviewed_atlas and not reviewed_top500 and candidate_ids:
            raise ReviewError(f"{context}: must review at least one offered OPDP candidate")
        if outcome == "same_problem_reviewed" and not reviewed_atlas and not reviewed_top500:
            raise ReviewError(f"{context}: same_problem_reviewed requires a reviewed source candidate")
        if outcome == "unresolved_due_to_incomplete_existing_text":
            candidate_by_id = {
                candidate.get("problem_id"): candidate
                for candidate in workbench_row.get("opdp_candidates", [])
                if isinstance(candidate, dict)
            }
            if not any(
                isinstance(candidate_by_id.get(problem_id), dict)
                and candidate_by_id[problem_id]
                .get("comparison", {})
                .get("statement", {})
                .get("source_text_complete_for_identity_evidence")
                is False
                for problem_id in reviewed_atlas
            ):
                raise ReviewError(
                    f"{context}: incomplete-text outcome requires a reviewed excerpt-only OPDP candidate"
                )
        if append_worthy:
            if public_workspace["source_status_as_published"] != "open":
                raise ReviewError(f"{context}: only source-published open entries may be append-worthy")
            if outcome not in APPENDABLE_OUTCOMES:
                raise ReviewError(f"{context}: outcome is not eligible for provisional append-worthiness")
        public_rows.append(
            {
                "schema": REVIEW_SCHEMA,
                "review_partition": {
                    "workbench_start_index": start_index,
                    "workbench_end_index": end_index,
                },
                "review_key": review_key,
                "workspace": {
                    "slug": public_workspace["slug"],
                    "source_route": public_workspace["source_route"],
                    "source_status_as_published": public_workspace["source_status_as_published"],
                    "title_sha256": public_workspace["title_sha256"],
                    "statement_sha256": public_workspace["statement_sha256"],
                },
                "review_outcome": outcome,
                "distinct_problem_worthy_of_append": append_worthy,
                "reviewed_atlas_problem_ids": reviewed_atlas,
                "reviewed_top500_problem_ids": reviewed_top500,
                "review_evidence": evidence,
                "non_destructive_contract": {
                    "collaboration_statement_or_snippet_retained": False,
                    "top500_exact_target_retained": False,
                    "opdp_statement_or_excerpt_retained": False,
                    "opdp_payload_modified": False,
                    "opdp_records_appended": False,
                    "open_status_verified": False,
                    "opdp_dimensions_recalculated": False,
                },
            }
        )
        seen.add(review_key)
    if seen != set(expected):
        raise ReviewError("decision input does not cover exactly the selected workbench partition")
    public_rows.sort(key=lambda row: row["review_key"])
    CROSSWALK.atomic_write(
        output, b"".join(CROSSWALK.canonical_json_bytes(row) + b"\n" for row in public_rows)
    )
    return len(public_rows)


def validate_append_drafts(*, review_path: Path, drafts_path: Path) -> int:
    """Check that a normalization draft covers exactly reviewed open candidates.

    This deliberately validates provenance and linkage only.  It does not turn
    a draft into an append authorization, verify source status, or infer a
    namespace number for OPDP.
    """

    reviews = load_jsonl(review_path)
    worthy: dict[str, dict[str, Any]] = {}
    for line_number, review in enumerate(reviews, 1):
        if review.get("schema") != REVIEW_SCHEMA:
            raise ReviewError(f"{review_path}:{line_number}: unexpected review schema")
        if review.get("distinct_problem_worthy_of_append") is not True:
            continue
        review_key = review.get("review_key")
        workspace = review.get("workspace")
        if not isinstance(review_key, str) or not isinstance(workspace, dict):
            raise ReviewError(f"{review_path}:{line_number}: malformed append-worthy review")
        if workspace.get("source_status_as_published") != "open":
            raise ReviewError(f"{review_path}:{line_number}: append-worthy review is not source-published open")
        worthy[review_key] = review
    drafts = load_jsonl(drafts_path)
    if len(drafts) != len(worthy):
        raise ReviewError(f"append draft input must contain exactly {len(worthy)} rows, found {len(drafts)}")
    seen: set[str] = set()
    for line_number, draft in enumerate(drafts, 1):
        context = f"append draft row {line_number}"
        if draft.get("schema") != APPEND_DRAFT_SCHEMA:
            raise ReviewError(f"{context}: unexpected append-draft schema")
        if draft.get("draft_status") != "requires_independent_curator_approval":
            raise ReviewError(f"{context}: draft must remain unapproved")
        review_key = draft.get("review_key")
        if not isinstance(review_key, str) or review_key not in worthy or review_key in seen:
            raise ReviewError(f"{context}: unknown or duplicate append-worthy review_key")
        review = worthy[review_key]
        workspace = review["workspace"]
        source = draft.get("source")
        if not isinstance(source, dict):
            raise ReviewError(f"{context}: missing source object")
        for draft_key, review_key_name in (
            ("source_id", "slug"),
            ("source_route", "source_route"),
            ("source_status_as_published", "source_status_as_published"),
        ):
            if source.get(draft_key) != workspace.get(review_key_name):
                raise ReviewError(f"{context}: source {draft_key} does not match review sidecar")
        if source.get("source_status_as_published") != "open":
            raise ReviewError(f"{context}: draft source must be source-published open")
        crosswalk = draft.get("crosswalk")
        if not isinstance(crosswalk, dict) or crosswalk.get("review_outcome") != review.get("review_outcome"):
            raise ReviewError(f"{context}: draft outcome does not match review sidecar")
        if crosswalk.get("distinct_problem_worthy_of_append") is not True:
            raise ReviewError(f"{context}: draft must preserve provisional append-worthiness")
        provenance = draft.get("statement_provenance")
        if not isinstance(provenance, dict) or provenance.get("mode") != NORMALIZATION_MODE:
            raise ReviewError(f"{context}: requires independent-normalization provenance")
        ensure_one_line_evidence(provenance.get("attestation"), f"{context}: provenance attestation")
        statement = ensure_one_line_evidence(draft.get("proposed_statement"), f"{context}: proposed statement")
        if "." not in statement and "?" not in statement:
            raise ReviewError(f"{context}: proposed statement must be a complete sentence")
        verification = draft.get("status_verification")
        if not isinstance(verification, dict) or verification.get("independently_verified") is not False:
            raise ReviewError(f"{context}: must not claim independently verified status")
        seen.add(review_key)
    if seen != set(worthy):
        raise ReviewError("append drafts do not cover exactly the append-worthy review rows")
    return len(drafts)


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workbench", type=Path, default=Path("data/PROOFATLAS_COLLABORATION_REVIEW_WORKBENCH_v1.jsonl")
    )
    parser.add_argument(
        "--crosswalk", type=Path, default=Path("data/PROOFATLAS_COLLABORATION_CROSSWALK_v1.jsonl")
    )
    parser.add_argument(
        "--collaboration-snapshot",
        type=Path,
        default=Path("scripts/mathdb_recovery/raw/proofatlas_collaboration-2026-09-19.html"),
    )
    parser.add_argument("--atlas-v17", type=Path, default=Path("data/Ulam_MathDB_OPDP_Assessments_v1.7.json.gz"))
    parser.add_argument("--start-index", type=int, required=True)
    parser.add_argument("--end-index", type=int, required=True)
    parser.add_argument("--emit-private-context", type=Path)
    parser.add_argument("--allow-private-context", action="store_true")
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--validate-append-drafts", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    try:
        doing_private = arguments.emit_private_context is not None
        doing_review = arguments.decisions is not None
        doing_draft_validation = arguments.validate_append_drafts is not None
        if sum((doing_private, doing_review, doing_draft_validation)) != 1:
            raise ReviewError("select exactly one of --emit-private-context, --decisions, or --validate-append-drafts")
        if doing_private:
            if not arguments.allow_private_context:
                raise ReviewError("--emit-private-context requires explicit --allow-private-context")
            count = private_context(
                workbench_path=arguments.workbench,
                collaboration_snapshot=arguments.collaboration_snapshot,
                atlas_path=arguments.atlas_v17,
                start_index=arguments.start_index,
                end_index=arguments.end_index,
                output=arguments.emit_private_context,
            )
            print(f"wrote {count} local-only collaboration review contexts")
        elif doing_review:
            if arguments.output is None:
                raise ReviewError("--output is required with --decisions")
            count = validate_and_write(
                workbench_path=arguments.workbench,
                crosswalk_path=arguments.crosswalk,
                decisions_path=arguments.decisions,
                start_index=arguments.start_index,
                end_index=arguments.end_index,
                output=arguments.output,
            )
            print(f"wrote {count} public-safe collaboration semantic review rows")
        else:
            if arguments.review is None:
                raise ReviewError("--review is required with --validate-append-drafts")
            count = validate_append_drafts(
                review_path=arguments.review,
                drafts_path=arguments.validate_append_drafts,
            )
            print(f"validated {count} collaboration append-normalization drafts")
    except ReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
