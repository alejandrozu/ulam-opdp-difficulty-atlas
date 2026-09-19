"""Offline guards for the ProofAtlas exact/strong identity audit."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "review_proofatlas_top500_matches.py"
SPEC = importlib.util.spec_from_file_location("proofatlas_match_review", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ProofAtlasMatchIdentityAuditTests(unittest.TestCase):
    def write_fixture_inputs(self, directory: Path) -> tuple[Path, Path]:
        crosswalk = directory / "crosswalk.jsonl"
        matched = {
            "schema": MODULE.CROSSWALK_SCHEMA,
            "decision": "strong_match",
            "proofatlas": {
                "problem_id": "problem.fixture",
                "release_rank": 1,
                "canonical_title": "Fixture title",
                "canonical_title_sha256": "a" * 64,
                "exact_target_sha256": "b" * 64,
            },
            "selected_candidates": [{"problem_id": 17}],
        }
        unrelated = {
            "schema": MODULE.CROSSWALK_SCHEMA,
            "decision": "no_strong_match",
            "proofatlas": {
                "problem_id": "problem.unmatched",
                "release_rank": 2,
                "canonical_title": "Unmatched title",
                "canonical_title_sha256": "c" * 64,
                "exact_target_sha256": "d" * 64,
            },
            "selected_candidates": [],
        }
        crosswalk.write_text(
            json.dumps(matched) + "\n" + json.dumps(unrelated) + "\n", encoding="utf-8"
        )
        proofatlas = directory / "proofatlas.json"
        proofatlas.write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "problemId": "problem.fixture",
                            "canonicalTitle": "Fixture title",
                            "exactTarget": "Private target text must never reach public output.",
                            "releaseRank": 1,
                            "releaseStatus": "open",
                            "formalStatementSource": {"url": "https://example.test/source"},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return crosswalk, proofatlas

    def test_scope_override_and_normalization_draft_are_public_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            crosswalk, proofatlas = self.write_fixture_inputs(root)
            overrides = root / "overrides.jsonl"
            overrides.write_text(
                json.dumps(
                    {
                        "proofatlas_problem_id": "problem.fixture",
                        "review_outcome": "related_not_same",
                        "distinct_problem_worthy_of_append": True,
                        "review_evidence": "The reviewed objects have materially different scopes.",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            decisions = root / "decisions.jsonl"
            self.assertEqual(
                MODULE.write_review_input_from_overrides(
                    crosswalk_path=crosswalk,
                    proofatlas_path=proofatlas,
                    overrides_path=overrides,
                    output=decisions,
                ),
                1,
            )
            review = root / "review.jsonl"
            self.assertEqual(
                MODULE.validate_and_write_review(
                    crosswalk_path=crosswalk,
                    proofatlas_path=proofatlas,
                    decisions_path=decisions,
                    output=review,
                ),
                1,
            )
            rendered_review = review.read_text(encoding="utf-8")
            self.assertNotIn("Private target text", rendered_review)
            self.assertNotIn("source_text", rendered_review)

            normalizations = root / "normalizations.jsonl"
            normalizations.write_text(
                json.dumps(
                    {
                        "proofatlas_problem_id": "problem.fixture",
                        "curator_authored_independent_normalization": "Determine whether the independent fixture condition holds.",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            draft_input = root / "draft-input.jsonl"
            self.assertEqual(
                MODULE.write_append_draft_input_from_normalizations(
                    review_path=review,
                    normalizations_path=normalizations,
                    output=draft_input,
                ),
                1,
            )
            output = root / "draft.jsonl"
            self.assertEqual(
                MODULE.validate_append_drafts(
                    review_path=review, drafts_path=draft_input, output=output
                ),
                1,
            )
            draft = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(draft["schema"], MODULE.APPEND_DRAFT_SCHEMA)
            self.assertEqual(
                draft["statement_provenance"]["mode"], MODULE.NORMALIZATION_MODE
            )
            self.assertEqual(
                draft["release_gate"], "draft_only_requires_curator_confirmation_before_append"
            )

    def test_private_context_requires_an_explicit_cli_gate(self) -> None:
        self.assertEqual(MODULE.main(["--emit-private-context", "ignored.jsonl"]), 2)


if __name__ == "__main__":
    unittest.main()
