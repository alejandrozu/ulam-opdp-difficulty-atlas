#!/usr/bin/env python3
"""Create a public-safe semantic-review workbench from a collaboration crosswalk.

The workbench is a deliberately lossy projection of
``PROOFATLAS_COLLABORATION_CROSSWALK_v1.jsonl``.  It carries only stable
workspace identifiers, hashes, source routes, candidate identifiers/locators,
and already-computed lexical metrics.  In particular it deliberately excludes
workspace titles and text, OPDP titles and text, Top 500 titles and exact
targets, and decision prose.  It is useful for partitioning human semantic
review without making an append or novelty claim.

It includes (a) every non-direct workspace whose crosswalk decision was not
``same_problem_reviewed`` and (b) source-declared direct workspaces labelled
``workspace_special_case`` or ``workspace_stronger_formulation``.  No row in
this artifact is authorized for append; a no-strong-match remains only a
source-recovery lead, especially because frozen MathDB records can be excerpts.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
CROSSWALK_MODULE_PATH = SCRIPT_DIR / "build_proofatlas_top500_crosswalk.py"
SPEC = importlib.util.spec_from_file_location("proofatlas_top500_crosswalk", CROSSWALK_MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
CROSSWALK = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CROSSWALK
SPEC.loader.exec_module(CROSSWALK)


INPUT_SCHEMA = "opdp.proofatlas-collaboration-crosswalk.v1"
OUTPUT_SCHEMA = "opdp.proofatlas-collaboration-review-workbench.v1"
INVENTORY_SCHEMA = "opdp.proofatlas-collaboration-review-workbench-inventory.v1"
DIRECT_BASIS = "source_declared_top500_researchWorkspace_link"
NON_DIRECT_BASIS = "frozen_opdp_v1_7_title_plus_statement_comparison"
DIRECT_VARIANT_RELATIONS = frozenset({"workspace_special_case", "workspace_stronger_formulation"})
NON_IDENTITY_DECISIONS = frozenset(
    {"variant_or_subproblem", "related_not_same", "appendable_no_strong_match", "unresolved"}
)
FORBIDDEN_KEYS = frozenset(
    {
        "title",
        "statement",
        "exact_target",
        "decision_evidence",
        "canonical_title",
        "top500_title",
        "source_status_as_published",
        "catalog_status",
        "reader_question",
        "source_text",
    }
)


class WorkbenchError(RuntimeError):
    pass


def require(value: Any, expected: type, context: str) -> Any:
    if not isinstance(value, expected):
        raise WorkbenchError(f"{context}: expected {expected.__name__}")
    return value


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        raise WorkbenchError(f"crosswalk does not exist: {path}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    raise WorkbenchError(f"{path}:{line_number}: blank JSONL line")
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise WorkbenchError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
                yield require(value, dict, f"{path}:{line_number}")
    except OSError as exc:
        raise WorkbenchError(f"cannot read {path}: {exc}") from exc


def safe_workspace(raw: dict[str, Any], context: str) -> dict[str, str]:
    workspace = require(raw.get("workspace"), dict, f"{context}.workspace")
    result: dict[str, str] = {}
    for key in ("slug", "source_route", "title_sha256", "statement_sha256"):
        value = require(workspace.get(key), str, f"{context}.workspace.{key}")
        if not value:
            raise WorkbenchError(f"{context}.workspace.{key}: must be nonempty")
        result[key] = value
    return result


def safe_comparison(raw: dict[str, Any], context: str) -> dict[str, Any]:
    comparison = require(raw.get("comparison"), dict, f"{context}.comparison")
    permitted = {
        "composite_quality",
        "exact_identity_candidate",
        "strong_identity_candidate",
        "strong_statement_evidence",
        "strong_title_evidence",
        "title_exact_after_fixed_presentation_normalization",
    }
    result = {key: comparison[key] for key in permitted if key in comparison}
    for channel in ("title", "statement"):
        values = require(comparison.get(channel), dict, f"{context}.comparison.{channel}")
        channel_permitted = {
            "candidate_signal_coverage",
            "reference_signal_coverage",
            "shared_signal_tokens",
            "signal_f1",
            "signal_jaccard",
            "longest_exact_token_run_capped_at_900",
            "source_text_complete_for_identity_evidence",
        }
        result[channel] = {key: values[key] for key in channel_permitted if key in values}
    return result


def safe_opdp_candidates(raw: Any, context: str) -> list[dict[str, Any]]:
    candidates = require(raw, list, f"{context}.selected_opdp_candidates")
    result: list[dict[str, Any]] = []
    for position, candidate_value in enumerate(candidates):
        candidate = require(candidate_value, dict, f"{context}.candidate[{position}]")
        safe: dict[str, Any] = {}
        for key in ("atlas_record_index", "problem_id", "problem_number", "title_sha256"):
            if key not in candidate:
                raise WorkbenchError(f"{context}.candidate[{position}]: missing {key}")
            safe[key] = candidate[key]
        safe["comparison"] = safe_comparison(candidate, f"{context}.candidate[{position}]")
        result.append(safe)
    return result


def safe_direct_links(raw: Any, context: str) -> list[dict[str, str]]:
    links = require(raw, list, f"{context}.top500_source_declared_links")
    result: list[dict[str, str]] = []
    for position, link_value in enumerate(links):
        link = require(link_value, dict, f"{context}.top500_link[{position}]")
        safe: dict[str, str] = {}
        for key in ("top500_problem_id", "top500_route", "scope_relation"):
            value = require(link.get(key), str, f"{context}.top500_link[{position}].{key}")
            if not value:
                raise WorkbenchError(f"{context}.top500_link[{position}].{key}: must be nonempty")
            safe[key] = value
        result.append(safe)
    return result


def contains_forbidden_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            # ``comparison.title`` and ``comparison.statement`` are nested
            # metric maps, not retained prose.  A scalar value under either
            # name would be source text and is forbidden.
            if key in {"title", "statement"} and not isinstance(nested, dict):
                return key
            if key in FORBIDDEN_KEYS - {"title", "statement"}:
                return key
            forbidden = contains_forbidden_key(nested)
            if forbidden is not None:
                return forbidden
    elif isinstance(value, list):
        for nested in value:
            forbidden = contains_forbidden_key(nested)
            if forbidden is not None:
                return forbidden
    return None


def select_row(raw: dict[str, Any], context: str) -> dict[str, Any] | None:
    if raw.get("schema") != INPUT_SCHEMA:
        raise WorkbenchError(f"{context}: unexpected crosswalk schema")
    decision = require(raw.get("decision"), str, f"{context}.decision")
    basis = require(raw.get("matching_basis"), str, f"{context}.matching_basis")
    workspace = safe_workspace(raw, context)
    direct_links = safe_direct_links(raw.get("top500_source_declared_links"), context)
    candidates = safe_opdp_candidates(raw.get("selected_opdp_candidates"), context)

    if basis == NON_DIRECT_BASIS and decision in NON_IDENTITY_DECISIONS:
        bucket = f"non_direct_{decision}"
    elif basis == DIRECT_BASIS and decision == "variant_or_subproblem" and any(
        link["scope_relation"] in DIRECT_VARIANT_RELATIONS for link in direct_links
    ):
        bucket = "source_declared_special_or_stronger"
    else:
        return None

    row = {
        "schema": OUTPUT_SCHEMA,
        "review_key": f"proofatlas-collaboration:{workspace['slug']}",
        "review_bucket": bucket,
        "original_crosswalk_decision": decision,
        "matching_basis": basis,
        "workspace": workspace,
        "top500_source_declared_links": direct_links,
        "opdp_candidates": candidates,
        "opdp_candidate_count": len(candidates),
        "append_authorization": False,
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
    forbidden = contains_forbidden_key(row)
    if forbidden is not None:
        raise WorkbenchError(f"{context}: output unexpectedly contains forbidden key {forbidden!r}")
    return row


def build(crosswalk_path: Path, output_path: Path, summary_path: Path) -> dict[str, Any]:
    resolved_input = crosswalk_path.resolve()
    if output_path.resolve() == resolved_input or summary_path.resolve() == resolved_input:
        raise WorkbenchError("refusing to overwrite the collaboration crosswalk")
    if output_path.resolve() == summary_path.resolve():
        raise WorkbenchError("workbench output and summary paths must differ")

    rows: list[dict[str, Any]] = []
    buckets: Counter[str] = Counter()
    decisions: Counter[str] = Counter()
    input_count = 0
    for line_number, raw in enumerate(read_jsonl(crosswalk_path), start=1):
        input_count += 1
        selected = select_row(raw, f"{crosswalk_path}:{line_number}")
        if selected is None:
            continue
        rows.append(selected)
        buckets[selected["review_bucket"]] += 1
        decisions[selected["original_crosswalk_decision"]] += 1
    if input_count != 268:
        raise WorkbenchError(f"{crosswalk_path}: expected 268 crosswalk rows, found {input_count}")
    keys = [row["review_key"] for row in rows]
    if len(keys) != len(set(keys)):
        raise WorkbenchError("review workbench contains duplicate workspace keys")
    rows.sort(key=lambda row: row["review_key"])
    body = b"".join(CROSSWALK.canonical_json_bytes(row) + b"\n" for row in rows)
    CROSSWALK.atomic_write(output_path, body)
    summary = {
        "schema": INVENTORY_SCHEMA,
        "purpose": (
            "Public-safe semantic-review routing index. It excludes source prose and makes no append, "
            "novelty, openness, or recategorization claim."
        ),
        "input": {
            "path": str(crosswalk_path).replace("\\", "/"),
            "sha256": CROSSWALK.sha256_file(crosswalk_path),
            "crosswalk_record_count": input_count,
        },
        "selection_policy": {
            "non_direct": "All non-direct rows whose crosswalk decision is not same_problem_reviewed.",
            "direct": "Only source-declared workspace_special_case or workspace_stronger_formulation routes.",
            "append_authorization": "Always false; even appendable_no_strong_match is only a source-recovery routing state.",
            "public_boundary": "Only IDs, hashes, source routes, candidate locators, and comparison metrics are retained.",
        },
        "results": {
            "review_workbench_rows": len(rows),
            "by_review_bucket": dict(sorted(buckets.items())),
            "by_original_crosswalk_decision": dict(sorted(decisions.items())),
            "opdp_candidate_references": sum(row["opdp_candidate_count"] for row in rows),
            "append_authorized_count": 0,
        },
        "output": {
            "path": str(output_path).replace("\\", "/"),
            "sha256": CROSSWALK.sha256_bytes(body),
            "record_count": len(rows),
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
    CROSSWALK.atomic_write(summary_path, CROSSWALK.canonical_json_bytes(summary) + b"\n")
    return summary


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--crosswalk",
        type=Path,
        default=Path("data/PROOFATLAS_COLLABORATION_CROSSWALK_v1.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/PROOFATLAS_COLLABORATION_REVIEW_WORKBENCH_v1.jsonl"),
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("data/PROOFATLAS_COLLABORATION_REVIEW_WORKBENCH_INVENTORY_v1.json"),
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    try:
        summary = build(arguments.crosswalk, arguments.output, arguments.summary)
    except WorkbenchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {summary['results']['review_workbench_rows']:,} public-safe review-workbench rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
