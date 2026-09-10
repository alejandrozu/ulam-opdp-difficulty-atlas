"""Offline contract tests for the rate-limited arXiv metadata acquirer."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquire_arxiv_metadata as acquisition  # noqa: E402


ATOM_SAMPLE = b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<feed xmlns=\"http://www.w3.org/2005/Atom\" xmlns:arxiv=\"http://arxiv.org/schemas/atom\" xmlns:opensearch=\"http://a9.com/-/spec/opensearch/1.1/\">
  <id>http://export.arxiv.org/api/query</id><updated>2026-09-10T00:00:00Z</updated>
  <opensearch:totalResults>2</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/2401.00001v3</id>
    <updated>2025-01-02T00:00:00Z</updated><published>2024-01-01T00:00:00Z</published>
    <title> A   sample theorem </title><summary> An abstract\nwith space. </summary>
    <author><name>Alice</name></author><category term=\"math.AG\" />
    <arxiv:primary_category term=\"math.AG\" /><arxiv:comment>Withdrawn by authors.</arxiv:comment>
    <arxiv:license>https://creativecommons.org/licenses/by/4.0/</arxiv:license>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/math/0101001v2</id>
    <updated>2001-01-03T00:00:00Z</updated><published>2001-01-01T00:00:00Z</published>
    <title>Legacy item</title><summary>Legacy abstract.</summary>
    <author><name>Bob</name></author><category term=\"math.NT\" />
    <arxiv:primary_category term=\"math.NT\" />
  </entry>
</feed>
"""


class ArxivMetadataAcquisitionTests(unittest.TestCase):
    def make_source_manifest(self, directory: Path) -> Path:
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
                "source_key": "arxiv:math/0101001",
                "source_type": "arxiv",
                "arxiv_id": "math/0101001",
                "canonical_url": "https://arxiv.org/abs/math/0101001",
                "observed_versions": [],
                "target_task_count": 1,
                "source_lead_count": 1,
                "acquisition_policy": "acquire_once_via_approved_arxiv_bulk_channel_then_match_each_target",
            },
        ]
        source_rows = b"".join(acquisition.canonical_json_bytes(row) + b"\n" for row in rows)
        (directory / "arxiv_sources.jsonl").write_bytes(source_rows)
        manifest = {
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
        path = directory / "arxiv_source_manifest.json"
        path.write_bytes(acquisition.canonical_json_bytes(manifest) + b"\n")
        return path

    def test_dry_run_creates_exact_two_id_plan_without_calling_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / "run"
            run_dir.mkdir()
            (run_dir / "run.json").write_text('{"schema":"opdp.mathdb.recovery-run.v1"}\n', encoding="utf-8")
            source_manifest = self.make_source_manifest(root)
            output = run_dir / "metadata-sample"
            original_fetch = acquisition.api_fetch
            try:
                def forbidden_fetch(*_args: object, **_kwargs: object) -> None:
                    raise AssertionError("dry run attempted a network request")

                acquisition.api_fetch = forbidden_fetch  # type: ignore[assignment]
                exit_code = acquisition.main(
                    [
                        "--run-dir", str(run_dir),
                        "--source-manifest", str(source_manifest),
                        "--output-dir", str(output),
                        "--max-ids", "2",
                    ]
                )
            finally:
                acquisition.api_fetch = original_fetch  # type: ignore[assignment]
            self.assertEqual(exit_code, 0)
            plan = json.loads((output / "request_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["selection"]["scope"], "complete")
            self.assertEqual(plan["batches"][0]["arxiv_ids"], ["2401.00001", "math/0101001"])
            self.assertEqual(
                plan["batches"][0]["request_url"],
                "https://export.arxiv.org/api/query?id_list=2401.00001,math/0101001&max_results=2",
            )
            self.assertFalse((output / "responses").exists())

    def test_fifty_id_batch_explicitly_requests_fifty_results(self) -> None:
        identifiers = [f"2401.{number:05d}" for number in range(1, 51)]
        request_url = acquisition.build_request_url(identifiers)
        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(request_url).query)
        self.assertEqual(parsed["id_list"], [",".join(identifiers)])
        self.assertEqual(parsed["max_results"], ["50"])
        self.assertTrue(request_url.endswith("&max_results=50"))

    def test_atom_parser_preserves_metadata_and_marks_nondecisive_status_fields(self) -> None:
        entries, feed = acquisition.parse_atom_response(
            ATOM_SAMPLE,
            {"2401.00001": ["v1"], "math/0101001": []},
        )
        self.assertEqual(feed["opensearch_total_results"], "2")
        self.assertEqual(entries[0]["title"], "A sample theorem")
        self.assertEqual(entries[0]["abstract"], "An abstract with space.")
        self.assertEqual(entries[0]["version_info"]["api_returned_version"], "v3")
        self.assertEqual(entries[0]["license_info"]["license_urls"], ["https://creativecommons.org/licenses/by/4.0/"])
        self.assertEqual(entries[0]["withdrawal_info"]["status"], "possible_withdrawal_indicator")
        self.assertEqual(entries[1]["arxiv_id"], "math/0101001")
        validation = acquisition.validate_response_entries(entries, ["2401.00001", "math/0101001"])
        self.assertEqual(validation["status"], "complete_exact_id_match")

    def test_response_envelope_is_hash_checked_on_resume(self) -> None:
        batch = {
            "index": 1,
            "batch_key": "a" * 64,
            "arxiv_ids": ["2401.00001", "math/0101001"],
            "observed_versions": {"2401.00001": ["v1"], "math/0101001": []},
            "request_url": "https://export.arxiv.org/api/query?id_list=2401.00001,math/0101001",
            "response_file": "responses/batch-00001-aaaaaaaaaaaaaaaa.json",
        }
        record = acquisition.make_response_record(
            batch,
            "2026-09-10T00:00:00Z",
            200,
            "https://export.arxiv.org/api/query?id_list=2401.00001,math/0101001",
            {"content-type": "application/atom+xml"},
            ATOM_SAMPLE,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / batch["response_file"]
            acquisition.write_immutable_json(path, record, "test response")
            loaded = acquisition.load_response_record(path, batch)
            self.assertEqual(loaded["response"]["body_sha256"], acquisition.sha256_bytes(ATOM_SAMPLE))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["response"]["body_sha256"] = "0" * 64
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(acquisition.AcquisitionError):
                acquisition.load_response_record(path, batch)


if __name__ == "__main__":
    unittest.main()
