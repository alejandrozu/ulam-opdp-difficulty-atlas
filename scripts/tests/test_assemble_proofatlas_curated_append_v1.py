#!/usr/bin/env python3
"""Offline contract tests for ProofAtlas curator-input assembly.

All snapshots and drafts in this test are synthetic.  They deliberately use
fake routes and fabricated source prose so the tests never fetch or copy a
ProofAtlas artifact.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
ASSEMBLER_PATH = REPOSITORY / "scripts" / "assemble_proofatlas_curated_append_v1.py"
SPEC = importlib.util.spec_from_file_location("proofatlas_assembler", ASSEMBLER_PATH)
assert SPEC is not None and SPEC.loader is not None
ASSEMBLER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ASSEMBLER
SPEC.loader.exec_module(ASSEMBLER)

TOP_ID = "problem.synthetic-017"
TOP_RANK = 17
TOP_FORMAL_URL = "https://example.invalid/formal/synthetic-017"
TOP_TARGET = "UPSTREAM TOP TARGET: establish the fabricated amber relation for every synthetic object."
TOP_READER_QUESTION = "UPSTREAM TOP READER: does every synthetic object satisfy the fabricated amber relation?"
COLLAB_SLUG = "synthetic-workspace"
COLLAB_ROUTE = f"https://proofatlas.ai/collaboration/{COLLAB_SLUG}/"
COLLAB_CARD_STATEMENT = "UPSTREAM CARD STATEMENT: resolve the fabricated cobalt recurrence for every synthetic seed."


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def synthetic_top500_snapshot(path: Path) -> None:
    records = []
    for rank in range(1, 501):
        identifier = TOP_ID if rank == TOP_RANK else f"problem.synthetic-{rank:03d}"
        target = TOP_TARGET if rank == TOP_RANK else f"Synthetic upstream target {rank}: prove a distinct fabricated assertion."
        reader = TOP_READER_QUESTION if rank == TOP_RANK else f"Synthetic reader question {rank}: is the fabricated assertion true?"
        formal_url = TOP_FORMAL_URL if rank == TOP_RANK else f"https://example.invalid/formal/{rank:03d}"
        records.append(
            {
                "problemId": identifier,
                "releaseRank": rank,
                "canonicalTitle": f"Synthetic Top Title {rank}",
                "releaseStatus": "open",
                "exactTarget": target,
                "readerQuestion": reader,
                "formalStatementSource": {"url": formal_url},
            }
        )
    write_json(
        path,
        {
            "schemaVersion": "proofatlas.public-open-problem-ranking.v3",
            "releaseVersion": "synthetic",
            "releaseId": "synthetic-top500",
            "publishedAt": "2026-09-19T00:00:00Z",
            "records": records,
        },
    )


def synthetic_collaboration_snapshot(path: Path) -> None:
    cards = []
    for ordinal in range(1, 269):
        slug = COLLAB_SLUG if ordinal == 1 else f"synthetic-workspace-{ordinal:03d}"
        statement = COLLAB_CARD_STATEMENT if ordinal == 1 else f"Synthetic card statement {ordinal}: solve a distinct fabricated recurrence."
        cards.append(
            "<a data-collaboration-workspace "
            f'data-discovery-slug="{slug}" '
            f'data-discovery-sort-title="Synthetic Collaboration Title {ordinal}" '
            'data-discovery-status="open" '
            f'data-discovery-statement="{statement}" '
            f'href="./{slug}/"></a>'
        )
    path.write_text("<html><body>" + "".join(cards) + "</body></html>", encoding="utf-8")


def top500_draft(statement: str, *, formal_url: str = TOP_FORMAL_URL, status: str = "open") -> dict[str, object]:
    return {
        "schema": "opdp.proofatlas-curator-normalization-draft.v1",
        "proofatlas_problem_id": TOP_ID,
        "release_rank": TOP_RANK,
        "source_published_status": status,
        "formal_statement_source_url": formal_url,
        "curator_authored_independent_normalization": statement,
        "curator_authorship_attestation": "Synthetic test normalizer authored this sentence independently without copying upstream source prose.",
    }


def legacy_low_rank_top500_draft(statement: str) -> dict[str, object]:
    return {
        "schema": "opdp.proofatlas-curator-normalization-draft.v1",
        "proofatlas_problem_id": TOP_ID,
        "release_rank": TOP_RANK,
        "source_published_status": "open",
        "formal_statement_source_url": TOP_FORMAL_URL,
        "curator_authored_independent_normalization": statement,
        "source_text_not_copied": True,
        "draft_status": "curator_authored_normalization_pending_source_and_status_review",
    }


def collaboration_draft(statement: str, *, route: str = COLLAB_ROUTE, status: str = "open") -> dict[str, object]:
    return {
        "schema": "opdp.proofatlas.collaboration-append-curation-draft.v1",
        "source": {
            "source_id": COLLAB_SLUG,
            "source_route": route,
            "source_status_as_published": status,
        },
        "proposed_statement": statement,
        "statement_provenance": {
            "mode": "curator_authored_independent_normalization",
            "attestation": "Synthetic test normalizer authored this sentence independently without copying upstream source prose.",
        },
    }


class ProofAtlasCuratedAssemblerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.temp = Path(self.temp_directory.name)
        self.top500 = self.temp / "top500.json"
        self.collaboration = self.temp / "collaboration.html"
        synthetic_top500_snapshot(self.top500)
        synthetic_collaboration_snapshot(self.collaboration)

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def assemble(
        self,
        *,
        top_rows: list[dict[str, object]],
        collaboration_rows: list[dict[str, object]] | None = None,
        top_paths: int = 1,
        collaboration_paths: int = 1,
        include_collaboration_snapshot: bool = False,
    ) -> tuple[Path, Path]:
        top_drafts = []
        for index in range(top_paths):
            path = self.temp / f"top-{index}.jsonl"
            write_jsonl(path, top_rows)
            top_drafts.append(path)
        collaboration_drafts = []
        for index in range(collaboration_paths):
            path = self.temp / f"collaboration-{index}.jsonl"
            write_jsonl(path, collaboration_rows or [])
            collaboration_drafts.append(path)
        output = self.temp / f"output-{len(top_rows)}-{len(collaboration_rows or [])}-{top_paths}-{collaboration_paths}.jsonl"
        manifest = self.temp / f"manifest-{len(top_rows)}-{len(collaboration_rows or [])}-{top_paths}-{collaboration_paths}.json"
        ASSEMBLER.assemble(
            top500_snapshot_path=self.top500,
            top500_drafts=top_drafts,
            collaboration_snapshot_path=(
                self.collaboration if collaboration_rows is not None or include_collaboration_snapshot else None
            ),
            collaboration_drafts=collaboration_drafts if collaboration_rows is not None else [],
            output=output,
            manifest=manifest,
            approve=True,
        )
        return output, manifest

    def test_mixed_sources_use_disjoint_stable_namespaces_without_source_prose(self) -> None:
        top_statement = "For every fabricated amber structure, derive a curator-defined bounded witness from the designated datum."
        collaboration_statement = "Every fabricated cobalt seed admits a finite curator-selected transition certificate."
        output, manifest = self.assemble(
            top_rows=[top500_draft(top_statement)],
            collaboration_rows=[collaboration_draft(collaboration_statement)],
        )
        rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["problem"]["namespace_number"] for row in rows], [TOP_RANK, 1_000_001])
        self.assertEqual(
            [row["problem"]["source_id"] for row in rows],
            [f"top500--{TOP_ID}", f"collaboration--{COLLAB_SLUG}"],
        )
        self.assertNotEqual(rows[0]["problem"]["namespace_number"], rows[1]["problem"]["namespace_number"])
        body = output.read_text(encoding="utf-8")
        self.assertNotIn(TOP_TARGET, body)
        self.assertNotIn(TOP_READER_QUESTION, body)
        self.assertNotIn(COLLAB_CARD_STATEMENT, body)
        result = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(result["results"]["top500_entries"], 1)
        self.assertEqual(result["results"]["collaboration_entries"], 1)

    def test_rejects_draft_source_metadata_mismatch(self) -> None:
        with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "formal URL differs"):
            self.assemble(
                top_rows=[top500_draft("A synthetic independent amber normalization.", formal_url="https://example.invalid/wrong")]
            )
        with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "route/status differs"):
            self.assemble(
                top_rows=[top500_draft("A synthetic independent amber normalization.")],
                collaboration_rows=[collaboration_draft("A synthetic independent cobalt normalization.", status="partially_resolved")],
            )

    def test_rejects_top500_normalization_without_its_own_attestation(self) -> None:
        draft = top500_draft("A synthetic independent amber normalization.")
        del draft["curator_authorship_attestation"]
        with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "curator_authorship_attestation"):
            self.assemble(top_rows=[draft])

    def test_accepts_legacy_low_rank_attestation_contract(self) -> None:
        output, _ = self.assemble(
            top_rows=[legacy_low_rank_top500_draft("A synthetic independently authored amber normalization.")]
        )
        row = json.loads(output.read_text(encoding="utf-8"))
        attestation = row["problem"]["curation"]["draft_attestation"]
        self.assertIn("source_text_not_copied=true", attestation)

    def test_rejects_duplicate_top500_and_collaboration_selections(self) -> None:
        with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "duplicate Top 500 curated selection"):
            self.assemble(
                top_rows=[top500_draft("A synthetic independent amber normalization.")],
                top_paths=2,
            )
        with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "duplicate collaboration curated selection"):
            self.assemble(
                top_rows=[top500_draft("A synthetic independent amber normalization.")],
                collaboration_rows=[collaboration_draft("A synthetic independent cobalt normalization.")],
                collaboration_paths=2,
            )

    def test_rejects_copied_upstream_target_or_card_statement(self) -> None:
        with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "materially copies frozen ProofAtlas source statement prose"):
            self.assemble(top_rows=[top500_draft(TOP_TARGET)])
        with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "materially copies frozen ProofAtlas source statement prose"):
            self.assemble(top_rows=[top500_draft("Curator introduction: " + TOP_TARGET)])
        with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "materially copies frozen ProofAtlas source statement prose"):
            self.assemble(
                top_rows=[top500_draft("A synthetic independent amber normalization.")],
                collaboration_rows=[collaboration_draft(COLLAB_CARD_STATEMENT)],
            )

    def test_rejects_copying_a_different_frozen_proofatlas_record(self) -> None:
        # The selected record's own source prose is unrelated to the copied
        # text in both cases.  This guards against cross-record leakage, which
        # the per-record check alone would miss.
        with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "materially copies frozen ProofAtlas source statement prose"):
            self.assemble(
                top_rows=[top500_draft(COLLAB_CARD_STATEMENT)],
                include_collaboration_snapshot=True,
            )
        with self.assertRaisesRegex(ASSEMBLER.AssemblyError, "materially copies frozen ProofAtlas source statement prose"):
            self.assemble(
                top_rows=[top500_draft("A synthetic independent amber normalization.")],
                collaboration_rows=[collaboration_draft(TOP_READER_QUESTION)],
            )


if __name__ == "__main__":
    unittest.main()
