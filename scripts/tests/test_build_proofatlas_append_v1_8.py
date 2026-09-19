#!/usr/bin/env python3
"""Synthetic integration test for the guarded ProofAtlas v1.8 append builder."""

from __future__ import annotations

import gzip
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[2]
BUILDER = REPOSITORY / "scripts" / "build_proofatlas_append_v1_8.py"
FIXTURE = REPOSITORY / "fixtures" / "proofatlas_append" / "synthetic_curated_proofatlas.jsonl"
ORDER = [
    "problem_id", "difficulty_label", "explanation", "problem_number", "title",
    "catalog_status", "assessment_gate", "classification", "intrinsic_difficulty",
    "ai_assessment", "human_attention", "tractability", "verification",
    "formalization", "prerequisites", "tool_leverage", "recommended_approach",
    "evidence", "rationales", "tags", "review", "flags", "source_text",
    "provenance", "source_record", "implementation",
]


def fixture_base_record() -> dict[str, object]:
    """A complete-schema synthetic legacy row, not a copied OPDP row."""

    return {
        "problem_id": 7,
        "difficulty_label": "T2 Research Sprint",
        "explanation": "Synthetic legacy record.",
        "problem_number": "SYNTH-7",
        "title": "Synthetic base question",
        "catalog_status": "open",
        "assessment_gate": {"value": "source_claimed_open", "confidence": {"level": 1, "code": "C1", "status": "assigned"}},
        "classification": {"category": {"id": 20, "name": "miscellaneous", "slug": "miscellaneous", "label": "Miscellaneous"}, "source_collection": {"id": 1, "name": "synthetic", "slug": "synthetic", "label": "Synthetic", "nested_object_present": True}, "scope": "atomic", "answer_type": "proof_disproof", "mathematical_objects": ["synthetic object"], "mathematical_objects_summary": "synthetic object", "ambiguity_score": 1.0, "legacy_difficulty": {"id": None, "level": None, "label": None, "description": None, "role_in_opdp": "absent_not_used"}},
        "intrinsic_difficulty": {"score": 3.0, "range": {"low": 2.0, "high": 4.0}, "confidence": {"level": 1, "code": "C1", "status": "assigned"}, "tier_code": "T2", "tier_label": "Research Sprint", "factors": {"conceptual_gap": {"code": "CG", "score": 1.0, "weight": 0.30}, "route_gap": {"code": "RG", "score": 1.0, "weight": 0.20}, "technical_depth": {"code": "TD", "score": 1.0, "weight": 0.20}, "known_barrier": {"code": "KB", "score": 1.0, "weight": 0.20}, "search_scale": {"code": "SS", "score": 1.0, "weight": 0.10}}},
        "ai_assessment": {"relative_adjustment": 0.0, "fit": "ai_neutral_mixed", "fit_label": "AI-neutral/mixed", "difficulty_score": 3.0, "range": {"low": 2.0, "high": 4.0}, "confidence": {"level": 1, "code": "C1", "status": "assigned"}, "protocol_id": "frontier-generalist-hs1-2026-07-31", "predictors": {"formal_fit": 0, "verification_feedback": 0, "tool_fit": 0, "context_load": 0, "reasoning_horizon": 0, "tacit_experimental_insight": 0}, "empirical_problem_episode_run": False},
        "human_attention": {"effort": {"score": 1, "code": "H1", "estimated_specialist_hours": {"minimum": 10, "maximum": 30, "minimum_inclusive": True, "maximum_inclusive": False, "maximum_kind": "finite", "display_label": "10-30"}, "confidence": {"level": 1, "code": "C1", "status": "assigned"}, "estimate_kind": "order_of_magnitude_prior_not_observed_labor"}, "exposure": {"score": 1, "code": "X1", "confidence": {"level": None, "code": None, "status": "not_separately_assigned"}}},
        "tractability": {"status": "scored", "score": 6, "code": "T6", "progress_probability": {"low": 0.4, "high": 0.55, "low_inclusive": True, "high_inclusive": False, "display_label": "40-55%"}, "target_combined_hours": 100, "minimum_outcome_level": 3, "target": "novel_independently_checked_partial_progress_not_full_resolution", "confidence": {"level": 1, "code": "C1", "status": "assigned"}},
        "verification": {"meaning": "independent_checking_burden_not_scientific_value", "if_true_score": 3.0, "if_false_score": 3.0, "confidence": {"level": 1, "code": "C1", "status": "assigned"}},
        "formalization": {"score": 3.0, "confidence": {"level": 1, "code": "C1", "status": "assigned"}, "meaning": "burden_to_encode_statement_and_prerequisites_not_unknown_proof"},
        "prerequisites": {"preparation_score": 3.0, "breadth_score": 1, "confidence": {"level": 1, "code": "C1", "status": "assigned"}},
        "tool_leverage": {"score": 5.0, "direction": "higher_is_more_favorable", "confidence": {"level": None, "code": None, "status": "not_separately_assigned"}},
        "recommended_approach": {"route_code": "synthetic_route", "route_label": "Synthetic route"},
        "evidence": {"codes": ["synthetic"], "reference_signal_count": 0, "estimated_proposal_year": None, "proposal_year_basis": "unknown", "proposal_year_basis_detail": "synthetic", "estimated_age_years_at_assessment": None, "literature_load_score": 0, "collection_barrier_prior": 0, "score_basis": "synthetic", "status_evidence": "open", "rights_note": None},
        "rationales": {key: "Synthetic rationale." for key in ["intrinsic_difficulty", "intrinsic_factors", "ai_assessment", "tool_leverage", "human_attention", "tractability", "verification", "formalization", "prerequisites", "status_and_data"]},
        "tags": [],
        "review": {"priority_code": "P3", "priority_label": "routine audit", "recommended_curation_action": "Synthetic.", "identity_collisions": {"problem_number_peer_ids": [], "normalized_title_peer_ids": [], "normalized_statement_peer_ids": []}, "manual_override": {"applied": False, "reviewer_id": None, "reviewed_at": None, "reason": None, "changes": []}},
        "flags": [],
        "source_text": {"title": "Synthetic base question", "statement": "This is synthetic base statement.", "background": "", "text_mode": "synthetic", "assessed_title_differs_from_source": False, "transport_qa": {"forbidden_control_character_count": 0, "occurrences": [], "json_serialization": "escaped_by_json_encoder", "display_policy": "sanitize_before_rendering_when_count_is_nonzero"}},
        "provenance": {"source_url_kind": "synthetic", "source_url_qa_flags": []},
        "source_record": {"synthetic": True},
        "implementation": {"record_schema_version": "1.0.0", "dataset_version": "fixture", "rule_version": "fixture"},
    }


def fixture_base_payload(problem_number: str = "SYNTH-7") -> dict[str, object]:
    record = fixture_base_record()
    record["problem_number"] = problem_number
    return {
        "format_name": "synthetic-opdp",
        "schema_version": "1.0.0",
        "export_id": "synthetic-v1.7",
        "generated_at": "2026-09-19T00:00:00Z",
        "dataset_snapshot": {"version": "fixture", "source_segments": []},
        "assessment_release": {"limitations": []},
        "integration_guide": {},
        "schema": {"record_field_order": ORDER, "record_contract": {}},
        "rubric": {"tag_registry": {}, "route_registry": {}, "flag_registry": {}},
        "protocols": {"frontier-generalist-hs1-2026-07-31": {}},
        "summary": {},
        "records": [record],
    }


def write_fixture_base(path: Path, problem_number: str = "SYNTH-7") -> dict[str, object]:
    base = fixture_base_payload(problem_number)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(base, handle, ensure_ascii=False, separators=(",", ":"))
    return base


class ProofAtlasAppendTest(unittest.TestCase):
    def test_append_preserves_prefix_and_quarantines_source_claims(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            base = fixture_base_payload()
            self.assertEqual(list(base["records"][0]), ORDER)
            base_path = temp / "base.json.gz"
            write_fixture_base(base_path)
            output = temp / "v1.8.json.gz"
            validation = temp / "validation.json"
            manifest = temp / "proofatlas-manifest.json"
            manifest.write_text(json.dumps({
                "schema": "opdp.proofatlas.curated-snapshot-manifest.v1",
                "status": "complete",
                "source_file": FIXTURE.name,
                "sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest().upper(),
                "record_count": 1,
                "snapshot_id": "synthetic-proofatlas-fixture-2026-09-19",
            }), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(BUILDER), "--base", str(base_path), "--proofatlas", str(FIXTURE), "--manifest", str(manifest), "--output", str(output), "--validation", str(validation), "--allow-base-mismatch"],
                cwd=REPOSITORY,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with gzip.open(output, "rt", encoding="utf-8") as handle:
                result = json.load(handle)
            report = json.loads(validation.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "pass")
            self.assertEqual(result["records"][0], base["records"][0])
            appended = result["records"][1]
            self.assertEqual(appended["problem_id"], 50_000_017)
            self.assertEqual(appended["problem_number"], "PROOFATLAS-synthetic-017")
            self.assertEqual(appended["assessment_gate"]["value"], "source_claimed_open")
            self.assertEqual(appended["assessment_gate"]["confidence"]["code"], "C0")
            self.assertNotEqual(appended["assessment_gate"]["value"], "verified_open")
            self.assertEqual(appended["source_record"]["schema"], "opdp.proofatlas.curated-problem.v1")
            self.assertTrue({"proofatlas_curated_input", "proofatlas_provisional_c0_assessment", "source_statement_not_independently_verified"}.issubset(appended["flags"]))
            self.assertEqual(result["summary"]["records_total"], 2)

    def test_rejects_absent_statement_provenance_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            base_path = temp / "base.json.gz"
            write_fixture_base(base_path)
            invalid = json.loads(FIXTURE.read_text(encoding="utf-8"))
            del invalid["snapshot"]["statement_provenance"]
            input_path = temp / "missing-provenance.jsonl"
            input_path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(BUILDER), "--base", str(base_path), "--proofatlas", str(input_path), "--output", str(temp / "out.json.gz"), "--validation", str(temp / "validation.json"), "--limit", "1", "--allow-base-mismatch"],
                cwd=REPOSITORY,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("statement_provenance", completed.stderr)

    def test_limit_requires_explicit_fixture_base_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            base_path = temp / "base.json.gz"
            write_fixture_base(base_path)
            completed = subprocess.run(
                [sys.executable, str(BUILDER), "--base", str(base_path), "--proofatlas", str(FIXTURE), "--output", str(temp / "out.json.gz"), "--validation", str(temp / "validation.json"), "--limit", "1"],
                cwd=REPOSITORY,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("--limit is fixture/smoke-only", completed.stderr)

    def test_declared_source_text_clearance_is_an_explicit_alternative(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            base_path = temp / "base.json.gz"
            write_fixture_base(base_path)
            cleared = json.loads(FIXTURE.read_text(encoding="utf-8"))
            cleared["snapshot"]["statement_provenance"] = {
                "mode": "source_text_with_declared_rights_clearance",
                "rights_clearance": {
                    "status": "declared_cleared",
                    "basis": "synthetic test authorization",
                    "reference": "synthetic-clearance-record-001",
                },
            }
            input_path = temp / "cleared-source-text.jsonl"
            input_path.write_text(json.dumps(cleared) + "\n", encoding="utf-8")
            output = temp / "out.json.gz"
            completed = subprocess.run(
                [sys.executable, str(BUILDER), "--base", str(base_path), "--proofatlas", str(input_path), "--output", str(output), "--validation", str(temp / "validation.json"), "--limit", "1", "--allow-base-mismatch"],
                cwd=REPOSITORY,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            with gzip.open(output, "rt", encoding="utf-8") as handle:
                appended = json.load(handle)["records"][1]
            self.assertEqual(appended["provenance"]["statement_provenance_mode"], "source_text_with_declared_rights_clearance")
            self.assertEqual(appended["provenance"]["statement_rights_clearance"]["status"], "declared_cleared")

    def test_rejects_non_locator_problem_source_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            base_path = temp / "base.json.gz"
            write_fixture_base(base_path)
            invalid = json.loads(FIXTURE.read_text(encoding="utf-8"))
            invalid["problem"]["source_url"] = "https://"
            input_path = temp / "invalid-source-url.jsonl"
            input_path.write_text(json.dumps(invalid) + "\n", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(BUILDER), "--base", str(base_path), "--proofatlas", str(input_path), "--output", str(temp / "out.json.gz"), "--validation", str(temp / "validation.json"), "--limit", "1", "--allow-base-mismatch"],
                cwd=REPOSITORY,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("problem.source_url", completed.stderr)

    def test_rejects_case_insensitive_display_identifier_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            base_path = temp / "base.json.gz"
            write_fixture_base(base_path, "proofatlas-SYNTHETIC-017")
            completed = subprocess.run(
                [sys.executable, str(BUILDER), "--base", str(base_path), "--proofatlas", str(FIXTURE), "--output", str(temp / "out.json.gz"), "--validation", str(temp / "validation.json"), "--limit", "1", "--allow-base-mismatch"],
                cwd=REPOSITORY,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("display identifier collision", completed.stderr)


if __name__ == "__main__":
    unittest.main()
