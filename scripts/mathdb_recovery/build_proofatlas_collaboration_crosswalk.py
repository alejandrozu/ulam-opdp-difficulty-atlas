#!/usr/bin/env python3
"""Inventory ProofAtlas collaboration workspaces without importing their prose.

The public collaboration directory embeds 268 workspace cards.  This tool
uses a frozen local HTML snapshot plus the frozen Top 500 JSON to distinguish
native, source-declared Top-500 links from the remaining workspaces, then
streams OPDP v1.7 for a conservative identity crosswalk.

It writes only public-safe identifiers, titles, hashes, routes, source status
metadata, and lexical comparison metrics.  Workspace statement/snippet text,
Top-500 exact targets, and OPDP statements/excerpts remain transient process
inputs and are never copied into its public artifacts.  The result is not an
OPDP append, a verification of whether a question remains open, or a license
to republish source text.

For a source-declared Top-500 link, the directory's own ``scopeRelation`` is
used as the primary evidence.  For non-linked cards, a unique high-confidence
title-plus-statement match to frozen OPDP v1.7 is marked
``same_problem_reviewed``.  A no-match is deliberately not proof of novelty:
only a compact provisional ``appendable_no_strong_match`` routing state is
emitted when the *directory's own* card has an open status and a minimally
well-formed question.  Its append-worthiness remains false until statement-
level source recovery and human curation are completed.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
CROSSWALK_MODULE_PATH = SCRIPT_DIR / "build_proofatlas_top500_crosswalk.py"
SPEC = importlib.util.spec_from_file_location("proofatlas_top500_crosswalk", CROSSWALK_MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
CROSSWALK = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CROSSWALK
SPEC.loader.exec_module(CROSSWALK)


TOOL_NAME = "OPDP ProofAtlas collaboration-directory conservative crosswalk builder"
TOOL_VERSION = "1.0.0"
COLLABORATION_SCHEMA = "opdp.proofatlas-collaboration-crosswalk.v1"
INVENTORY_SCHEMA = "opdp.proofatlas-collaboration-inventory.v1"
DEFAULT_DIRECTORY_URL = "https://proofatlas.ai/collaboration/"
ALLOWED_SOURCE_STATUSES = frozenset(
    {"open", "partially_resolved", "open_with_unverified_claim", "solved", "resolved_up_to_finite_check"}
)


class CollaborationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Workspace:
    index: int
    slug: str
    title: str
    statement: str
    route: str
    source_status: str
    recognition_tier: str
    recognition: str
    area: str


class WorkspaceParser(HTMLParser):
    """Extract only root workspace-card attributes from a frozen directory."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        values = {key: value or "" for key, value in attrs}
        if "data-collaboration-workspace" in values:
            self.rows.append(values)


def require_str(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CollaborationError(f"{context}: expected nonempty string")
    return value


def canonical_route(href: str, slug: str) -> str:
    route = urljoin(DEFAULT_DIRECTORY_URL, href)
    parsed = urlparse(route)
    if parsed.scheme != "https" or parsed.hostname not in {"proofatlas.ai", "www.proofatlas.ai"}:
        raise CollaborationError(f"workspace {slug}: route escapes ProofAtlas: {href!r}")
    expected_suffix = f"/collaboration/{slug}/"
    if not parsed.path.endswith(expected_suffix):
        raise CollaborationError(
            f"workspace {slug}: route {parsed.path!r} does not match expected slug route"
        )
    return f"https://proofatlas.ai{expected_suffix}"


def load_workspaces(path: Path) -> tuple[list[Workspace], dict[str, Any]]:
    if not path.is_file():
        raise CollaborationError(f"collaboration snapshot does not exist: {path}")
    try:
        body = path.read_bytes()
        source = body.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CollaborationError(f"cannot read collaboration snapshot {path}: {exc}") from exc
    parser = WorkspaceParser()
    parser.feed(source)
    parser.close()
    workspaces: list[Workspace] = []
    slugs: set[str] = set()
    for index, raw in enumerate(parser.rows):
        context = f"workspace card {index}"
        slug = require_str(raw.get("data-discovery-slug"), f"{context}.slug")
        if slug in slugs:
            raise CollaborationError(f"{context}: duplicate slug {slug!r}")
        title = require_str(raw.get("data-discovery-sort-title"), f"{context}.title")
        statement = require_str(raw.get("data-discovery-statement"), f"{context}.statement")
        source_status = require_str(raw.get("data-discovery-status"), f"{context}.status")
        if source_status not in ALLOWED_SOURCE_STATUSES:
            raise CollaborationError(f"{context}: unknown source status {source_status!r}")
        workspaces.append(
            Workspace(
                index=index,
                slug=slug,
                title=title,
                statement=statement,
                route=canonical_route(require_str(raw.get("href"), f"{context}.href"), slug),
                source_status=source_status,
                recognition_tier=raw.get("data-discovery-recognition-tier", ""),
                recognition=raw.get("data-discovery-recognition", ""),
                area=raw.get("data-discovery-area", ""),
            )
        )
        slugs.add(slug)
    if len(workspaces) != 268:
        raise CollaborationError(
            f"{path}: expected 268 workspace cards, found {len(workspaces):,}; snapshot shape changed"
        )
    return workspaces, {
        "snapshot_path": str(path).replace("\\", "/"),
        "snapshot_sha256": CROSSWALK.sha256_bytes(body),
        "snapshot_bytes": len(body),
        "workspace_count": len(workspaces),
        "raw_snapshot_retention": "local_ignored_snapshot_not_a_release_artifact",
    }


def workspace_slug_from_route(route: str) -> str:
    parsed = urlparse(route)
    pieces = [part for part in parsed.path.split("/") if part]
    if len(pieces) != 2 or pieces[0] != "collaboration":
        raise CollaborationError(f"Top 500 research-workspace route has unexpected shape: {route!r}")
    return pieces[1]


def load_top500_links(path: Path) -> tuple[dict[str, list[dict[str, str]]], dict[str, Any]]:
    if not path.is_file():
        raise CollaborationError(f"Top 500 snapshot does not exist: {path}")
    try:
        body = path.read_bytes()
        payload = json.loads(body.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollaborationError(f"cannot parse Top 500 snapshot {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schemaVersion") != CROSSWALK.TOP500_SCHEMA:
        raise CollaborationError(f"{path}: expected {CROSSWALK.TOP500_SCHEMA} payload")
    records = payload.get("records")
    if not isinstance(records, list) or len(records) != 500:
        raise CollaborationError(f"{path}: expected 500 Top 500 records")
    links: dict[str, list[dict[str, str]]] = defaultdict(list)
    for record_index, record in enumerate(records):
        if not isinstance(record, dict):
            raise CollaborationError(f"Top 500 record {record_index}: expected object")
        problem_id = require_str(record.get("problemId"), f"Top 500 record {record_index}.problemId")
        title = require_str(record.get("canonicalTitle"), f"Top 500 record {record_index}.title")
        raw_links = record.get("researchWorkspaces")
        if raw_links is None:
            continue
        if not isinstance(raw_links, list):
            raise CollaborationError(f"Top 500 record {problem_id}: researchWorkspaces must be array")
        for raw_link in raw_links:
            if not isinstance(raw_link, dict):
                raise CollaborationError(f"Top 500 record {problem_id}: malformed research workspace")
            route = require_str(raw_link.get("route"), f"Top 500 record {problem_id}.route")
            scope = require_str(
                raw_link.get("scopeRelation"), f"Top 500 record {problem_id}.scopeRelation"
            )
            slug = workspace_slug_from_route(route)
            links[slug].append(
                {
                    "top500_problem_id": problem_id,
                    "top500_title": title,
                    "scope_relation": scope,
                    "top500_route": f"https://proofatlas.ai{route}",
                }
            )
    return dict(links), {
        "snapshot_path": str(path).replace("\\", "/"),
        "snapshot_sha256": CROSSWALK.sha256_bytes(body),
        "snapshot_bytes": len(body),
        "top500_record_count": len(records),
        "source_declared_research_link_count": sum(len(value) for value in links.values()),
        "source_declared_research_linked_workspace_count": len(links),
    }


def workspace_target(workspace: Workspace) -> Any:
    """Adapt a workspace card to the shared transient matcher representation."""

    title_tokens = CROSSWALK.tokenise(workspace.title)
    statement_tokens = CROSSWALK.tokenise(workspace.statement)
    return CROSSWALK.ProofAtlasTarget(
        index=workspace.index,
        problem_id=workspace.slug,
        rank=workspace.index + 1,
        title=workspace.title,
        exact_target=workspace.statement,
        release_status=workspace.source_status,
        formal_statement_url=workspace.route,
        title_key=CROSSWALK.title_compare_key(title_tokens),
        title_tokens=title_tokens,
        title_signal=CROSSWALK.signal_tokens(title_tokens),
        statement_tokens=statement_tokens,
        statement_signal=CROSSWALK.signal_tokens(statement_tokens),
        title_sha256=CROSSWALK.sha256_text(workspace.title),
        statement_sha256=CROSSWALK.sha256_text(workspace.statement),
    )


def run_v17_match(
    workspaces: list[Workspace], atlas_path: Path, max_atlas_records: int | None
) -> tuple[dict[str, list[dict[str, Any]]], int, int, int]:
    """Stream v1.7 exactly once for workspaces without native Top-500 linkage."""

    targets = [workspace_target(workspace) for workspace in workspaces]
    by_title, by_token = CROSSWALK.build_target_retrieval_index(targets)
    target_by_index = {target.index: target for target in targets}
    retained: dict[int, list[dict[str, Any]]] = {target.index: [] for target in targets}
    scanned = 0
    compared = 0
    excerpt_rows = 0
    for record_index, raw in enumerate(CROSSWALK.iter_v17_records(atlas_path)):
        if max_atlas_records is not None and scanned >= max_atlas_records:
            break
        scanned += 1
        record = CROSSWALK.atlas_record_from_raw(raw, record_index)
        if record is None:
            continue
        if record.source_excerpt_only:
            excerpt_rows += 1
        title_tokens = CROSSWALK.tokenise(record.title)
        for target_index in CROSSWALK.candidate_target_indices(title_tokens, by_title, by_token):
            target = target_by_index[target_index]
            comparison = CROSSWALK.comparison_for(target, record)
            compared += 1
            title_shared = int(comparison["title"]["shared_signal_tokens"])
            if (
                comparison["strong_identity_candidate"]
                or title_shared >= 2
                or comparison["title_exact_after_fixed_presentation_normalization"]
            ):
                CROSSWALK.retain_candidate(
                    retained[target_index], CROSSWALK.candidate_output(record, comparison)
                )
        if scanned % 10_000 == 0:
            print(f"compared {scanned:,} streamed OPDP records", file=sys.stderr, flush=True)
    return ({workspace.slug: retained[workspace.index] for workspace in workspaces}, scanned, compared, excerpt_rows)


def minimal_formal_question(workspace: Workspace) -> bool:
    """A cautious routing predicate, not a proof of self-containedness."""

    tokens = CROSSWALK.tokenise(workspace.statement)
    if len(tokens) < 10:
        return False
    signal = set(tokens)
    question_cues = {"does", "can", "must", "every", "for", "prove", "determine", "find", "is"}
    non_problem_markers = {"retained", "audit", "census", "route", "endpoint"}
    title_tokens = set(CROSSWALK.tokenise(workspace.title))
    return bool(signal & question_cues) and not bool(title_tokens & non_problem_markers)


def direct_decision(links: list[dict[str, str]]) -> tuple[str, str]:
    relations = {link["scope_relation"] for link in links}
    if relations <= {"same_canonical_problem", "workspace_contains_exact_target"}:
        return (
            "same_problem_reviewed",
            "ProofAtlas Top 500 itself links this workspace to the canonical problem or states that "
            "the workspace contains its exact target; no separate collaboration import is justified.",
        )
    return (
        "variant_or_subproblem",
        "ProofAtlas Top 500 directly links this workspace but labels its relationship as a special-case "
        "or stronger formulation; it is retained as a linked variant, not an independent append candidate.",
    )


def non_direct_decision(
    workspace: Workspace, candidates: list[dict[str, Any]]
) -> tuple[str, str, list[dict[str, Any]], bool]:
    match_decision, _match_reason, selected = CROSSWALK.decision_for(candidates)
    if match_decision in {"exact_match", "strong_match"}:
        return (
            "same_problem_reviewed",
            "A unique frozen OPDP v1.7 record cleared the conservative title-plus-statement identity gate; "
            "this does not independently verify status or source completeness.",
            selected,
            False,
        )
    if match_decision == "ambiguous":
        return (
            "unresolved",
            "Multiple frozen OPDP records have similarly strong lexical evidence; a source-level scope review "
            "is needed before choosing an identity or an append path.",
            selected,
            False,
        )
    # No strong candidate.  A weak lexical neighbor is informative as a related
    # lead, but it is not a safe identity or a basis for automatic append.
    if candidates:
        first = candidates[0]
        shared = int(first["comparison"]["title"]["shared_signal_tokens"])
        if shared >= 2:
            return (
                "related_not_same",
                "Only topical/title overlap with frozen OPDP was retained; it did not clear the required "
                "statement-evidence gate, so no identity or append conclusion follows.",
                selected,
                False,
            )
    if workspace.source_status == "open" and minimal_formal_question(workspace):
        return (
            "appendable_no_strong_match",
            "The directory presents an open, minimally well-formed question and no strong frozen-OPDP match "
            "was found. It is a source-recovery queue item, not a proof of novelty or an authorized append.",
            [],
            False,
        )
    return (
        "unresolved",
        "No strong frozen-OPDP match was found, but the directory status or concise card text is insufficient "
        "to treat this workspace as a defensible append candidate.",
        selected,
        False,
    )


def build(
    *,
    collaboration_snapshot: Path,
    top500_snapshot: Path,
    atlas_path: Path,
    output_path: Path,
    summary_path: Path,
    max_atlas_records: int | None = None,
) -> dict[str, Any]:
    if output_path.resolve() == atlas_path.resolve() or summary_path.resolve() == atlas_path.resolve():
        raise CollaborationError("refusing to use OPDP v1.7 itself as a crosswalk output")
    if output_path.resolve() == summary_path.resolve():
        raise CollaborationError("crosswalk output and summary paths must differ")
    workspaces, collaboration_metadata = load_workspaces(collaboration_snapshot)
    direct_links, top500_metadata = load_top500_links(top500_snapshot)
    known_slugs = {workspace.slug for workspace in workspaces}
    dangling = sorted(set(direct_links) - known_slugs)
    if dangling:
        raise CollaborationError(f"Top 500 links reference absent workspace slugs: {dangling[:5]!r}")
    non_direct = [workspace for workspace in workspaces if workspace.slug not in direct_links]
    retained, scanned, compared, excerpt_rows = run_v17_match(
        non_direct, atlas_path, max_atlas_records
    )

    rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    source_status_counts: Counter[str] = Counter()
    link_relation_counts: Counter[str] = Counter()
    for workspace in workspaces:
        links = direct_links.get(workspace.slug, [])
        if links:
            decision, evidence = direct_decision(links)
            selected: list[dict[str, Any]] = []
            for link in links:
                link_relation_counts[link["scope_relation"]] += 1
            append_worthy = False
            matching_basis = "source_declared_top500_researchWorkspace_link"
        else:
            decision, evidence, selected, append_worthy = non_direct_decision(
                workspace, retained[workspace.slug]
            )
            matching_basis = "frozen_opdp_v1_7_title_plus_statement_comparison"
        counts[decision] += 1
        source_status_counts[workspace.source_status] += 1
        rows.append(
            {
                "schema": COLLABORATION_SCHEMA,
                "workspace": {
                    "slug": workspace.slug,
                    "title": workspace.title,
                    "title_sha256": CROSSWALK.sha256_text(workspace.title),
                    "statement_sha256": CROSSWALK.sha256_text(workspace.statement),
                    "source_route": workspace.route,
                    "source_status_as_published": workspace.source_status,
                    "recognition_tier_as_published": workspace.recognition_tier,
                    "area_as_published": workspace.area,
                },
                "top500_source_declared_links": links,
                "matching_basis": matching_basis,
                "decision": decision,
                "decision_evidence": evidence,
                "distinct_problem_worthy_of_append": append_worthy,
                "selected_opdp_candidates": selected,
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
    rows.sort(key=lambda row: row["workspace"]["slug"])
    output_body = b"".join(CROSSWALK.canonical_json_bytes(row) + b"\n" for row in rows)
    CROSSWALK.atomic_write(output_path, output_body)
    summary = {
        "schema": INVENTORY_SCHEMA,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "purpose": (
            "Public-safe inventory and conservative crosswalk of ProofAtlas collaboration workspaces. "
            "It does not append records, reconstruct statements, verify current openness, or rescore OPDP."
        ),
        "non_destructive_contract": {
            "network_access_used_by_this_builder": False,
            "raw_collaboration_snapshot_publicly_retained": False,
            "collaboration_statement_or_snippet_retained_in_public_sidecars": False,
            "top500_exact_targets_retained_in_public_sidecars": False,
            "opdp_statements_or_excerpts_retained_in_public_sidecars": False,
            "opdp_payload_modified": False,
            "opdp_records_appended": False,
            "open_status_verified": False,
            "opdp_dimensions_recalculated": False,
        },
        "inputs": {
            "proofatlas_collaboration_directory": collaboration_metadata,
            "proofatlas_top500": top500_metadata,
            "opdp_v1_7": {
                "path": str(atlas_path).replace("\\", "/"),
                "sha256": CROSSWALK.sha256_file(atlas_path),
                "records_streamed_for_non_direct_workspaces": scanned,
                "mathdb_excerpt_rows_seen": excerpt_rows,
                "scope": "complete" if max_atlas_records is None else "sample_not_for_release",
            },
        },
        "routing_policy": {
            "direct_top500_link": "Use source-declared researchWorkspaces relation before lexical inference.",
            "same_problem_reviewed": "Unique source-declared canonical link or unique frozen-OPDP title-plus-statement match.",
            "variant_or_subproblem": "Source-declared special-case or stronger-formulation link; no independent append is authorized.",
            "related_not_same": "Only topical/title overlap was retained and it failed the statement gate.",
            "appendable_no_strong_match": (
                "Open source card with a minimally well-formed question and no strong frozen-OPDP match; "
                "a routing queue label only, not novelty proof or append authorization."
            ),
            "unresolved": "Ambiguous identity, non-open source status, or insufficient concise card evidence.",
            "append_worthiness": "Always false in this inventory pending per-workspace source recovery and curator approval.",
        },
        "results": {
            "workspace_count": len(workspaces),
            "source_declared_top500_linked_workspaces": len(direct_links),
            "non_direct_workspaces": len(non_direct),
            "source_declared_link_relations": dict(sorted(link_relation_counts.items())),
            "source_status_counts": dict(sorted(source_status_counts.items())),
            "opdp_candidate_pairs_compared_for_non_direct_workspaces": compared,
            "decisions": dict(sorted(counts.items())),
            "distinct_problem_worthy_of_append_count": 0,
        },
        "output": {
            "path": str(output_path).replace("\\", "/"),
            "sha256": CROSSWALK.sha256_bytes(output_body),
            "record_count": len(rows),
        },
    }
    CROSSWALK.atomic_write(summary_path, CROSSWALK.canonical_json_bytes(summary) + b"\n")
    return summary


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--collaboration-snapshot",
        type=Path,
        required=True,
        help="frozen local collaboration-directory HTML; do not pass a live URL",
    )
    parser.add_argument(
        "--top500-snapshot",
        type=Path,
        default=Path("scripts/mathdb_recovery/raw/proofatlas_top500-v16-2026-09-19.json"),
    )
    parser.add_argument(
        "--atlas-v17", type=Path, default=Path("data/Ulam_MathDB_OPDP_Assessments_v1.7.json.gz")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/PROOFATLAS_COLLABORATION_CROSSWALK_v1.jsonl"),
    )
    parser.add_argument(
        "--summary", type=Path, default=Path("data/PROOFATLAS_COLLABORATION_INVENTORY_v1.json")
    )
    parser.add_argument("--max-atlas-records", type=int)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    try:
        if arguments.max_atlas_records is not None and arguments.max_atlas_records <= 0:
            raise CollaborationError("--max-atlas-records must be positive when supplied")
        summary = build(
            collaboration_snapshot=arguments.collaboration_snapshot,
            top500_snapshot=arguments.top500_snapshot,
            atlas_path=arguments.atlas_v17,
            output_path=arguments.output,
            summary_path=arguments.summary,
            max_atlas_records=arguments.max_atlas_records,
        )
    except CollaborationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        "wrote {workspaces:,} collaboration rows: {decisions}".format(
            workspaces=summary["results"]["workspace_count"],
            decisions=summary["results"]["decisions"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
