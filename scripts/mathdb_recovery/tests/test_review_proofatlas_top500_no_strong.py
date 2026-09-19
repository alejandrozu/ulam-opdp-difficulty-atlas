"""Offline guards for the bounded no-strong-match ProofAtlas reviewer."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "review_proofatlas_top500_no_strong.py"
SPEC = importlib.util.spec_from_file_location("proofatlas_no_strong_review", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class NoStrongProofAtlasReviewTests(unittest.TestCase):
    def write_fixture_inputs(self, directory: Path, status: str = "open") -> tuple[Path, Path]:
        index = directory / "index.jsonl"
        index.write_text(
            json.dumps(
                {
                    "schema": MODULE.INDEX_SCHEMA,
                    "proofatlas": {
                        "problem_id": "problem.fixture",
                        "release_rank": 250,
                        "canonical_title": "Fixture title",
                        "canonical_title_sha256": "a" * 64,
                        "exact_target_sha256": "b" * 64,
                    },
                    "candidates": [{"problem_id": 17}],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        proofatlas = directory / "proofatlas.json"
        proofatlas.write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "problemId": "problem.fixture",
                            "canonicalTitle": "Fixture title",
                            "exactTarget": "Private source target that must not enter the sidecar.",
                            "releaseRank": 250,
                            "releaseStatus": status,
                            "formalStatementSource": {"url": "https://example.test/source"},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return index, proofatlas

    def test_public_sidecar_and_append_draft_are_content_minimized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index, proofatlas = self.write_fixture_inputs(root)
            decisions = root / "decisions.jsonl"
            decisions.write_text(
                json.dumps(
                    {
                        "proofatlas_problem_id": "problem.fixture",
                        "review_outcome": "appendable_no_strong_match",
                        "distinct_problem_worthy_of_append": True,
                        "review_evidence": "The separately scoped object has no retained equivalent.",
                        "atlas_problem_ids_reviewed": [17],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            review = root / "review.jsonl"
            self.assertEqual(
                MODULE.validate_and_write_review(
                    index_path=index,
                    proofatlas_path=proofatlas,
                    decisions_path=decisions,
                    output=review,
                    rank_min=250,
                    rank_max=250,
                ),
                1,
            )
            rendered_review = review.read_text(encoding="utf-8")
            self.assertNotIn("Private source target", rendered_review)
            self.assertNotIn("source_text", rendered_review)

            drafts = root / "drafts.jsonl"
            drafts.write_text(
                json.dumps(
                    {
                        "proofatlas": {
                            "problem_id": "problem.fixture",
                            "release_rank": 250,
                            "formal_statement_source_url": "https://example.test/source",
                            "release_status_as_published": "open",
                        },
                        "statement_provenance": {
                            "mode": MODULE.NORMALIZATION_MODE,
                            "attestation": "Independent curator-authored paraphrase; it does not reproduce source prose.",
                        },
                        "curator_authored_independent_normalization": "Decide whether the independently stated fixture property holds.",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            draft_output = root / "append-drafts.jsonl"
            self.assertEqual(
                MODULE.validate_append_drafts(review_path=review, drafts_path=drafts, output=draft_output),
                1,
            )
            draft = json.loads(draft_output.read_text(encoding="utf-8"))
            self.assertEqual(draft["statement_provenance"]["mode"], MODULE.NORMALIZATION_MODE)
            self.assertEqual(
                draft["release_gate"], "draft_only_requires_curator_confirmation_before_append"
            )

    def test_nonopen_status_requires_an_explicit_note(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index, proofatlas = self.write_fixture_inputs(root, "open_with_solved_subcases")
            decisions = root / "decisions.jsonl"
            decisions.write_text(
                json.dumps(
                    {
                        "proofatlas_problem_id": "problem.fixture",
                        "review_outcome": "same_problem_reviewed",
                        "distinct_problem_worthy_of_append": False,
                        "review_evidence": "The reviewed frozen record has the same scope.",
                        "atlas_problem_ids_reviewed": [17],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(MODULE.ReviewError):
                MODULE.validate_and_write_review(
                    index_path=index,
                    proofatlas_path=proofatlas,
                    decisions_path=decisions,
                    output=root / "review.jsonl",
                    rank_min=250,
                    rank_max=250,
                )

    def test_private_context_has_an_explicit_cli_gate(self) -> None:
        self.assertEqual(MODULE.main(["--emit-private-context", "ignored.jsonl"]), 2)


if __name__ == "__main__":
    unittest.main()
