from pathlib import Path
import math
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from opdp_cert import (  # noqa: E402
    Episode,
    build_schedule,
    baseline_priority,
    equation_shape,
    priority,
    profile_implication,
    profile_json,
    structural_priority,
)


class EquationShapeTests(unittest.TestCase):
    def test_unicode_and_ascii_operators_are_equivalent(self):
        unicode = equation_shape("x = (y ◇ y) ◇ z")
        ascii_ = equation_shape("x = (y * y) * z")
        self.assertEqual(unicode, ascii_)
        self.assertEqual(unicode.operation_count, 2)
        self.assertEqual(unicode.square_patterns, 1)

    def test_collapse_and_variable_burdens(self):
        shape = equation_shape("x = y ◇ y")
        self.assertTrue(shape.lhs_singleton)
        self.assertTrue(shape.singleton_collapse)
        self.assertEqual(shape.lhs_only, 1)
        self.assertEqual(shape.rhs_only, 1)
        self.assertEqual(shape.repeated_occurrences, 1)

    def test_rejects_malformed_equations(self):
        with self.assertRaises(ValueError):
            equation_shape("x ◇ y")
        with self.assertRaises(ValueError):
            equation_shape("x = (y ◇ z")


class ProfileTests(unittest.TestCase):
    def test_legacy_false_bucket_is_preserved(self):
        profile = profile_implication("x = x ◇ y", "x = x ◇ x", known="false")
        self.assertEqual(profile.legacy_bucket, 0)
        self.assertEqual(baseline_priority(profile)[0], 0)

    def test_legacy_structural_buckets_are_preserved(self):
        collapse = profile_implication("x = y ◇ y", "x = x ◇ x", known="true")
        lhs_only = profile_implication("x ◇ y = y ◇ y", "x = x ◇ x", known="true")
        self.assertEqual(collapse.legacy_bucket, 1)
        self.assertEqual(lhs_only.legacy_bucket, 2)

    def test_known_true_and_unknown_tail_buckets(self):
        true = profile_implication("x ◇ y = y ◇ x", "x ◇ x = x ◇ x", known="true")
        unknown = profile_implication("x ◇ y = y ◇ x", "x ◇ x = x ◇ x")
        self.assertEqual(true.legacy_bucket, 4)
        self.assertEqual(unknown.legacy_bucket, 5)
        self.assertTrue(unknown.out_of_oracle)

    def test_baseline_is_exact_bucket_and_structural_refines_inside_it(self):
        plain = profile_implication("x ◇ y = y ◇ x", "x = x ◇ x", known="true")
        hit = profile_implication(
            "x ◇ y = y ◇ x",
            "x = x ◇ x",
            known="true",
            signals={"exact_pair": True},
        )
        self.assertEqual(baseline_priority(plain), (plain.legacy_bucket,))
        self.assertEqual(baseline_priority(plain), baseline_priority(hit))
        self.assertEqual(structural_priority(plain)[0], plain.legacy_bucket)
        self.assertLess(structural_priority(hit), structural_priority(plain))

    def test_invalid_priority_policy_falls_back_to_baseline(self):
        profile = profile_implication("x ◇ y = y ◇ x", "x = x ◇ x", known="true")
        self.assertEqual(priority(profile, "not-reviewed"), baseline_priority(profile))

    def test_profile_is_repeatable_and_json_is_canonical(self):
        args = ("x = (y ◇ z) ◇ x", "x = y ◇ (z ◇ x)")
        one = profile_implication(*args, known="unknown")
        two = profile_implication(*args, known="unknown")
        self.assertEqual(one, two)
        self.assertEqual(profile_json(one), profile_json(two))
        self.assertFalse(profile_json(one).endswith("\n"))


class ScheduleTests(unittest.TestCase):
    def test_schedule_never_exceeds_time_or_token_budget(self):
        profile = profile_implication("x = y ◇ y", "x = x ◇ x", known="unknown")
        schedule = build_schedule(profile, "marathon", 17.0, 4096)
        self.assertLessEqual(sum(attempt.seconds for attempt in schedule), 17.001)
        self.assertLessEqual(sum(attempt.max_tokens for attempt in schedule), 4096)
        self.assertNotIn("llm_judge_repair", [attempt.route for attempt in schedule])

    def test_solo_llm_route_requires_token_headroom(self):
        profile = profile_implication("x ◇ y = y ◇ x", "x = x ◇ x", known="true")
        no_tokens = build_schedule(profile, "solo", 3600.0, 0)
        with_tokens = build_schedule(profile, "solo", 3600.0, 20000)
        self.assertNotIn("llm_true", [attempt.route for attempt in no_tokens])
        self.assertIn("llm_true", [attempt.route for attempt in with_tokens])

    def test_infeasible_llm_does_not_shrink_deterministic_budget(self):
        profile = profile_implication("x ◇ y = y ◇ x", "x = x ◇ x", known="true")
        schedule = build_schedule(profile, "solo", 100.0, 0)
        self.assertAlmostEqual(sum(attempt.seconds for attempt in schedule), 100.0)

    def test_structural_policy_is_explicit(self):
        profile = profile_implication("x = y ◇ y", "x = x ◇ x", known="unknown")
        baseline = build_schedule(profile, "marathon", 30.0, policy="baseline")
        structural = build_schedule(profile, "marathon", 30.0, policy="structural_v0")
        self.assertNotEqual([x.route for x in baseline], [x.route for x in structural])
        fallback = build_schedule(profile, "marathon", 30.0, policy="unreviewed")
        self.assertEqual(fallback, baseline)

    def test_known_directions_never_schedule_opposite_certificate_routes(self):
        true_profile = profile_implication("x ◇ y = y ◇ x", "x = x ◇ x", known="true")
        false_profile = profile_implication("x ◇ y = y ◇ x", "x = x ◇ x", known="false")
        true_routes = {attempt.route for attempt in build_schedule(true_profile, "solo", 3600.0, 20000)}
        false_routes = {attempt.route for attempt in build_schedule(false_profile, "solo", 3600.0, 20000)}
        self.assertFalse(true_routes & {"finite_ce", "infinite_parity", "csp_false", "llm_false"})
        self.assertFalse(
            false_routes
            & {
                "lemma_chain_quick",
                "matching_chain",
                "mini_twee",
                "mini_twee_deep",
                "structural_true",
                "transitivity",
                "bfs",
                "specialized_simp",
                "simp_constancy",
                "rw_chain",
                "hybrid_calc",
                "invertibility",
                "tactic_sweep",
                "llm_true",
            }
        )

    def test_unknown_schedule_retains_both_certificate_directions(self):
        profile = profile_implication("x ◇ y = y ◇ x", "x = x ◇ x", known="unknown")
        routes = {attempt.route for attempt in build_schedule(profile, "solo", 3600.0, 20000)}
        self.assertIn("finite_ce", routes)
        self.assertIn("lemma_chain_quick", routes)
        self.assertIn("llm_both", routes)


class EpisodeTests(unittest.TestCase):
    def test_accepted_episode_requires_certificate(self):
        with self.assertRaises(ValueError):
            Episode(
                "p", "g", "h", "route", 1.0, 0.5, True, False,
                toolchain_commit="817a4653",
            )

    def test_episode_json_is_stable(self):
        episode = Episode(
            "p",
            "family:p",
            "abc",
            "given_clause",
            9.0,
            3.5,
            True,
            False,
            certificate_bytes=1200,
            toolchain_commit="817a4653",
        )
        self.assertEqual(episode.to_json(), episode.to_json())
        self.assertIn('"accepted":true', episode.to_json())

    def test_episode_rejects_conflicting_or_nonfinite_states(self):
        with self.assertRaises(ValueError):
            Episode(
                "p", "g", "h", "route", 1.0, 0.5, True, True,
                certificate_bytes=1, toolchain_commit="817a4653",
            )
        with self.assertRaises(ValueError):
            Episode(
                "p", "g", "h", "route", math.nan, 0.5, False, False,
                failure_reason="failed", toolchain_commit="817a4653",
            )
        with self.assertRaises(ValueError):
            Episode(
                "p", "g", "h", "route", 1.0, 0.5, False, False,
                toolchain_commit="817a4653",
            )
        with self.assertRaises(ValueError):
            Episode(
                "p", "g", "h", "route", 1.0, 0.5, 1, False,
                certificate_bytes=1, toolchain_commit="817a4653",
            )
        with self.assertRaises(ValueError):
            Episode(
                "p", "g", "h", "route", 1.0, 0.5, True, False,
                token_reservation=1.5, certificate_bytes=1,
                toolchain_commit="817a4653",
            )


class BudgetValidationTests(unittest.TestCase):
    def test_schedule_rejects_nonfinite_time(self):
        profile = profile_implication("x = y ◇ y", "x = x ◇ x", known="unknown")
        for value in (math.nan, math.inf, -math.inf):
            with self.assertRaises(ValueError):
                build_schedule(profile, "marathon", value)


class DistributionSafetyTests(unittest.TestCase):
    def test_solver_module_is_small_and_stdlib_only(self):
        source = (ROOT / "opdp_cert.py").read_text(encoding="utf-8")
        self.assertLess(len(source.encode("utf-8")), 20_000)
        for banned_import in ("import requests", "import numpy", "import scipy", "import socket"):
            self.assertNotIn(banned_import, source)


if __name__ == "__main__":
    unittest.main()
