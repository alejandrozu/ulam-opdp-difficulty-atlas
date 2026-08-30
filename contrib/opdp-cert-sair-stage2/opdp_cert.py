"""OPDP-Cert: certificate-safe routing primitives for SAIR Stage 2.

This module profiles an equational implication and proposes an attempt order.
It never infers a verdict or emits a certificate.  A caller must retain its
existing proof/countermodel generation and Lean verification boundary.

The default ``baseline`` priority preserves EULER's six primary Marathon
buckets exactly.  The ``structural_v0`` priority and route schedule are research
scaffolds and must be benchmarked before competition use.

Python 3.11+, standard library only.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import re
from typing import Mapping, Sequence


VERSION = "0.1.0"
POLICY_VERSION = "opdp-cert-sair-baseline-2026-08-30"

_VAR_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_]*\b")
_OP_RE = re.compile(r"[◇*]")
_SQUARE_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]*)\b\s*[◇*]\s*\b\1\b"
)


def _normalise_equation(text: str) -> str:
    return " ".join(text.replace("*", "◇").split())


def _split_equation(text: str) -> tuple[str, str]:
    if text.count("=") != 1:
        raise ValueError(f"expected one '=' in equation, got {text!r}")
    lhs, rhs = text.split("=", 1)
    lhs, rhs = lhs.strip(), rhs.strip()
    if not lhs or not rhs:
        raise ValueError(f"empty equation side in {text!r}")
    return lhs, rhs


def _max_parenthesis_depth(text: str) -> int:
    depth = maximum = 0
    for char in text:
        if char == "(":
            depth += 1
            maximum = max(maximum, depth)
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise ValueError(f"unbalanced parentheses in {text!r}")
    if depth:
        raise ValueError(f"unbalanced parentheses in {text!r}")
    return maximum


def _variables(text: str) -> tuple[str, ...]:
    return tuple(_VAR_RE.findall(text))


@dataclass(frozen=True, slots=True)
class EquationShape:
    """Cheap static features of one equation."""

    operation_count: int
    max_depth: int
    distinct_variables: int
    repeated_occurrences: int
    lhs_only: int
    rhs_only: int
    lhs_singleton: bool
    singleton_collapse: bool
    square_patterns: int
    side_imbalance: int
    utf8_bytes: int


def equation_shape(equation: str) -> EquationShape:
    """Extract deterministic, syntax-only features from a magma equation."""

    equation = _normalise_equation(equation)
    lhs, rhs = _split_equation(equation)
    lhs_vars = _variables(lhs)
    rhs_vars = _variables(rhs)
    all_vars = lhs_vars + rhs_vars
    counts = Counter(all_vars)
    lhs_set, rhs_set = set(lhs_vars), set(rhs_vars)
    lhs_singleton = len(lhs_vars) == 1 and not _OP_RE.search(lhs)
    return EquationShape(
        operation_count=len(_OP_RE.findall(equation)),
        max_depth=_max_parenthesis_depth(equation),
        distinct_variables=len(counts),
        repeated_occurrences=sum(max(0, count - 1) for count in counts.values()),
        lhs_only=len(lhs_set - rhs_set),
        rhs_only=len(rhs_set - lhs_set),
        lhs_singleton=lhs_singleton,
        singleton_collapse=lhs_singleton and lhs_vars[0] not in rhs_set,
        square_patterns=len(_SQUARE_RE.findall(equation)),
        side_imbalance=abs(len(_OP_RE.findall(lhs)) - len(_OP_RE.findall(rhs))),
        utf8_bytes=len(equation.encode("utf-8")),
    )


def _known(value: str | None) -> str:
    value = (value or "unknown").lower()
    if value not in {"true", "false", "unknown"}:
        raise ValueError("known must be 'true', 'false', or 'unknown'")
    return value


@dataclass(frozen=True, slots=True)
class GoalProfile:
    """Challenge-specific OPDP profile with raw counts and 0..9 burdens."""

    profile_id: str
    known: str
    legacy_bucket: int
    hypothesis_operations: int
    goal_operations: int
    max_depth: int
    distinct_variables: int
    repeated_occurrences: int
    free_variables: int
    substitution_signal: bool
    collapse_signal: bool
    square_signal: bool
    out_of_oracle: bool
    exact_pair: bool
    bank_hit: bool
    transitivity_hops: int
    search_scale: int
    verification_burden: int
    certificate_risk: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def profile_implication(
    equation1: str,
    equation2: str,
    known: str | None = None,
    signals: Mapping[str, object] | None = None,
) -> GoalProfile:
    """Build a profile from the hypothesis, goal, oracle signal, and cheap hits.

    Supported optional signals are ``exact_pair``, ``bank_hit``, and
    ``transitivity_hops``.  They are hints for scheduling only.
    """

    known = _known(known)
    signals = signals or {}
    hyp, goal = equation_shape(equation1), equation_shape(equation2)
    eq1, eq2 = _normalise_equation(equation1), _normalise_equation(equation2)
    h_lhs, h_rhs = _split_equation(eq1)
    h_free = set(_variables(h_lhs)) ^ set(_variables(h_rhs))

    if known == "false":
        legacy = 0
    elif hyp.singleton_collapse:
        legacy = 1
    elif hyp.lhs_only:
        legacy = 2
    elif 0 < len(h_free) <= 2:
        legacy = 3
    elif known == "true":
        legacy = 4
    else:
        legacy = 5

    operations = hyp.operation_count + goal.operation_count
    repeats = hyp.repeated_occurrences + goal.repeated_occurrences
    variables = len(set(_variables(eq1)) | set(_variables(eq2)))
    max_depth = max(hyp.max_depth, goal.max_depth)
    search_scale = min(9, (operations + 2 * repeats + max_depth + 1) // 3)
    verification = min(
        9,
        (goal.operation_count + goal.distinct_variables + goal.max_depth + 1) // 2,
    )
    certificate_risk = min(
        9,
        (goal.utf8_bytes // 18) + goal.side_imbalance + (1 if variables > 4 else 0),
    )
    digest = hashlib.sha256(f"{eq1}\n{eq2}".encode("utf-8")).hexdigest()[:16]

    return GoalProfile(
        profile_id=digest,
        known=known,
        legacy_bucket=legacy,
        hypothesis_operations=hyp.operation_count,
        goal_operations=goal.operation_count,
        max_depth=max_depth,
        distinct_variables=variables,
        repeated_occurrences=repeats,
        free_variables=len(h_free),
        substitution_signal=0 < len(h_free) <= 2,
        collapse_signal=hyp.singleton_collapse or bool(hyp.lhs_only),
        square_signal=bool(hyp.square_patterns or goal.square_patterns),
        out_of_oracle=known == "unknown",
        exact_pair=bool(signals.get("exact_pair", False)),
        bank_hit=bool(signals.get("bank_hit", False)),
        transitivity_hops=max(0, int(signals.get("transitivity_hops", 0) or 0)),
        search_scale=search_scale,
        verification_burden=verification,
        certificate_risk=certificate_risk,
    )


def baseline_priority(profile: GoalProfile) -> tuple[object, ...]:
    """Return EULER's compatibility key; smaller is attempted earlier.

    A caller should append the original manifest index.  That reproduces the
    stable ordering of EULER's current ``_difficulty`` sort exactly.
    """

    return (profile.legacy_bucket,)


def structural_priority(profile: GoalProfile) -> tuple[object, ...]:
    """Return an experimental deterministic within-bucket refinement."""

    cheap_hit = profile.exact_pair or profile.bank_hit
    structural = profile.substitution_signal or profile.collapse_signal
    return (
        profile.legacy_bucket,
        0 if cheap_hit else 1,
        0 if structural else 1,
        profile.verification_burden,
        profile.search_scale,
        profile.certificate_risk,
        profile.profile_id,
    )


# Backward-compatible descriptive alias for the exact EULER key.
compat_priority = baseline_priority


def priority(profile: GoalProfile, policy: str = "baseline") -> tuple[object, ...]:
    """Dispatch a priority policy; unknown policies safely use ``baseline``."""

    if isinstance(policy, str) and policy.lower() == "structural_v0":
        return structural_priority(profile)
    return baseline_priority(profile)


@dataclass(frozen=True, slots=True)
class RouteAttempt:
    route: str
    seconds: float
    max_tokens: int
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _route_plan(profile: GoalProfile, track: str, policy: str) -> list[tuple[str, float, int, str]]:
    true_routes = [
        ("lemma_chain_quick", 1.0, 0, "two-round proof-recording completion"),
        ("matching_chain", 8.0, 0, "short rewrite/matching chain"),
        ("mini_twee", 6.0, 0, "proof-recording completion"),
        ("mini_twee_deep", 15.0, 0, "deeper proof-recording completion"),
        ("structural_true", 5.0, 0, "direct structural proof templates"),
        ("transitivity", 60.0, 0, "bounded composition through an intermediate law"),
        ("bfs", 10.0, 0, "bounded tree-rewrite proof search"),
        ("specialized_simp", 5.0, 0, "specialized simplification proof"),
        ("simp_constancy", 5.0, 0, "constancy simplification proof"),
        ("rw_chain", 5.0, 0, "recorded rewrite chain"),
        ("hybrid_calc", 5.0, 0, "hybrid calculation proof"),
        ("invertibility", 20.0, 0, "invertibility proof sweep"),
        ("tactic_sweep", 30.0, 0, "bounded tactic proof sweep"),
    ]
    false_routes = [
        ("finite_ce", 12.0, 0, "self-checked finite countermodel search"),
        ("csp_false", 30.0, 0, "bounded finite CSP countermodel search"),
    ]

    if track == "marathon":
        offline_quick = ("offline_true_quick", 9.0, 0, "current bounded TRUE prepass")
        offline_tail = ("offline_true_tail", 60.0, 0, "remaining deterministic TRUE routes")
        if profile.known == "true":
            routes = [offline_tail]
        elif profile.known == "false":
            routes = false_routes
        else:
            routes = [offline_quick] + false_routes + [offline_tail]
    else:
        infinite_false = ("infinite_parity", 1.0, 0, "judge-gated infinite countermodel")
        if profile.known == "true":
            routes = true_routes
        elif profile.known == "false":
            routes = [false_routes[0], infinite_false, false_routes[1]]
        else:
            # Unknown is not a verdict.  It retains both certificate directions.
            routes = [false_routes[0], infinite_false] + true_routes + [false_routes[1]]

    if policy == "structural_v0":
        if profile.collapse_signal or profile.substitution_signal:
            routes.sort(
                key=lambda row: (
                    0
                    if row[0]
                    in {"offline_true_quick", "offline_true_tail", "structural_true", "lemma_chain_quick"}
                    else 1
                )
            )
        elif profile.known != "true" and profile.search_scale <= 3:
            routes.sort(key=lambda row: (0 if row[0] in {"finite_ce", "csp_false"} else 1))

    if track == "solo":
        llm_route = "llm_true" if profile.known == "true" else "llm_false"
        if profile.known == "unknown":
            llm_route = "llm_both"
        routes.append((llm_route, 900.0, 16384, "reserved judge-guided fallback"))
    return routes


def build_schedule(
    profile: GoalProfile,
    track: str,
    available_seconds: float,
    available_tokens: int = 0,
    policy: str = "baseline",
) -> tuple[RouteAttempt, ...]:
    """Return a bounded route schedule, never a verdict.

    This is an uncalibrated reference schedule, not an exact EULER baseline or a
    drop-in executor.  The compact EULER transplant changes priority only.
    ``structural_v0`` may reorder routes and is intentionally opt-in.  Requested
    token caps are conservative because the Marathon proxy charges reservations
    even for some failed calls.
    """

    track = track.lower() if isinstance(track, str) else ""
    policy = policy.lower() if isinstance(policy, str) else "baseline"
    if track not in {"solo", "marathon"}:
        raise ValueError("track must be 'solo' or 'marathon'")
    if policy not in {"baseline", "structural_v0"}:
        policy = "baseline"
    if (
        not isinstance(available_seconds, (int, float))
        or isinstance(available_seconds, bool)
        or not math.isfinite(available_seconds)
        or type(available_tokens) is not int
        or available_seconds <= 0
        or available_tokens < 0
    ):
        raise ValueError("budgets must be non-negative and time must be positive")

    raw = _route_plan(profile, track, policy)
    # Do not let an infeasible token route shrink the deterministic allocations.
    raw = [
        row
        for row in raw
        if row[2] == 0 or min(row[2], available_tokens) >= 512
    ]
    desired = sum(row[1] for row in raw)
    scale = min(1.0, available_seconds / desired) if desired else 1.0
    remaining_seconds = float(available_seconds)
    remaining_tokens = int(available_tokens)
    attempts: list[RouteAttempt] = []
    for route, seconds, tokens, reason in raw:
        allocated = min(remaining_seconds, max(0.01, seconds * scale))
        if allocated <= 0:
            break
        token_cap = min(tokens, remaining_tokens)
        if tokens and token_cap < 512:
            continue
        attempts.append(RouteAttempt(route, allocated, token_cap, reason))
        remaining_seconds -= allocated
        remaining_tokens -= token_cap
    return tuple(attempts)


@dataclass(frozen=True, slots=True)
class Episode:
    """One route attempt for offline calibration; timeouts are explicit."""

    problem_id: str
    group_id: str
    profile_id: str
    route: str
    time_limit_seconds: float
    elapsed_seconds: float
    accepted: bool
    timed_out: bool
    token_reservation: int = 0
    certificate_bytes: int = 0
    failure_reason: str = ""
    toolchain_commit: str = ""
    policy_version: str = POLICY_VERSION

    def __post_init__(self) -> None:
        if (
            not isinstance(self.time_limit_seconds, (int, float))
            or isinstance(self.time_limit_seconds, bool)
            or not isinstance(self.elapsed_seconds, (int, float))
            or isinstance(self.elapsed_seconds, bool)
            or not math.isfinite(self.time_limit_seconds)
            or not math.isfinite(self.elapsed_seconds)
            or self.time_limit_seconds <= 0
            or self.elapsed_seconds < 0
        ):
            raise ValueError("episode times are invalid")
        if type(self.accepted) is not bool or type(self.timed_out) is not bool:
            raise ValueError("accepted and timed_out must be booleans")
        if (
            type(self.token_reservation) is not int
            or type(self.certificate_bytes) is not int
            or self.token_reservation < 0
            or self.certificate_bytes < 0
        ):
            raise ValueError("episode costs are invalid")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (
                self.problem_id,
                self.group_id,
                self.profile_id,
                self.route,
                self.toolchain_commit,
                self.policy_version,
            )
        ):
            raise ValueError("episode identifiers, route, toolchain, and policy are required")
        if not isinstance(self.failure_reason, str):
            raise ValueError("failure_reason must be a string")
        if self.accepted and self.timed_out:
            raise ValueError("an accepted episode cannot be timed out")
        if self.accepted and self.certificate_bytes <= 0:
            raise ValueError("accepted episodes require a certificate byte count")
        if not self.accepted and not self.failure_reason.strip():
            raise ValueError("unsuccessful episodes require a failure reason")

    def to_json(self) -> str:
        return json.dumps(
            asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False
        )


def profile_json(profile: GoalProfile) -> str:
    return json.dumps(profile.to_dict(), sort_keys=True, separators=(",", ":"))


__all__: Sequence[str] = (
    "VERSION",
    "POLICY_VERSION",
    "EquationShape",
    "GoalProfile",
    "RouteAttempt",
    "Episode",
    "equation_shape",
    "profile_implication",
    "baseline_priority",
    "structural_priority",
    "priority",
    "compat_priority",
    "build_schedule",
    "profile_json",
)
