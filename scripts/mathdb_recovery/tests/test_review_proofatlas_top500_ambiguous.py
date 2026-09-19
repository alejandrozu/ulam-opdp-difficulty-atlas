"""Offline guards for public-safe review of ambiguous ProofAtlas crosswalk rows."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "review_proofatlas_top500_ambiguous.py"
SPEC = importlib.util.spec_from_file_location("proofatlas_ambiguous_review", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class AmbiguousProofAtlasReviewTests(unittest.TestCase):
    def write_crosswalk(self, directory: Path) -> Path:
        crosswalk = directory / "crosswalk.jsonl"
        row = {
            "schema": "opdp.proofatlas-top500-crosswalk.v1",
            "decision": "ambiguous",
            "proofatlas": {
                "problem_id": "problem.fixture",
                "release_rank": 1,
                "canonical_title": "Fixture title",
                "canonical_title_sha256": "a" * 64,
                "exact_target_sha256": "b" * 64,
            },
            "selected_candidates": [{"problem_id": 17}, {"problem_id": 18}],
        }
        crosswalk.write_text(json.dumps(row) + "\n", encoding="utf-8")
        return crosswalk

    def test_public_review_requires_a_permitted_append_outcome_and_has_no_text_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            crosswalk = self.write_crosswalk(root)
            decisions = root / "decisions.jsonl"
            decisions.write_text(
                json.dumps(
                    {
                        "proofatlas_problem_id": "problem.fixture",
                        "review_outcome": "variant_or_subproblem",
                        "distinct_problem_worthy_of_append": True,
                        "review_evidence": "The scopes are materially different.",
                        "atlas_problem_ids_reviewed": [17],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output = root / "review.jsonl"
            self.assertEqual(
                MODULE.validate_and_write_review(
                    crosswalk_path=crosswalk, decisions_path=decisions, output=output
                ),
                1,
            )
            rendered = output.read_text(encoding="utf-8")
            self.assertNotIn("raw complete target", rendered)
            self.assertNotIn("source_text", rendered)
            row = json.loads(rendered)
            self.assertEqual(row["review_outcome"], "variant_or_subproblem")
            self.assertTrue(row["distinct_problem_worthy_of_append"])

    def test_same_problem_cannot_be_marked_append_worthy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            crosswalk = self.write_crosswalk(root)
            decisions = root / "decisions.jsonl"
            decisions.write_text(
                json.dumps(
                    {
                        "proofatlas_problem_id": "problem.fixture",
                        "review_outcome": "same_problem_reviewed",
                        "distinct_problem_worthy_of_append": True,
                        "review_evidence": "Same target.",
                        "atlas_problem_ids_reviewed": [17],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(MODULE.ReviewError):
                MODULE.validate_and_write_review(
                    crosswalk_path=crosswalk,
                    decisions_path=decisions,
                    output=root / "review.jsonl",
                )

    def test_private_context_has_an_explicit_cli_gate(self) -> None:
        self.assertEqual(MODULE.main(["--emit-private-context", "ignored.jsonl"]), 2)


if __name__ == "__main__":
    unittest.main()
