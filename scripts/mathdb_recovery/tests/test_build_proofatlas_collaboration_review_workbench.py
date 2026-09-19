"""Guards for the public-safe ProofAtlas collaboration review workbench."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "build_proofatlas_collaboration_review_workbench.py"
SPEC = importlib.util.spec_from_file_location("proofatlas_collaboration_workbench", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def comparison() -> dict[str, object]:
    return {
        "composite_quality": 0.5,
        "exact_identity_candidate": False,
        "strong_identity_candidate": False,
        "strong_statement_evidence": False,
        "strong_title_evidence": True,
        "title_exact_after_fixed_presentation_normalization": False,
        "title": {"shared_signal_tokens": 2, "signal_f1": 0.8},
        "statement": {"shared_signal_tokens": 1, "signal_f1": 0.2},
    }


def non_direct_row(decision: str) -> dict[str, object]:
    return {
        "schema": MODULE.INPUT_SCHEMA,
        "workspace": {
            "slug": "fixture-workspace",
            "title": "SECRET WORKSPACE TITLE",
            "title_sha256": "a" * 64,
            "statement": "SECRET WORKSPACE STATEMENT",
            "statement_sha256": "b" * 64,
            "source_route": "https://proofatlas.ai/collaboration/fixture-workspace/",
        },
        "top500_source_declared_links": [],
        "matching_basis": MODULE.NON_DIRECT_BASIS,
        "decision": decision,
        "selected_opdp_candidates": [
            {
                "atlas_record_index": 7,
                "problem_id": 99,
                "problem_number": "FIX-99",
                "title": "SECRET OPDP TITLE",
                "title_sha256": "c" * 64,
                "comparison": comparison(),
            }
        ],
    }


class CollaborationWorkbenchTests(unittest.TestCase):
    def test_nonidentity_projection_strips_all_source_prose(self) -> None:
        selected = MODULE.select_row(
            non_direct_row("related_not_same"), "fixture crosswalk row"
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        rendered = json.dumps(selected, sort_keys=True)
        self.assertNotIn("SECRET WORKSPACE TITLE", rendered)
        self.assertNotIn("SECRET WORKSPACE STATEMENT", rendered)
        self.assertNotIn("SECRET OPDP TITLE", rendered)
        self.assertFalse(selected["append_authorization"])
        self.assertEqual(selected["review_bucket"], "non_direct_related_not_same")
        self.assertIsNone(MODULE.contains_forbidden_key(selected))

    def test_same_non_direct_problem_is_not_review_workbench_input(self) -> None:
        self.assertIsNone(MODULE.select_row(non_direct_row("same_problem_reviewed"), "fixture"))

    def test_source_declared_special_case_is_included_without_top500_title(self) -> None:
        raw = non_direct_row("variant_or_subproblem")
        raw["matching_basis"] = MODULE.DIRECT_BASIS
        raw["top500_source_declared_links"] = [
            {
                "top500_problem_id": "problem.fixture",
                "top500_title": "SECRET TOP500 TITLE",
                "top500_route": "https://proofatlas.ai/collaboration/fixture-workspace/",
                "scope_relation": "workspace_special_case",
            }
        ]
        raw["selected_opdp_candidates"] = []
        selected = MODULE.select_row(raw, "fixture")
        self.assertIsNotNone(selected)
        assert selected is not None
        rendered = json.dumps(selected, sort_keys=True)
        self.assertNotIn("SECRET TOP500 TITLE", rendered)
        self.assertEqual(selected["review_bucket"], "source_declared_special_or_stronger")


if __name__ == "__main__":
    unittest.main()
