#!/usr/bin/env python3
"""Emit a public-safe semantic review for workbench positions 43--84.

This is a deliberately non-destructive review artifact.  Its decision map was
made against locally frozen Collaboration source context and the full frozen
OPDP v1.7 corpus.  The public result retains no workspace or atlas prose.  In
particular, ``reviewed_atlas_problem_ids`` may include full-corpus records that
were not in the workbench's original lexical shortlist; that makes the review
more accurate than the shortlist-only v1 validator for exact duplicates.

It does not append records, verify current open status, or recalculate any
OPDP dimensions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


WORKBENCH_SCHEMA = "opdp.proofatlas-collaboration-review-workbench.v1"
CROSSWALK_SCHEMA = "opdp.proofatlas-collaboration-crosswalk.v1"
OUTPUT_SCHEMA = "opdp.proofatlas-collaboration-semantic-review.v2"
SUMMARY_SCHEMA = "opdp.proofatlas-collaboration-semantic-review-summary.v2"
START_INDEX = 43
END_INDEX = 84

ALLOWED_OUTCOMES = {
    "same_problem_reviewed",
    "variant_or_subproblem",
    "related_not_same",
    "appendable_no_strong_match",
    "unresolved_due_to_incomplete_existing_text",
}

# Direct Top-500 links have public release ranks.  Non-direct collaboration
# routes intentionally have no inferred Top-500 rank.
TOP500_RELEASE_RANKS = {
    "problem.the-generalized-sato-tate-conjecture-for-higher-dimensional-abelian-varieties": 147,
    "problem.grothendieck-period-conjecture": 48,
    "problem.hadwiger-boltyanski-illumination-conjecture": 454,
    "problem.hartshorne-conjecture-on-complete-intersections-in-codimension-two": 297,
    "problem.weinstein-conjecture-on-periodic-orbits-of-reeb-flows": 164,
}

# Evidence is curator-authored, one-line, nonquoted rationale.  It deliberately
# avoids exporting either source statements or frozen OPDP text.
DECISIONS: dict[str, dict[str, Any]] = {
    "finite-lattice-representation-problem": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [1453, 40315863],
        "evidence": "The source and frozen records identify the standard finite-algebra congruence-lattice realization question.",
    },
    "finitistic-dimension-conjecture": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [40393228],
        "evidence": "The source is the standard uniform finite-projective-dimension question for Artin algebras represented by the named frozen record.",
    },
    "five-dimensional-kissing-number": {
        "outcome": "variant_or_subproblem",
        "append": True,
        "atlas": [1369, 40396558],
        "evidence": "The source fixes one unresolved ambient dimension, whereas the reviewed corpus records pose the broader dimension-parametrized packing question.",
    },
    "fortunes-conjecture": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [9400124, 40389395],
        "evidence": "The same named prime-offset construction and primality assertion already occur in frozen atlas records.",
    },
    "gallai-path-decomposition-conjecture": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [40398771, 40370973],
        "evidence": "The source is the ordinary connected-graph path-decomposition bound, not the separate planar specialization also offered in the shortlist.",
    },
    "generalized-sato-tate-cubic-gl2-type-abelian-threefolds": {
        "outcome": "variant_or_subproblem",
        "append": True,
        "atlas": [40333859, 40332878, 40328676],
        "evidence": "The source isolates a cubic GL2-type threefold case of a broader abelian-variety equidistribution program.",
    },
    "generalized-sato-tate-generic-picard-rank-18-k3": {
        "outcome": "variant_or_subproblem",
        "append": True,
        "atlas": [40349843, 9400084],
        "evidence": "The source specifies a Picard-rank and endomorphism regime within wider K3 and motivic equidistribution formulations.",
    },
    "generalized-star-height-problem": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [40316039, 1459, 1221],
        "evidence": "The same bounded generalized-star-height question for regular languages is already represented by multiple frozen records.",
    },
    "gilbert-pollak-conjecture": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [40316017, 40382255],
        "evidence": "The source and reviewed records concern the classical planar Steiner-ratio extremal assertion.",
    },
    "goldfelds-conjecture": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [40319160, 40403257, 40366192],
        "evidence": "Full-corpus review found records for the fixed-elliptic-curve quadratic-twist rank distribution, despite the workbench shortlist selecting a broader family formulation.",
    },
    "graceful-tree-conjecture": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [31, 3212, 40315999],
        "evidence": "The source is the ordinary graceful labeling assertion for all finite trees already present in the frozen corpus.",
    },
    "grothendieck-period-conjecture": {
        "outcome": "variant_or_subproblem",
        "append": True,
        "atlas": [40401724, 40359733, 40381575],
        "evidence": "The workspace uses a full algebraic-relations formulation, while its declared Top-500 linkage and corpus records retain narrower motivic formulations.",
    },
    "hadamard-maximal-determinant-order-23": {
        "outcome": "variant_or_subproblem",
        "append": True,
        "atlas": [1600016, 40315871],
        "evidence": "The source asks for a fixed unresolved matrix order, whereas the reviewed records formulate the maximum-determinant problem uniformly over orders.",
    },
    "hadwiger-boltyanski-illumination-conjecture": {
        "outcome": "variant_or_subproblem",
        "append": True,
        "atlas": [40343921, 40365332, 40369236],
        "evidence": "The workspace adds an equality-characterization component to the declared Top-500 illumination bound.",
    },
    "halls-random-triangle-conjecture": {
        "outcome": "variant_or_subproblem",
        "append": True,
        "atlas": [40335846],
        "evidence": "The source studies one acute-triangle event, while the reviewed record is a stronger distributional triangle-shape formulation.",
    },
    "halperin-carlsson-toral-rank-conjecture": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [40397002, 40369560],
        "evidence": "The source is the standard almost-free torus-action cohomology lower-bound conjecture already represented in the frozen corpus.",
    },
    "harary-hill-conjecture": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [40362810],
        "evidence": "The source and reviewed record are the same crossing-number formula problem for complete graphs.",
    },
    "harborths-conjecture": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [1405, 40316010],
        "evidence": "The source is the standard integral-edge-length drawing question for planar graphs already represented in the frozen corpus.",
    },
    "hartshorne-complete-intersection-conjecture": {
        "outcome": "variant_or_subproblem",
        "append": True,
        "atlas": [40324098],
        "evidence": "The workspace states a general dimension-versus-codimension form beyond the declared codimension-two Top-500 target and related Fano form.",
    },
    "heesch-conjecture": {
        "outcome": "appendable_no_strong_match",
        "append": True,
        "atlas": [],
        "evidence": "No semantically matching frozen atlas record was found for the finite-surrounding-layer tiling target after broader private corpus search.",
    },
    "higher-dimensional-weinstein-conjecture": {
        "outcome": "variant_or_subproblem",
        "append": False,
        "atlas": [1141, 30001505, 40315924],
        "evidence": "The workspace restricts the general Reeb-orbit assertion to higher dimensions, but its source-published status is not open-only.",
    },
    "hirsch-dimension4-minimal-corridor-census": {
        "outcome": "related_not_same",
        "append": False,
        "atlas": [40316162, 40376454],
        "evidence": "The workspace is a diagnostic census for hypothetical low-dimensional Hirsch witnesses, not a separately self-contained mathematical target; the offered record is unrelated.",
    },
    "hirsch-dimension4-normalization-fiber-audit": {
        "outcome": "related_not_same",
        "append": False,
        "atlas": [40316162, 40392652],
        "evidence": "The workspace is a normalization audit around potential Hirsch constructions rather than an independent theorem target; the offered record is unrelated.",
    },
    "hopf-sign-conjecture-nonpositive-curvature": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [40376036],
        "evidence": "Full-corpus review located the matching Euler-characteristic sign conjecture for closed even-dimensional nonpositively curved manifolds.",
    },
    "huneke-wiegand-conjecture": {
        "outcome": "appendable_no_strong_match",
        "append": True,
        "atlas": [40354464, 40338833, 40354465, 40362799],
        "evidence": "Reviewed records are lifting, higher-dimensional, or ring-class variants; none states the source's base one-dimensional tensor-dual criterion.",
    },
    "igusa-denef-loeser-monodromy-conjecture": {
        "outcome": "unresolved_due_to_incomplete_existing_text",
        "append": False,
        "atlas": [40374940, 40335358, 40396006],
        "evidence": "The source names a common monodromy theme, but the available frozen candidates are excerpt-only p-adic, motivic, or strong variants that do not establish exact scope identity.",
    },
    "improve-classical-semiprime-factorization": {
        "outcome": "variant_or_subproblem",
        "append": True,
        "atlas": [1514, 1285, 40393482],
        "evidence": "The source sets a worst-case classical benchmark relative to general-purpose factoring methods, distinct from the corpus's polynomial-time and component-selection questions.",
    },
    "infinitely-many-decimal-palindromic-primes": {
        "outcome": "variant_or_subproblem",
        "append": True,
        "atlas": [9400155, 40359344],
        "evidence": "The source fixes decimal notation, while reviewed records pose the broader all-base palindromic-prime question.",
    },
    "infinitely-many-emirps": {
        "outcome": "appendable_no_strong_match",
        "append": True,
        "atlas": [26],
        "evidence": "The offered record concerns a different prime family, and wider frozen-corpus search found no matching digit-reversal-prime infinitude target.",
    },
    "kahane-quantitative-beurling-helson-conjecture": {
        "outcome": "appendable_no_strong_match",
        "append": True,
        "atlas": [40357353],
        "evidence": "The offered Helson record concerns a distinct Dirichlet-series zero question; broader frozen search found no matching circle-map norm-growth criterion.",
    },
    "kobon-triangle-problem": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [40315970, 7200050, 40389034],
        "evidence": "The source and frozen records ask the same extremal count of triangular cells in straight-line arrangements.",
    },
    "kontsevich-homological-mirror-symmetry": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [40374550],
        "evidence": "The source and reviewed record are the general categorical mirror-symmetry assertion, notwithstanding source-published partial-resolution language.",
    },
    "langlands-functoriality-gl4xgl2-to-gl8": {
        "outcome": "variant_or_subproblem",
        "append": True,
        "atlas": [40347265, 40343918, 40401031],
        "evidence": "The source isolates a concrete tensor-product transfer, while reviewed corpus records cover broader Langlands or adjacent GL4-by-GL2 questions.",
    },
    "large-steiner-systems-construction-problem": {
        "outcome": "related_not_same",
        "append": False,
        "atlas": [40402136],
        "evidence": "The source is source-published as solved, and the offered record concerns a different approximate large-girth design question.",
    },
    "log-rank-conjecture": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [40327568, 40322618, 40323552],
        "evidence": "Full-corpus review found the standard deterministic communication-complexity log-rank formulation; the workbench shortlist also included a distinct approximation-rank variant.",
    },
    "lonely-runner-conjecture": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [1489, 30, 3344, 40315909],
        "evidence": "The source is the ordinary distinct-speed circular-track separation conjecture already duplicated in frozen atlas records.",
    },
    "loss-to-time-state-preserving-quantum-extraction-ibcs-stark": {
        "outcome": "related_not_same",
        "append": False,
        "atlas": [40315498],
        "evidence": "The workspace bundles several compiler and interface objectives rather than one self-contained mathematical target; the offered quantum-state record addresses a distinct task.",
    },
    "magic-square-of-squares": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [1243, 3391, 40316053],
        "evidence": "The source and frozen records ask the same distinct-square three-by-three magic-square existence question.",
    },
    "manins-conjecture-on-rational-points": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [40357143, 40374651, 40348824],
        "evidence": "The source is the established Fano rational-point asymptotic conjecture represented in frozen Batyrev--Manin--Peyre records.",
    },
    "markov-uniqueness-conjecture": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [40316110, 40369626, 9400109],
        "evidence": "The source and reviewed records share the standard uniqueness assertion for the maximal member of a positive Markov triple.",
    },
    "matchings-jack-conjecture": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [40354065, 40362620],
        "evidence": "The classical Jack connection-coefficient positivity target is present in the frozen corpus; the shortlist's Macdonald item is a generalization.",
    },
    "matrix-multiplication-exponent-conjecture-omega-equals-2": {
        "outcome": "same_problem_reviewed",
        "append": False,
        "atlas": [3100086, 40346080, 40388199],
        "evidence": "The source is the standard exponent-two matrix-multiplication question already represented by multiple frozen records.",
    },
}


class ReviewError(RuntimeError):
    """Raised for malformed inputs or unsafe decision output."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            values = [json.loads(line) for line in handle if line.strip()]
    except OSError as exc:
        raise ReviewError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReviewError(f"invalid JSONL in {path}: {exc}") from exc
    if not all(isinstance(value, dict) for value in values):
        raise ReviewError(f"{path}: every row must be an object")
    return values


def check_no_source_prose(value: Any) -> None:
    """Reject the principal full-text keys that must never reach this sidecar."""

    forbidden = {"title", "statement", "exact_target", "reader_question", "source_text", "canonical_title"}
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in forbidden:
                raise ReviewError(f"public sidecar unexpectedly includes forbidden key {key!r}")
            check_no_source_prose(nested)
    elif isinstance(value, list):
        for nested in value:
            check_no_source_prose(nested)


def crosswalk_metadata(path: Path) -> dict[str, dict[str, str]]:
    """Read only the public metadata needed to attach source status safely."""

    values = read_jsonl(path)
    if len(values) != 268:
        raise ReviewError(f"{path}: expected 268 collaboration crosswalk rows, found {len(values)}")
    metadata: dict[str, dict[str, str]] = {}
    for row in values:
        if row.get("schema") != CROSSWALK_SCHEMA:
            raise ReviewError(f"{path}: unexpected crosswalk schema")
        workspace = row.get("workspace")
        if not isinstance(workspace, dict):
            raise ReviewError(f"{path}: malformed workspace")
        slug = workspace.get("slug")
        route = workspace.get("source_route")
        status = workspace.get("source_status_as_published")
        title_sha256 = workspace.get("title_sha256")
        statement_sha256 = workspace.get("statement_sha256")
        if not all(isinstance(value, str) and value for value in (slug, route, status, title_sha256, statement_sha256)):
            raise ReviewError(f"{path}: incomplete public workspace metadata")
        if slug in metadata:
            raise ReviewError(f"{path}: duplicate workspace slug")
        metadata[slug] = {
            "source_route": route,
            "source_status_as_published": status,
            "title_sha256": title_sha256,
            "statement_sha256": statement_sha256,
        }
    return metadata


def public_row(
    workbench_row: dict[str, Any], position: int, metadata: dict[str, dict[str, str]]
) -> dict[str, Any]:
    if workbench_row.get("schema") != WORKBENCH_SCHEMA:
        raise ReviewError(f"workbench position {position}: unexpected schema")
    workspace = workbench_row.get("workspace")
    if not isinstance(workspace, dict):
        raise ReviewError(f"workbench position {position}: malformed workspace")
    slug = workspace.get("slug")
    if not isinstance(slug, str) or slug not in DECISIONS:
        raise ReviewError(f"workbench position {position}: unreviewed or malformed slug")
    decision = DECISIONS[slug]
    outcome = decision["outcome"]
    append = decision["append"]
    atlas_ids = decision["atlas"]
    evidence = decision["evidence"]
    if outcome not in ALLOWED_OUTCOMES:
        raise ReviewError(f"{slug}: invalid outcome")
    if not isinstance(append, bool):
        raise ReviewError(f"{slug}: append flag must be boolean")
    if not isinstance(atlas_ids, list) or not all(isinstance(value, int) for value in atlas_ids):
        raise ReviewError(f"{slug}: malformed atlas IDs")
    if len(atlas_ids) != len(set(atlas_ids)):
        raise ReviewError(f"{slug}: duplicate atlas ID")
    if not isinstance(evidence, str) or not evidence or "\n" in evidence or len(evidence) > 600:
        raise ReviewError(f"{slug}: evidence must be a single nonempty <=600-character line")
    public_workspace = metadata.get(slug)
    if public_workspace is None:
        raise ReviewError(f"{slug}: missing crosswalk metadata")
    source_status = public_workspace["source_status_as_published"]
    if append and source_status != "open":
        raise ReviewError(f"{slug}: only source-published open routes may be provisionally append-worthy")

    top500_links = workbench_row.get("top500_source_declared_links")
    if not isinstance(top500_links, list):
        raise ReviewError(f"{slug}: malformed Top-500 links")
    reviewed_top500: list[dict[str, Any]] = []
    for link in top500_links:
        if not isinstance(link, dict) or not isinstance(link.get("top500_problem_id"), str):
            raise ReviewError(f"{slug}: malformed Top-500 link")
        top500_id = link["top500_problem_id"]
        rank = TOP500_RELEASE_RANKS.get(top500_id)
        if rank is None:
            raise ReviewError(f"{slug}: missing release rank for declared Top-500 ID {top500_id}")
        reviewed_top500.append(
            {
                "problem_id": top500_id,
                "release_rank": rank,
                "scope_relation_as_published": link.get("scope_relation"),
            }
        )

    offered = workbench_row.get("opdp_candidates")
    if not isinstance(offered, list):
        raise ReviewError(f"{slug}: malformed offered candidates")
    offered_ids = []
    for candidate in offered:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("problem_id"), int):
            raise ReviewError(f"{slug}: malformed offered candidate")
        offered_ids.append(candidate["problem_id"])

    row = {
        "schema": OUTPUT_SCHEMA,
        "review_partition": {
            "workbench_start_index": START_INDEX,
            "workbench_end_index": END_INDEX,
            "workbench_position": position,
        },
        "review_key": workbench_row.get("review_key"),
        "workspace": {
            "slug": slug,
            "source_route": public_workspace["source_route"],
            "source_status_as_published": source_status,
            "title_sha256": public_workspace["title_sha256"],
            "statement_sha256": public_workspace["statement_sha256"],
            "source_top500_relation": (
                "explicit_top500_route" if reviewed_top500 else "route_not_in_top500_json"
            ),
            "source_top500_release_rank": [entry["release_rank"] for entry in reviewed_top500],
        },
        "review_outcome": outcome,
        "distinct_problem_worthy_of_append": append,
        "reviewed_atlas_problem_ids": atlas_ids,
        "workbench_candidate_ids_consulted": offered_ids,
        "reviewed_top500": reviewed_top500,
        "review_evidence": evidence,
        "review_method": {
            "frozen_source_context": "private local Collaboration snapshot",
            "frozen_atlas_context": "full OPDP v1.7 private semantic search",
            "id_scope": "reviewed_atlas_problem_ids may include full-corpus records beyond the workbench lexical shortlist",
            "source_status_interpretation": "source-published only; not independently verified",
        },
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
    check_no_source_prose(row)
    return row


def build(
    workbench_path: Path, crosswalk_path: Path, output_path: Path, summary_path: Path
) -> dict[str, Any]:
    workbench = read_jsonl(workbench_path)
    if len(workbench) != 126:
        raise ReviewError(f"{workbench_path}: expected 126 workbench rows, found {len(workbench)}")
    selected = workbench[START_INDEX - 1 : END_INDEX]
    if len(selected) != END_INDEX - START_INDEX + 1:
        raise ReviewError("fixed review range is incomplete")
    metadata = crosswalk_metadata(crosswalk_path)
    rows = [public_row(row, position, metadata) for position, row in enumerate(selected, START_INDEX)]
    keys = [row["review_key"] for row in rows]
    if len(set(keys)) != len(keys):
        raise ReviewError("duplicate review keys")
    expected_slugs = set(DECISIONS)
    actual_slugs = {row["workspace"]["slug"] for row in rows}
    if expected_slugs != actual_slugs:
        raise ReviewError("decision map does not exactly cover the fixed range")
    body = b"".join(canonical_json_bytes(row) + b"\n" for row in rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(body)
    counts = Counter(row["review_outcome"] for row in rows)
    source_status_counts = Counter(row["workspace"]["source_status_as_published"] for row in rows)
    summary = {
        "schema": SUMMARY_SCHEMA,
        "purpose": "Public-safe semantic review of a fixed Collaboration workbench partition; not an append authorization.",
        "input": {
            "workbench_path": str(workbench_path).replace("\\", "/"),
            "workbench_sha256": sha256_file(workbench_path),
            "workbench_total_rows": len(workbench),
            "crosswalk_path": str(crosswalk_path).replace("\\", "/"),
            "crosswalk_sha256": sha256_file(crosswalk_path),
            "reviewed_positions": [START_INDEX, END_INDEX],
        },
        "results": {
            "reviewed_rows": len(rows),
            "by_outcome": dict(sorted(counts.items())),
            "provisional_append_worthy_count": sum(
                1 for row in rows if row["distinct_problem_worthy_of_append"]
            ),
            "source_status_as_published": dict(sorted(source_status_counts.items())),
            "records_appended": 0,
        },
        "output": {
            "path": str(output_path).replace("\\", "/"),
            "sha256": hashlib.sha256(body).hexdigest(),
            "record_count": len(rows),
        },
        "public_boundary": {
            "source_prose_retained": False,
            "opdp_prose_retained": False,
            "independent_open_status_verification": False,
            "append_authorization": False,
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_bytes(canonical_json_bytes(summary) + b"\n")
    return summary


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workbench", type=Path, default=Path("data/PROOFATLAS_COLLABORATION_REVIEW_WORKBENCH_v1.jsonl")
    )
    parser.add_argument(
        "--crosswalk", type=Path, default=Path("data/PROOFATLAS_COLLABORATION_CROSSWALK_v1.jsonl")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/PROOFATLAS_COLLABORATION_SEMANTIC_REVIEW_ROWS_043_084_v2.jsonl"),
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("data/PROOFATLAS_COLLABORATION_SEMANTIC_REVIEW_ROWS_043_084_v2.json"),
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_arguments(argv)
    try:
        summary = build(args.workbench, args.crosswalk, args.output, args.summary)
    except ReviewError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"wrote {summary['results']['reviewed_rows']} public-safe rows; "
        f"{summary['results']['provisional_append_worthy_count']} provisional append leads"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
