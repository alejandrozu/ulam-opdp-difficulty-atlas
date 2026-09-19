#!/usr/bin/env python3
"""Prepare an offline, private review workbench for ProofAtlas abstentions.

The public Top 500 crosswalk deliberately abstains whenever simple lexical
evidence cannot establish identity.  This helper performs a broader *local*
candidate search for exactly those abstentions.  It is a review aid, not an
importer: it does not write an OPDP payload, make a claim about current
openness, or copy either corpus's mathematical prose into a release artifact.

The SQLite workbench is intentionally caller-selected and should live outside
the repository or below an ignored ``raw/``/``runs/`` directory.  It contains
source text solely to enable private human review.  The command emits only a
content-minimized candidate index: identifiers, titles already public in the
OPDP, hashes, and retrieval-method labels.  A future sidecar must be produced
from separately reviewed judgments, never inferred automatically by this tool.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
CROSSWALK_MODULE_PATH = SCRIPT_DIR / "build_proofatlas_top500_crosswalk.py"
MODULE_SPEC = importlib.util.spec_from_file_location("opdp_proofatlas_crosswalk", CROSSWALK_MODULE_PATH)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:  # pragma: no cover - installation failure
    raise RuntimeError(f"cannot import crosswalk helper: {CROSSWALK_MODULE_PATH}")
CROSSWALK = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = CROSSWALK
MODULE_SPEC.loader.exec_module(CROSSWALK)


SCHEMA = "opdp.proofatlas-top500-no-strong-review-workbench.v1"
DEFAULT_LIMIT = 24


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def parse_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("rt", encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected object")
            if row.get("decision") == "no_strong_match":
                rows.append(row)
    if not rows:
        raise ValueError(f"{path}: no no_strong_match rows found")
    return rows


def parse_proofatlas(path: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise ValueError(f"{path}: missing records array")
    output: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        identifier = record.get("problemId")
        title = record.get("canonicalTitle")
        target = record.get("exactTarget")
        if not isinstance(identifier, str) or not isinstance(title, str) or not isinstance(target, str):
            continue
        output[identifier] = record
    return output


def open_workbench(path: Path, reset: bool) -> sqlite3.Connection:
    if reset and path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS records USING fts5("
        "problem_id UNINDEXED, problem_number UNINDEXED, title, statement, "
        "text_mode UNINDEXED, source_excerpt_only UNINDEXED, tokenize='unicode61')"
    )
    return connection


def build_workbench(connection: sqlite3.Connection, atlas_path: Path) -> int:
    existing = int(connection.execute("SELECT count(*) FROM records").fetchone()[0])
    if existing:
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS records_vocab USING fts5vocab(records, 'row')")
        connection.commit()
        return existing
    batch: list[tuple[str, str, str, str, str, int]] = []
    total = 0
    for index, raw in enumerate(CROSSWALK.iter_v17_records(atlas_path)):
        record = CROSSWALK.atlas_record_from_raw(raw, index)
        if record is None:
            continue
        batch.append(
            (
                str(record.problem_id),
                record.problem_number,
                record.title,
                record.statement,
                record.text_mode or "unknown",
                1 if record.source_excerpt_only else 0,
            )
        )
        total += 1
        if len(batch) >= 500:
            connection.executemany("INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)", batch)
            batch.clear()
    if batch:
        connection.executemany("INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)", batch)
    connection.commit()
    connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS records_vocab USING fts5vocab(records, 'row')")
    connection.commit()
    return total


def terms_for_query(value: str) -> list[str]:
    # Complexity classes and standard mathematical abbreviations (NP, NC,
    # QMA, GL, etc.) routinely have two characters, so omit only one-letter
    # tokens.  This is still a private retrieval aid, not an identity rule.
    return [term for term in CROSSWALK.signal_tokens(CROSSWALK.tokenise(value)) if len(term) >= 2]


def fts_literal(term: str) -> str:
    # All terms come from the fixed word tokenizer.  Quotes nevertheless make
    # the generated query robust to FTS grammar changes and reserved words.
    return '"' + term.replace('"', '""') + '"'


def query_rows(connection: sqlite3.Connection, query: str, limit: int) -> list[dict[str, Any]]:
    try:
        rows = connection.execute(
            "SELECT problem_id, problem_number, title, text_mode, source_excerpt_only, "
            "bm25(records, 4.0, 1.0) AS score "
            "FROM records WHERE records MATCH ? ORDER BY score LIMIT ?",
            (query, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        {
            "problem_id": int(problem_id),
            "problem_number": problem_number,
            "title": title,
            "title_sha256": hashlib.sha256(title.encode("utf-8")).hexdigest(),
            "source_text_mode": text_mode,
            "source_statement_completeness": (
                "excerpt_not_guaranteed_complete" if bool(excerpt_only) else "frozen_record_statement"
            ),
            "fts_bm25": round(float(score), 6),
        }
        for problem_id, problem_number, title, text_mode, excerpt_only, score in rows
    ]


def add_candidate(
    candidates: dict[int, dict[str, Any]], candidate: dict[str, Any], method: str
) -> None:
    identifier = int(candidate["problem_id"])
    prior = candidates.get(identifier)
    if prior is None:
        candidate["retrieval_methods"] = [method]
        candidates[identifier] = candidate
        return
    methods = prior.setdefault("retrieval_methods", [])
    if method not in methods:
        methods.append(method)
    prior["fts_bm25"] = min(float(prior["fts_bm25"]), float(candidate["fts_bm25"]))


def title_relation(reference_title: str, candidate_title: str) -> dict[str, Any]:
    """Return presentation-only title features; never a semantic decision."""

    reference_tokens = CROSSWALK.signal_tokens(CROSSWALK.tokenise(reference_title))
    candidate_tokens = CROSSWALK.signal_tokens(CROSSWALK.tokenise(candidate_title))
    shared = reference_tokens & candidate_tokens
    union = reference_tokens | candidate_tokens
    reference_key = CROSSWALK.title_compare_key(CROSSWALK.tokenise(reference_title))
    candidate_key = CROSSWALK.title_compare_key(CROSSWALK.tokenise(candidate_title))
    sequence = difflib.SequenceMatcher(None, reference_key, candidate_key).ratio()
    return {
        "title_exact_after_fixed_presentation_normalization": reference_key == candidate_key,
        "reference_signal_coverage": round(len(shared) / len(reference_tokens), 6) if reference_tokens else 0.0,
        "candidate_signal_coverage": round(len(shared) / len(candidate_tokens), 6) if candidate_tokens else 0.0,
        "signal_jaccard": round(len(shared) / len(union), 6) if union else 0.0,
        "sequence_similarity": round(sequence, 6),
        "shared_signal_token_count": len(shared),
    }


def rare_terms(connection: sqlite3.Connection, value: str, limit: int = 10) -> list[str]:
    candidates = terms_for_query(value)
    ranked: list[tuple[int, int, str]] = []
    for index, term in enumerate(candidates):
        result = connection.execute("SELECT doc FROM records_vocab WHERE term = ?", (term,)).fetchone()
        documents = int(result[0]) if result is not None else 0
        # One-off terms cannot establish a candidate in a different corpus;
        # retain words that occur at least twice and favor discriminative ones.
        if documents >= 2:
            ranked.append((documents, index, term))
    ranked.sort()
    seen: set[str] = set()
    output: list[str] = []
    for _documents, _index, term in ranked:
        if term not in seen:
            output.append(term)
            seen.add(term)
        if len(output) >= limit:
            break
    return output


def candidate_queries(
    connection: sqlite3.Connection, title: str, exact_target: str
) -> list[tuple[str, str]]:
    title_terms = terms_for_query(title)
    target_terms = terms_for_query(exact_target)
    queries: list[tuple[str, str]] = []
    if len(title_terms) >= 2:
        queries.append(("title_phrase", "title : \"" + " ".join(title_terms) + "\""))
        queries.append(("title_all_terms", " AND ".join(fts_literal(term) for term in title_terms[:8])))
        # The broad title-only route is intentionally retained as a discovery
        # signal for abbreviated, reordered, or scope-qualified aliases.  It
        # is never by itself a decision rule.
        queries.append(("title_term_or", "title : (" + " OR ".join(fts_literal(term) for term in title_terms[:12]) + ")"))
    elif title_terms:
        queries.append(("title_single_term", "title : " + fts_literal(title_terms[0])))
    # Exact target terms are a discovery aid only.  We never serialize the
    # target or snippets; this broad OR query gives an independent route to
    # aliases whose titles do not share the canonical ProofAtlas phrasing.
    filtered = [term for term in target_terms if term not in set(title_terms)]
    if filtered:
        queries.append(("target_term_or", " OR ".join(fts_literal(term) for term in filtered[:16])))
    rare = rare_terms(connection, exact_target)
    if len(rare) >= 2:
        queries.append(("target_rare_term_or", " OR ".join(fts_literal(term) for term in rare)))
        queries.append(("target_rare_term_pair", " AND ".join(fts_literal(term) for term in rare[:2])))
    return queries


def build_review_index(
    *,
    crosswalk_rows: list[dict[str, Any]],
    proofatlas: dict[str, dict[str, Any]],
    connection: sqlite3.Connection,
    limit: int,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in sorted(crosswalk_rows, key=lambda item: int(item["proofatlas"]["release_rank"])):
        source = row["proofatlas"]
        problem_id = source["problem_id"]
        raw = proofatlas.get(problem_id)
        if raw is None:
            raise ValueError(f"ProofAtlas source lacks {problem_id}")
        candidates: dict[int, dict[str, Any]] = {}
        for method, query in candidate_queries(
            connection, str(raw["canonicalTitle"]), str(raw["exactTarget"])
        ):
            for candidate in query_rows(connection, query, limit):
                add_candidate(candidates, candidate, method)
        # Retain the original comparison's weak candidate(s) too.  This
        # materially helps a reviewer distinguish lexical abstention from an
        # absent candidate without copying either statement.
        for candidate in row.get("selected_candidates", []):
            if not isinstance(candidate, dict) or not isinstance(candidate.get("problem_id"), int):
                continue
            source_text_mode = candidate.get("source_text_mode")
            internal = {
                "problem_id": candidate["problem_id"],
                "problem_number": candidate.get("problem_number"),
                "title": candidate.get("title"),
                "title_sha256": candidate.get("title_sha256"),
                "source_text_mode": source_text_mode,
                "source_statement_completeness": candidate.get("source_statement_completeness"),
                "fts_bm25": None,
            }
            identifier = int(internal["problem_id"])
            if identifier not in candidates:
                internal["retrieval_methods"] = ["original_crosswalk_weak_candidate"]
                candidates[identifier] = internal
            elif "original_crosswalk_weak_candidate" not in candidates[identifier]["retrieval_methods"]:
                candidates[identifier]["retrieval_methods"].append("original_crosswalk_weak_candidate")
        ordered = sorted(
            candidates.values(),
            key=lambda item: (
                1 if item["fts_bm25"] is None else 0,
                float(item["fts_bm25"] or 0),
                int(item["problem_id"]),
            ),
        )[:limit]
        for candidate in ordered:
            candidate["retrieval_methods"].sort()
            candidate["title_relation"] = title_relation(str(raw["canonicalTitle"]), str(candidate["title"]))
        output.append(
            {
                "schema": SCHEMA,
                "proofatlas": {
                    "problem_id": problem_id,
                    "release_rank": source["release_rank"],
                    "canonical_title": source["canonical_title"],
                    "canonical_title_sha256": source["canonical_title_sha256"],
                    "exact_target_sha256": source["exact_target_sha256"],
                    "formal_statement_source_url": source.get("formal_statement_source_url"),
                },
                "candidate_search": {
                    "methods": [
                        "private_fts_title_phrase_or_all_terms",
                        "private_fts_exact_target_terms",
                        "original_crosswalk_weak_candidate",
                    ],
                    "candidate_count_retained": len(ordered),
                    "candidate_text_not_retained": True,
                    "proofatlas_exact_target_not_retained": True,
                },
                "candidates": ordered,
                "review_required": True,
            }
        )
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crosswalk", type=Path, required=True)
    parser.add_argument("--proofatlas-raw", type=Path, required=True)
    parser.add_argument("--atlas-v17", type=Path, required=True)
    parser.add_argument("--workbench", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reset-workbench", action="store_true")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    arguments = parser.parse_args(argv)
    if arguments.limit < 1 or arguments.limit > 100:
        parser.error("--limit must be between 1 and 100")
    if arguments.output.resolve() in {arguments.atlas_v17.resolve(), arguments.proofatlas_raw.resolve()}:
        parser.error("refusing to use source corpus as output")
    rows = parse_rows(arguments.crosswalk)
    proofatlas = parse_proofatlas(arguments.proofatlas_raw)
    arguments.workbench.parent.mkdir(parents=True, exist_ok=True)
    connection = open_workbench(arguments.workbench, arguments.reset_workbench)
    try:
        record_count = build_workbench(connection, arguments.atlas_v17)
        review = build_review_index(
            crosswalk_rows=rows,
            proofatlas=proofatlas,
            connection=connection,
            limit=arguments.limit,
        )
    finally:
        connection.close()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_bytes(b"".join(canonical_json(row) + b"\n" for row in review))
    print(
        json.dumps(
            {
                "workbench_records": record_count,
                "review_rows": len(review),
                "output": str(arguments.output),
                "output_sha256": sha256_file(arguments.output),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
