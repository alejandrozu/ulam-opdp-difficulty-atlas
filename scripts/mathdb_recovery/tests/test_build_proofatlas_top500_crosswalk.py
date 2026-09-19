"""Offline contract tests for the conservative ProofAtlas Top 500 matcher."""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "build_proofatlas_top500_crosswalk.py"
SPEC = importlib.util.spec_from_file_location("proofatlas_crosswalk", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeResponse:
    def __init__(self, body: bytes, url: str) -> None:
        self._body = body
        self._url = url
        self.status = 200
        self.headers = {"Content-Type": "application/json"}

    def read(self, _limit: int | None = None) -> bytes:
        return self._body

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> bool:
        return False


def proofatlas_fixture() -> dict[str, object]:
    records: list[dict[str, object]] = []
    named = [
        (
            "problem.alpha-theorem",
            "Alpha Theorem",
            "For every graph G with n vertices, the chromatic number is at most n.",
        ),
        (
            "problem.beta-conjecture",
            "Beta Conjecture",
            "For every finite set A, property beta holds.",
        ),
        (
            "problem.gamma-problem",
            "Gamma Problem",
            "For every positive integer n, gamma n is prime.",
        ),
    ]
    for rank in range(1, 501):
        if rank <= len(named):
            problem_id, title, statement = named[rank - 1]
        else:
            problem_id = f"problem.unrelated-{rank}"
            title = f"Unrelated Problem {rank}"
            statement = f"Determine whether unrelated condition {rank} holds for all inputs."
        records.append(
            {
                "problemId": problem_id,
                "canonicalTitle": title,
                "exactTarget": statement,
                "releaseRank": rank,
                "releaseStatus": "open",
                "formalStatementSource": {"url": f"https://example.test/{rank}"},
            }
        )
    return {
        "schemaVersion": MODULE.TOP500_SCHEMA,
        "publicationId": "fixture.top500",
        "releaseId": "fixture.release",
        "releaseVersion": 1,
        "publishedAt": "2026-09-19T00:00:00Z",
        "editionDate": "2026-09-19",
        "recordCount": 500,
        "records": records,
    }


def v17_record(
    problem_id: int, title: str, statement: str, text_mode: str = "verbatim_source_snapshot"
) -> dict[str, object]:
    return {
        "problem_id": problem_id,
        "problem_number": f"TEST-{problem_id}",
        "title": title,
        "catalog_status": "open",
        "source_text": {"statement": statement, "text_mode": text_mode},
        "provenance": {"source_collection_label": "Test source"},
        "flags": ["source_excerpt_only"] if text_mode == "mathdb_public_list_excerpt" else [],
    }


class ProofAtlasTop500CrosswalkTests(unittest.TestCase):
    def write_fixture_inputs(self, directory: Path) -> tuple[Path, Path]:
        proofatlas_path = directory / "proofatlas.json"
        proofatlas_path.write_text(json.dumps(proofatlas_fixture()), encoding="utf-8")
        atlas_path = directory / "atlas.json.gz"
        atlas = {
            "format_name": "fixture",
            "records": [
                v17_record(
                    1,
                    "Alpha Theorem Problem",
                    "For every graph G with n vertices, the chromatic number is at most n.",
                ),
                v17_record(
                    2,
                    "Beta Conjecture",
                    "For every finite set A, property beta holds.",
                ),
                v17_record(
                    3,
                    "Beta Conjecture",
                    "For every finite set A, property beta holds.",
                ),
                v17_record(
                    4,
                    "Gamma Problem",
                    "For every positive integer n, gamma n is prime.",
                    "mathdb_public_list_excerpt",
                ),
            ],
        }
        with gzip.open(atlas_path, "wt", encoding="utf-8") as handle:
            json.dump(atlas, handle)
        return proofatlas_path, atlas_path

    def test_local_crosswalk_is_streamed_safe_and_does_not_copy_statements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proofatlas_path, atlas_path = self.write_fixture_inputs(root)
            before = MODULE.sha256_file(atlas_path)
            output = root / "crosswalk.jsonl"
            summary = root / "inventory.json"
            exit_code = MODULE.main(
                [
                    "--proofatlas-json",
                    str(proofatlas_path),
                    "--atlas-v17",
                    str(atlas_path),
                    "--output",
                    str(output),
                    "--summary",
                    str(summary),
                ]
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(MODULE.sha256_file(atlas_path), before)
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), 500)
            self.assertEqual(rows[0]["decision"], "exact_match")
            self.assertEqual(rows[1]["decision"], "ambiguous")
            self.assertEqual(rows[2]["decision"], "strong_match")
            self.assertEqual(
                rows[2]["selected_candidates"][0]["source_statement_completeness"],
                "excerpt_not_guaranteed_complete",
            )
            self.assertEqual(rows[3]["decision"], "no_strong_match")
            self.assertEqual(rows[3]["newness_inference"], "not_determined_by_this_crosswalk")
            public_body = output.read_text(encoding="utf-8")
            self.assertNotIn("For every graph G with n vertices", public_body)
            self.assertNotIn("For every finite set A, property beta holds", public_body)
            inventory = json.loads(summary.read_text(encoding="utf-8"))
            self.assertFalse(inventory["non_destructive_contract"]["opdp_payload_modified"])
            self.assertFalse(
                inventory["non_destructive_contract"]["proofatlas_exact_targets_retained_in_public_sidecars"]
            )
            self.assertEqual(inventory["results"]["decisions"]["exact_match"], 1)

    def test_fetch_flag_requires_explicit_network_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.object(MODULE.urllib.request, "urlopen") as urlopen:
                exit_code = MODULE.main(
                    ["--fetch-proofatlas", "--raw-snapshot", str(root / "raw.json")]
                )
            self.assertEqual(exit_code, 2)
            urlopen.assert_not_called()

    def test_explicit_fetch_writes_new_snapshot_and_never_overwrites_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            body = json.dumps(proofatlas_fixture()).encode("utf-8")
            snapshot = root / "raw.json"
            with mock.patch.object(
                MODULE.urllib.request,
                "urlopen",
                return_value=FakeResponse(body, MODULE.DEFAULT_PROOFATLAS_URL),
            ):
                metadata = MODULE.fetch_snapshot(MODULE.DEFAULT_PROOFATLAS_URL, snapshot, 1)
            self.assertTrue(snapshot.is_file())
            self.assertEqual(snapshot.read_bytes(), body)
            self.assertEqual(metadata["retrieval_mode"], "explicit_network_fetch")
            with self.assertRaises(MODULE.CrosswalkError):
                with mock.patch.object(
                    MODULE.urllib.request,
                    "urlopen",
                    return_value=FakeResponse(body, MODULE.DEFAULT_PROOFATLAS_URL),
                ):
                    MODULE.fetch_snapshot(MODULE.DEFAULT_PROOFATLAS_URL, snapshot, 1)


if __name__ == "__main__":
    unittest.main()
