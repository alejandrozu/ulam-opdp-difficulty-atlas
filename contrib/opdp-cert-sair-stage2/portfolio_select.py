"""Deterministic offline portfolio selection for OPDP-Cert.

The heuristic maximizes *observed joint coverage* under byte/time/token budgets.
It does not assume route independence and makes no private-set guarantee.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: str
    source_bytes: int
    runtime_seconds: float
    tokens: int
    covers: frozenset[str]

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "Candidate":
        return cls(
            candidate_id=str(value["candidate_id"]),
            source_bytes=int(value.get("source_bytes", 0)),
            runtime_seconds=float(value.get("runtime_seconds", 0.0)),
            tokens=int(value.get("tokens", 0)),
            covers=frozenset(str(cell) for cell in value.get("covers", [])),
        )


@dataclass(frozen=True, slots=True)
class Selection:
    candidate_ids: tuple[str, ...]
    covered_cells: tuple[str, ...]
    weighted_coverage: float
    source_bytes: int
    runtime_seconds: float
    tokens: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _validate(candidates: Iterable[Candidate]) -> tuple[Candidate, ...]:
    values = tuple(candidates)
    ids = [candidate.candidate_id for candidate in values]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate_id values must be unique")
    for candidate in values:
        if not isinstance(candidate.candidate_id, str) or not candidate.candidate_id.strip():
            raise ValueError("candidate_id values must be non-empty")
        if (
            type(candidate.source_bytes) is not int
            or type(candidate.tokens) is not int
            or not isinstance(candidate.runtime_seconds, (int, float))
            or isinstance(candidate.runtime_seconds, bool)
            or not math.isfinite(candidate.runtime_seconds)
            or candidate.source_bytes < 0
            or candidate.runtime_seconds < 0
            or candidate.tokens < 0
        ):
            raise ValueError(f"invalid cost for {candidate.candidate_id}")
    return values


def select_portfolio(
    candidates: Iterable[Candidate],
    cell_weights: Mapping[str, float],
    max_bytes: int,
    max_seconds: float = math.inf,
    max_tokens: int = 2**63 - 1,
) -> Selection:
    """Greedy budgeted coverage with deterministic tie-breaking.

    Marginal value is computed from cells not already covered.  The result is
    compared with the best feasible singleton, which protects high-value items
    that density-greedy can otherwise miss.
    """

    values = _validate(candidates)
    if (
        type(max_bytes) is not int
        or type(max_tokens) is not int
        or not isinstance(max_seconds, (int, float))
        or isinstance(max_seconds, bool)
        or math.isnan(max_seconds)
        or max_bytes < 0
        or max_seconds < 0
        or max_tokens < 0
    ):
        raise ValueError("budgets must be non-negative")
    weights = {str(cell): float(weight) for cell, weight in cell_weights.items()}
    if any(weight < 0 or not math.isfinite(weight) for weight in weights.values()):
        raise ValueError("cell weights must be finite and non-negative")

    def fits(candidate: Candidate, used_b: int, used_s: float, used_t: int) -> bool:
        return (
            used_b + candidate.source_bytes <= max_bytes
            and used_s + candidate.runtime_seconds <= max_seconds
            and used_t + candidate.tokens <= max_tokens
        )

    def gain(candidate: Candidate, covered: set[str]) -> float:
        return sum(weights.get(cell, 1.0) for cell in candidate.covers - covered)

    chosen: list[Candidate] = []
    covered: set[str] = set()
    used_b, used_s, used_t = 0, 0.0, 0
    remaining = list(values)
    while remaining:
        scored = []
        for candidate in remaining:
            if not fits(candidate, used_b, used_s, used_t):
                continue
            marginal = gain(candidate, covered)
            if marginal <= 0:
                continue
            normalized_cost = 0.0
            if max_bytes:
                normalized_cost += candidate.source_bytes / max_bytes
            if math.isfinite(max_seconds) and max_seconds:
                normalized_cost += candidate.runtime_seconds / max_seconds
            if max_tokens:
                normalized_cost += candidate.tokens / max_tokens
            density = marginal / max(normalized_cost, 1e-12)
            scored.append((density, marginal, candidate.candidate_id, candidate))
        if not scored:
            break
        _, _, _, best = max(scored, key=lambda row: (row[0], row[1], row[2]))
        chosen.append(best)
        covered.update(best.covers)
        used_b += best.source_bytes
        used_s += best.runtime_seconds
        used_t += best.tokens
        remaining.remove(best)

    feasible_singletons = [candidate for candidate in values if fits(candidate, 0, 0.0, 0)]
    best_singleton = max(
        feasible_singletons,
        key=lambda candidate: (gain(candidate, set()), candidate.candidate_id),
        default=None,
    )
    greedy_value = sum(weights.get(cell, 1.0) for cell in covered)
    singleton_value = gain(best_singleton, set()) if best_singleton else -1.0
    if best_singleton is not None and singleton_value > greedy_value:
        chosen, covered = [best_singleton], set(best_singleton.covers)
        used_b = best_singleton.source_bytes
        used_s = best_singleton.runtime_seconds
        used_t = best_singleton.tokens
        greedy_value = singleton_value

    return Selection(
        candidate_ids=tuple(candidate.candidate_id for candidate in chosen),
        covered_cells=tuple(sorted(covered)),
        weighted_coverage=round(greedy_value, 9),
        source_bytes=used_b,
        runtime_seconds=round(used_s, 9),
        tokens=used_t,
    )


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON candidate/coverage file")
    parser.add_argument("--output", type=Path, help="write JSON result here")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    candidates = [Candidate.from_dict(item) for item in payload["candidates"]]
    budgets = payload["budgets"]
    selection = select_portfolio(
        candidates,
        payload.get("cell_weights", {}),
        int(budgets["source_bytes"]),
        float(budgets.get("runtime_seconds", math.inf)),
        int(budgets.get("tokens", 2**63 - 1)),
    )
    text = json.dumps(
        selection.to_dict(), indent=2, sort_keys=True, allow_nan=False
    ) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
