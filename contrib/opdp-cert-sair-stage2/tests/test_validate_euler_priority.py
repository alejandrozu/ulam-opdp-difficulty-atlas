from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from validate_euler_priority import (  # noqa: E402
    EULER_COMMIT,
    EXPECTED_ROWS,
    OFFICIAL_COMMIT,
    _result_is_valid,
)


def good_result():
    return {
        "baseline_bucket_mismatches": 0,
        "default_policy": "baseline",
        "id_text_mismatch_known": "unknown",
        "euler_commit": EULER_COMMIT,
        "official_commit": OFFICIAL_COMMIT,
        "sets": {
            name: {
                "rows": rows,
                "bucket_matches": rows,
                "bucket_mismatches": [],
                "input_mutations": 0,
            }
            for name, rows in EXPECTED_ROWS.items()
        },
    }


class ValidatorGateTests(unittest.TestCase):
    def test_all_safety_checks_are_required(self):
        self.assertTrue(_result_is_valid(good_result()))
        for key, bad_value in (
            ("baseline_bucket_mismatches", 1),
            ("default_policy", "structural_v0"),
            ("id_text_mismatch_known", "false"),
        ):
            result = good_result()
            result[key] = bad_value
            self.assertFalse(_result_is_valid(result), key)

        result = good_result()
        result["sets"]["normal"]["input_mutations"] = 1
        self.assertFalse(_result_is_valid(result))

        result = good_result()
        del result["sets"]["hard3"]
        self.assertFalse(_result_is_valid(result))


if __name__ == "__main__":
    unittest.main()
