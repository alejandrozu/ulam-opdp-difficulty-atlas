#!/usr/bin/env python3
"""Create/validate a compact human review sidecar for ambiguous ProofAtlas links.

This companion deliberately separates private reading material from a public
decision record.  ``--emit-private-context`` requires an explicit flag and
writes complete source-target/candidate text only to a caller-selected local
path (normally an ignored ``runs/`` location).  The public review sidecar
contains only identifiers, titles, hashes, decision labels, and short
paraphrased evidence; it never copies a ProofAtlas target or OPDP statement.
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


TOOL_NAME = "OPDP ProofAtlas Top 500 ambiguous-link reviewer"
TOOL_VERSION = "1.0.0"
REVIEW_SCHEMA = "opdp.proofatlas-top500-ambiguous-review.v1"
ALLOWED_OUTCOMES = frozenset(
    {"same_problem_reviewed", "variant_or_subproblem", "related_not_same", "unresolved"}
)


class ReviewError(RuntimeError):
    pass


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


def ambiguous_rows(path: Path) -> list[dict[str, Any]]:
    rows = load_jsonl(path)
    result = [row for row in rows if row.get("decision") == "ambiguous"]
    if not result:
        raise ReviewError(f"{path}: no ambiguous crosswalk rows found")
    return result


def private_context(
    *, crosswalk_path: Path, proofatlas_path: Path, atlas_path: Path, output: Path
) -> int:
    """Write locally scoped full-text reading context; never a release artifact."""

    rows = ambiguous_rows(crosswalk_path)
    try:
        raw = json.loads(proofatlas_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewError(f"cannot parse ProofAtlas snapshot {proofatlas_path}: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("records"), list):
        raise ReviewError("ProofAtlas snapshot does not contain records")
    proofatlas = {
        entry.get("problemId"): entry
        for entry in raw["records"]
        if isinstance(entry, dict) and isinstance(entry.get("problemId"), str)
    }
    candidate_ids = {
        candidate["problem_id"]
        for row in rows
        for candidate in row.get("selected_candidates", [])
        if isinstance(candidate, dict) and isinstance(candidate.get("problem_id"), int)
    }
    atlas: dict[int, dict[str, Any]] = {}
    for item in CROSSWALK.iter_v17_records(atlas_path):
        problem_id = item.get("problem_id")
        if problem_id in candidate_ids:
            atlas[problem_id] = item
    if candidate_ids - set(atlas):
        raise ReviewError("one or more selected candidate IDs were not found in frozen OPDP v1.7")

    # This file intentionally contains source text and must stay under an
    # ignored local run directory.  It has a conspicuous schema so it cannot
    # be mistaken for a release artifact.
    context_rows: list[dict[str, Any]] = []
    for row in rows:
        proof = row["proofatlas"]
        source = proofatlas.get(proof["problem_id"])
        if not isinstance(source, dict):
            raise ReviewError(f"ProofAtlas problem missing from raw snapshot: {proof['problem_id']}")
        candidates = []
        for candidate in row["selected_candidates"]:
            record = atlas[candidate["problem_id"]]
            source_text = record.get("source_text")
            candidates.append(
                {
                    "problem_id": candidate["problem_id"],
                    "problem_number": candidate["problem_number"],
                    "title": record.get("title"),
                    "statement": source_text.get("statement") if isinstance(source_text, dict) else None,
                    "text_mode": source_text.get("text_mode") if isinstance(source_text, dict) else None,
                    "comparison": candidate.get("comparison"),
                }
            )
        context_rows.append(
            {
                "schema": "opdp.private-proofatlas-top500-ambiguous-context.v1",
                "warning": "LOCAL REVIEW CONTEXT ONLY: contains source text; do not commit or publish.",
                "proofatlas_problem_id": proof["problem_id"],
                "proofatlas_rank": proof["release_rank"],
                "proofatlas_title": source.get("canonicalTitle"),
                "proofatlas_exact_target": source.get("exactTarget"),
                "candidates": candidates,
            }
        )
    CROSSWALK.atomic_write(
        output, b"".join(CROSSWALK.canonical_json_bytes(row) + b"\n" for row in context_rows)
    )
    return len(context_rows)


def validate_and_write_review(
    *, crosswalk_path: Path, decisions_path: Path, output: Path
) -> int:
    """Validate compact human decisions against ambiguous crosswalk IDs only."""

    rows = ambiguous_rows(crosswalk_path)
    expected = {row["proofatlas"]["problem_id"]: row for row in rows}
    supplied = load_jsonl(decisions_path)
    if len(supplied) != len(expected):
        raise ReviewError(
            f"decision input must contain exactly {len(expected)} rows, found {len(supplied)}"
        )
    public_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, decision in enumerate(supplied, 1):
        problem_id = decision.get("proofatlas_problem_id")
        outcome = decision.get("review_outcome")
        evidence = decision.get("review_evidence")
        chosen = decision.get("atlas_problem_ids_reviewed")
        worthy = decision.get("distinct_problem_worthy_of_append")
        if not isinstance(problem_id, str) or problem_id not in expected:
            raise ReviewError(f"decision row {line_number}: unknown ProofAtlas ambiguous problem ID")
        if problem_id in seen:
            raise ReviewError(f"decision row {line_number}: duplicate ProofAtlas problem ID")
        if outcome not in ALLOWED_OUTCOMES:
            raise ReviewError(f"decision row {line_number}: invalid review_outcome")
        if not isinstance(worthy, bool):
            raise ReviewError(
                f"decision row {line_number}: distinct_problem_worthy_of_append must be boolean"
            )
        if worthy and outcome not in {"variant_or_subproblem", "related_not_same"}:
            raise ReviewError(
                f"decision row {line_number}: only a variant_or_subproblem or related_not_same "
                "decision may be append-worthy"
            )
        if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 600:
            raise ReviewError(f"decision row {line_number}: review_evidence must be a 1-line <=600-char string")
        if "\n" in evidence or "\r" in evidence:
            raise ReviewError(f"decision row {line_number}: review_evidence must be one line")
        if not isinstance(chosen, list) or not chosen or not all(
            isinstance(value, int) and not isinstance(value, bool) for value in chosen
        ):
            raise ReviewError(f"decision row {line_number}: atlas_problem_ids_reviewed must be nonempty int list")
        candidate_ids = {
            candidate["problem_id"] for candidate in expected[problem_id]["selected_candidates"]
        }
        if not set(chosen) <= candidate_ids:
            raise ReviewError(f"decision row {line_number}: reviewed ID not in selected ambiguous candidates")
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
                },
                "review_outcome": outcome,
                "distinct_problem_worthy_of_append": worthy,
                "review_evidence": evidence,
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
        raise ReviewError("decision input does not cover exactly the ambiguous crosswalk IDs")
    public_rows.sort(key=lambda row: row["proofatlas"]["release_rank"])
    CROSSWALK.atomic_write(
        output, b"".join(CROSSWALK.canonical_json_bytes(row) + b"\n" for row in public_rows)
    )
    return len(public_rows)


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--crosswalk",
        type=Path,
        default=Path("data/PROOFATLAS_TOP500_CROSSWALK_v1.jsonl"),
    )
    parser.add_argument("--proofatlas-json", type=Path)
    parser.add_argument(
        "--atlas-v17", type=Path, default=Path("data/Ulam_MathDB_OPDP_Assessments_v1.7.json.gz")
    )
    parser.add_argument("--emit-private-context", type=Path)
    parser.add_argument("--allow-private-context", action="store_true")
    parser.add_argument("--decisions", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/PROOFATLAS_TOP500_AMBIGUOUS_REVIEW_v1.jsonl"),
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    try:
        doing_context = arguments.emit_private_context is not None
        doing_review = arguments.decisions is not None
        if doing_context == doing_review:
            raise ReviewError("select exactly one of --emit-private-context or --decisions")
        if doing_context:
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
            print(f"wrote {count} private ambiguous-review contexts")
        else:
            count = validate_and_write_review(
                crosswalk_path=arguments.crosswalk,
                decisions_path=arguments.decisions,
                output=arguments.output,
            )
            print(f"wrote {count} public-safe ambiguous review rows")
    except ReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
