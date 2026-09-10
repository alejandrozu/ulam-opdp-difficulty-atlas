"""Synthetic, no-network tests for the read-only metadata-run validator."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquire_arxiv_metadata as acquisition  # noqa: E402
import validate_arxiv_metadata_acquisition as validator  # noqa: E402


ATOM_TWO_ENTRIES = b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<feed xmlns=\"http://www.w3.org/2005/Atom\" xmlns:arxiv=\"http://arxiv.org/schemas/atom\">
  <id>https://export.arxiv.org/api/query</id><updated>2026-09-10T00:00:00Z</updated>
  <entry>
    <id>http://arxiv.org/abs/2401.00001v2</id><updated>2024-01-02T00:00:00Z</updated><published>2024-01-01T00:00:00Z</published>
    <title>First item</title><summary>First metadata-only abstract.</summary><author><name>Alice</name></author>
    <category term=\"math.AG\" /><arxiv:primary_category term=\"math.AG\" />
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.00002v1</id><updated>2024-01-03T00:00:00Z</updated><published>2024-01-03T00:00:00Z</published>
    <title>Second item</title><summary>Second metadata-only abstract.</summary><author><name>Bob</name></author>
    <category term=\"math.NT\" /><arxiv:primary_category term=\"math.NT\" />
  </entry>
</feed>
"""


class MetadataAcquisitionValidatorTests(unittest.TestCase):
    def make_fixture(self, root: Path) -> tuple[Path, Path, Path, Path]:
        run_dir = root / "recovery-run"
        run_dir.mkdir()
        (run_dir / "run.json").write_bytes(
            acquisition.canonical_json_bytes({"schema": "opdp.mathdb.recovery-run.v1"}) + b"\n"
        )
        rows = [
            {
                "schema": acquisition.SOURCE_ROW_SCHEMA,
                "source_key": "arxiv:2401.00001",
                "source_type": "arxiv",
                "arxiv_id": "2401.00001",
                "canonical_url": "https://arxiv.org/abs/2401.00001",
                "observed_versions": ["v1"],
                "target_task_count": 1,
                "source_lead_count": 1,
                "acquisition_policy": "acquire_once_via_approved_arxiv_bulk_channel_then_match_each_target",
            },
            {
                "schema": acquisition.SOURCE_ROW_SCHEMA,
                "source_key": "arxiv:2401.00002",
                "source_type": "arxiv",
                "arxiv_id": "2401.00002",
                "canonical_url": "https://arxiv.org/abs/2401.00002",
                "observed_versions": [],
                "target_task_count": 1,
                "source_lead_count": 1,
                "acquisition_policy": "acquire_once_via_approved_arxiv_bulk_channel_then_match_each_target",
            },
        ]
        source_rows = b"".join(acquisition.canonical_json_bytes(row) + b"\n" for row in rows)
        source_dir = root / "source-manifest"
        source_dir.mkdir()
        (source_dir / "arxiv_sources.jsonl").write_bytes(source_rows)
        source_manifest = source_dir / "arxiv_source_manifest.json"
        source_manifest.write_bytes(
            acquisition.canonical_json_bytes(
                {
                    "schema": acquisition.SOURCE_MANIFEST_SCHEMA,
                    "outputs": {
                        "arxiv_sources": {
                            "path": "arxiv_sources.jsonl",
                            "sha256": acquisition.sha256_bytes(source_rows),
                            "count": len(rows),
                        }
                    },
                    "summary": {"unique_arxiv_sources": len(rows)},
                }
            )
            + b"\n"
        )
        _, manifest_sha, rows_sha, selected = acquisition.read_source_rows(source_manifest)
        metadata_dir = run_dir / "metadata"
        plan, _ = acquisition.prepare_output(
            metadata_dir,
            run_dir,
            source_manifest,
            manifest_sha,
            rows_sha,
            selected,
            "all_manifest_sources",
            50,
            False,
        )
        batch = plan["batches"][0]
        response = acquisition.make_response_record(
            batch,
            "2026-09-10T00:00:00Z",
            200,
            batch["request_url"],
            {"content-type": "application/atom+xml"},
            ATOM_TWO_ENTRIES,
        )
        response_path = acquisition.result_response_path(metadata_dir, batch)
        acquisition.write_immutable_json(response_path, response, "synthetic response")
        return run_dir, source_manifest, metadata_dir, response_path

    def test_valid_complete_run_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, source_manifest, metadata_dir, _ = self.make_fixture(Path(temporary))
            before = {
                path.relative_to(run_dir).as_posix(): acquisition.sha256_file(path)
                for path in run_dir.rglob("*")
                if path.is_file()
            }
            report = validator.validate_acquisition(run_dir, source_manifest, metadata_dir, 25)
            after = {
                path.relative_to(run_dir).as_posix(): acquisition.sha256_file(path)
                for path in run_dir.rglob("*")
                if path.is_file()
            }
            self.assertTrue(report["read_only"])
            self.assertEqual(report["status"], "valid_complete")
            self.assertEqual(report["findings"]["error_count"], 0)
            self.assertEqual(before, after)

    def test_rejects_envelope_that_claims_full_text_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, source_manifest, metadata_dir, response_path = self.make_fixture(Path(temporary))
            payload = json.loads(response_path.read_text(encoding="utf-8"))
            payload["content_scope"]["raw_full_text_requested"] = True
            response_path.write_text(json.dumps(payload), encoding="utf-8")
            report = validator.validate_acquisition(run_dir, source_manifest, metadata_dir, 25)
            self.assertEqual(report["status"], "invalid")
            self.assertGreater(report["findings"]["error_count"], 0)
            self.assertIn("RESPONSE_ENVELOPE", {item["code"] for item in report["findings"]["examples"]})

    def test_rejects_plan_with_missing_max_results_parameter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir, source_manifest, metadata_dir, _ = self.make_fixture(Path(temporary))
            plan_path = metadata_dir / "request_plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["batches"][0]["request_url"] = "https://export.arxiv.org/api/query?id_list=2401.00001,2401.00002"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            report = validator.validate_acquisition(run_dir, source_manifest, metadata_dir, 25)
            self.assertEqual(report["status"], "invalid")
            codes = {item["code"] for item in report["findings"]["examples"]}
            self.assertTrue({"PLAN_HASH", "BATCH_REQUEST_URL"} & codes)


if __name__ == "__main__":
    unittest.main()
