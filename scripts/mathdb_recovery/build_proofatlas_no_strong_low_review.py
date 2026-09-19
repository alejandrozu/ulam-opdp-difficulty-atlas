#!/usr/bin/env python3
"""Emit the manually reviewed low-rank ProofAtlas abstention tranche.

This is a deliberately content-minimized release artifact.  It records review
decisions for the ProofAtlas Top 500 rows that the conservative lexical
crosswalk labeled ``no_strong_match`` at ranks 3 through 249.  It never
serializes the ProofAtlas target text or an OPDP statement/excerpt, changes an
OPDP payload, verifies present-day openness, or assigns OPDP scores.

The per-row calls are intentionally explicit: a source title or a raw semantic
similarity score is not sufficient to call two mathematical targets identical.
``unresolved_due_to_incomplete_existing_text`` therefore remains the default
whenever the only plausible retained record is a MathDB public-list excerpt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


SCHEMA = "opdp.proofatlas-top500-no-strong-review.v1"
SUMMARY_SCHEMA = "opdp.proofatlas-top500-no-strong-review-summary.v1"
LOW_RANK_MAX = 249
DECISIONS = frozenset(
    {
        "same_problem_reviewed",
        "variant_or_subproblem",
        "related_not_same",
        "appendable_no_strong_match",
        "unresolved_due_to_incomplete_existing_text",
    }
)

# Entries below encode a human scope review over the frozen private source and
# private full-text workbench.  Values are current-OPDP problem IDs.  They are
# identifiers only; no source prose is reproduced in the public output.
SAME_PROBLEM: dict[int, int] = {
    3: 1346,
    4: 1515,
    8: 3403,
    22: 3364,
    26: 3100086,
    32: 30001375,
    34: 43,
    65: 9400087,
    81: 9400091,
    105: 3034,
    122: 3281,
    123: 9400068,
    135: 9400178,
    157: 3349,
    158: 30004922,
    178: 1357,
    184: 7800001,
    201: 7800004,
    222: 20003353,
    233: 30002380,
    240: 1278,
}

UNRESOLVED_EXCERPT: dict[int, int] = {
    7: 40343918,
    24: 40393813,
    47: 40403784,
    62: 40332994,
    66: 40368154,
    83: 40392264,
    87: 40398698,
    88: 40332879,
    90: 40374363,
    91: 40348860,
    96: 40400787,
    126: 40401958,
    129: 40404573,
    160: 40326462,
    176: 40337565,
    179: 40360861,
    183: 40381165,
    218: 40402392,
    235: 40366550,
    244: 40333905,
}

# ``True`` says the review found a materially different, standalone target;
# ``False`` says the target is only a special case of an already represented
# complete problem and should not be appended as a new problem.
VARIANT: dict[int, tuple[int, bool]] = {
    30: (7900001, True),
    38: (30005545, False),
    51: (3403, True),
    52: (40396634, True),
    59: (40396074, True),
    68: (40396083, True),
    72: (40399071, True),
    74: (40337322, True),
    86: (40377752, True),
    95: (40317664, True),
    97: (7200017, False),
    110: (40399736, False),
    112: (40332066, False),
    119: (40334225, False),
    131: (40358967, True),
    136: (30002994, True),
    139: (30000406, False),
    143: (40402295, True),
    170: (40332703, True),
    173: (40335687, True),
    209: (40339601, True),
    229: (40366935, True),
    247: (40396078, True),
}


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{line_number}: expected object")
                rows.append(row)
    return rows


def choose_candidate(
    index_row: dict[str, Any], problem_id: int, workbench: sqlite3.Connection
) -> dict[str, Any]:
    for candidate in index_row.get("candidates", []):
        if isinstance(candidate, dict) and candidate.get("problem_id") == problem_id:
            return {
                "problem_id": candidate["problem_id"],
                "problem_number": candidate.get("problem_number"),
                "title_sha256": candidate.get("title_sha256"),
                "source_text_mode": candidate.get("source_text_mode"),
                "source_statement_completeness": candidate.get("source_statement_completeness"),
                "retrieval_methods": candidate.get("retrieval_methods", []),
                "title_relation": candidate.get("title_relation"),
            }
    # The public candidate index is deliberately capped.  A reviewed lead can
    # therefore fall below its public retrieval limit; recover only its safe
    # metadata from the local ignored workbench, never its statement text.
    row = workbench.execute(
        "SELECT problem_number, title, text_mode, source_excerpt_only FROM records WHERE problem_id = ?",
        (str(problem_id),),
    ).fetchone()
    if row is None:
        raise ValueError(
            f"manual review references unknown OPDP problem_id {problem_id} "
            f"for ProofAtlas rank {index_row['proofatlas']['release_rank']}"
        )
    problem_number, title, text_mode, source_excerpt_only = row
    if not isinstance(title, str):
        raise ValueError(f"workbench problem_id {problem_id} has no title")
    return {
        "problem_id": problem_id,
        "problem_number": problem_number,
        "title_sha256": hashlib.sha256(title.encode("utf-8")).hexdigest(),
        "source_text_mode": text_mode,
        "source_statement_completeness": (
            "excerpt_not_guaranteed_complete" if bool(source_excerpt_only) else "frozen_record_statement"
        ),
        "retrieval_methods": ["manual_private_scope_review"],
        "title_relation": None,
    }


def status_review(status: str | None) -> dict[str, Any]:
    concern = "source_published_open_not_independently_verified"
    if status == "open_with_solved_subcases":
        concern = "source_published_open_with_solved_subcases_not_independently_verified"
    return {
        "proofatlas_release_status_as_published": status,
        "independently_verified_current_open_status": False,
        "status_concern": concern,
    }


def reviewed_row(
    index_row: dict[str, Any], crosswalk_row: dict[str, Any], workbench: sqlite3.Connection
) -> dict[str, Any]:
    proofatlas = index_row["proofatlas"]
    rank = proofatlas["release_rank"]
    if not isinstance(rank, int) or rank > LOW_RANK_MAX:
        raise ValueError("outside low-rank review scope")
    published_status = crosswalk_row["proofatlas"].get("release_status_as_published")
    candidate: dict[str, Any] | None = None
    if rank in SAME_PROBLEM:
        decision = "same_problem_reviewed"
        candidate = choose_candidate(index_row, SAME_PROBLEM[rank], workbench)
        distinct: bool | None = None
        evidence = (
            "Private review found the same named mathematical target in a complete frozen OPDP "
            "record; the original abstention was a lexical threshold effect."
        )
    elif rank in UNRESOLVED_EXCERPT:
        decision = "unresolved_due_to_incomplete_existing_text"
        candidate = choose_candidate(index_row, UNRESOLVED_EXCERPT[rank], workbench)
        distinct = None
        evidence = (
            "The closest scope-compatible candidate is retained only as a MathDB public-list excerpt, "
            "so the frozen current text cannot resolve identity versus a scope variant."
        )
    elif rank in VARIANT:
        reviewed_candidate, distinct = VARIANT[rank]
        decision = "variant_or_subproblem"
        candidate = choose_candidate(index_row, reviewed_candidate, workbench)
        if distinct:
            evidence = (
                "Private review found a related named formulation but a materially different standalone "
                "scope; it is eligible for guarded separate intake, not an identity merge."
            )
        else:
            evidence = (
                "Private review found the target to be a special case of an already represented complete "
                "formulation; it should not be appended as a separate problem."
            )
    else:
        decision = "appendable_no_strong_match"
        distinct = None
        # An initial weak candidate, where present, is retained only as an
        # explicitly nonidentity lead.  It does not become a source match.
        original = crosswalk_row.get("selected_candidates", [])
        related_nonidentity = None
        if isinstance(original, list) and original and isinstance(original[0], dict):
            possible = original[0].get("problem_id")
            if isinstance(possible, int):
                try:
                    related_nonidentity = choose_candidate(index_row, possible, workbench)
                except ValueError:
                    related_nonidentity = {"problem_id": possible, "not_retained_in_second_pass": True}
        candidate = related_nonidentity
        evidence = (
            "Private title, target-term, and full-v1.7 search found no scope-compatible identity; the "
            "source target was reviewed as self-contained and is a guarded append candidate."
        )
    if decision not in DECISIONS:
        raise AssertionError(decision)
    append_recommendation = (
        "guarded_append_candidate"
        if decision == "appendable_no_strong_match" or (decision == "variant_or_subproblem" and distinct)
        else "do_not_append_from_this_review"
        if decision == "same_problem_reviewed" or (decision == "variant_or_subproblem" and distinct is False)
        else "requires_source_scope_recovery"
    )
    return {
        "schema": SCHEMA,
        "proofatlas": {
            "problem_id": proofatlas["problem_id"],
            "release_rank": rank,
            "canonical_title": proofatlas["canonical_title"],
            "canonical_title_sha256": proofatlas["canonical_title_sha256"],
            "exact_target_sha256": proofatlas["exact_target_sha256"],
            "formal_statement_source_url": proofatlas.get("formal_statement_source_url"),
        },
        "review_decision": decision,
        "distinct_problem_worthy_of_append": distinct,
        "append_recommendation": append_recommendation,
        "target_self_contained_under_private_review": True,
        "review_evidence": evidence,
        "reviewed_opdp_candidate": candidate,
        "candidate_search": {
            "methods": index_row["candidate_search"]["methods"],
            "proofatlas_exact_target_not_retained": True,
            "opdp_statement_or_excerpt_not_retained": True,
        },
        "release_status_review": status_review(published_status if isinstance(published_status, str) else None),
        "non_destructive_contract": {
            "opdp_payload_modified": False,
            "opdp_records_appended": False,
            "opdp_dimensions_recalculated": False,
            "open_status_verified": False,
            "proofatlas_exact_target_retained": False,
            "opdp_statement_or_excerpt_retained": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-index", type=Path, required=True)
    parser.add_argument("--crosswalk", type=Path, required=True)
    parser.add_argument(
        "--workbench",
        type=Path,
        required=True,
        help="local ignored SQLite workbench used only to recover capped candidate metadata",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    arguments = parser.parse_args(argv)
    indexed = {
        row["proofatlas"]["problem_id"]: row
        for row in jsonl(arguments.candidate_index)
        if row.get("review_required") is True
        and isinstance(row.get("proofatlas"), dict)
        and isinstance(row["proofatlas"].get("release_rank"), int)
        and row["proofatlas"]["release_rank"] <= LOW_RANK_MAX
    }
    crosswalk = {
        row["proofatlas"]["problem_id"]: row
        for row in jsonl(arguments.crosswalk)
        if row.get("decision") == "no_strong_match"
        and isinstance(row.get("proofatlas"), dict)
        and isinstance(row["proofatlas"].get("release_rank"), int)
        and row["proofatlas"]["release_rank"] <= LOW_RANK_MAX
    }
    if set(indexed) != set(crosswalk):
        raise ValueError("candidate index and crosswalk low-rank source IDs differ")
    expected = set(SAME_PROBLEM) | set(UNRESOLVED_EXCERPT) | set(VARIANT)
    surplus = expected - {row["proofatlas"]["release_rank"] for row in indexed.values()}
    if surplus:
        raise ValueError(f"manual decision references absent ranks: {sorted(surplus)}")
    if not arguments.workbench.is_file():
        raise ValueError(f"private review workbench does not exist: {arguments.workbench}")
    workbench_uri = arguments.workbench.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(workbench_uri, uri=True) as workbench:
        rows = [
            reviewed_row(row, crosswalk[problem_id], workbench)
            for problem_id, row in sorted(indexed.items(), key=lambda pair: pair[1]["proofatlas"]["release_rank"])
        ]
    if len(rows) != 119:
        raise ValueError(f"expected 119 low-rank abstentions, found {len(rows)}")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(b"".join(canonical_json(row) + b"\n" for row in rows))
    decision_counts = Counter(row["review_decision"] for row in rows)
    appendable = [
        row
        for row in rows
        if row["append_recommendation"] == "guarded_append_candidate"
    ]
    summary = {
        "schema": SUMMARY_SCHEMA,
        "purpose": (
            "Second-pass semantic scope review of ProofAtlas Top 500 no-strong-match rows at "
            "release ranks 3-249. It is not an OPDP append, source-statement publication, current-status "
            "verification, or rescore."
        ),
        "source_inputs": {
            "candidate_index_path": str(arguments.candidate_index).replace("\\", "/"),
            "candidate_index_sha256": sha256_file(arguments.candidate_index),
            "crosswalk_path": str(arguments.crosswalk).replace("\\", "/"),
            "crosswalk_sha256": sha256_file(arguments.crosswalk),
        },
        "scope": {"rank_min": min(row["proofatlas"]["release_rank"] for row in rows), "rank_max": LOW_RANK_MAX, "record_count": len(rows)},
        "results": {
            "decisions": dict(sorted(decision_counts.items())),
            "guarded_append_candidates": len(appendable),
            "source_published_status_unverified": len(rows),
        },
        "non_destructive_contract": rows[0]["non_destructive_contract"],
        "output": {
            "path": str(arguments.output).replace("\\", "/"),
            "sha256": sha256_file(arguments.output),
            "record_count": len(rows),
        },
    }
    arguments.summary.parent.mkdir(parents=True, exist_ok=True)
    arguments.summary.write_bytes(canonical_json(summary) + b"\n")
    print(json.dumps(summary["results"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
