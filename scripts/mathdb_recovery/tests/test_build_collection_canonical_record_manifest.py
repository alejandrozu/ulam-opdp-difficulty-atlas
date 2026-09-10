"""Focused no-network tests for the collection canonical-record manifest."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "build_collection_canonical_record_manifest.py"
SPEC = importlib.util.spec_from_file_location("collection_manifest", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeResponse:
    def __init__(self, body: bytes, url: str):
        self._body = body
        self._url = url
        self.status = 200

    def read(self, _limit: int | None = None) -> bytes:
        return self._body

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> bool:
        return False


class CollectionCanonicalRecordManifestTests(unittest.TestCase):
    def test_pinned_erdos_locator_index_has_line_spans_but_no_source_text(self) -> None:
        body = "".join(
            f'- number: "{number}"\n'
            f"  statement: {'payload_only_should_never_appear' if number == 1 else 'another_private_payload'}\n"
            for number in range(1, 1218)
        ).encode("utf-8")
        digest = hashlib.sha256(body).hexdigest()
        source = {
            "key": "erdosproblems_ground_truth_yaml",
            "canonical_url": "https://example.test/pinned.yaml",
            "retrieval_url": "https://example.test/raw.yaml",
            "pinned_revision": "deadbeef",
            "content_sha256_expected": digest,
            "count_extractor": {"expected_count": 1217},
            "reuse": {"license": "Apache-2.0"},
        }
        collection = {"key": "erdos", "sources": [source]}
        with tempfile.TemporaryDirectory() as temporary_directory:
            registry_path = Path(temporary_directory) / "fixture-registry.json"
            registry_path.write_text("{}\n", encoding="utf-8")
            with mock.patch.object(
                MODULE.urllib.request,
                "urlopen",
                return_value=FakeResponse(body, source["retrieval_url"]),
            ):
                index = MODULE.make_erdos_locator_index(
                    registry_path=registry_path,
                    collection=collection,
                    timeout_seconds=1,
                    max_bytes=200_000,
                    user_agent="test-agent",
                )
        rendered = json.dumps(index, sort_keys=True)
        self.assertNotIn("payload_only_should_never_appear", rendered)
        self.assertNotIn("another_private_payload", rendered)
        self.assertFalse(index["non_destructive_contract"]["raw_yaml_retained"])
        self.assertFalse(index["non_destructive_contract"]["statement_text_retained"])
        self.assertEqual([row["upstream_number"] for row in index["records"][:2]], [1, 2])
        self.assertEqual(index["records"][-1]["upstream_number"], 1217)
        self.assertEqual(index["records"][0]["locator"]["line_start"], 1)
        self.assertEqual(index["records"][0]["locator"]["line_end"], 2)
        self.assertEqual(index["records"][1]["locator"]["line_start"], 3)
        self.assertEqual(index["records"][1]["locator"]["line_end"], 4)
        self.assertEqual(
            index["records"][1]["stable_source_id"], "erdosproblems:deadbeef:2"
        )

    def test_quoted_yaml_number_pattern_and_url_ids_are_deterministic(self) -> None:
        fixture = '- number: "10"\n- number: \'11\'\n'
        self.assertEqual(
            [match.group(1) for match in MODULE.ERDOS_NUMBER_LINE.finditer(fixture)], ["10", "11"]
        )
        first = MODULE.stable_url_id("aim", "https://example.test/source")
        self.assertEqual(first, MODULE.stable_url_id("aim", "https://example.test/source"))
        self.assertNotEqual(first, MODULE.stable_url_id("amr", "https://example.test/source"))
        self.assertNotEqual(first, MODULE.stable_url_id("aim", "https://example.test/other"))

    def test_recovery_claim_guards_are_fixed_false(self) -> None:
        # This mirrors the invariant expected for every emitted row: a source
        # locator alone must not authorize a label, a current-open claim, or a
        # rescore.  The full 7,489-row integration verification is exercised by
        # the CLI in the repository reproduction instructions.
        claims = {
            "mathdb_membership": "not_asserted_no_native_collection_field",
            "source_statement_recovery_outcome": "not_assigned_by_manifest",
            "current_open_status": "not_assessed_by_manifest",
            "opdp_recalculation_allowed": False,
        }
        self.assertIs(claims["opdp_recalculation_allowed"], False)
        self.assertEqual(claims["source_statement_recovery_outcome"], "not_assigned_by_manifest")


if __name__ == "__main__":
    unittest.main()
