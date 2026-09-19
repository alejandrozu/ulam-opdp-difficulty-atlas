#!/usr/bin/env python3
"""Build an append-only OPDP v1.8 ProofAtlas expansion.

This is intentionally an *offline* builder.  It consumes only a locally frozen,
curator-approved JSONL snapshot; it never fetches ProofAtlas and it never treats
a source status as independently verified.  The v1.7 base is parsed and emitted
one record at a time so the current large exchange export does not need to fit
in memory.  Every base record is carried through unchanged at JSON value level.

The curated input contract is deliberately small and explicit::

  {"schema":"opdp.proofatlas.curated-problem.v1",
   "snapshot":{"snapshot_id":"...", "curation_status":"approved_for_opdp_append",
               "source_name":"ProofAtlas", "source_url":"https://..."},
   "problem":{"namespace_number":1, "source_id":"...", "title":"...",
              "statement":"...", "source_url":"https://...",
              "status":"open", "attribution":{"source_name":"ProofAtlas"}}}

``namespace_number`` is a curator-assigned, stable positive integer.  It maps
reversibly to ``50,000,000 + namespace_number`` and the visible identifier is
``PROOFATLAS-{source_id}``.  It is intentionally not inferred from a title or
web URL, which prevents an upstream presentation change from renumbering OPDP.

All generated ProofAtlas dimensions are C0 provisional cue-based estimates.
They are useful as clearly quarantined intake records, not as verified openness
or source-complete final assessments.  A later evidence/recovery process can
replace those estimates without modifying the copied v1.7 prefix.
"""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import io
import json
import math
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import urlsplit

# Reuse the already-audited bounded streaming parser rather than loading the
# 100k-record v1.7 JSON value as one Python string/object.
from validate_v1_7_mathdb_expansion import (  # type: ignore
    DuplicateKeyError,
    StreamedPayload,
    ValidationError,
    canonical_digest,
    sha256_file,
    strict_json_line,
)


RELEASE_VERSION = "1.8.0"
RELEASE_DATE = "2026-09-19"
RULE_VERSION = "OPDP-1.0-rulepass-2026-07-31"
AI_PROTOCOL_ID = "frontier-generalist-hs1-2026-07-31"
EXPECTED_BASE_VERSION = "1.7.0"
EXPECTED_BASE_COUNT = 102_563
RECORD_SCHEMA_VERSION = "1.0.0"
PROOFATLAS_NAMESPACE_OFFSET = 50_000_000
PROOFATLAS_SET_ID = 500_000
CURATED_SCHEMA = "opdp.proofatlas.curated-problem.v1"
MANIFEST_SCHEMA = "opdp.proofatlas.curated-snapshot-manifest.v1"
STATEMENT_PROVENANCE_NORMALIZATION = "curator_authored_independent_normalization"
STATEMENT_PROVENANCE_CLEARED_SOURCE = "source_text_with_declared_rights_clearance"
RIGHTS_CLEARANCE_STATUS = "declared_cleared"

RECORD_FIELD_ORDER = [
    "problem_id",
    "difficulty_label",
    "explanation",
    "problem_number",
    "title",
    "catalog_status",
    "assessment_gate",
    "classification",
    "intrinsic_difficulty",
    "ai_assessment",
    "human_attention",
    "tractability",
    "verification",
    "formalization",
    "prerequisites",
    "tool_leverage",
    "recommended_approach",
    "evidence",
    "rationales",
    "tags",
    "review",
    "flags",
    "source_text",
    "provenance",
    "source_record",
    "implementation",
]

RATIONALE_FIELDS = [
    "intrinsic_difficulty",
    "intrinsic_factors",
    "ai_assessment",
    "tool_leverage",
    "human_attention",
    "tractability",
    "verification",
    "formalization",
    "prerequisites",
    "status_and_data",
]

HOUR_BANDS = [
    (0, 0, 10, "<10"),
    (1, 10, 30, "10-30"),
    (2, 30, 100, "30-100"),
    (3, 100, 300, "100-300"),
    (4, 300, 1_000, "300-1,000"),
    (5, 1_000, 3_000, "1,000-3,000"),
    (6, 3_000, 10_000, "3,000-10,000"),
    (7, 10_000, 30_000, "10,000-30,000"),
    (8, 30_000, 100_000, "30,000-100,000"),
    (9, 100_000, 300_000, "100,000-300,000"),
    (10, 300_000, None, ">300,000"),
]
TRACTABILITY_BANDS = [
    (0, 0.00, 0.01, "<1%"),
    (1, 0.01, 0.03, "1-3%"),
    (2, 0.03, 0.07, "3-7%"),
    (3, 0.07, 0.15, "7-15%"),
    (4, 0.15, 0.25, "15-25%"),
    (5, 0.25, 0.40, "25-40%"),
    (6, 0.40, 0.55, "40-55%"),
    (7, 0.55, 0.70, "55-70%"),
    (8, 0.70, 0.85, "70-85%"),
    (9, 0.85, 0.95, "85-95%"),
    (10, 0.95, 1.00, ">95%"),
]
TIERS = [
    ("T1", "Accessible/Audit", 0.0, 2.5),
    ("T2", "Research Sprint", 2.5, 4.5),
    ("T3", "Serious Project", 4.5, 6.5),
    ("T4", "Frontier Challenge", 6.5, 8.5),
    ("T5", "Grand Challenge", 8.5, 10.000001),
]

# This is the same OPDP category vocabulary as the v1.7 append.  It is a
# deterministic routing aid, not an assertion that ProofAtlas uses this taxon.
CATEGORY_INFO: dict[str, tuple[int, str, str, float, float]] = {
    "Number Theory": (1, "number_theory", "number-theory", 5.7, 4.0),
    "Combinatorics": (2, "combinatorics", "combinatorics", 5.2, 3.7),
    "Graph Theory": (3, "graph_theory", "graph-theory", 5.0, 3.5),
    "Algebra": (4, "algebra", "algebra", 6.2, 4.8),
    "Algebraic Geometry": (5, "algebraic_geometry", "algebraic-geometry", 8.0, 7.2),
    "Geometry": (6, "geometry", "geometry", 5.9, 5.8),
    "Topology": (7, "topology", "topology", 7.1, 6.8),
    "Analysis": (8, "analysis", "analysis", 6.7, 6.7),
    "Partial Differential Equations": (9, "partial_differential_equations", "partial-differential-equations", 7.8, 7.8),
    "Set Theory": (10, "set_theory", "set-theory", 7.2, 5.8),
    "Dynamical Systems": (11, "dynamical_systems", "dynamical-systems", 6.9, 7.0),
    "Computer Science": (15, "computer_science", "computer-science", 5.9, 4.2),
    "Mathematical Physics": (16, "physics", "physics", 7.8, 8.0),
    "Group Theory": (17, "group_theory", "group-theory", 6.3, 4.8),
    "Logic": (18, "logic", "logic", 6.7, 4.8),
    "Probability": (19, "probability", "probability", 6.0, 6.0),
    "Miscellaneous": (20, "miscellaneous", "miscellaneous", 5.0, 5.5),
}
CATEGORY_PATTERNS = [
    ("Algebraic Geometry", r"algebraic geometry|scheme|variet|motivic|\betale\b|stack\b|algebraic cycle"),
    ("Partial Differential Equations", r"partial differential|\bpde\b|navier.?stokes|euler equation|sobolev|elliptic equation|parabolic equation|hyperbolic equation"),
    ("Number Theory", r"number theory|diophant|prime\b|arithmetic geometry|l-function|zeta function|modular form"),
    ("Graph Theory", r"graph theory|hypergraph|digraph|vertex|edge.colou?r|network"),
    ("Combinatorics", r"combinator|ramsey|extremal|matroid|design theory|additive combinatorics"),
    ("Group Theory", r"group theory|finite group|group action|geometric group|representation theory"),
    ("Topology", r"topolog|homotop|homolog|cohomolog|knot theory|low.dimension"),
    ("Dynamical Systems", r"dynamical|ergodic|billiard|periodic orbit|chaos|hamiltonian"),
    ("Probability", r"probab|stochastic|random matrix|percolation|random walk"),
    ("Set Theory", r"set theory|forcing\b|cardinal|continuum hypothesis|descriptive set"),
    ("Logic", r"mathematical logic|model theory|proof theory|computability|recursion theory"),
    ("Computer Science", r"computer science|algorithm|complexity|machine learning|cryptograph|\bp\s*(?:=|vs)\s*np\b"),
    ("Mathematical Physics", r"mathematical physics|quantum|gauge theor|yang.?mills|statistical mechanics|string theory|relativity"),
    ("Analysis", r"analysis|operator algebra|functional analysis|harmonic analysis|measure theory|banach|hilbert space"),
    ("Geometry", r"geometry|manifold|symplectic|contact structure|curvature|geodesic"),
    ("Algebra", r"algebra|ring theory|commutative ring|galois|module|polynomial ideal"),
]
ADVANCED_PATTERNS = [
    r"algebraic cycle|scheme|motivic|étale|\betale\b|derived categor|stack\b|cohomolog|homotop|spectral sequence",
    r"floer|gauge theor|seiberg|symplectic|contact (?:structure|manifold|topology)|teichm",
    r"automorphic|langlands|l-function|elliptic curve|abelian variet|galois representation",
    r"c\*[- ]?algebra|von neumann|operator algebra|banach|hilbert space",
    r"navier.?stokes|yang.?mills|nonlinear pde|partial differential|regularity|sobolev",
    r"infinite[- ]dimensional|measurable cardinal|forcing\b|large cardinal|model theory",
]
CONTINUOUS_TACIT = re.compile(r"manifold|knot|embedding|isotop|symplectic|contact|curvature|geodesic|fluid|pde|dynamical|quantum|gauge|hamiltonian|measure space", re.I)
COMPUTE = re.compile(r"algorithm|compute|enumerat|finite graph|finite group|matrix|polynomial|integer solution|ramsey number|coloring|tiling|packing|configuration|code\b|sat\b|linear programming|optimization|exact value", re.I)
FORMAL = re.compile(r"for every|for all|there exists|does there exist|is it true|prove or disprove|conjecture|if .* then|such that|supremum|infimum", re.I)
PROGRAMMATIC = re.compile(r"develop (?:a |the |new )?.*(?:theory|framework|mathematics)|understand the (?:general|full|structure)|model and predict|build (?:a |the )?(?:theory|framework)|investigate (?:all|the general)|comprehensive (?:classification|theory)|research program|what can be said|to what extent", re.I)
RESOLUTION = re.compile(r"\b(?:this (?:problem|conjecture|inequality) is not true|counterexample (?:was|is|has been)|has been (?:solved|settled|disproved)|was (?:solved|settled|disproved)|is now known|affirmative solution|negative solution|independent of zfc)\b", re.I)
FAMOUS = re.compile(r"p versus np|riemann hypothesis|yang.?mills|navier.?stokes|birch and swinnerton|hodge conjecture|collatz|twin prime|goldbach|abc conjecture|hadwiger conjecture|inverse galois|continuum hypothesis|smooth 4.*poincar|jacobian conjecture|odd perfect", re.I)
SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class BuildError(RuntimeError):
    """A release-contract violation that leaves no completed output behind."""


def fail(message: str) -> None:
    raise BuildError(message)


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def value_digest_update(digest: "hashlib._Hash", value: Any) -> None:
    digest.update(compact_json(value).encode("utf-8"))
    digest.update(b"\n")


def normalize_key(value: Any) -> str:
    text = str(value or "").casefold()
    text = "".join(ch for ch in text if not ("\u0300" <= ch <= "\u036f"))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def clean_text(value: Any, maximum: int = 32_700) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    text = "".join(" " if (ord(ch) <= 8 or ord(ch) in {11, 12, 127} or 14 <= ord(ch) <= 31) else ch for ch in text)
    return text if len(text) <= maximum else text[: maximum - 31] + " [truncated by OPDP builder]"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def round1(value: float) -> float:
    return round(value + 1e-12, 1)


def round_half(value: float) -> float:
    return round(value * 2) / 2


def confidence(level: int | None, status: str = "assigned") -> dict[str, Any]:
    if status != "assigned":
        return {"level": None, "code": None, "status": status}
    return {"level": level, "code": f"C{level}", "status": "assigned"}


def tier_for(score: float) -> tuple[str, str]:
    for code, label, low, high in TIERS:
        if low <= score < high:
            return code, label
    return TIERS[-1][0], TIERS[-1][1]


def hour_band(score: int) -> tuple[int, int | None, str]:
    _, minimum, maximum, label = HOUR_BANDS[score]
    return minimum, maximum, label


def tractability_band(score: int) -> tuple[float, float, str, bool]:
    _, low, high, label = TRACTABILITY_BANDS[score]
    return low, high, label, score == 10


def find_controls(value: Any, pointer: str = "") -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, str):
        for index, char in enumerate(value):
            number = ord(char)
            if number <= 8 or number in {11, 12, 127} or 14 <= number <= 31:
                found.append({"path": pointer, "index": index, "code_point": f"U+{number:04X}"})
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(find_controls(item, f"{pointer}/{index}"))
    elif isinstance(value, dict):
        for key, item in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            found.extend(find_controls(item, f"{pointer}/{escaped}"))
    return found


def hash_statement(value: Any) -> str:
    return hashlib.sha256(normalize_key(value).encode("utf-8")).hexdigest()


def valid_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or any(character.isspace() or ord(character) < 32 for character in candidate):
        return None
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        return None
    return candidate


def statement_provenance_mode(snapshot: dict[str, Any], prefix: str) -> str:
    """Validate the curator's public-text provenance/rights declaration.

    An append output retains each supplied statement, so a generic source URL or
    a null license is not enough to justify copying source prose.  The default
    mode is an independently authored normalization; copying source text is
    accepted only when a curator has recorded a concrete clearance declaration.
    This is a contract gate, not a legal opinion or independent verification of
    the declaration.
    """

    provenance = snapshot.get("statement_provenance")
    if not isinstance(provenance, dict):
        fail(f"{prefix} snapshot.statement_provenance must be an object")
    mode = provenance.get("mode")
    if mode == STATEMENT_PROVENANCE_NORMALIZATION:
        attestation = provenance.get("attestation")
        if not isinstance(attestation, str) or not attestation.strip():
            fail(
                f"{prefix} curator-authored normalization requires a nonempty "
                "snapshot.statement_provenance.attestation"
            )
        return mode
    if mode == STATEMENT_PROVENANCE_CLEARED_SOURCE:
        clearance = provenance.get("rights_clearance")
        if not isinstance(clearance, dict):
            fail(
                f"{prefix} source-text mode requires "
                "snapshot.statement_provenance.rights_clearance"
            )
        if clearance.get("status") != RIGHTS_CLEARANCE_STATUS:
            fail(
                f"{prefix} source-text mode requires rights_clearance.status="
                f"{RIGHTS_CLEARANCE_STATUS!r}"
            )
        for key in ("basis", "reference"):
            if not isinstance(clearance.get(key), str) or not clearance[key].strip():
                fail(
                    f"{prefix} source-text mode requires a nonempty "
                    f"rights_clearance.{key}"
                )
        return mode
    fail(
        f"{prefix} snapshot.statement_provenance.mode must be "
        f"{STATEMENT_PROVENANCE_NORMALIZATION!r} or "
        f"{STATEMENT_PROVENANCE_CLEARED_SOURCE!r}"
    )


def categorise(text: str) -> str:
    for name, pattern in CATEGORY_PATTERNS:
        if re.search(pattern, text, re.I):
            return name
    return "Miscellaneous"


def answer_type(text: str) -> str:
    if PROGRAMMATIC.search(text):
        return "programmatic"
    if re.search(r"exact value|determine (?:the )?(?:exact|maximum|minimum)|what is (?:the )?(?:largest|smallest|minimum|maximum|exact)|find all (?:integer|rational|real)? ?solutions", text, re.I):
        return "exact_value"
    if re.search(r"upper bound|lower bound|best (?:possible )?constant|asymptotic|growth rate|order of magnitude|largest|smallest|maximal|minimal|supremum|infimum", text, re.I):
        return "bound"
    if re.search(r"algorithm|polynomial[- ]time|computational complexity|decidable|computable|efficient procedure", text, re.I):
        return "algorithm"
    if re.search(r"classify|characterize|determine all|which (?:groups|spaces|graphs|manifolds|integers|varieties)", text, re.I):
        return "classification"
    if re.search(r"\bconstruct(?:ion)?\b|\bexhibit\b|find an example|give an example", text, re.I):
        return "construction"
    if re.search(r"does there exist|is there (?:a|an|any)|are there infinitely many|existence of|prove .* exists", text, re.I):
        return "existence"
    return "proof_disproof"


def scope_for(statement: str, kind: str) -> str:
    if kind == "programmatic":
        return "programmatic"
    if statement.count("?") >= 2 and not re.search(r"more formally|equivalently|in other words", statement, re.I):
        return "compound"
    if re.search(r"for all|for every|for each|in every dimension|for arbitrary|classify all|determine all", statement, re.I):
        return "family"
    return "atomic"


def mathematical_objects(text: str, fallback: str) -> list[str]:
    patterns = [
        (r"l-function|riemann zeta|automorphic", "L-functions"),
        (r"elliptic curve|abelian variet", "elliptic curves and abelian varieties"),
        (r"algebraic cycle|projective variet|scheme\b", "algebraic varieties and cycles"),
        (r"manifold|cobord|diffeomorph", "manifolds"),
        (r"knot|link invariant|jones polynomial", "knots and link invariants"),
        (r"finite group|group action|automorphism of .*group|subgroup", "groups and group actions"),
        (r"hypergraph", "hypergraphs"),
        (r"graph|tree\b|vertex|edge coloring", "graphs"),
        (r"prime|divisor|diophant|integer solution|perfect number", "primes and Diophantine structure"),
        (r"partial differential|navier|euler equation|regularity", "nonlinear PDE"),
        (r"dynamical|hamiltonian|periodic orbit|billiard", "dynamical systems"),
        (r"operator algebra|c\*[- ]?algebra|von neumann", "operator algebras"),
        (r"probability|random|stochastic", "probabilistic structure"),
        (r"set theory|cardinal|forcing", "set-theoretic foundations"),
        (r"algorithm|complexity|polynomial time|comput", "algorithms and complexity"),
        (r"matroid", "matroids"),
        (r"matrix|determinant|eigenvalue|spectral", "matrices and spectral data"),
        (r"polynomial (?:equation|system|ideal|ring)|algebraic equation", "polynomial systems"),
        (r"ramsey", "Ramsey-type structure"),
        (r"homotop|homolog|cohomolog", "homotopy and (co)homology"),
        (r"symplectic|contact", "symplectic/contact geometry"),
    ]
    found: list[str] = []
    for pattern, label in patterns:
        if re.search(pattern, text, re.I):
            found.append(label)
        if len(found) == 2:
            break
    return found or [fallback.casefold()]


def map_status(value: Any) -> tuple[str, str]:
    """Return (catalog_status, OPDP gate); never infer verified_open."""

    status = normalize_key(value)
    if re.fullmatch(r"solved|resolved|closed|refuted|disproved|false", status):
        return "solved", "solved"
    if re.search(r"partial|progress|claim solved", status):
        return "partially_solved", "status_unclear"
    if status in {"open", "unsolved", "active"}:
        return "open", "source_claimed_open"
    return "open", "status_unclear"


def provisional_assessment(problem: dict[str, Any], assessment_year: int) -> dict[str, Any]:
    """A deliberately conservative C0 lexical pass.

    The numerical layout follows the public OPDP factor shapes so consumers can
    read it without a schema fork.  It is *not* a claim that ProofAtlas supplied
    enough literature or status evidence for calibrated scores.
    """

    title = clean_text(problem["title"], 2_000)
    statement = clean_text(problem["statement"])
    background = clean_text(problem.get("background", ""))
    text = f"{title}\n{statement}\n{background}"
    category = categorise(text)
    category_id, category_name, category_slug, category_prior, formal_prior = CATEGORY_INFO[category]
    kind = answer_type(f"{title}\n{statement}")
    scope = scope_for(statement, kind)
    catalog_status, gate = map_status(problem.get("status"))
    advanced = sum(bool(re.search(pattern, text, re.I)) for pattern in ADVANCED_PATTERNS)
    references = min(99, len(re.findall(r"\[[A-Za-z]{1,8}\d{2,4}[a-z]?\]|doi:|arxiv:", background, re.I)))
    breadth = min(5, max(1, sum(bool(re.search(pattern, text, re.I)) for pattern in [
        r"number theory|prime|diophant|integer", r"combinator|graph|ramsey|matroid",
        r"algebra|group|ring|galois|representation", r"geometry|manifold|topolog|knot|homotop",
        r"analysis|pde|measure|operator|functional", r"probab|random|stochastic",
        r"algorithm|complexity|comput", r"physics|quantum|statistical mechanics",
        r"logic|set theory|cardinal|model theory",
    ])))
    ambiguity = round1(clamp(1.5 + (2.0 if scope == "compound" else 0) + (5.0 if scope == "programmatic" else 0) + (1.2 if len(statement) < 80 else 0) - (0.7 if FORMAL.search(statement) else 0), 0, 10))
    prereq = round1(clamp(category_prior + 0.55 * advanced + 0.35 * (breadth - 1), 0, 10))
    formalization = round1(clamp(formal_prior + (2.3 if scope == "programmatic" else 0) + (0.7 if scope == "compound" else 0) + (0.5 if CONTINUOUS_TACIT.search(text) else 0) - (0.8 if COMPUTE.search(statement) and FORMAL.search(statement) else 0), 0, 10))
    leverage = round1(clamp(4.5 + (2.0 if kind == "exact_value" else 0) + (1.5 if kind in {"algorithm", "construction"} else 0) + (1.2 if COMPUTE.search(statement) else 0) - (3.0 if scope == "programmatic" else 0) - (1.0 if CONTINUOUS_TACIT.search(statement) else 0), 0, 10))
    factor_base = {"exact_value": 1.5, "bound": 2.0, "algorithm": 2.1, "construction": 1.9, "existence": 2.3, "classification": 2.6, "proof_disproof": 2.4, "programmatic": 3.4}.get(kind, 2.4)
    conceptual = round_half(clamp(factor_base + (0.25 if scope == "compound" else 0), 0, 4))
    route = round_half(clamp({"exact_value": 1.7, "bound": 2.2, "algorithm": 2.2, "construction": 2.0, "existence": 2.3, "classification": 2.6, "proof_disproof": 2.4, "programmatic": 3.5}.get(kind, 2.4) + (0.25 if scope == "compound" else 0), 0, 4))
    technical = round_half(clamp(prereq * 0.4 + (0.25 if scope == "compound" else 0), 0, 4))
    barrier = round_half(clamp(1.2 + 0.2 * min(4, references / 4) + (0.3 if FAMOUS.search(title) else 0), 0, 4))
    search = round_half(clamp({"exact_value": 1.6, "bound": 2.4, "algorithm": 2.5, "construction": 2.3, "existence": 2.6, "classification": 3.0, "proof_disproof": 2.8, "programmatic": 3.8}.get(kind, 2.8) + (0.25 if scope == "family" else 0) + (0.35 if scope == "compound" else 0), 0, 4))
    difficulty = round1(2.5 * (0.30 * conceptual + 0.20 * route + 0.20 * technical + 0.20 * barrier + 0.10 * search))
    if catalog_status == "solved":
        difficulty = min(difficulty, 8.5)
    d_low, d_high = round1(clamp(difficulty - 3.0, 0, 10)), round1(clamp(difficulty + 3.0, 0, 10))
    mean_verify = round1(clamp(2.5 + 0.28 * formalization + 0.13 * prereq - 0.08 * leverage, 0, 10))
    ai_formal = -1 if formalization <= 4 and ambiguity <= 3 else (1 if formalization >= 7 or ambiguity >= 7 else 0)
    ai_feedback = -1 if mean_verify <= 3 else (1 if mean_verify >= 6 else 0)
    ai_tool = -1 if leverage >= 7 else (1 if leverage <= 3 else 0)
    ai_context = 1 if references >= 12 else 0
    ai_horizon = 1 if difficulty >= 7 or scope == "programmatic" else 0
    ai_tacit = 1 if CONTINUOUS_TACIT.search(text) or scope == "programmatic" else (-1 if COMPUTE.search(statement) and prereq <= 6 else 0)
    ai_relative = round1(0.5 * sum([ai_formal, ai_feedback, ai_tool, ai_context, ai_horizon, ai_tacit]))
    ai_difficulty = round1(clamp(difficulty + ai_relative, 0, 10))
    ai_low, ai_high = round1(clamp(ai_difficulty - 3.5, 0, 10)), round1(clamp(ai_difficulty + 3.5, 0, 10))
    ai_fit = "AI-favored" if ai_relative <= -1 else ("AI-hostile" if ai_relative >= 1 else "AI-neutral/mixed")
    tractability = None if gate == "solved" else int(round(clamp(8.0 - 0.75 * ai_difficulty + 0.2 * leverage - 0.15 * mean_verify, 0, 10)))
    human_effort = min(10, max(1, 1 + (1 if references >= 3 else 0) + (1 if references >= 10 else 0)))
    exposure = 5 if FAMOUS.search(title) else 1
    objects = mathematical_objects(text, category)
    tier_code, tier_label = tier_for(difficulty)
    source_status = str(problem.get("status") or "unspecified")
    caveat = " ProofAtlas source text and status have not been independently verified; every assigned estimate is provisional C0 and is not a claim of current openness."
    route_label = "Status and literature audit" if gate in {"status_unclear", "solved"} else ("Scope decomposition and success-criterion rewrite" if scope == "programmatic" else "Expert-guided proof search")
    return {
        "title": title,
        "statement": statement,
        "background": background,
        "category": category,
        "category_id": category_id,
        "category_name": category_name,
        "category_slug": category_slug,
        "answer_type": kind,
        "scope": scope,
        "catalog_status": catalog_status,
        "gate": gate,
        "ambiguity": ambiguity,
        "references": references,
        "breadth": breadth,
        "prereq": prereq,
        "formalization": formalization,
        "leverage": leverage,
        "conceptual": conceptual,
        "route": route,
        "technical": technical,
        "barrier": barrier,
        "search": search,
        "difficulty": difficulty,
        "d_low": d_low,
        "d_high": d_high,
        "tier_code": tier_code,
        "tier_label": tier_label,
        "verification": mean_verify,
        "ai_relative": ai_relative,
        "ai_difficulty": ai_difficulty,
        "ai_low": ai_low,
        "ai_high": ai_high,
        "ai_fit": ai_fit,
        "ai_predictors": {
            "formal_fit": ai_formal,
            "verification_feedback": ai_feedback,
            "tool_fit": ai_tool,
            "context_load": ai_context,
            "reasoning_horizon": ai_horizon,
            "tacit_experimental_insight": ai_tacit,
        },
        "tractability": tractability,
        "human_effort": human_effort,
        "exposure": exposure,
        "objects": objects,
        "route_label": route_label,
        "caveat": caveat,
        "source_status": source_status,
    }


def validate_envelope(entry: Any, line_number: int) -> dict[str, Any]:
    prefix = f"curated input line {line_number}:"
    if not isinstance(entry, dict):
        fail(f"{prefix} JSONL entry must be an object")
    if entry.get("schema") != CURATED_SCHEMA:
        fail(f"{prefix} schema must be {CURATED_SCHEMA!r}")
    snapshot = entry.get("snapshot")
    problem = entry.get("problem")
    if not isinstance(snapshot, dict) or not isinstance(problem, dict):
        fail(f"{prefix} snapshot and problem must be objects")
    if snapshot.get("curation_status") != "approved_for_opdp_append":
        fail(f"{prefix} snapshot.curation_status must be 'approved_for_opdp_append'")
    if normalize_key(snapshot.get("source_name")) != "proofatlas":
        fail(f"{prefix} snapshot.source_name must be 'ProofAtlas'")
    if not isinstance(snapshot.get("snapshot_id"), str) or not snapshot["snapshot_id"].strip():
        fail(f"{prefix} snapshot.snapshot_id must be a nonempty string")
    if not valid_url(snapshot.get("source_url")):
        fail(f"{prefix} snapshot.source_url must be an http(s) URL")
    statement_provenance_mode(snapshot, prefix)
    number = problem.get("namespace_number")
    if isinstance(number, bool) or not isinstance(number, int) or not (1 <= number < 10_000_000):
        fail(f"{prefix} problem.namespace_number must be an integer in [1, 9,999,999]")
    source_id = problem.get("source_id")
    if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
        fail(f"{prefix} problem.source_id must match {SOURCE_ID.pattern!r}")
    for key in ("title", "statement"):
        if not isinstance(problem.get(key), str) or not problem[key].strip():
            fail(f"{prefix} problem.{key} must be a nonempty string")
    if problem.get("statement_completeness", "full_statement") != "full_statement":
        fail(f"{prefix} accepts only curator-declared full_statement records")
    attribution = problem.get("attribution")
    if not isinstance(attribution, dict) or not isinstance(attribution.get("source_name"), str):
        fail(f"{prefix} problem.attribution.source_name must be a string")
    source_url = problem.get("source_url")
    if not valid_url(source_url):
        fail(
            f"{prefix} problem.source_url must be an http(s) URL so each "
            "curator-authored statement retains a problem-level provenance locator"
        )
    return entry


def load_curated_input(path: Path, limit: int | None) -> tuple[list[dict[str, Any]], str, str]:
    if not path.is_file():
        fail(f"curated input does not exist: {path}")
    entries: list[dict[str, Any]] = []
    prior_number = 0
    source_ids: set[str] = set()
    source_ids_casefolded: set[str] = set()
    snapshot_digest: str | None = None
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:  # type: ignore[arg-type]
        for line_number, text in enumerate(handle, start=1):
            if not text.strip():
                continue
            entry = validate_envelope(strict_json_line(text, source=path, line_number=line_number), line_number)
            number = entry["problem"]["namespace_number"]
            source_id = entry["problem"]["source_id"]
            if number <= prior_number:
                fail(f"curated input line {line_number}: namespace_number must be strictly increasing")
            if source_id in source_ids:
                fail(f"curated input line {line_number}: duplicate source_id {source_id!r}")
            if source_id.casefold() in source_ids_casefolded:
                fail(
                    f"curated input line {line_number}: source_id collides "
                    f"case-insensitively with an earlier source_id: {source_id!r}"
                )
            prior_number = number
            source_ids.add(source_id)
            source_ids_casefolded.add(source_id.casefold())
            current_snapshot = canonical_digest(entry["snapshot"])
            if snapshot_digest is None:
                snapshot_digest = current_snapshot
            elif snapshot_digest != current_snapshot:
                fail(f"curated input line {line_number}: every entry must bind to the same frozen snapshot object")
            entries.append(entry)
            if limit is not None and len(entries) >= limit:
                break
    if not entries:
        fail("curated input has no selected entries")
    input_sha = sha256_file(path).upper()
    return entries, input_sha, snapshot_digest or ""


def load_manifest(path: Path, *, input_path: Path, input_sha: str, count: int, snapshot_id: str) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"invalid ProofAtlas manifest {path}: {exc}")
    if not isinstance(manifest, dict):
        fail("ProofAtlas manifest must be an object")
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("status") != "complete":
        fail(f"ProofAtlas manifest must have schema={MANIFEST_SCHEMA!r} and status='complete'")
    if manifest.get("source_file") != input_path.name:
        fail("ProofAtlas manifest source_file does not match --proofatlas basename")
    if str(manifest.get("sha256", "")).upper() != input_sha:
        fail("ProofAtlas manifest sha256 does not match --proofatlas")
    if manifest.get("record_count") != count:
        fail("ProofAtlas manifest record_count does not match selected input count")
    if manifest.get("snapshot_id") != snapshot_id:
        fail("ProofAtlas manifest snapshot_id does not match curated entries")
    return manifest


class SummaryAccumulator:
    """Recompute public summary counters from streamed records, not cached input."""

    def __init__(self) -> None:
        self.total = 0
        self.catalog = Counter({key: 0 for key in ["open", "partially_solved", "solved"]})
        self.gates = Counter({key: 0 for key in ["verified_open", "source_claimed_open", "status_unclear", "solved", "ill_posed"]})
        self.tiers = Counter({key: 0 for key in ["T1", "T2", "T3", "T4", "T5"]})
        self.fits = Counter({key: 0 for key in ["ai_favored", "ai_neutral_mixed", "ai_hostile"]})
        self.confidence = Counter({f"C{i}": 0 for i in range(4)})
        self.scopes = Counter({key: 0 for key in ["atomic", "family", "compound", "programmatic"]})
        self.answers = Counter({key: 0 for key in ["proof_disproof", "bound", "existence", "algorithm", "classification", "construction", "exact_value", "programmatic", "experimental"]})
        self.hours = Counter({f"H{i}": 0 for i in range(11)})
        self.tractability = Counter({**{f"T{i}": 0 for i in range(11)}, "not_applicable": 0})
        self.priorities = Counter({key: 0 for key in ["P0", "P1", "P2", "P3"]})
        self.tags: Counter[str] = Counter()
        self.flags: Counter[str] = Counter()
        self.categories: Counter[str] = Counter()
        self.sources: Counter[str] = Counter()
        self.routes: Counter[str] = Counter()
        self.ids: set[int] = set()
        self.numbers: set[str] = set()
        self.number_group_counts: Counter[str] = Counter()
        self.flagged = self.curation = self.tractability_count = 0
        self.sum_d = self.sum_ai = self.sum_t = 0.0
        self.fallback_urls = self.malformed_urls = self.controls = self.control_records = 0
        self.missing_set = self.missing_nested_set = self.zero_tractability = 0

    @staticmethod
    def _nested(value: Any, *keys: str, default: Any = None) -> Any:
        current = value
        for key in keys:
            if not isinstance(current, dict):
                return default
            current = current.get(key)
        return current if current is not None else default

    def update(self, record: dict[str, Any]) -> None:
        self.total += 1
        problem_id = record.get("problem_id")
        number = record.get("problem_number")
        if isinstance(problem_id, int):
            self.ids.add(problem_id)
        if isinstance(number, str):
            self.numbers.add(number)
            self.number_group_counts[number] += 1
        self.catalog[str(record.get("catalog_status"))] += 1
        self.gates[str(self._nested(record, "assessment_gate", "value", default="unknown"))] += 1
        self.tiers[str(self._nested(record, "intrinsic_difficulty", "tier_code", default="unknown"))] += 1
        self.fits[str(self._nested(record, "ai_assessment", "fit", default="unknown"))] += 1
        code = self._nested(record, "intrinsic_difficulty", "confidence", "code")
        if code is not None:
            self.confidence[str(code)] += 1
        self.scopes[str(self._nested(record, "classification", "scope", default="unknown"))] += 1
        self.answers[str(self._nested(record, "classification", "answer_type", default="unknown"))] += 1
        hour = self._nested(record, "human_attention", "effort", "code")
        if hour is not None:
            self.hours[str(hour)] += 1
        tractability_code = self._nested(record, "tractability", "code")
        tractability_score = self._nested(record, "tractability", "score")
        if tractability_code is None:
            self.tractability["not_applicable"] += 1
        else:
            self.tractability[str(tractability_code)] += 1
            self.tractability_count += 1
            if isinstance(tractability_score, (int, float)) and not isinstance(tractability_score, bool):
                self.sum_t += tractability_score
        priority = self._nested(record, "review", "priority_code")
        if priority is not None:
            self.priorities[str(priority)] += 1
        category = self._nested(record, "classification", "category", "label")
        source = self._nested(record, "classification", "source_collection", "label")
        route = self._nested(record, "recommended_approach", "route_code")
        if category is not None:
            self.categories[str(category)] += 1
        if source is not None:
            self.sources[str(source)] += 1
        if route is not None:
            self.routes[str(route)] += 1
        for tag in record.get("tags", []) if isinstance(record.get("tags"), list) else []:
            self.tags[str(tag)] += 1
        flags = record.get("flags", []) if isinstance(record.get("flags"), list) else []
        for flag in flags:
            self.flags[str(flag)] += 1
        self.flagged += int(bool(flags))
        self.curation += int("needs_curation" in flags)
        difficulty = self._nested(record, "intrinsic_difficulty", "score")
        ai = self._nested(record, "ai_assessment", "difficulty_score")
        if isinstance(difficulty, (int, float)) and not isinstance(difficulty, bool):
            self.sum_d += difficulty
        if isinstance(ai, (int, float)) and not isinstance(ai, bool):
            self.sum_ai += ai
        source_kind = str(self._nested(record, "provenance", "source_url_kind", default=""))
        self.fallback_urls += int("fallback" in source_kind)
        qa_flags = self._nested(record, "provenance", "source_url_qa_flags", default=[])
        self.malformed_urls += int(isinstance(qa_flags, list) and "duplicated_scheme" in qa_flags)
        control_count = self._nested(record, "source_text", "transport_qa", "forbidden_control_character_count", default=0)
        if isinstance(control_count, int):
            self.controls += control_count
            self.control_records += int(control_count > 0)
        set_id = self._nested(record, "classification", "source_collection", "id")
        nested = self._nested(record, "classification", "source_collection", "nested_object_present")
        self.missing_set += int(set_id is None)
        self.missing_nested_set += int(set_id is not None and nested is not True)
        self.zero_tractability += int(tractability_score == 0)

    def finish(self) -> dict[str, Any]:
        duplicate_groups = sum(1 for count in self.number_group_counts.values() if count > 1)
        duplicate_records = sum(count for count in self.number_group_counts.values() if count > 1)
        return {
            "records_total": self.total,
            "catalog_status_counts": dict(sorted(self.catalog.items())),
            "assessment_gate_counts": dict(sorted(self.gates.items())),
            "difficulty_tier_counts": dict(sorted(self.tiers.items())),
            "ai_fit_counts": dict(sorted(self.fits.items())),
            "difficulty_confidence_counts": dict(sorted(self.confidence.items())),
            "scope_counts": dict(sorted(self.scopes.items())),
            "answer_type_counts": dict(sorted(self.answers.items())),
            "human_effort_band_counts": dict(sorted(self.hours.items())),
            "tractability_band_counts": dict(sorted(self.tractability.items())),
            "review_priority_counts": dict(sorted(self.priorities.items())),
            "tag_counts": dict(sorted(self.tags.items())),
            "flag_counts": dict(sorted(self.flags.items())),
            "category_counts": dict(sorted(self.categories.items())),
            "source_collection_counts": dict(sorted(self.sources.items())),
            "records_with_flags": self.flagged,
            "records_needing_curation": self.curation,
            "tractability_scored_records": self.tractability_count,
            "means": {
                "intrinsic_difficulty": round1(self.sum_d / self.total) if self.total else None,
                "ai_difficulty": round1(self.sum_ai / self.total) if self.total else None,
                "tractability": round1(self.sum_t / self.tractability_count) if self.tractability_count else None,
            },
            "corpus_audit": {
                "unique_problem_ids": len(self.ids),
                "unique_problem_numbers": len(self.numbers),
                "duplicate_problem_number_groups": duplicate_groups,
                "records_in_duplicate_problem_number_groups": duplicate_records,
                "surplus_problem_number_records": duplicate_records - duplicate_groups,
                "source_url_fallback_records": self.fallback_urls,
                "malformed_duplicated_scheme_source_urls": self.malformed_urls,
                "forbidden_control_character_occurrences": self.controls,
                "records_with_forbidden_control_characters": self.control_records,
                "missing_set_id_records": self.missing_set,
                "set_id_present_but_nested_set_missing_records": self.missing_nested_set,
                "intentional_null_tractability_records": self.total - self.tractability_count,
                "valid_zero_tractability_records": self.zero_tractability,
            },
            "route_counts": dict(sorted(self.routes.items())),
            "summary_semantics": "Cached convenience values recomputable from records; means use the stated denominator and are rounded to one decimal.",
        }


def inspect_base(path: Path, max_value_chars: int) -> tuple[dict[str, Any], dict[str, Any], set[int], set[str], dict[str, list[int]], dict[str, list[int]], SummaryAccumulator, str, str, int]:
    """First base pass: validate shape, compute semantic digest and compact indexes."""

    if not path.is_file():
        fail(f"base export does not exist: {path}")
    base_ids: set[int] = set()
    base_numbers: set[str] = set()
    titles: dict[str, list[int]] = defaultdict(list)
    statements: dict[str, list[int]] = defaultdict(list)
    accumulator = SummaryAccumulator()
    digest = hashlib.sha256()
    count = 0
    with StreamedPayload(path, max_value_chars=max_value_chars) as source:
        metadata = source.start()
        schema = metadata.get("schema")
        if not isinstance(schema, dict) or schema.get("record_field_order") != RECORD_FIELD_ORDER:
            fail("base export does not declare the canonical OPDP 1.0 record field order")
        for record in source.records():
            if list(record) != RECORD_FIELD_ORDER:
                fail(f"base record {record.get('problem_id')!r} violates its own record-field order")
            problem_id = record.get("problem_id")
            if not isinstance(problem_id, int) or isinstance(problem_id, bool):
                fail("base record has no integer problem_id")
            if problem_id in base_ids:
                fail(f"base export has duplicate problem_id {problem_id}")
            base_ids.add(problem_id)
            problem_number = record.get("problem_number")
            if isinstance(problem_number, str):
                base_numbers.add(problem_number)
            title = normalize_key(record.get("title"))
            if title:
                titles[title].append(problem_id)
            statement = record.get("source_text", {}).get("statement") if isinstance(record.get("source_text"), dict) else ""
            if normalize_key(statement):
                statements[hash_statement(statement)].append(problem_id)
            accumulator.update(record)
            value_digest_update(digest, record)
            count += 1
    return (
        metadata,
        schema,
        base_ids,
        base_numbers,
        titles,
        statements,
        accumulator,
        digest.hexdigest().upper(),
        canonical_digest(metadata).upper(),
        count,
    )


def add_index(mapping: dict[str, list[int]], key: str, problem_id: int) -> None:
    if key:
        mapping.setdefault(key, []).append(problem_id)


def peer_ids(mapping: dict[str, list[int]], key: str, self_id: int) -> list[int]:
    return [item for item in mapping.get(key, []) if item != self_id]


def build_record(entry: dict[str, Any], *, input_sha: str, assessment_date: str, title_index: dict[str, list[int]], statement_index: dict[str, list[int]], id_index: set[int]) -> dict[str, Any]:
    snapshot = entry["snapshot"]
    problem = entry["problem"]
    namespace_number = problem["namespace_number"]
    problem_id = PROOFATLAS_NAMESPACE_OFFSET + namespace_number
    if problem_id in id_index:
        fail(f"ProofAtlas namespace mapping collides with existing problem_id {problem_id}")
    pnumber = f"PROOFATLAS-{problem['source_id']}"
    assessment = provisional_assessment(problem, int(assessment_date[:4]))
    title_key = normalize_key(assessment["title"])
    statement_key = hash_statement(assessment["statement"])
    title_peers = peer_ids(title_index, title_key, problem_id)
    statement_peers = peer_ids(statement_index, statement_key, problem_id)
    number_peers: list[int] = []  # New namespace IDs cannot collide with v1.7 display IDs by construction.
    controls = find_controls(entry)
    flags = [
        "proofatlas_curated_input",
        "proofatlas_provisional_c0_assessment",
        "source_statement_not_independently_verified",
    ]
    if assessment["gate"] == "status_unclear":
        flags.append("status_NEEDS_REVIEW")
    if assessment["catalog_status"] == "solved":
        flags.append("catalog_solved_record")
    if assessment["scope"] == "compound":
        flags.append("compound_scope")
    if assessment["scope"] == "programmatic":
        flags.append("programmatic_scope")
    if title_peers:
        flags.append("title_collision")
    if statement_peers:
        flags.append("duplicate_statement")
    if controls:
        flags.append("invalid_control_character")
    flags = sorted(set(flags))
    source_url = valid_url(problem.get("source_url")) or snapshot["source_url"]
    status_confidence = confidence(0)
    minimum, maximum, hour_label = hour_band(assessment["human_effort"])
    t_score = assessment["tractability"]
    if t_score is None:
        probability = None
        tractability_confidence = confidence(None, "not_applicable")
    else:
        low, high, label, high_inclusive = tractability_band(t_score)
        probability = {"low": low, "high": high, "low_inclusive": True, "high_inclusive": high_inclusive, "display_label": label}
        tractability_confidence = confidence(0)
    priority_code, priority_label = ("P0", "immediate curation") if assessment["gate"] in {"status_unclear", "solved"} else ("P2", "calibration review")
    category = {"id": assessment["category_id"], "name": assessment["category_name"], "slug": assessment["category_slug"], "label": assessment["category"]}
    source_attribution = copy.deepcopy(problem["attribution"])
    statement_provenance = copy.deepcopy(snapshot["statement_provenance"])
    statement_mode = statement_provenance["mode"]
    caveat = assessment["caveat"]
    result: dict[str, Any] = {
        "problem_id": problem_id,
        "difficulty_label": f"{assessment['tier_code']} {assessment['tier_label']}",
        "explanation": f"{assessment['tier_code']} {assessment['tier_label']}: D{assessment['difficulty']:.1f} and AI {assessment['ai_difficulty']:.1f}; {assessment['route_label'].casefold()} is the initial route.{caveat}",
        "problem_number": pnumber,
        "title": assessment["title"],
        "catalog_status": assessment["catalog_status"],
        "assessment_gate": {"value": assessment["gate"], "confidence": status_confidence},
        "classification": {
            "category": category,
            "source_collection": {"id": PROOFATLAS_SET_ID, "name": "proofatlas", "slug": "proofatlas", "label": "ProofAtlas", "nested_object_present": True},
            "scope": assessment["scope"],
            "answer_type": assessment["answer_type"],
            "mathematical_objects": assessment["objects"],
            "mathematical_objects_summary": "; ".join(assessment["objects"]),
            "ambiguity_score": assessment["ambiguity"],
            "legacy_difficulty": {"id": None, "level": None, "label": None, "description": None, "role_in_opdp": "absent_not_used"},
        },
        "intrinsic_difficulty": {
            "score": assessment["difficulty"],
            "range": {"low": assessment["d_low"], "high": assessment["d_high"]},
            "confidence": confidence(0),
            "tier_code": assessment["tier_code"],
            "tier_label": assessment["tier_label"],
            "factors": {
                "conceptual_gap": {"code": "CG", "score": assessment["conceptual"], "weight": 0.30},
                "route_gap": {"code": "RG", "score": assessment["route"], "weight": 0.20},
                "technical_depth": {"code": "TD", "score": assessment["technical"], "weight": 0.20},
                "known_barrier": {"code": "KB", "score": assessment["barrier"], "weight": 0.20},
                "search_scale": {"code": "SS", "score": assessment["search"], "weight": 0.10},
            },
        },
        "ai_assessment": {
            "relative_adjustment": assessment["ai_relative"],
            "fit": {"AI-favored": "ai_favored", "AI-neutral/mixed": "ai_neutral_mixed", "AI-hostile": "ai_hostile"}[assessment["ai_fit"]],
            "fit_label": assessment["ai_fit"],
            "difficulty_score": assessment["ai_difficulty"],
            "range": {"low": assessment["ai_low"], "high": assessment["ai_high"]},
            "confidence": confidence(0),
            "protocol_id": AI_PROTOCOL_ID,
            "predictors": assessment["ai_predictors"],
            "empirical_problem_episode_run": False,
        },
        "human_attention": {
            "effort": {
                "score": assessment["human_effort"],
                "code": f"H{assessment['human_effort']}",
                "estimated_specialist_hours": {"minimum": minimum, "maximum": maximum, "minimum_inclusive": True, "maximum_inclusive": None if maximum is None else False, "maximum_kind": "unbounded" if maximum is None else "finite", "display_label": hour_label},
                "confidence": confidence(0),
                "estimate_kind": "order_of_magnitude_prior_not_observed_labor",
            },
            "exposure": {"score": assessment["exposure"], "code": f"X{assessment['exposure']}", "confidence": confidence(None, "not_separately_assigned")},
        },
        "tractability": {
            "status": "not_applicable" if t_score is None else "scored",
            "score": t_score,
            "code": None if t_score is None else f"T{t_score}",
            "progress_probability": probability,
            "target_combined_hours": 100,
            "minimum_outcome_level": 3,
            "target": "novel_independently_checked_partial_progress_not_full_resolution",
            "confidence": tractability_confidence,
        },
        "verification": {"meaning": "independent_checking_burden_not_scientific_value", "if_true_score": assessment["verification"], "if_false_score": assessment["verification"], "confidence": confidence(0)},
        "formalization": {"score": assessment["formalization"], "confidence": confidence(0), "meaning": "burden_to_encode_statement_and_prerequisites_not_unknown_proof"},
        "prerequisites": {"preparation_score": assessment["prereq"], "breadth_score": assessment["breadth"], "confidence": confidence(0)},
        "tool_leverage": {"score": assessment["leverage"], "direction": "higher_is_more_favorable", "confidence": confidence(0)},
        "recommended_approach": {"route_code": normalize_key(assessment["route_label"]).replace(" ", "_"), "route_label": assessment["route_label"]},
        "evidence": {
            "codes": ["source:ProofAtlas", "text:curator_declared_full_statement", f"statement:{assessment['answer_type']}/{assessment['scope']}", f"refs:{assessment['references']}", "status:not_independently_verified", assessment["gate"]],
            "reference_signal_count": assessment["references"],
            "estimated_proposal_year": None,
            "proposal_year_basis": "unknown",
            "proposal_year_basis_detail": "not supplied as independently auditable provenance",
            "estimated_age_years_at_assessment": None,
            "literature_load_score": min(10, round1(2 + min(4, assessment["references"] / 4))),
            "collection_barrier_prior": None,
            "score_basis": "deterministic provisional lexical cues from curator-declared full statement; source status, provenance, and statements not independently verified",
            "status_evidence": assessment["source_status"],
            "rights_note": (
                "The curator attests that this statement is an independently authored normalization; "
                "the builder does not infer a license to copy ProofAtlas prose."
                if statement_mode == STATEMENT_PROVENANCE_NORMALIZATION
                else "Source text is retained only under the curator-declared rights-clearance record in the frozen envelope; "
                "the builder does not independently validate that clearance."
            ),
        },
        "rationales": {
            "intrinsic_difficulty": f"D{assessment['difficulty']:.1f} [{assessment['d_low']:.1f}-{assessment['d_high']:.1f}]/C0 - The lexical pass sees a {assessment['scope']} {assessment['answer_type'].replace('_', ' ')} target in {assessment['category']}.{caveat}",
            "intrinsic_factors": f"CG{assessment['conceptual']:.1f}/RG{assessment['route']:.1f}/TD{assessment['technical']:.1f}/KB{assessment['barrier']:.1f}/SS{assessment['search']:.1f} - These values are deterministic statement-shape cues, not observed research barriers.{caveat}",
            "ai_assessment": f"AI{assessment['ai_relative']:+.1f} -> {assessment['ai_difficulty']:.1f} [{assessment['ai_low']:.1f}-{assessment['ai_high']:.1f}]/C0 - This protocol-dated cue estimate is not an agent run result.{caveat}",
            "tool_leverage": f"L{assessment['leverage']:.1f}/10/C0 - Computational and symbolic cues affect possible leverage only; no tools were run against this item.{caveat}",
            "human_attention": f"H{assessment['human_effort']}/C0 - This is an order-of-magnitude prior rather than an observation of human labor.{caveat}",
            "tractability": f"T{t_score if t_score is not None else 'N/A'}/C0 - The 100-hour partial-progress target is a provisional routing heuristic.{caveat}",
            "verification": f"V{assessment['verification']:.1f}/{assessment['verification']:.1f}/C0 - This estimates checking burden conditional on a candidate claim, not mathematical value.{caveat}",
            "formalization": f"F{assessment['formalization']:.1f}/C0 - This estimates statement/prerequisite encoding burden, not an unknown proof's formalization burden.{caveat}",
            "prerequisites": f"P{assessment['prereq']:.1f}/B{assessment['breadth']}/C0 - Category cues supply a provisional preparation prior.{caveat}",
            "status_and_data": f"{assessment['catalog_status']} -> {assessment['gate']}/C0 - Source-declared status is retained as evidence only and requires an independent current-literature audit.{caveat}",
        },
        "tags": ["needs_curation"],
        "review": {
            "priority_code": priority_code,
            "priority_label": priority_label,
            "recommended_curation_action": "Verify provenance, current open status, and statement completeness before replacing the C0 intake assessment.",
            "identity_collisions": {"problem_number_peer_ids": number_peers, "normalized_title_peer_ids": title_peers, "normalized_statement_peer_ids": statement_peers},
            "manual_override": {"applied": False, "reviewer_id": None, "reviewed_at": None, "reason": None, "changes": []},
        },
        "flags": flags,
        "source_text": {
            "title": problem["title"],
            "statement": problem["statement"],
            "background": problem.get("background", ""),
            "text_mode": "proofatlas_curated_full_statement",
            "assessed_title_differs_from_source": assessment["title"] != problem["title"],
            "transport_qa": {"forbidden_control_character_count": len(controls), "occurrences": controls, "json_serialization": "escaped_by_json_encoder", "display_policy": "sanitize_before_rendering_when_count_is_nonzero"},
        },
        "provenance": {
            "ulam_url": None,
            "canonical_source_url": source_url,
            "source_url_used": source_url,
            "source_url_kind": "proofatlas_attributed_source" if valid_url(problem.get("source_url")) else "proofatlas_snapshot_fallback",
            "source_url_verification_status": "not_independently_verified",
            "source_url_qa_flags": [],
            "problem_number_is_unique": True,
            "ulam_url_is_unique": None,
            "source_collection_id": PROOFATLAS_SET_ID,
            "source_collection_label": "ProofAtlas",
            "category_id": assessment["category_id"],
            "category_label": assessment["category"],
            "proposed_by": problem.get("proposed_by"),
            "proposed_year": problem.get("proposed_year"),
            "source_created_at": problem.get("created_at"),
            "source_updated_at": problem.get("updated_at"),
            "source_published": problem.get("published"),
            "engagement": {"view_count": None, "favorite_count": None, "excluded_from_scoring": True},
            "declared_dataset_license": snapshot.get("declared_license"),
            "dataset_terms_url": snapshot.get("terms_url"),
            "proofatlas_url": source_url,
            "proofatlas_source_id": problem["source_id"],
            "proofatlas_namespace_number": namespace_number,
            "proofatlas_snapshot": copy.deepcopy(snapshot),
            "proofatlas_attribution": source_attribution,
            "statement_provenance_mode": statement_mode,
            "statement_rights_clearance": copy.deepcopy(statement_provenance.get("rights_clearance")),
            "statement_completeness": "curator_declared_full_statement_not_independently_verified",
            "category_assignment_method": "deterministic OPDP keyword projection from curator-declared ProofAtlas title, statement, and optional background",
        },
        "source_record": copy.deepcopy(entry),
        "implementation": {
            "record_schema_version": RECORD_SCHEMA_VERSION,
            "dataset_version": RELEASE_VERSION,
            "dataset_sha256": input_sha,
            "rule_version": RULE_VERSION,
            "assessment_date": assessment_date,
            "ai_protocol_id": AI_PROTOCOL_ID,
            "calculation_state": "fresh_provisional_c0",
            "input_mode": "lossless_curated_proofatlas_envelope_plus_stored_editorial_inputs",
            "derived_fields_regenerated": True,
            "expert_certified": False,
            "workbook_formula_canonicalized_fields": [],
            "source_envelope_schema": CURATED_SCHEMA,
            "source_snapshot": copy.deepcopy(snapshot),
        },
    }
    if list(result) != RECORD_FIELD_ORDER:
        fail("internal error: generated ProofAtlas record does not use canonical field order")
    return result


def validate_new_record(record: dict[str, Any], entry: dict[str, Any], base_ids: set[int]) -> list[str]:
    errors: list[str] = []
    number = entry["problem"]["namespace_number"]
    expected_id = PROOFATLAS_NAMESPACE_OFFSET + number
    if list(record) != RECORD_FIELD_ORDER:
        errors.append(f"record {record.get('problem_id')}: canonical field order mismatch")
    if record.get("problem_id") != expected_id or record.get("problem_number") != f"PROOFATLAS-{entry['problem']['source_id']}":
        errors.append(f"record {record.get('problem_id')}: ProofAtlas namespace mapping mismatch")
    if expected_id in base_ids:
        errors.append(f"record {expected_id}: collides with v1.7 base")
    if record.get("source_record") != entry:
        errors.append(f"record {expected_id}: source_record is not lossless")
    if record.get("assessment_gate", {}).get("value") == "verified_open":
        errors.append(f"record {expected_id}: builder must not emit verified_open")
    if record.get("classification", {}).get("source_collection", {}).get("label") != "ProofAtlas":
        errors.append(f"record {expected_id}: missing ProofAtlas source collection")
    if record.get("implementation", {}).get("dataset_version") != RELEASE_VERSION:
        errors.append(f"record {expected_id}: wrong dataset version")
    if not set(["proofatlas_curated_input", "proofatlas_provisional_c0_assessment", "source_statement_not_independently_verified"]).issubset(set(record.get("flags", []))):
        errors.append(f"record {expected_id}: required intake/disclosure flags absent")
    assigned_paths = [
        ("assessment_gate", "confidence"),
        ("intrinsic_difficulty", "confidence"),
        ("ai_assessment", "confidence"),
        ("human_attention", "effort", "confidence"),
        ("verification", "confidence"),
        ("formalization", "confidence"),
        ("prerequisites", "confidence"),
        ("tool_leverage", "confidence"),
    ]
    for path in assigned_paths:
        current: Any = record
        for key in path:
            current = current.get(key) if isinstance(current, dict) else None
        if not isinstance(current, dict) or current.get("code") != "C0":
            errors.append(f"record {expected_id}: {'.'.join(path)} must be C0")
    if record.get("tractability", {}).get("status") == "scored" and record.get("tractability", {}).get("confidence", {}).get("code") != "C0":
        errors.append(f"record {expected_id}: scored tractability must be C0")
    rationales = record.get("rationales", {})
    if not isinstance(rationales, dict) or any("independently verified" not in str(rationales.get(key, "")) for key in RATIONALE_FIELDS):
        errors.append(f"record {expected_id}: independent-verification caveat missing from rationale")
    return errors


def clone_registry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): copy.deepcopy(item) for key, item in value.items()}


def refresh_registries(payload: dict[str, Any], summary: dict[str, Any]) -> None:
    rubric = payload.setdefault("rubric", {})
    if not isinstance(rubric, dict):
        fail("base rubric must be an object")
    rubric["tag_registry"] = clone_registry(rubric.get("tag_registry"))
    rubric["route_registry"] = clone_registry(rubric.get("route_registry"))
    rubric["flag_registry"] = clone_registry(rubric.get("flag_registry"))
    custom_flags = {
        "proofatlas_curated_input": "Record entered through an explicit locally frozen, curator-approved ProofAtlas envelope.",
        "proofatlas_provisional_c0_assessment": "All assigned dimensions are C0 provisional lexical intake estimates and require evidence-based recalibration.",
        "source_statement_not_independently_verified": "Curator-declared statement completeness has not been independently checked against a canonical source.",
    }
    for code, description in custom_flags.items():
        rubric["flag_registry"].setdefault(code, {"description": description, "record_count": 0})
    for key, item in rubric["tag_registry"].items():
        if isinstance(item, dict):
            item["record_count"] = summary["tag_counts"].get(key, 0)
    for key, item in rubric["route_registry"].items():
        if isinstance(item, dict):
            item["record_count"] = summary["route_counts"].get(key, 0)
    for key, item in rubric["flag_registry"].items():
        if isinstance(item, dict):
            item["record_count"] = summary["flag_counts"].get(key, 0)


def build_header(base: dict[str, Any], *, base_count: int, input_path: Path, input_sha: str, manifest: dict[str, Any] | None, entries: list[dict[str, Any]], summary: dict[str, Any], assessment_date: str) -> dict[str, Any]:
    payload = copy.deepcopy(base)
    payload.pop("records", None)
    snapshot = entries[0]["snapshot"]
    statement_provenance = snapshot["statement_provenance"]
    additions = len(entries)
    ids = [PROOFATLAS_NAMESPACE_OFFSET + entry["problem"]["namespace_number"] for entry in entries]
    old_snapshot = base.get("dataset_snapshot", {}) if isinstance(base.get("dataset_snapshot"), dict) else {}
    segments = copy.deepcopy(old_snapshot.get("source_segments", [])) if isinstance(old_snapshot.get("source_segments"), list) else []
    segments.append({
        "role": "proofatlas_additions",
        "dataset_version": RELEASE_VERSION,
        "record_count": additions,
        "id_minimum": min(ids),
        "id_maximum": max(ids),
        "input_sha256": input_sha,
        "input_schema": CURATED_SCHEMA,
        "snapshot_id": snapshot["snapshot_id"],
        "text_basis": statement_provenance["mode"],
        "policy": "One losslessly retained curator envelope per record; statement provenance/rights mode is explicitly declared; all dimension estimates are C0 provisional and no status is emitted as verified_open.",
    })
    payload["export_id"] = f"opdp-v{RELEASE_VERSION}__{RULE_VERSION}"
    payload["generated_at"] = f"{assessment_date}T00:00:00Z"
    payload["dataset_snapshot"] = {
        "name": "OPDP combined UnsolvedMath, MathDB, and curator-approved ProofAtlas snapshot",
        "version": RELEASE_VERSION,
        "source_file": input_path.name,
        "record_count": summary["records_total"],
        "sha256": input_sha,
        "dataset_url": snapshot["source_url"],
        "source_file_url": None,
        "website_url": snapshot["source_url"],
        "declared_license": snapshot.get("declared_license"),
        "terms_url": snapshot.get("terms_url"),
        "statement_provenance": copy.deepcopy(statement_provenance),
        "sha256_scope": "exact_frozen_curated_proofatlas_jsonl_input",
        "text_preservation": f"All {base_count:,} v1.7 OPDP records are preserved exactly at JSON value level. Each ProofAtlas addition retains its complete frozen curator envelope under source_record.",
        "upstream_revision": snapshot.get("upstream_revision"),
        "upstream_problems_sha256": input_sha,
        "upstream_inventory_sha256": manifest.get("inventory_sha256") if manifest else None,
        "upstream_inventory_count": manifest.get("inventory_count") if manifest else None,
        "upstream_snapshot_consistency": {"curated_snapshot_id": snapshot["snapshot_id"], "curation_status": snapshot["curation_status"], "all_entries_same_snapshot": True},
        "upstream_source_file_url": snapshot["source_url"],
        "compatibility_mode": "append_only",
        "source_segments": segments,
    }
    prior_release = base.get("assessment_release", {}) if isinstance(base.get("assessment_release"), dict) else {}
    limitations = list(prior_release.get("limitations", [])) if isinstance(prior_release.get("limitations"), list) else []
    limitations.append("ProofAtlas additions are accepted only from an explicit frozen, curator-approved local input with an explicit statement-provenance/rights mode. The builder does not infer a license or independently validate a declared rights clearance. Statements, attribution, provenance, and source-declared status are not independently verified by this builder; all assigned dimensions are C0 provisional and no record is marked verified_open.")
    payload["assessment_release"] = {
        **prior_release,
        "assessment_date": assessment_date,
        "records_assessed": summary["records_total"],
        "records_total": summary["records_total"],
        "generator_name": "opdp-proofatlas-append-builder",
        "generator_version": "1.0.0",
        "limitations": limitations,
        "compatibility": {"mode": "append_only", "base_export_id": base.get("export_id"), "base_record_count": base_count, "added_record_count": additions, "legacy_record_policy": "Every complete v1.7 record is retained without alteration.", "schema_policy": "Top-level and per-record schema versions remain unchanged; consumers may ingest the expansion without a schema migration."},
    }
    guide = base.get("integration_guide", {}) if isinstance(base.get("integration_guide"), dict) else {}
    payload["integration_guide"] = {
        **guide,
        "join_and_route_warning": f"Use numeric problem_id. Existing v1.7 ids are unchanged; ProofAtlas ids are {PROOFATLAS_NAMESPACE_OFFSET:,} + curator namespace_number ({min(ids)}-{max(ids)}) and display as PROOFATLAS-{{source_id}}.",
        "raw_text_warning": "Each ProofAtlas source_record is the lossless frozen curator envelope. Statement provenance/rights mode is curator-declared, not independently adjudicated; curator-authored normalizations and cleared source text must not be presented as independently audited provenance.",
    }
    schema = copy.deepcopy(base.get("schema", {}))
    if not isinstance(schema, dict):
        fail("base schema must be an object")
    contract = copy.deepcopy(schema.get("record_contract", {})) if isinstance(schema.get("record_contract"), dict) else {}
    contract["problem_id"] = "Stable numeric OPDP id; v1.7 ids are frozen; each ProofAtlas id is 50,000,000 plus curator namespace_number."
    contract["source_record"] = "Complete frozen curator envelope, copied losslessly."
    schema["record_contract"] = contract
    payload["schema"] = schema
    protocols = copy.deepcopy(base.get("protocols", {}))
    if isinstance(protocols, dict) and isinstance(protocols.get(AI_PROTOCOL_ID), dict):
        protocols[AI_PROTOCOL_ID]["assessment_date"] = assessment_date
    payload["protocols"] = protocols
    payload["summary"] = summary
    refresh_registries(payload, summary)
    return payload


def write_gzip_payload(output: Path, header: dict[str, Any], base_path: Path, appended: list[dict[str, Any]], expected_base_digest: str, expected_header_digest: str, max_value_chars: int) -> str:
    """Atomically stream a new payload and prove the copied base pass was stable."""

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    copied = hashlib.sha256()
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
                with io.TextIOWrapper(compressed, encoding="utf-8", newline="\n") as writer:
                    writer.write("{")
                    first_header = True
                    for key, value in header.items():
                        if not first_header:
                            writer.write(",")
                        first_header = False
                        writer.write(json.dumps(key, ensure_ascii=False))
                        writer.write(":")
                        json.dump(value, writer, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                    writer.write(",\"records\":[")
                    wrote = False
                    with StreamedPayload(base_path, max_value_chars=max_value_chars) as source:
                        copied_metadata = source.start()
                        for record in source.records():
                            if wrote:
                                writer.write(",")
                            json.dump(record, writer, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                            value_digest_update(copied, record)
                            wrote = True
                    if copied.hexdigest().upper() != expected_base_digest:
                        fail("base export changed between audit pass and output-copy pass")
                    if canonical_digest(copied_metadata).upper() != expected_header_digest:
                        fail("base export metadata changed between audit pass and output-copy pass")
                    for record in appended:
                        if wrote:
                            writer.write(",")
                        json.dump(record, writer, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                        wrote = True
                    writer.write("]}")
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)
    return sha256_file(output).upper()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build(args: argparse.Namespace) -> dict[str, Any]:
    assessment_date = args.date
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", assessment_date):
        fail("--date must be YYYY-MM-DD")
    try:
        year = int(assessment_date[:4])
        if not 2000 <= year <= 2100:
            raise ValueError
    except ValueError:
        fail("--date has an invalid year")
    base_path = args.base.resolve()
    input_path = args.proofatlas.resolve()
    output_path = args.output.resolve()
    validation_path = args.validation.resolve()
    protected = {base_path, input_path}
    if output_path in protected or validation_path in protected or output_path == validation_path:
        fail("output and validation paths must be distinct from all inputs and from each other")
    (
        metadata,
        schema,
        base_ids,
        base_numbers,
        title_index,
        statement_index,
        accumulator,
        base_digest,
        base_header_digest,
        base_count,
    ) = inspect_base(base_path, args.max_value_chars)
    version = metadata.get("dataset_snapshot", {}).get("version") if isinstance(metadata.get("dataset_snapshot"), dict) else None
    if args.limit is not None:
        if args.manifest:
            fail("--limit is fixture/smoke-only and must not be combined with --manifest")
        if not args.allow_base_mismatch:
            fail("--limit is fixture/smoke-only and requires --allow-base-mismatch")
        if version == EXPECTED_BASE_VERSION and base_count == EXPECTED_BASE_COUNT:
            fail("--limit must not target the canonical v1.7 base; create a synthetic fixture base instead")
    if not args.allow_base_mismatch and (version != EXPECTED_BASE_VERSION or base_count != EXPECTED_BASE_COUNT):
        fail(f"expected canonical v{EXPECTED_BASE_VERSION} with {EXPECTED_BASE_COUNT:,} records; use --allow-base-mismatch only for a synthetic fixture")
    entries, input_sha, _ = load_curated_input(input_path, args.limit)
    snapshot_id = entries[0]["snapshot"]["snapshot_id"]
    manifest = None
    if args.manifest:
        manifest = load_manifest(args.manifest.resolve(), input_path=input_path, input_sha=input_sha, count=len(entries), snapshot_id=snapshot_id)
    elif args.limit is None:
        fail("--manifest is required for a complete release build; omit it only with --limit fixture/smoke builds")
    appended: list[dict[str, Any]] = []
    errors: list[str] = []
    all_ids = set(base_ids)
    all_display_numbers_casefolded = {number.casefold() for number in base_numbers}
    # Add all new identity keys before constructing any record so a collision is
    # visible on every affected output rather than only on the later source row.
    for entry in entries:
        p = entry["problem"]
        record_id = PROOFATLAS_NAMESPACE_OFFSET + p["namespace_number"]
        if record_id in all_ids:
            fail(f"ProofAtlas ID collision at {record_id}")
        display_number = f"PROOFATLAS-{p['source_id']}"
        if display_number.casefold() in all_display_numbers_casefolded:
            fail(f"ProofAtlas display identifier collision at {display_number!r}")
        all_ids.add(record_id)
        all_display_numbers_casefolded.add(display_number.casefold())
        add_index(title_index, normalize_key(p["title"]), record_id)
        add_index(statement_index, hash_statement(p["statement"]), record_id)
    for entry in entries:
        record = build_record(entry, input_sha=input_sha, assessment_date=assessment_date, title_index=title_index, statement_index=statement_index, id_index=base_ids)
        errors.extend(validate_new_record(record, entry, base_ids))
        accumulator.update(record)
        appended.append(record)
    summary = accumulator.finish()
    if summary["records_total"] != base_count + len(appended):
        errors.append("summary record total does not equal base plus append count")
    if summary["corpus_audit"]["unique_problem_ids"] != summary["records_total"]:
        errors.append("problem_id is not unique after append")
    if errors:
        report = {"validation_schema_version": "1.0.0", "release_version": RELEASE_VERSION, "status": "fail", "errors": errors[:200], "error_count": len(errors)}
        atomic_json(validation_path, report)
        fail(f"ProofAtlas append validation failed with {len(errors)} error(s); see {validation_path}")
    header = build_header(metadata, base_count=base_count, input_path=input_path, input_sha=input_sha, manifest=manifest, entries=entries, summary=summary, assessment_date=assessment_date)
    output_sha = write_gzip_payload(
        output_path,
        header,
        base_path,
        appended,
        base_digest,
        base_header_digest,
        args.max_value_chars,
    )
    input_sha_after = sha256_file(input_path).upper()
    if input_sha_after != input_sha:
        output_path.unlink(missing_ok=True)
        fail("curated input changed during build; output was removed")
    report = {
        "validation_schema_version": "1.0.0",
        "release_version": RELEASE_VERSION,
        "rule_version": RULE_VERSION,
        "status": "pass",
        "inputs": {"base_export_id": metadata.get("export_id"), "base_version": version, "base_count": base_count, "proofatlas_count": len(entries), "proofatlas_sha256": input_sha, "curated_schema": CURATED_SCHEMA, "manifest_used": bool(manifest)},
        "outputs": {"records_total": summary["records_total"], "additions": len(entries), "first_proofatlas_id": min(record["problem_id"] for record in appended), "last_proofatlas_id": max(record["problem_id"] for record in appended), "output_path": str(output_path), "output_sha256": output_sha, "output_bytes": output_path.stat().st_size},
        "checks": {"legacy_v17_deep_digest_unchanged": True, "legacy_v17_header_digest_unchanged": True, "unique_problem_ids": True, "stable_reversible_proofatlas_ids": True, "collision_free_proofatlas_display_identifiers": True, "lossless_curated_source_records": True, "all_assigned_dimensions_c0": True, "verified_open_never_emitted": True, "canonical_record_schema_unchanged": True, "streamed_v17_base_copy": True},
        "limitations": ["This builder performs no network retrieval.", "All ProofAtlas additions are C0 provisional intake assessments.", "Source-declared status is not independently verified and is never promoted to verified_open."],
    }
    atomic_json(validation_path, report)
    return report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--base", type=Path, required=True, help="canonical gzip-compressed v1.7 export")
    result.add_argument("--proofatlas", type=Path, required=True, help="locally frozen curator-approved JSONL or JSONL.GZ input")
    result.add_argument("--output", type=Path, required=True, help="gzip-compressed v1.8 output")
    result.add_argument("--validation", type=Path, required=True, help="validation report JSON")
    result.add_argument("--manifest", type=Path, help="required complete curated snapshot manifest")
    result.add_argument("--date", default=RELEASE_DATE, help="assessment date, YYYY-MM-DD")
    result.add_argument("--limit", type=int, help="fixture/smoke limit; disables complete-manifest requirement")
    result.add_argument("--allow-base-mismatch", action="store_true", help="fixture-only bypass of canonical v1.7 version/count check")
    result.add_argument("--max-value-chars", type=int, default=64 * 1024 * 1024, help="maximum one base JSON value/record, not whole payload")
    return result


def main() -> int:
    args = parser().parse_args()
    if args.limit is not None and args.limit <= 0:
        sys.stderr.write("--limit must be positive\n")
        return 2
    try:
        report = build(args)
    except (BuildError, ValidationError, DuplicateKeyError, OSError, UnicodeError, EOFError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
