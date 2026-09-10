#!/usr/bin/env python3
"""Build conservative local candidate links between frozen Ulam and MathDB rows.

This is a *candidate generator*, deliberately separated from statement
recovery and OPDP scoring.  It operates entirely on local frozen artifacts:

* ``MATHDB_COLLECTION_ULAM_OVERLAP_v1.jsonl`` supplies the 7,489 Ulam
  collection-overlap identities and source hashes;
* the frozen Ulam v1.6 export supplies the already-captured statement only
  transiently, for deterministic comparison; and
* the frozen MathDB public-list catalog supplies titles and excerpts.

The emitted JSONL contains no Ulam statement and no MathDB excerpt.  It
records hashes, public identifiers, deterministic comparison metrics, and
only the highest-precision candidate links.  In particular, it never claims
that a MathDB record is a member of an external collection, that a statement
has been recovered, that a problem remains open, or that OPDP should be
recalculated.

No network access is used or implemented here.
"""

from __future__ import annotations

import argparse
import bisect
import contextlib
import gzip
import hashlib
import html
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


TOOL_NAME = "OPDP frozen collection/Ulam candidate-link builder"
TOOL_VERSION = "1.0.0"
OVERLAP_SCHEMA = "opdp.mathdb.collection-ulam-overlap.v1"
CATALOG_SCHEMA = "opdp.mathdb.problem-summary.v1"
OUTPUT_SCHEMA = "opdp.mathdb.collection-ulam-candidate-links.v1"
SUMMARY_SCHEMA = "opdp.mathdb.collection-ulam-candidate-links-summary.v1"
NORMALIZER_VERSION = "opdp-presentational-token-normalizer-v2"


# This intentionally small list prevents generic prose from driving a link.
# It is used only for retrieval/ranking; every sequence metric retains all
# tokens, including mathematical symbols rendered as words such as ``zeta``.
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "conjecture",
        "consider",
        "determine",
        "do",
        "does",
        "every",
        "find",
        "for",
        "from",
        "given",
        "has",
        "have",
        "how",
        "if",
        "in",
        "is",
        "it",
        "let",
        "may",
        "more",
        "must",
        "no",
        "not",
        "of",
        "on",
        "one",
        "open",
        "or",
        "problem",
        "prove",
        "question",
        "show",
        "some",
        "such",
        "that",
        "the",
        "then",
        "there",
        "these",
        "this",
        "three",
        "to",
        "two",
        "under",
        "using",
        "we",
        "what",
        "when",
        "whether",
        "which",
        "with",
        "without",
        "would",
    }
)

# TeX commands that are visually/presentational only.  Semantic commands are
# retained as ordinary word tokens rather than rewritten into a different
# mathematical expression.
PRESENTATIONAL_TEX_COMMANDS = re.compile(
    r"\\(?:left|right|big|Big|bigl|bigr|Bigl|Bigr|"
    r"displaystyle|textstyle|scriptstyle|scriptscriptstyle|"
    r"quad|qquad|enspace|;|,|!|:)",
    re.IGNORECASE,
)
TEX_COMMAND = re.compile(r"\\([A-Za-z]+)")
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
MULTISPACE = re.compile(r"\s+")
ERDOS_MATHDB_ID_RE = re.compile(r"^erdos\s+problem\s*#\s*(\d+)\b", re.IGNORECASE)
KOUROVKA_MATHDB_ID_RE = re.compile(
    r"^kourovka\s+notebook\s+problem\s+21\s*[.]\s*(\d+)\b", re.IGNORECASE
)
ERDOS_ULAM_ID_RE = re.compile(r"^EP-(\d+)$")
KOUROVKA_ULAM_ID_RE = re.compile(r"^KOU-21\.(\d+)$")


class CandidateLinkError(RuntimeError):
    """An immutable-input or output-integrity failure."""


@dataclass(frozen=True)
class OverlapQuery:
    collection_key: str
    record_index: int
    problem_id: int
    problem_number: str
    title: str
    record_sha256: str
    source_record_sha256: str | None
    statement_sha256: str
    statement_bytes: int
    statement_text_mode: str | None
    statement: str
    title_normalized: str
    title_compare_key: str
    statement_normalized: str
    title_tokens: tuple[str, ...]
    statement_tokens: tuple[str, ...]
    statement_token_set: frozenset[str]
    title_signal_tokens: frozenset[str]


@dataclass(frozen=True)
class CatalogEntry:
    index: int
    number: int
    uuid: str
    title: str
    excerpt: str
    inventory_url: str | None
    problem_sha256: str
    title_normalized: str
    title_compare_key: str
    excerpt_normalized: str


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_string(mapping: dict[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CandidateLinkError(f"{context}: {key} must be a nonempty string")
    return value


def require_int(value: Any, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CandidateLinkError(f"{context}: expected integer")
    return value


def normalize_text(value: str) -> str:
    """Normalize only layout/typography for a reproducible lexical comparison.

    This is intentionally not a mathematical normalizer: it does not expand
    macros, infer notation, simplify formulas, translate prose, or alter
    quantifiers.  It decodes HTML entities, folds Unicode presentation forms,
    removes a small fixed set of TeX spacing/layout commands, exposes other
    TeX command names as tokens, and collapses whitespace.
    """

    text = html.unescape(value)
    text = unicodedata.normalize("NFKC", text)
    text = (
        text.replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .replace("\ufeff", "")
        .replace("\u2026", "...")
        .replace("\u2010", "-")
        .replace("\u2011", "-")
        .replace("\u2012", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2212", "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )
    text = PRESENTATIONAL_TEX_COMMANDS.sub(" ", text)
    text = TEX_COMMAND.sub(lambda match: f" {match.group(1)} ", text)
    text = text.replace("{", " ").replace("}", " ")
    text = text.casefold()
    return MULTISPACE.sub(" ", text).strip()


def identifier_fold(value: str) -> str:
    """Fold accents only for fixed external display-ID patterns."""

    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def tokenize(normalized: str) -> tuple[str, ...]:
    return tuple(TOKEN_RE.findall(normalized))


def title_compare_key(normalized_title: str) -> str:
    """Return the exact-title comparison key with one harmless display fold.

    Only a leading English definite article is dropped.  This handles a title
    such as ``The Riemann Hypothesis`` versus ``Riemann hypothesis`` without
    treating abbreviations, synonyms, or different mathematical notation as
    equivalent.
    """

    tokens = tokenize(normalized_title)
    if tokens[:1] == ("the",):
        tokens = tokens[1:]
    return " ".join(tokens)


def signal_token_set(tokens: Iterable[str]) -> frozenset[str]:
    return frozenset(
        token
        for token in tokens
        if token not in STOPWORDS and (len(token) >= 3 or token in {"p", "np", "z"})
    )


def rounded(value: float) -> float:
    return round(value, 6)


def safe_ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def f1(left: float, right: float) -> float:
    return (2.0 * left * right / (left + right)) if left and right else 0.0


def extract_ulam_identifier(collection_key: str, problem_number: str) -> str | None:
    if collection_key == "erdos":
        match = ERDOS_ULAM_ID_RE.fullmatch(problem_number)
    elif collection_key == "kourovka":
        match = KOUROVKA_ULAM_ID_RE.fullmatch(problem_number)
    else:
        return None
    return match.group(1) if match else None


def extract_mathdb_identifier(collection_key: str, title: str) -> str | None:
    folded = identifier_fold(title)
    if collection_key == "erdos":
        match = ERDOS_MATHDB_ID_RE.search(folded)
    elif collection_key == "kourovka":
        match = KOUROVKA_MATHDB_ID_RE.search(folded)
    else:
        return None
    return match.group(1) if match else None


def iter_overlap_rows(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    try:
        handle = path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise CandidateLinkError(f"cannot read overlap JSONL {path}: {exc}") from exc
    with handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                raise CandidateLinkError(f"{path} line {line_number}: blank line")
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise CandidateLinkError(f"{path} line {line_number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise CandidateLinkError(f"{path} line {line_number}: expected object")
            yield line_number, row


def load_overlap_queries(overlap_path: Path, ulam_path: Path) -> list[OverlapQuery]:
    """Read frozen statements transiently and verify every overlap binding."""

    try:
        with gzip.open(ulam_path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CandidateLinkError(f"cannot load frozen Ulam v1.6 export {ulam_path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise CandidateLinkError(f"{ulam_path}: expected JSON object with records array")
    records = payload["records"]
    result: list[OverlapQuery] = []
    seen_keys: set[tuple[str, int]] = set()
    for line_number, row in iter_overlap_rows(overlap_path):
        if row.get("schema") != OVERLAP_SCHEMA:
            raise CandidateLinkError(f"{overlap_path} line {line_number}: wrong schema")
        if row.get("mathdb_membership") != "not_asserted_no_native_collection_field":
            raise CandidateLinkError(f"{overlap_path} line {line_number}: unsupported membership assertion")
        collection_key = require_string(row, "collection_key", f"{overlap_path} line {line_number}")
        ulam = row.get("ulam")
        if not isinstance(ulam, dict):
            raise CandidateLinkError(f"{overlap_path} line {line_number}: ulam must be an object")
        record_index = require_int(ulam.get("record_index_zero_based"), f"{overlap_path} line {line_number}")
        if record_index < 0 or record_index >= len(records):
            raise CandidateLinkError(f"{overlap_path} line {line_number}: Ulam record index out of range")
        key = (collection_key, record_index)
        if key in seen_keys:
            raise CandidateLinkError(f"{overlap_path} line {line_number}: duplicate overlap binding {key!r}")
        seen_keys.add(key)
        record = records[record_index]
        if not isinstance(record, dict):
            raise CandidateLinkError(f"Ulam record {record_index}: expected object")
        if record.get("problem_id") != ulam.get("problem_id"):
            raise CandidateLinkError(f"Ulam record {record_index}: problem_id disagrees with overlap row")
        if record.get("problem_number") != ulam.get("problem_number"):
            raise CandidateLinkError(f"Ulam record {record_index}: problem_number disagrees with overlap row")
        if record.get("title") != ulam.get("title"):
            raise CandidateLinkError(f"Ulam record {record_index}: title disagrees with overlap row")
        observed_record_sha = sha256_bytes(canonical_json_bytes(record))
        if observed_record_sha != ulam.get("record_canonical_sha256"):
            raise CandidateLinkError(f"Ulam record {record_index}: canonical record SHA-256 differs")
        source_record = record.get("source_record")
        observed_source_sha = (
            sha256_bytes(canonical_json_bytes(source_record)) if isinstance(source_record, dict) else None
        )
        if observed_source_sha != ulam.get("source_record_canonical_sha256"):
            raise CandidateLinkError(f"Ulam record {record_index}: source_record SHA-256 differs")
        source_text = record.get("source_text")
        overlap_source_text = ulam.get("source_text")
        if not isinstance(source_text, dict) or not isinstance(overlap_source_text, dict):
            raise CandidateLinkError(f"Ulam record {record_index}: missing source_text object")
        statement = require_string(source_text, "statement", f"Ulam record {record_index} source_text")
        observed_statement_sha = sha256_text(statement)
        if observed_statement_sha != overlap_source_text.get("statement_sha256"):
            raise CandidateLinkError(f"Ulam record {record_index}: statement SHA-256 differs")
        if len(statement.encode("utf-8")) != overlap_source_text.get("statement_utf8_bytes"):
            raise CandidateLinkError(f"Ulam record {record_index}: statement byte count differs")
        title = require_string(record, "title", f"Ulam record {record_index}")
        problem_number = require_string(record, "problem_number", f"Ulam record {record_index}")
        problem_id = require_int(record.get("problem_id"), f"Ulam record {record_index}")
        title_normalized = normalize_text(title)
        statement_normalized = normalize_text(statement)
        title_tokens = tokenize(title_normalized)
        statement_tokens = tokenize(statement_normalized)
        if not title_tokens or not statement_tokens:
            raise CandidateLinkError(f"Ulam record {record_index}: normalization produced empty title or statement")
        result.append(
            OverlapQuery(
                collection_key=collection_key,
                record_index=record_index,
                problem_id=problem_id,
                problem_number=problem_number,
                title=title,
                record_sha256=observed_record_sha,
                source_record_sha256=observed_source_sha,
                statement_sha256=observed_statement_sha,
                statement_bytes=len(statement.encode("utf-8")),
                statement_text_mode=source_text.get("text_mode"),
                statement=statement,
                title_normalized=title_normalized,
                title_compare_key=title_compare_key(title_normalized),
                statement_normalized=statement_normalized,
                title_tokens=title_tokens,
                statement_tokens=statement_tokens,
                statement_token_set=frozenset(statement_tokens),
                title_signal_tokens=signal_token_set(title_tokens),
            )
        )
    if not result:
        raise CandidateLinkError(f"{overlap_path}: contains no overlap rows")
    return result


def load_catalog(path: Path) -> list[CatalogEntry]:
    result: list[CatalogEntry] = []
    seen_numbers: set[int] = set()
    seen_uuids: set[str] = set()
    try:
        handle = path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise CandidateLinkError(f"cannot read MathDB catalog {path}: {exc}") from exc
    with handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                raise CandidateLinkError(f"{path} line {line_number}: blank line")
            try:
                envelope = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise CandidateLinkError(f"{path} line {line_number}: invalid JSON: {exc}") from exc
            if not isinstance(envelope, dict) or envelope.get("schema") != CATALOG_SCHEMA:
                raise CandidateLinkError(f"{path} line {line_number}: wrong catalog schema")
            problem = envelope.get("problem")
            snapshot = envelope.get("snapshot")
            if not isinstance(problem, dict) or not isinstance(snapshot, dict):
                raise CandidateLinkError(f"{path} line {line_number}: missing problem/snapshot object")
            number = require_int(problem.get("number"), f"{path} line {line_number} problem")
            uuid = require_string(problem, "id", f"{path} line {line_number} problem")
            title = require_string(problem, "title", f"{path} line {line_number} problem")
            excerpt = require_string(problem, "excerpt", f"{path} line {line_number} problem")
            if number in seen_numbers:
                raise CandidateLinkError(f"{path} line {line_number}: duplicate MathDB number {number}")
            if uuid in seen_uuids:
                raise CandidateLinkError(f"{path} line {line_number}: duplicate MathDB UUID {uuid!r}")
            seen_numbers.add(number)
            seen_uuids.add(uuid)
            observed_problem_sha = sha256_bytes(canonical_json_bytes(problem))
            if observed_problem_sha != snapshot.get("problem_canonical_sha256"):
                raise CandidateLinkError(
                    f"{path} line {line_number}: problem canonical SHA-256 differs from snapshot"
                )
            title_normalized = normalize_text(title)
            excerpt_normalized = normalize_text(excerpt)
            # The public catalog permits an excerpt made solely of an ellipsis.
            # Such a row cannot supply lexical evidence, but it remains a valid
            # frozen catalog record and must be counted as unmatched rather than
            # causing the complete local audit to fail.
            inventory_url = snapshot.get("inventory_url")
            if inventory_url is not None and not isinstance(inventory_url, str):
                raise CandidateLinkError(f"{path} line {line_number}: inventory_url must be string or null")
            result.append(
                CatalogEntry(
                    index=len(result),
                    number=number,
                    uuid=uuid,
                    title=title,
                    excerpt=excerpt,
                    inventory_url=inventory_url,
                    problem_sha256=observed_problem_sha,
                    title_normalized=title_normalized,
                    title_compare_key=title_compare_key(title_normalized),
                    excerpt_normalized=excerpt_normalized,
                )
            )
    if not result:
        raise CandidateLinkError(f"{path}: contains no catalog rows")
    return result


def build_indices(
    catalog: list[CatalogEntry],
) -> tuple[
    dict[str, list[int]],
    dict[str, list[int]],
    Counter[str],
    dict[str, dict[str, list[int]]],
]:
    """Build deterministic, bounded inverted indexes from title/excerpt tokens."""

    document_frequency: Counter[str] = Counter()
    for entry in catalog:
        tokens = signal_token_set(
            (*tokenize(entry.title_normalized), *tokenize(entry.excerpt_normalized))
        )
        document_frequency.update(tokens)

    exact_title_index: dict[str, list[int]] = defaultdict(list)
    title_index: dict[str, list[int]] = defaultdict(list)
    anchor_index: dict[str, list[int]] = defaultdict(list)
    identifier_index: dict[str, dict[str, list[int]]] = {
        "erdos": defaultdict(list),
        "kourovka": defaultdict(list),
    }
    title_max_document_frequency = max(1, int(len(catalog) * 0.08))
    body_anchor_max_document_frequency = 160
    max_anchor_tokens_per_catalog_entry = 24
    for entry in catalog:
        exact_title_index[entry.title_compare_key].append(entry.index)
        title_tokens = signal_token_set(tokenize(entry.title_normalized))
        for token in sorted(title_tokens):
            if document_frequency[token] <= title_max_document_frequency:
                title_index[token].append(entry.index)
        all_tokens = signal_token_set(
            (*tokenize(entry.title_normalized), *tokenize(entry.excerpt_normalized))
        )
        rare_tokens = sorted(
            (token for token in all_tokens if document_frequency[token] <= body_anchor_max_document_frequency),
            key=lambda token: (document_frequency[token], token),
        )[:max_anchor_tokens_per_catalog_entry]
        for token in rare_tokens:
            anchor_index[token].append(entry.index)
        for collection_key in ("erdos", "kourovka"):
            identifier = extract_mathdb_identifier(collection_key, entry.title)
            if identifier is not None:
                identifier_index[collection_key][identifier].append(entry.index)
    return exact_title_index, title_index, document_frequency, identifier_index | {"anchors": anchor_index}


def greedy_ordered_coverage(excerpt_tokens: tuple[str, ...], statement_tokens: tuple[str, ...]) -> float:
    """Cheap, deterministic lower bound on ordered token correspondence."""

    if not excerpt_tokens:
        return 0.0
    positions: dict[str, list[int]] = defaultdict(list)
    for position, token in enumerate(statement_tokens):
        positions[token].append(position)
    previous = -1
    matched = 0
    for token in excerpt_tokens:
        candidates = positions.get(token)
        if not candidates:
            continue
        offset = bisect.bisect_right(candidates, previous)
        if offset < len(candidates):
            previous = candidates[offset]
            matched += 1
    return safe_ratio(matched, len(excerpt_tokens))


def sequence_metrics(
    excerpt_tokens: tuple[str, ...], statement_tokens: tuple[str, ...]
) -> tuple[float, int]:
    """Return exact token-LCS coverage and longest contiguous token run.

    The full Ulam statement is used unless it would make a quadratic comparison
    exceed the explicit 300,000-cell guard.  In that rare case a deterministic
    4,096-token prefix is used and the caller reports this fact in evidence.
    """

    if not excerpt_tokens or not statement_tokens:
        return 0.0, 0
    if len(excerpt_tokens) * len(statement_tokens) > 300_000:
        statement_tokens = statement_tokens[:4096]
    previous_lcs = [0] * (len(statement_tokens) + 1)
    previous_run = [0] * (len(statement_tokens) + 1)
    longest = 0
    for left_token in excerpt_tokens:
        current_lcs = [0]
        current_run = [0]
        for right_index, right_token in enumerate(statement_tokens, start=1):
            if left_token == right_token:
                current_lcs.append(previous_lcs[right_index - 1] + 1)
                run = previous_run[right_index - 1] + 1
                current_run.append(run)
                if run > longest:
                    longest = run
            else:
                current_lcs.append(max(previous_lcs[right_index], current_lcs[right_index - 1]))
                current_run.append(0)
        previous_lcs = current_lcs
        previous_run = current_run
    return safe_ratio(previous_lcs[-1], len(excerpt_tokens)), longest


def title_metrics(query: OverlapQuery, entry: CatalogEntry) -> dict[str, Any]:
    query_set = query.title_signal_tokens
    candidate_tokens = tokenize(entry.title_normalized)
    candidate_set = signal_token_set(candidate_tokens)
    shared = query_set & candidate_set
    recall = safe_ratio(len(shared), len(query_set))
    precision = safe_ratio(len(shared), len(candidate_set))
    union = query_set | candidate_set
    exact = query.title_normalized == entry.title_normalized
    compare_key_exact = query.title_compare_key == entry.title_compare_key
    contained = (
        len(query.title_normalized) >= 18
        and len(entry.title_normalized) >= 18
        and (
            query.title_normalized in entry.title_normalized
            or entry.title_normalized in query.title_normalized
        )
    )
    return {
        "exact_normalized": exact,
        "exact_title_compare_key": compare_key_exact,
        "normalized_containment": contained,
        "signal_token_count_ulam": len(query_set),
        "signal_token_count_mathdb": len(candidate_set),
        "shared_signal_token_count": len(shared),
        "token_recall_ulam": rounded(recall),
        "token_precision_mathdb": rounded(precision),
        "token_f1": rounded(f1(recall, precision)),
        "token_jaccard": rounded(safe_ratio(len(shared), len(union))),
    }


def candidate_preliminary_score(query: OverlapQuery, entry: CatalogEntry) -> tuple[float, dict[str, Any]]:
    title = title_metrics(query, entry)
    excerpt_tokens = tokenize(entry.excerpt_normalized)
    excerpt_signal = signal_token_set(excerpt_tokens)
    excerpt_shared = excerpt_signal & query.statement_token_set
    excerpt_set_recall = safe_ratio(len(excerpt_shared), len(excerpt_signal))
    excerpt_ordered = greedy_ordered_coverage(excerpt_tokens, query.statement_tokens)
    score = min(
        1.0,
        0.48 * float(title["token_f1"])
        + 0.27 * excerpt_set_recall
        + 0.20 * excerpt_ordered
        + (0.05 if title["exact_normalized"] or title["normalized_containment"] else 0.0),
    )
    return score, {
        "title": title,
        "excerpt_signal_token_count": len(excerpt_signal),
        "excerpt_shared_signal_token_count": len(excerpt_shared),
        "excerpt_signal_set_recall_in_ulam_statement": rounded(excerpt_set_recall),
        "excerpt_greedy_ordered_coverage": rounded(excerpt_ordered),
    }


def classify_candidate(
    *,
    query: OverlapQuery,
    entry: CatalogEntry,
    metric: dict[str, Any],
    lcs_coverage: float,
    longest_contiguous: int,
    identifier_exact: bool,
    unique_exact_title: bool,
) -> tuple[str | None, float]:
    """Apply explicit high-precision candidate-link guards.

    ``identifier_exact`` is limited to two format-specific external display-ID
    patterns.  It is still called a candidate link, not a membership decision.
    All non-ID links require both a compatible title and strong excerpt-to-Ulam
    statement evidence, or an unusually strong literal text signature.
    """

    title = metric["title"]
    title_f1 = float(title["token_f1"])
    title_jaccard = float(title["token_jaccard"])
    title_recall = float(title["token_recall_ulam"])
    shared_title = int(title["shared_signal_token_count"])
    excerpt_set_recall = float(metric["excerpt_signal_set_recall_in_ulam_statement"])
    contiguous_component = min(1.0, longest_contiguous / 12.0)
    composite = min(
        1.0,
        0.30 * title_f1
        + 0.20 * title_jaccard
        + 0.25 * lcs_coverage
        + 0.15 * excerpt_set_recall
        + 0.10 * contiguous_component,
    )
    if identifier_exact:
        return "identifier_exact", 1.0
    if (
        unique_exact_title
        and bool(title["exact_title_compare_key"])
        and len(query.title_tokens) >= 2
        and len(query.title_normalized) >= 10
    ):
        return "unique_exact_normalized_title", max(composite, 0.95)
    if (
        bool(title["exact_normalized"])
        and shared_title >= 3
        and lcs_coverage >= 0.45
        and longest_contiguous >= 5
    ):
        return "exact_normalized_title_with_excerpt", composite
    if (
        bool(title["normalized_containment"])
        and title_recall >= 0.75
        and title_jaccard >= 0.50
        and lcs_coverage >= 0.60
        and longest_contiguous >= 7
    ):
        return "title_containment_with_strong_excerpt", composite
    if (
        shared_title >= 3
        and title_recall >= 0.70
        and title_jaccard >= 0.45
        and lcs_coverage >= 0.72
        and longest_contiguous >= 8
    ):
        return "high_title_and_excerpt_agreement", composite
    if (
        shared_title >= 2
        and lcs_coverage >= 0.90
        and longest_contiguous >= 12
        and excerpt_set_recall >= 0.80
    ):
        return "near_literal_excerpt_with_title_anchor", composite
    return None, composite


def output_candidate(
    *,
    entry: CatalogEntry,
    query: OverlapQuery,
    metric: dict[str, Any],
    lcs_coverage: float,
    longest_contiguous: int,
    candidate_tier: str,
    score: float,
    identifier_exact: bool,
) -> dict[str, Any]:
    source_tokens = tokenize(query.statement_normalized)
    excerpt_tokens = tokenize(entry.excerpt_normalized)
    return {
        "rank": 0,  # reassigned after sorting
        "candidate_tier": candidate_tier,
        "candidate_score": rounded(score),
        "identifier_exact": identifier_exact,
        "mathdb": {
            "number": entry.number,
            "uuid": entry.uuid,
            "inventory_url": entry.inventory_url,
            "title": entry.title,
            "problem_canonical_sha256": entry.problem_sha256,
            "title_sha256": sha256_text(entry.title),
            "excerpt_sha256": sha256_text(entry.excerpt),
            "title_normalized_sha256": sha256_text(entry.title_normalized),
            "title_compare_key_sha256": sha256_text(entry.title_compare_key),
            "excerpt_normalized_sha256": sha256_text(entry.excerpt_normalized),
        },
        "comparison": {
            "normalizer_version": NORMALIZER_VERSION,
            "title": metric["title"],
            "excerpt_vs_frozen_ulam_statement": {
                "mathdb_excerpt_token_count": len(excerpt_tokens),
                "ulam_statement_token_count": len(source_tokens),
                "excerpt_signal_token_count": metric["excerpt_signal_token_count"],
                "excerpt_shared_signal_token_count": metric[
                    "excerpt_shared_signal_token_count"
                ],
                "excerpt_signal_set_recall_in_ulam_statement": metric[
                    "excerpt_signal_set_recall_in_ulam_statement"
                ],
                "excerpt_greedy_ordered_coverage": metric["excerpt_greedy_ordered_coverage"],
                "excerpt_token_lcs_coverage": rounded(lcs_coverage),
                "longest_contiguous_token_run": longest_contiguous,
                "quadratic_comparison_prefix_cap_applied": (
                    len(excerpt_tokens) * len(source_tokens) > 300_000
                ),
            },
        },
    }


def candidate_indices_for_query(
    *,
    query: OverlapQuery,
    catalog: list[CatalogEntry],
    exact_title_index: dict[str, list[int]],
    title_index: dict[str, list[int]],
    document_frequency: Counter[str],
    identifier_index: dict[str, dict[str, list[int]]],
    max_pool_size: int,
) -> tuple[list[int], set[int]]:
    """Return a bounded lexical candidate pool plus exact-ID candidate IDs."""

    scores: dict[int, float] = defaultdict(float)
    exact_identifier_indices: set[int] = set()
    for index in exact_title_index.get(query.title_compare_key, []):
        scores[index] += 10_000.0
    identifier = extract_ulam_identifier(query.collection_key, query.problem_number)
    if identifier is not None:
        for index in identifier_index.get(query.collection_key, {}).get(identifier, []):
            scores[index] += 20_000.0
            exact_identifier_indices.add(index)
    catalog_size = len(catalog)
    for token in sorted(query.title_signal_tokens):
        ids = title_index.get(token, [])
        if not ids:
            continue
        idf = 1.0 + math.log((catalog_size + 1) / (document_frequency[token] + 1))
        for index in ids:
            scores[index] += 2.0 * idf
    anchors = [
        token
        for token in query.statement_token_set
        if token in identifier_index["anchors"] and token not in STOPWORDS
    ]
    anchors.sort(key=lambda token: (document_frequency[token], token))
    for token in anchors[:24]:
        ids = identifier_index["anchors"][token]
        idf = 1.0 + math.log((catalog_size + 1) / (document_frequency[token] + 1))
        for index in ids:
            scores[index] += idf
    selected = sorted(scores, key=lambda index: (-scores[index], index))[:max_pool_size]
    # A direct identifier candidate must remain eligible even when a malformed
    # catalog happens to have many same-title rows.
    selected_set = set(selected)
    selected_set.update(exact_identifier_indices)
    return sorted(selected_set), exact_identifier_indices


def match_query(
    *,
    query: OverlapQuery,
    catalog: list[CatalogEntry],
    exact_title_index: dict[str, list[int]],
    title_index: dict[str, list[int]],
    document_frequency: Counter[str],
    identifier_index: dict[str, dict[str, list[int]]],
    top_k: int,
    max_pool_size: int,
    metric_top_k: int,
) -> tuple[str, list[dict[str, Any]], int]:
    candidate_indices, exact_identifier_indices = candidate_indices_for_query(
        query=query,
        catalog=catalog,
        exact_title_index=exact_title_index,
        title_index=title_index,
        document_frequency=document_frequency,
        identifier_index=identifier_index,
        max_pool_size=max_pool_size,
    )
    preliminary: list[tuple[float, int, dict[str, Any]]] = []
    for index in candidate_indices:
        score, metric = candidate_preliminary_score(query, catalog[index])
        preliminary.append((score, index, metric))
    preliminary.sort(key=lambda item: (-item[0], item[1]))
    metric_indices = {index for _, index, _ in preliminary[:metric_top_k]}
    metric_indices.update(exact_identifier_indices)
    accepted: list[dict[str, Any]] = []
    for preliminary_score, index, metric in preliminary:
        if index not in metric_indices:
            continue
        entry = catalog[index]
        excerpt_tokens = tokenize(entry.excerpt_normalized)
        lcs_coverage, longest_contiguous = sequence_metrics(excerpt_tokens, query.statement_tokens)
        candidate_tier, score = classify_candidate(
            query=query,
            entry=entry,
            metric=metric,
            lcs_coverage=lcs_coverage,
            longest_contiguous=longest_contiguous,
            identifier_exact=index in exact_identifier_indices,
            unique_exact_title=(
                len(exact_title_index.get(query.title_compare_key, [])) == 1
                and query.title_compare_key == entry.title_compare_key
            ),
        )
        if candidate_tier is None:
            continue
        accepted.append(
            output_candidate(
                entry=entry,
                query=query,
                metric=metric,
                lcs_coverage=lcs_coverage,
                longest_contiguous=longest_contiguous,
                candidate_tier=candidate_tier,
                score=score,
                identifier_exact=index in exact_identifier_indices,
            )
        )
    accepted.sort(
        key=lambda candidate: (
            not bool(candidate["identifier_exact"]),
            -float(candidate["candidate_score"]),
            int(candidate["mathdb"]["number"]),
        )
    )
    # A collection-specific, exact display-ID equality is a stronger and more
    # specific signal than any lexical near-match.  Retaining secondary text
    # matches beside it would create misleading links (for example, two
    # neighboring Kourovka problems sharing a long common setup).  Preserve
    # only exact-ID alternatives; if the catalog has duplicated that display
    # ID, the row remains explicitly ambiguous below.
    if any(bool(candidate["identifier_exact"]) for candidate in accepted):
        accepted = [candidate for candidate in accepted if bool(candidate["identifier_exact"])]
    for rank, candidate in enumerate(accepted, start=1):
        candidate["rank"] = rank
    accepted = accepted[:top_k]
    if not accepted:
        return "no_candidate", [], len(candidate_indices)
    top = accepted[0]
    if len(accepted) > 1:
        runner_up = accepted[1]
        top_is_identifier = bool(top["identifier_exact"])
        runner_is_identifier = bool(runner_up["identifier_exact"])
        near_tie = abs(float(top["candidate_score"]) - float(runner_up["candidate_score"])) <= 0.03
        if (top_is_identifier and runner_is_identifier) or (not top_is_identifier and near_tie):
            return "ambiguous_candidates", accepted, len(candidate_indices)
    return "candidate_link", accepted, len(candidate_indices)


def make_row(
    query: OverlapQuery, decision: str, candidates: list[dict[str, Any]], pool_size: int
) -> dict[str, Any]:
    return {
        "schema": OUTPUT_SCHEMA,
        "collection_key": query.collection_key,
        "non_assertions": {
            "mathdb_membership_not_asserted": True,
            "statement_recovery_label_not_assigned": True,
            "opdp_recalculation_not_authorized": True,
            "current_open_status_not_verified": True,
        },
        "ulam": {
            "problem_id": query.problem_id,
            "problem_number": query.problem_number,
            "title": query.title,
            "record_index_zero_based": query.record_index,
            "record_canonical_sha256": query.record_sha256,
            "source_record_canonical_sha256": query.source_record_sha256,
            "statement_sha256": query.statement_sha256,
            "statement_utf8_bytes": query.statement_bytes,
            "statement_text_mode": query.statement_text_mode,
            "title_sha256": sha256_text(query.title),
            "title_normalized_sha256": sha256_text(query.title_normalized),
            "title_compare_key_sha256": sha256_text(query.title_compare_key),
            "statement_normalized_sha256": sha256_text(query.statement_normalized),
        },
        "candidate_decision": decision,
        "candidate_pool_size": pool_size,
        "ranked_candidates": candidates,
    }


def atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary_path.unlink()


def build(
    *,
    overlap_path: Path,
    ulam_path: Path,
    catalog_path: Path,
    output_path: Path,
    summary_path: Path,
    top_k: int,
    max_pool_size: int,
    metric_top_k: int,
) -> dict[str, Any]:
    if top_k < 1:
        raise CandidateLinkError("--top-k must be at least 1")
    if max_pool_size < top_k:
        raise CandidateLinkError("--max-pool-size must be at least --top-k")
    if metric_top_k < top_k:
        raise CandidateLinkError("--metric-top-k must be at least --top-k")
    log("loading and SHA-verifying frozen Ulam overlap bindings")
    queries = load_overlap_queries(overlap_path, ulam_path)
    log(f"loaded {len(queries):,} frozen Ulam collection-overlap records")
    log("loading and SHA-verifying MathDB public catalog rows")
    catalog = load_catalog(catalog_path)
    log(f"loaded {len(catalog):,} MathDB public-list catalog records")
    log("building deterministic lexical indexes")
    exact_title_index, title_index, document_frequency, identifier_index = build_indices(catalog)
    log("matching frozen statements against public titles/excerpts (local only)")

    rows: list[dict[str, Any]] = []
    per_collection: dict[str, Counter[str]] = defaultdict(Counter)
    tiers: Counter[str] = Counter()
    pool_sizes: list[int] = []
    for position, query in enumerate(queries, start=1):
        decision, candidates, pool_size = match_query(
            query=query,
            catalog=catalog,
            exact_title_index=exact_title_index,
            title_index=title_index,
            document_frequency=document_frequency,
            identifier_index=identifier_index,
            top_k=top_k,
            max_pool_size=max_pool_size,
            metric_top_k=metric_top_k,
        )
        rows.append(make_row(query, decision, candidates, pool_size))
        counts = per_collection[query.collection_key]
        counts["queries"] += 1
        counts[decision] += 1
        counts["accepted_candidate_links"] += len(candidates)
        for candidate in candidates:
            tiers[candidate["candidate_tier"]] += 1
        pool_sizes.append(pool_size)
        if position % 500 == 0 or position == len(queries):
            log(f"matched {position:,}/{len(queries):,} overlap records")

    output_bytes = b"".join(canonical_json_bytes(row) + b"\n" for row in rows)
    atomic_write(output_path, output_bytes)
    collection_summary: dict[str, Any] = {}
    for collection_key in sorted(per_collection):
        counts = per_collection[collection_key]
        collection_summary[collection_key] = {
            "frozen_ulam_overlap_records": counts["queries"],
            "candidate_link_records": counts["candidate_link"],
            "ambiguous_candidate_records": counts["ambiguous_candidates"],
            "unmatched_records": counts["no_candidate"],
            "accepted_candidate_links_retained": counts["accepted_candidate_links"],
        }
    global_counts = Counter(row["candidate_decision"] for row in rows)
    summary: dict[str, Any] = {
        "schema": SUMMARY_SCHEMA,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "purpose": (
            "Ranked local lexical candidate links between frozen Ulam collection-overlap "
            "records and MathDB public title/excerpt rows. This is not a MathDB collection "
            "membership assertion, statement recovery result, openness verification, or OPDP rescore."
        ),
        "non_destructive_contract": {
            "network_access_used": False,
            "full_ulam_statements_retained_in_output": False,
            "full_mathdb_excerpts_retained_in_output": False,
            "mathdb_membership_asserted": False,
            "recovery_labels_assigned": False,
            "open_status_verified": False,
            "opdp_dimensions_recalculated": False,
            "v17_payload_modified": False,
        },
        "inputs": {
            "frozen_ulam_overlap": {
                "path": str(overlap_path).replace("\\", "/"),
                "sha256": sha256_file(overlap_path),
                "record_count": len(queries),
            },
            "frozen_ulam_v1_6": {
                "path": str(ulam_path).replace("\\", "/"),
                "sha256": sha256_file(ulam_path),
                "record_count": 15458,
            },
            "frozen_mathdb_public_catalog": {
                "path": str(catalog_path).replace("\\", "/"),
                "sha256": sha256_file(catalog_path),
                "record_count": len(catalog),
                "text_basis": "mathdb_public_list_excerpt",
            },
        },
        "normalization": {
            "version": NORMALIZER_VERSION,
            "operations": [
                "HTML entity decoding",
                "Unicode NFKC and fixed typography folding",
                "fixed TeX spacing/layout-command removal",
                "non-layout TeX command names exposed as ordinary lexical tokens",
                "brace removal and whitespace collapse",
                "Unicode word-token extraction",
                "exact-title comparison key removes one leading English definite article only",
            ],
            "non_operations": [
                "no macro expansion",
                "no symbolic simplification",
                "no notation inference",
                "no translation",
                "no statement reconstruction",
            ],
        },
        "candidate_policy": {
            "retained_candidate_tiers": {
                "identifier_exact": (
                    "Exact equality of a fixed collection display identifier: EP-n to "
                    "Erdos Problem #n, or KOU-21.n to Kourovka Notebook Problem 21.n."
                ),
                "unique_exact_normalized_title": (
                    "Unique exact title after fixed presentation normalization and optional "
                    "leading-'The' removal, with at least two title tokens and ten normalized characters."
                ),
                "exact_normalized_title_with_excerpt": (
                    "Exact normalized title, at least three shared non-generic title tokens, "
                    "LCS excerpt coverage >= 0.45, and contiguous token run >= 5."
                ),
                "title_containment_with_strong_excerpt": (
                    "Normalized title containment with title recall >= 0.75, title Jaccard >= "
                    "0.50, LCS excerpt coverage >= 0.60, and contiguous token run >= 7."
                ),
                "high_title_and_excerpt_agreement": (
                    "At least three shared title tokens, title recall >= 0.70, title Jaccard >= "
                    "0.45, LCS excerpt coverage >= 0.72, and contiguous token run >= 8."
                ),
                "near_literal_excerpt_with_title_anchor": (
                    "At least two shared title tokens, LCS excerpt coverage >= 0.90, contiguous "
                    "token run >= 12, and excerpt content-token recall >= 0.80."
                ),
            },
            "ambiguity_rule": (
                "Ambiguous only if more than one retained candidate has an exact external identifier, "
                "or if the top two non-identifier candidate scores differ by at most 0.03."
            ),
            "identifier_precedence": (
                "When an exact fixed display-ID candidate exists, lower-ranked lexical candidates "
                "are suppressed; only duplicate exact-ID alternatives are retained."
            ),
            "candidate_pool": {
                "max_pool_size": max_pool_size,
                "metric_top_k": metric_top_k,
                "retrieval": (
                    "Exact normalized title, fixed external display-ID patterns, title-token inverted "
                    "index, and low-document-frequency title/excerpt token anchors."
                ),
            },
        },
        "results": {
            "frozen_ulam_overlap_records": len(rows),
            "candidate_link_records": global_counts["candidate_link"],
            "ambiguous_candidate_records": global_counts["ambiguous_candidates"],
            "unmatched_records": global_counts["no_candidate"],
            "accepted_candidate_links_retained": sum(
                len(row["ranked_candidates"]) for row in rows
            ),
            "candidate_tier_counts": dict(sorted(tiers.items())),
            "candidate_pool_size": {
                "minimum": min(pool_sizes) if pool_sizes else 0,
                "maximum": max(pool_sizes) if pool_sizes else 0,
                "mean": rounded(sum(pool_sizes) / len(pool_sizes)) if pool_sizes else 0.0,
            },
            "by_collection": collection_summary,
        },
        "output": {
            "path": str(output_path).replace("\\", "/"),
            "sha256": sha256_bytes(output_bytes),
            "record_count": len(rows),
        },
    }
    atomic_write(summary_path, canonical_json_bytes(summary) + b"\n")
    return summary


def self_test() -> None:
    """Small deterministic guards for the normalizer and sequence machinery."""

    if normalize_text("Erd\u0151s\\, Problem\\! #1") != "erd\u0151s problem #1":
        raise CandidateLinkError("self-test: presentation normalizer")
    if identifier_fold("Erd\u0151s Problem #17") != "erdos problem #17":
        raise CandidateLinkError("self-test: identifier accent fold")
    if title_compare_key("the riemann hypothesis") != "riemann hypothesis":
        raise CandidateLinkError("self-test: exact-title comparison key")
    coverage, contiguous = sequence_metrics(("a", "b", "c"), ("q", "a", "b", "z", "c"))
    if coverage != 1.0 or contiguous != 2:
        raise CandidateLinkError("self-test: sequence metrics")
    if greedy_ordered_coverage(("a", "b", "c"), ("a", "x", "b", "c")) != 1.0:
        raise CandidateLinkError("self-test: greedy ordered coverage")


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overlap",
        type=Path,
        default=Path("data/MATHDB_COLLECTION_ULAM_OVERLAP_v1.jsonl"),
        help="frozen 7,489-record Ulam collection-overlap JSONL",
    )
    parser.add_argument(
        "--ulam-v16",
        type=Path,
        default=Path("data/Ulam_UnsolvedMath_OPDP_Assessments_v1.6.json.gz"),
        help="frozen statement-bearing Ulam v1.6 export",
    )
    parser.add_argument(
        "--mathdb-catalog",
        type=Path,
        default=Path("../opdp-v1_7-mathdb-build/mathdb-snapshot-2026-09-08/mathdb_problem_summaries.jsonl"),
        help="frozen MathDB public-title/excerpt catalog JSONL",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/MATHDB_COLLECTION_ULAM_CANDIDATE_LINKS_v1.jsonl"),
        help="output JSONL containing hashes and candidate-link evidence only",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("data/MATHDB_COLLECTION_ULAM_CANDIDATE_LINKS_v1.json"),
        help="output aggregate summary JSON",
    )
    parser.add_argument("--top-k", type=int, default=3, help="maximum retained candidates per Ulam row")
    parser.add_argument(
        "--max-pool-size",
        type=int,
        default=100,
        help="maximum lexical candidates considered per Ulam row",
    )
    parser.add_argument(
        "--metric-top-k",
        type=int,
        default=8,
        help="maximum preliminary candidates receiving quadratic sequence metrics",
    )
    parser.add_argument(
        "--self-test", action="store_true", help="run deterministic unit checks and exit"
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    try:
        self_test()
        if arguments.self_test:
            print("self-test passed")
            return 0
        summary = build(
            overlap_path=arguments.overlap,
            ulam_path=arguments.ulam_v16,
            catalog_path=arguments.mathdb_catalog,
            output_path=arguments.output,
            summary_path=arguments.summary,
            top_k=arguments.top_k,
            max_pool_size=arguments.max_pool_size,
            metric_top_k=arguments.metric_top_k,
        )
    except CandidateLinkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    print(
        "wrote {records:,} candidate-link rows: {links:,} unique, {ambiguous:,} ambiguous, "
        "{unmatched:,} unmatched".format(
            records=summary["results"]["frozen_ulam_overlap_records"],
            links=summary["results"]["candidate_link_records"],
            ambiguous=summary["results"]["ambiguous_candidate_records"],
            unmatched=summary["results"]["unmatched_records"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
