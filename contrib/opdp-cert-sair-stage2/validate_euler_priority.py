"""Validate the compact priority block against EULER and official fixtures."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import runpy
import subprocess
import time


HERE = Path(__file__).resolve().parent
BLOCK = HERE / "euler_priority_block.py"
SETS = ("normal", "hard1", "hard2", "hard3")
EXPECTED_ROWS = {"normal": 1000, "hard1": 69, "hard2": 200, "hard3": 400}
EULER_COMMIT = "5524bd9949873d64ea9e121f0683b7158e3d6145"
OFFICIAL_COMMIT = "817a4653bf762584931d49c6714c9fcfab7df66a"


def _commit(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()


def _load_problems(official_repo: Path, name: str) -> list[dict[str, object]]:
    path = official_repo / "examples" / "problems" / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _result_is_valid(result: dict[str, object]) -> bool:
    sets = result.get("sets", {})
    return bool(
        result.get("baseline_bucket_mismatches") == 0
        and result.get("default_policy") == "baseline"
        and result.get("id_text_mismatch_known") == "unknown"
        and result.get("euler_commit") == EULER_COMMIT
        and result.get("official_commit") == OFFICIAL_COMMIT
        and isinstance(sets, dict)
        and set(sets) == set(SETS)
        and all(
            isinstance(sets[name], dict)
            and sets[name].get("rows") == EXPECTED_ROWS[name]
            and sets[name].get("bucket_matches") == EXPECTED_ROWS[name]
            and sets[name].get("bucket_mismatches") == []
            and sets[name].get("input_mutations") == 0
            for name in SETS
        )
    )


def validate(euler_repo: Path, official_repo: Path) -> dict[str, object]:
    solver = euler_repo / "SUBMIT-NOW-2026-08-29" / "EULER" / "solver.py"
    namespace = runpy.run_path(str(solver), run_name="opdp_euler_validation")
    exec(compile(BLOCK.read_text(encoding="utf-8"), str(BLOCK), "exec"), namespace)
    old_key = namespace["_difficulty"]
    features = namespace["_opdp_marathon_features"]
    priority = namespace["opdp_marathon_priority"]

    totals: dict[str, object] = {}
    all_rows: list[dict[str, object]] = []
    started = time.perf_counter()
    for name in SETS:
        problems = _load_problems(official_repo, name)
        all_rows.extend(problems)
        mismatches = []
        mutations = 0
        for index, problem in enumerate(problems):
            before = copy.deepcopy(problem)
            old = old_key(problem)
            baseline = priority(problem, "baseline")[0]
            structural = priority(problem, "structural_v0")[0]
            if old != baseline or old != structural:
                mismatches.append({"index": index, "old": old, "baseline": baseline, "structural": structural})
            mutations += problem != before
        old_order = [i for i, _ in sorted(enumerate(problems), key=lambda item: (old_key(item[1]), item[0]))]
        structural_order = [
            i for i, _ in sorted(
                enumerate(problems),
                key=lambda item: priority(item[1], "structural_v0") + (item[0],),
            )
        ]
        totals[name] = {
            "rows": len(problems),
            "bucket_matches": len(problems) - len(mismatches),
            "bucket_mismatches": mismatches,
            "input_mutations": mutations,
            "structural_position_changes": sum(a != b for a, b in zip(old_order, structural_order)),
        }

    mismatch_probe = dict(all_rows[0])
    mismatch_probe["equation1"] = "x = x"
    mismatch_features = features(mismatch_probe)
    elapsed = time.perf_counter() - started
    total_rows = sum(int(totals[name]["rows"]) for name in SETS)
    total_matches = sum(int(totals[name]["bucket_matches"]) for name in SETS)

    result = {
        "router_version": namespace["OPDP_CERT_ROUTER_VERSION"],
        "default_policy": namespace["OPDP_CERT_POLICY"],
        "euler_commit": _commit(euler_repo),
        "official_commit": _commit(official_repo),
        "sets": totals,
        "total_rows": total_rows,
        "baseline_bucket_matches": total_matches,
        "baseline_bucket_mismatches": total_rows - total_matches,
        "id_text_mismatch_known": mismatch_features["known"],
        "elapsed_seconds": round(elapsed, 6),
        "interpretation": "Compatibility evidence only; no accepted-score claim.",
    }
    result["validation_passed"] = _result_is_valid(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--euler-repo", type=Path, required=True)
    parser.add_argument("--official-repo", type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.euler_repo.resolve(), args.official_repo.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["validation_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
