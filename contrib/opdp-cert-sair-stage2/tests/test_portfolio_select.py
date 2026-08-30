from pathlib import Path
import math
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portfolio_select import Candidate, select_portfolio  # noqa: E402


class PortfolioTests(unittest.TestCase):
    def test_joint_coverage_is_not_double_counted(self):
        candidates = [
            Candidate("a", 10, 1.0, 0, frozenset({"x", "y"})),
            Candidate("b", 10, 1.0, 0, frozenset({"y", "z"})),
            Candidate("c", 10, 1.0, 0, frozenset({"q"})),
        ]
        result = select_portfolio(candidates, {}, max_bytes=20, max_seconds=3.0)
        self.assertLessEqual(result.source_bytes, 20)
        self.assertEqual(len(result.covered_cells), len(set(result.covered_cells)))
        self.assertEqual(result.weighted_coverage, float(len(result.covered_cells)))

    def test_respects_all_three_budgets(self):
        candidates = [
            Candidate("a", 6, 4.0, 8, frozenset({"x", "y"})),
            Candidate("b", 5, 3.0, 5, frozenset({"z"})),
            Candidate("c", 4, 1.0, 2, frozenset({"q"})),
        ]
        result = select_portfolio(candidates, {}, 10, 5.0, 10)
        self.assertLessEqual(result.source_bytes, 10)
        self.assertLessEqual(result.runtime_seconds, 5.0)
        self.assertLessEqual(result.tokens, 10)

    def test_best_singleton_safeguard(self):
        candidates = [
            Candidate("small", 1, 0.0, 0, frozenset({"low"})),
            Candidate("large", 10, 0.0, 0, frozenset({"high"})),
        ]
        weights = {"low": 1.1, "high": 10.0}
        result = select_portfolio(candidates, weights, max_bytes=10)
        self.assertEqual(result.candidate_ids, ("large",))
        self.assertEqual(result.weighted_coverage, 10.0)

    def test_is_deterministic(self):
        candidates = [
            Candidate("a", 1, 0.0, 0, frozenset({"x"})),
            Candidate("b", 1, 0.0, 0, frozenset({"y"})),
        ]
        one = select_portfolio(candidates, {}, max_bytes=1)
        two = select_portfolio(candidates, {}, max_bytes=1)
        self.assertEqual(one, two)

    def test_rejects_duplicate_ids_and_negative_costs(self):
        with self.assertRaises(ValueError):
            select_portfolio(
                [
                    Candidate("same", 1, 0.0, 0, frozenset()),
                    Candidate("same", 1, 0.0, 0, frozenset()),
                ],
                {},
                10,
            )
        with self.assertRaises(ValueError):
            select_portfolio([Candidate("bad", -1, 0.0, 0, frozenset())], {}, 10)
        with self.assertRaises(ValueError):
            select_portfolio([Candidate("bad", 1, math.inf, 0, frozenset())], {}, 10)


if __name__ == "__main__":
    unittest.main()
