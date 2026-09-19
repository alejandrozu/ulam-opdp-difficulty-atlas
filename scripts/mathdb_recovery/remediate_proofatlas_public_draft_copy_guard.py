#!/usr/bin/env python3
"""Replace legacy source-overlapping ProofAtlas draft normalizations safely.

The associated release assembler checks a curator-written statement against
the full frozen ProofAtlas Top 500 and collaboration-card corpus in memory.
This narrow, deterministic maintenance script keeps the already-public review
drafts consistent with that rule without retaining any upstream source prose.

It deliberately contains only independently authored replacement language,
stable source identifiers, and an authorship attestation.  It neither fetches
nor serializes ProofAtlas target/question/card text.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


REPOSITORY = Path(__file__).resolve().parents[2]
ATTESTATION = (
    "Curator-authored independent normalization; this wording was written "
    "without copying any ProofAtlas target, reader question, or collaboration-card statement."
)

# These replacements describe the same scoped mathematical targets but use
# independent wording.  They were checked locally against the complete frozen
# ProofAtlas corpus by assemble_proofatlas_curated_append_v1.py.
REPLACEMENTS: dict[str, str] = {
    "problem.weinstein-conjecture-on-periodic-orbits-of-reeb-flows": (
        "Given a contact form on a compact contact manifold in dimension above three, must the associated Reeb field possess a periodic trajectory?"
    ),
    "problem.griffiths-positivity-conjecture-for-ample-vector-bundles": (
        "For an ample bundle in the holomorphic category, seek a Hermitian metric whose Chern curvature is positive in Griffiths’s sense."
    ),
    "problem.exact-capacity-region-of-the-two-user-gaussian-interference-channel": (
        "Characterize every achievable rate pair for the two-sender Gaussian interference model, without imposing weak, strong, or other restricted-interference regimes."
    ),
    "problem.margulis-bounded-diagonal-orbit-conjecture": (
        "Decide whether a bounded diagonal-subgroup orbit in the relevant homogeneous space must arise from the arithmetic configuration anticipated by Margulis."
    ),
    "problem.top500-omission-w041044-shelah-s-conjecture-on-nip-fields": (
        "Establish whether an infinite field with NIP theory is forced into the expected algebraic, order-theoretic, or valued-field alternatives."
    ),
    "problem.toms-winter-strict-comparison-implies-z-stability": (
        "Does strict comparison in a simple separable unital nuclear C-star algebra entail Z-stability, namely tensorial absorption of the Jiang–Su algebra?"
    ),
    "problem.exact-consistency-strength-of-the-proper-forcing-axiom-pfa": (
        "Determine the weakest large-cardinal hypothesis that is equiconsistent with the Proper Forcing Axiom."
    ),
    "problem.polynomial-time-solvability-of-condon-s-simple-stochastic-games": (
        "Find a deterministic procedure with polynomial running time that computes the winner values of any finite simple stochastic game."
    ),
    "proofatlas-collaboration:morrison-kawamata-cone-conjecture": (
        "Determine whether automorphisms or pseudo-automorphisms of a klt Calabi–Yau pair admit a rational-polyhedral fundamental chamber for the nef or movable cone."
    ),
    "proofatlas-collaboration:rational-homological-quillen-conjecture-p2": (
        "At the prime 2, decide whether Quillen’s predicted rational-homology comparison for the relevant arithmetic linear groups is valid."
    ),
    "proofatlas-collaboration:shortest-superpermutations-general-case": (
        "For arbitrary n, determine the least word length over n letters needed to contain each ordering of those letters as a consecutive block."
    ),
}

PUBLIC_FILES: dict[Path, set[str]] = {
    REPOSITORY / "fixtures" / "proofatlas_top500_match_identity_append_drafts_v1.jsonl": {
        key for key in REPLACEMENTS if key.startswith("problem.") and key not in {
            "problem.toms-winter-strict-comparison-implies-z-stability",
            "problem.exact-consistency-strength-of-the-proper-forcing-axiom-pfa",
            "problem.polynomial-time-solvability-of-condon-s-simple-stochastic-games",
        }
    },
    REPOSITORY / "fixtures" / "proofatlas_top500_no_strong_highrank_append_drafts_v1.jsonl": {
        "problem.toms-winter-strict-comparison-implies-z-stability",
        "problem.exact-consistency-strength-of-the-proper-forcing-axiom-pfa",
        "problem.polynomial-time-solvability-of-condon-s-simple-stochastic-games",
    },
    REPOSITORY / "data" / "PROOFATLAS_COLLABORATION_APPEND_CURATION_DRAFT_085_126_v1.jsonl": {
        key for key in REPLACEMENTS if key.startswith("proofatlas-collaboration:")
    },
}


def atomic_write(path: Path, body: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def record_identifier(record: dict[str, Any]) -> str | None:
    proofatlas = record.get("proofatlas")
    if isinstance(proofatlas, dict) and isinstance(proofatlas.get("problem_id"), str):
        return proofatlas["problem_id"]
    source = record.get("source")
    if isinstance(source, dict) and isinstance(source.get("source_id"), str):
        return f"proofatlas-collaboration:{source['source_id']}"
    return None


def statement_key(record: dict[str, Any]) -> str:
    for key in ("curator_authored_independent_normalization", "proposed_statement"):
        if isinstance(record.get(key), str):
            return key
    raise ValueError("selected public draft is missing a recognized normalization field")


def rewrite_public_file(path: Path, expected: set[str]) -> set[str]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    found: set[str] = set()
    for row in rows:
        identifier = record_identifier(row)
        if identifier not in expected:
            continue
        key = statement_key(row)
        row[key] = REPLACEMENTS[identifier]
        provenance = row.get("statement_provenance")
        if not isinstance(provenance, dict):
            raise ValueError(f"{path}: {identifier} lacks statement_provenance")
        provenance["attestation"] = ATTESTATION
        found.add(identifier)
    if found != expected:
        missing = sorted(expected - found)
        unexpected = sorted(found - expected)
        raise ValueError(f"{path}: replacement selection mismatch (missing={missing}, unexpected={unexpected})")
    atomic_write(path, "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows))
    return found


def check_public_file(path: Path, expected: set[str]) -> set[str]:
    """Verify the public draft contains the reviewed independent replacements."""

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    found: set[str] = set()
    for row in rows:
        identifier = record_identifier(row)
        if identifier not in expected:
            continue
        key = statement_key(row)
        if row.get(key) != REPLACEMENTS[identifier]:
            raise ValueError(f"{path}: {identifier} does not contain its reviewed independent replacement")
        provenance = row.get("statement_provenance")
        if not isinstance(provenance, dict) or provenance.get("attestation") != ATTESTATION:
            raise ValueError(f"{path}: {identifier} has no matching no-copy attestation")
        found.add(identifier)
    if found != expected:
        missing = sorted(expected - found)
        unexpected = sorted(found - expected)
        raise ValueError(f"{path}: replacement selection mismatch (missing={missing}, unexpected={unexpected})")
    return found


def write_weinstein_replacement() -> Path:
    """Make an ignored local override for the selected cross-source match."""

    source = REPOSITORY / "fixtures" / "proofatlas_top500_match_identity_append_drafts_v1.jsonl"
    identifier = "problem.weinstein-conjecture-on-periodic-orbits-of-reeb-flows"
    for line in source.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if record_identifier(row) == identifier:
            row["curator_authored_independent_normalization"] = REPLACEMENTS[identifier]
            provenance = row.get("statement_provenance")
            if not isinstance(provenance, dict):
                raise ValueError(f"{source}: {identifier} lacks statement_provenance")
            provenance["attestation"] = ATTESTATION
            destination = REPOSITORY / "scripts" / "mathdb_recovery" / "raw" / "PROOFATLAS_COPY_GUARD_GLOBAL_TOP500_DRAFTS_v1.jsonl"
            atomic_write(destination, json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            return destination
    raise ValueError(f"{source}: missing {identifier}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="rewrite the listed public drafts and create the ignored local Weinstein override",
    )
    args = parser.parse_args()
    if args.apply:
        rewritten: set[str] = set()
        for path, expected in PUBLIC_FILES.items():
            rewritten.update(rewrite_public_file(path, expected))
        local_override = write_weinstein_replacement()
        result = {"mode": "apply", "public_replacements": len(rewritten), "local_override": local_override.name}
    else:
        checked: set[str] = set()
        for path, expected in PUBLIC_FILES.items():
            checked.update(check_public_file(path, expected))
        result = {"mode": "check", "public_replacements": len(checked)}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
