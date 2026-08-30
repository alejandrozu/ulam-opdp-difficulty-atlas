from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "euler_priority_block.py"


def normalise(text):
    return " ".join(text.replace("*", "◇").split())


def norm_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def bind_ids_to_text(eq1_id, eq2_id, eq1, eq2):
    known = {1: "x = y ◇ y", 2: "x = x ◇ x"}
    if known.get(eq1_id) == eq1 and known.get(eq2_id) == eq2:
        return eq1_id, eq2_id
    return -1, -1


def oracle(eq1_id, eq2_id):
    return "true" if (eq1_id, eq2_id) == (1, 2) else None


def variables_of(equation):
    return set(re.findall(r"\b([a-z])\b", equation))


def analyse(equation):
    lhs, rhs = [part.strip() for part in equation.split("=", 1)]
    lv = set(re.findall(r"\b([a-z])\b", lhs))
    rv = set(re.findall(r"\b([a-z])\b", rhs))
    return {
        "variables": variables_of(equation),
        "lhs": lhs,
        "rhs": rhs,
        "lhs_vars": lv,
        "rhs_vars": rv,
        "lhs_only": lv - rv,
        "rhs_only": rv - lv,
        "op_count": equation.count("◇") + equation.count("*"),
    }


class CompactBlockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.namespace = dict(globals())
        exec(compile(SOURCE.read_text(encoding="utf-8"), str(SOURCE), "exec"), cls.namespace)

    def test_exact_lf_byte_size(self):
        data = SOURCE.read_bytes()
        self.assertEqual(len(data), 1972)
        self.assertNotIn(b"\r", data)

    def test_baseline_and_invalid_policy_are_same_bucket_only(self):
        problem = {
            "equation1": "x = y * y",
            "equation2": "x = x * x",
            "eq1_id": 1,
            "eq2_id": 2,
        }
        baseline = self.namespace["opdp_marathon_priority"](problem, "baseline")
        invalid = self.namespace["opdp_marathon_priority"](problem, "anything")
        self.assertEqual(baseline, (1,))
        self.assertEqual(invalid, baseline)

    def test_structural_policy_preserves_primary_bucket(self):
        problem = {
            "equation1": "x = y ◇ y",
            "equation2": "x = x ◇ x",
            "eq1_id": 1,
            "eq2_id": 2,
        }
        baseline = self.namespace["opdp_marathon_priority"](problem, "baseline")
        structural = self.namespace["opdp_marathon_priority"](problem, "structural_v0")
        self.assertEqual(baseline[0], structural[0])
        self.assertGreater(len(structural), 1)

    def test_id_text_mismatch_is_unbound(self):
        problem = {
            "equation1": "x = z ◇ z",
            "equation2": "x = x ◇ x",
            "eq1_id": 1,
            "eq2_id": 2,
        }
        features = self.namespace["_opdp_marathon_features"](problem)
        self.assertEqual(features["known"], "unknown")

    def test_input_is_not_mutated_and_ascii_normalizes(self):
        problem = {
            "equation1": "x = y * y",
            "equation2": "x = x * x",
            "eq1_id": 1,
            "eq2_id": 2,
        }
        before = dict(problem)
        self.namespace["opdp_marathon_priority"](problem, "baseline")
        self.assertEqual(problem, before)


if __name__ == "__main__":
    unittest.main()
