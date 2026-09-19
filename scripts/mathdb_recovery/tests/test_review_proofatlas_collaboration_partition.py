"""Safety contracts for fixed-partition ProofAtlas collaboration review."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "review_proofatlas_collaboration_partition.py"
SPEC = importlib.util.spec_from_file_location("proofatlas_collaboration_partition_review", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_fixture_pair(root: Path, source_status: str = "open") -> tuple[Path, Path]:
    workbench = root / "workbench.jsonl"
    crosswalk = root / "crosswalk.jsonl"
    workbench_rows: list[dict[str, object]] = []
    crosswalk_rows: list[dict[str, object]] = []
    for number in range(1, 269):
        slug = f"fixture-{number:03d}"
        route = f"https://proofatlas.ai/collaboration/{slug}/"
        workbench_rows.append(
            {
                "schema": MODULE.WORKBENCH_SCHEMA,
                "review_key": f"fixture:{slug}",
                "workspace": {
                    "slug": slug,
                    "source_route": route,
                    "title_sha256": "a" * 64,
                    "statement_sha256": "b" * 64,
                },
                "opdp_candidates": [
                    {
                        "problem_id": number,
                        "comparison": {
                            "statement": {"source_text_complete_for_identity_evidence": True}
                        },
                    }
                ],
                "top500_source_declared_links": [],
            }
        )
        crosswalk_rows.append(
            {
                "schema": MODULE.CROSSWALK_SCHEMA,
                "workspace": {
                    "slug": slug,
                    "source_route": route,
                    "source_status_as_published": source_status,
                    "title_sha256": "a" * 64,
                    "statement_sha256": "b" * 64,
                    "title": "SECRET WORKSPACE TITLE",
                    "statement": "SECRET WORKSPACE STATEMENT",
                },
            }
        )
    workbench.write_text("".join(json.dumps(row) + "\n" for row in workbench_rows), encoding="utf-8")
    crosswalk.write_text("".join(json.dumps(row) + "\n" for row in crosswalk_rows), encoding="utf-8")
    return workbench, crosswalk


class CollaborationPartitionReviewTests(unittest.TestCase):
    def test_public_sidecar_strips_source_prose(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbench, crosswalk = write_fixture_pair(root)
            decisions = root / "decisions.jsonl"
            decisions.write_text(
                json.dumps(
                    {
                        "review_key": "fixture:fixture-001",
                        "review_outcome": "same_problem_reviewed",
                        "distinct_problem_worthy_of_append": False,
                        "reviewed_atlas_problem_ids": [1],
                        "reviewed_top500_problem_ids": [],
                        "review_evidence": "The complete candidate covers the same target.",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output = root / "review.jsonl"
            self.assertEqual(
                MODULE.validate_and_write(
                    workbench_path=workbench,
                    crosswalk_path=crosswalk,
                    decisions_path=decisions,
                    start_index=1,
                    end_index=1,
                    output=output,
                ),
                1,
            )
            rendered = output.read_text(encoding="utf-8")
            self.assertNotIn("SECRET WORKSPACE TITLE", rendered)
            self.assertNotIn("SECRET WORKSPACE STATEMENT", rendered)
            self.assertIn("source_status_as_published", rendered)

    def test_non_open_source_cannot_be_marked_append_worthy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbench, crosswalk = write_fixture_pair(root, source_status="partially_resolved")
            decisions = root / "decisions.jsonl"
            decisions.write_text(
                json.dumps(
                    {
                        "review_key": "fixture:fixture-001",
                        "review_outcome": "related_not_same",
                        "distinct_problem_worthy_of_append": True,
                        "reviewed_atlas_problem_ids": [1],
                        "reviewed_top500_problem_ids": [],
                        "review_evidence": "It is a distinct target.",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(MODULE.ReviewError):
                MODULE.validate_and_write(
                    workbench_path=workbench,
                    crosswalk_path=crosswalk,
                    decisions_path=decisions,
                    start_index=1,
                    end_index=1,
                    output=root / "review.jsonl",
                )

    def test_private_context_requires_explicit_gate(self) -> None:
        self.assertEqual(
            MODULE.main(
                [
                    "--start-index",
                    "1",
                    "--end-index",
                    "1",
                    "--emit-private-context",
                    "ignored.jsonl",
                ]
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
