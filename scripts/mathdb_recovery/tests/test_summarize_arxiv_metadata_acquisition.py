"""Synthetic, no-network tests for the content-free metadata public summary."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import acquire_arxiv_metadata as acquisition  # noqa: E402
import summarize_arxiv_metadata_acquisition as summary_tool  # noqa: E402


ATOM_TWO_ENTRIES = b"""<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<feed xmlns=\"http://www.w3.org/2005/Atom\" xmlns:arxiv=\"http://arxiv.org/schemas/atom\">
  <id>https://export.arxiv.org/api/query</id><updated>2026-09-10T00:00:00Z</updated>
  <entry>
    <id>http://arxiv.org/abs/2401.00001v2</id><updated>2024-01-02T00:00:00Z</updated><published>2024-01-01T00:00:00Z</published>
    <title>LEAK_TITLE_ONE</title><summary>LEAK_ABSTRACT_ONE</summary><author><name>LEAK_AUTHOR_ONE</name></author>
    <category term=\"math.AG\" /><arxiv:primary_category term=\"math.AG\" />
    <arxiv:comment>Withdrawn according to LEAK_COMMENT_ONE.</arxiv:comment>
    <arxiv:license>https://creativecommons.org/licenses/by/4.0/</arxiv:license>
    <arxiv:doi>10.9999/LEAK_DOI_ONE</arxiv:doi>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.00002v1</id><updated>2024-01-03T00:00:00Z</updated><published>2024-01-03T00:00:00Z</published>
    <title>LEAK_TITLE_TWO</title><summary>LEAK_ABSTRACT_TWO</summary><author><name>LEAK_AUTHOR_TWO</name></author>
    <category term=\"math.NT\" /><arxiv:primary_category term=\"math.NT\" />
  </entry>
</feed>
"""


class PublicMetadataSummaryTests(unittest.TestCase):
    def make_fixture(
        self,
        root: Path,
        *,
        batch_size: int = 50,
        completed_batch_count: int | None = None,
        add_error_event: bool = False,
    ) -> tuple[Path, Path, Path, list[Path]]:
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
            batch_size,
            False,
        )
        if completed_batch_count is None:
            completed_batch_count = len(plan["batches"])
        response_paths: list[Path] = []
        for batch in plan["batches"][:completed_batch_count]:
            # Make a response body whose entry set exactly matches each planned
            # batch.  The 1-ID incomplete fixture uses a tailored body below.
            body = ATOM_TWO_ENTRIES
            if len(batch["arxiv_ids"]) == 1:
                identifier = batch["arxiv_ids"][0]
                if identifier == "2401.00001":
                    body = ATOM_TWO_ENTRIES.split(b"<entry>", 2)[0] + b"<entry>" + ATOM_TWO_ENTRIES.split(b"<entry>", 2)[1].split(b"</entry>", 1)[0] + b"</entry></feed>"
                else:
                    second = ATOM_TWO_ENTRIES.split(b"<entry>", 2)[2]
                    body = ATOM_TWO_ENTRIES.split(b"<entry>", 1)[0] + b"<entry>" + second
            response = acquisition.make_response_record(
                batch,
                "2026-09-10T00:00:00Z",
                200,
                batch["request_url"],
                {"content-type": "application/atom+xml"},
                body,
            )
            response_path = acquisition.result_response_path(metadata_dir, batch)
            acquisition.write_immutable_json(response_path, response, "synthetic response")
            response_paths.append(response_path)
        if add_error_event:
            batch = plan["batches"][0]
            event = {
                "schema": acquisition.ERROR_SCHEMA,
                "event_id": "11111111-1111-1111-1111-111111111111",
                "occurred_at_utc": "2026-09-10T00:00:00Z",
                "phase": "http",
                "batch_index": batch["index"],
                "batch_key": batch["batch_key"],
                "attempt": 1,
                "request_url": batch["request_url"],
                "arxiv_ids": batch["arxiv_ids"],
                "request_started_at_utc": "2026-09-10T00:00:00Z",
                "error": {
                    "class": "HTTPError",
                    "message": "LEAK_ERROR_MESSAGE_429",
                    "http_status": 429,
                },
            }
            acquisition.append_error(metadata_dir / "error_ledger.jsonl", event)
        return run_dir, source_manifest, metadata_dir, response_paths

    def test_complete_summary_is_aggregate_only_and_writable_outside_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, source_manifest, metadata_dir, _ = self.make_fixture(root, add_error_event=True)
            before = {
                path.relative_to(run_dir).as_posix(): acquisition.sha256_file(path)
                for path in run_dir.rglob("*")
                if path.is_file()
            }
            summary = summary_tool.make_public_summary(run_dir, source_manifest, metadata_dir)
            after = {
                path.relative_to(run_dir).as_posix(): acquisition.sha256_file(path)
                for path in run_dir.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)
            self.assertEqual(summary["snapshot"]["snapshot_state"], "stable_quiescent")
            self.assertEqual(summary["validation"]["status"], "valid_complete")
            self.assertEqual(summary["verification"]["status"], "verified_complete")
            self.assertTrue(summary["publication_gate"]["final_artifact_eligible"])
            self.assertEqual(summary["coverage"]["requested_id_count"], 2)
            self.assertEqual(summary["coverage"]["completed_requested_id_count"], 2)
            self.assertEqual(summary["metadata_field_presence_counts"]["title_field_present"], 2)
            self.assertEqual(summary["metadata_field_presence_counts"]["abstract_field_present"], 2)
            self.assertEqual(summary["metadata_field_presence_counts"]["author_list_nonempty"], 2)
            self.assertEqual(summary["license_field_presence_counts"]["provided_by_api_response"], 1)
            self.assertEqual(summary["withdrawal_marker_aggregate"]["possible_withdrawal_indicator"], 1)
            self.assertEqual(summary["error_ledger"]["recorded_event_count"], 1)
            self.assertEqual(summary["error_ledger"]["http_status_counts"], {"http_429": 1})
            serialized = json.dumps(summary, ensure_ascii=False)
            for forbidden in (
                "2401.00001",
                "2401.00002",
                "LEAK_TITLE_ONE",
                "LEAK_TITLE_TWO",
                "LEAK_ABSTRACT_ONE",
                "LEAK_AUTHOR_ONE",
                "LEAK_ERROR_MESSAGE_429",
                "api/query?id_list",
                "<entry>",
            ):
                self.assertNotIn(forbidden, serialized)
            output = root / "public" / "aggregate.json"
            digest = summary_tool.write_immutable_artifact(output, summary, run_dir)
            self.assertEqual(digest, acquisition.sha256_file(output))
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), summary)

    def test_incomplete_run_is_preview_only_and_cannot_write_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, source_manifest, metadata_dir, _ = self.make_fixture(
                root,
                batch_size=1,
                completed_batch_count=1,
            )
            summary = summary_tool.make_public_summary(run_dir, source_manifest, metadata_dir)
            self.assertEqual(summary["validation"]["status"], "valid_incomplete")
            self.assertEqual(summary["verification"]["status"], "verified_incomplete")
            self.assertFalse(summary["publication_gate"]["final_artifact_eligible"])
            self.assertEqual(summary["coverage"]["requested_id_count"], 2)
            self.assertEqual(summary["coverage"]["completed_requested_id_count"], 1)
            self.assertEqual(summary["coverage"]["pending_requested_id_count"], 1)
            output = root / "public" / "must-not-exist.json"
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                exit_code = summary_tool.main(
                    [
                        "--run-dir", str(run_dir),
                        "--source-manifest", str(source_manifest),
                        "--metadata-dir", str(metadata_dir),
                        "--allow-incomplete-preview",
                        "--output", str(output),
                    ]
                )
            self.assertEqual(exit_code, 2)
            self.assertFalse(output.exists())

    def test_writer_lock_blocks_dynamic_reads_and_does_not_expose_lock_contents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, source_manifest, metadata_dir, _ = self.make_fixture(root)
            (metadata_dir / ".acquisition.lock").write_text(
                '{"pid": 987654, "token": "LEAK_LOCK_TOKEN"}\n', encoding="utf-8"
            )
            with mock.patch.object(
                summary_tool.validator,
                "validate_acquisition",
                side_effect=AssertionError("validator must not run while a lock is present"),
            ):
                summary = summary_tool.make_public_summary(run_dir, source_manifest, metadata_dir)
            self.assertEqual(summary["snapshot"]["snapshot_state"], "writer_lock_present")
            self.assertEqual(summary["verification"]["status"], "blocked_writer_lock_present")
            self.assertIsNone(summary["coverage"])
            self.assertNotIn("LEAK_LOCK_TOKEN", json.dumps(summary))
            self.assertNotIn("987654", json.dumps(summary))

    def test_torn_error_ledger_warning_is_not_publishable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, source_manifest, metadata_dir, _ = self.make_fixture(root)
            (metadata_dir / "error_ledger.jsonl").write_bytes(b'{"schema":"partial"')
            summary = summary_tool.make_public_summary(run_dir, source_manifest, metadata_dir)
            self.assertEqual(summary["snapshot"]["snapshot_state"], "stable_quiescent")
            self.assertEqual(summary["validation"]["status"], "valid_complete")
            self.assertGreater(summary["validation"]["warning_count"], 0)
            self.assertEqual(summary["verification"]["status"], "blocked_validator_warning")
            self.assertFalse(summary["publication_gate"]["final_artifact_eligible"])
            self.assertIsNone(summary["coverage"])

    def test_invalid_envelope_suppresses_aggregates_and_cannot_leak_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, source_manifest, metadata_dir, response_paths = self.make_fixture(root)
            payload = json.loads(response_paths[0].read_text(encoding="utf-8"))
            payload["content_scope"]["raw_full_text_requested"] = True
            response_paths[0].write_text(json.dumps(payload), encoding="utf-8")
            summary = summary_tool.make_public_summary(run_dir, source_manifest, metadata_dir)
            self.assertEqual(summary["validation"]["status"], "invalid")
            self.assertEqual(summary["verification"]["status"], "blocked_integrity_error")
            self.assertIsNone(summary["metadata_field_presence_counts"])
            self.assertNotIn("LEAK_TITLE_ONE", json.dumps(summary))

    def test_changed_snapshot_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir, source_manifest, metadata_dir, _ = self.make_fixture(root)
            actual_capture = summary_tool.capture_evidence_snapshot
            calls = 0

            def changed_capture(*args: object, **kwargs: object) -> tuple[str, dict[str, str]]:
                nonlocal calls
                calls += 1
                digest, bindings = actual_capture(*args, **kwargs)  # type: ignore[arg-type]
                return (digest if calls == 1 else "0" * 64), bindings

            with mock.patch.object(summary_tool, "capture_evidence_snapshot", side_effect=changed_capture):
                summary = summary_tool.make_public_summary(run_dir, source_manifest, metadata_dir)
            self.assertEqual(summary["snapshot"]["snapshot_state"], "changed_during_read")
            self.assertEqual(summary["verification"]["status"], "blocked_input_changed_during_read")
            self.assertIsNone(summary["coverage"])


if __name__ == "__main__":
    unittest.main()
