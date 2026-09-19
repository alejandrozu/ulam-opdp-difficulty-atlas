#!/usr/bin/env python3
"""Build a conservative, provenance-preserving ProofAtlas Top 500 crosswalk.

This tool answers a deliberately narrow question: which entries in a frozen
ProofAtlas Top 500 release have a strong *identity candidate* in the frozen
OPDP v1.7 export?  It is not an importer.  In particular, it never rewrites
the OPDP payload, creates an OPDP record, assigns a recovery label, verifies
an open status, or recalculates a difficulty dimension.

The ProofAtlas source contains full public statements.  Those statements are
kept only in a caller-selected local raw snapshot (normally under this
directory's ignored ``raw/`` directory).  The public JSONL sidecar below
contains identifiers, titles, hashes, and comparison metrics--never a
ProofAtlas exact-target string or an OPDP statement/excerpt.  Therefore a
future release can be audited without republishing a second copy of either
source corpus.

Network access is disabled by default.  A live acquisition requires BOTH
``--fetch-proofatlas`` and ``--allow-network`` and writes a new immutable raw
snapshot; it cannot overwrite an existing one.  Alternatively pass an
already-frozen local JSON snapshot via ``--proofatlas-json``.

Important abstention rule
--------------------------
87,105 MathDB rows in v1.7 contain public-list excerpts rather than complete
statements.  A ``no_strong_match`` result is consequently *not evidence that
the ProofAtlas problem is new to OPDP*.  It only means this title-plus-text
comparison did not find enough evidence in the frozen input.  The output
states that rule on every no-match row and in its aggregate manifest.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator


TOOL_NAME = "OPDP ProofAtlas Top 500 conservative crosswalk builder"
TOOL_VERSION = "1.0.0"
TOP500_SCHEMA = "proofatlas.public-open-problem-ranking.v3"
CROSSWALK_SCHEMA = "opdp.proofatlas-top500-crosswalk.v1"
MANIFEST_SCHEMA = "opdp.proofatlas-top500-inventory.v1"
NORMALIZER_VERSION = "opdp-proofatlas-title-statement-normalizer-v1"
DEFAULT_PROOFATLAS_URL = (
    "https://proofatlas.ai/data/open-problems/top500-v16-science-v1.json"
)
DEFAULT_USER_AGENT = "OPDP-ProofAtlas-Crosswalk/1.0 (+https://github.com/alejandrozu/ulam-opdp-difficulty-atlas)"
MAX_FETCH_BYTES = 16 * 1024 * 1024

# Generic words are retained in ordered-token runs but cannot by themselves
# retrieve or justify a candidate.  This is a lexical normalizer only: it does
# not expand macros, simplify expressions, translate notation, or infer an
# equivalent theorem.
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
        "does",
        "every",
        "for",
        "from",
        "given",
        "has",
        "have",
        "if",
        "in",
        "is",
        "it",
        "let",
        "must",
        "not",
        "of",
        "on",
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
        "to",
        "under",
        "using",
        "we",
        "what",
        "when",
        "whether",
        "which",
        "with",
        "without",
    }
)
PRESENTATIONAL_TEX_COMMANDS = re.compile(
    r"\\(?:left|right|big|Big|bigl|bigr|Bigl|Bigr|"
    r"displaystyle|textstyle|scriptstyle|scriptscriptstyle|quad|qquad|enspace|;|,|!|:)",
    re.IGNORECASE,
)
TEX_COMMAND = re.compile(r"\\([A-Za-z]+)")
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
MULTISPACE = re.compile(r"\s+")
RECORDS_MARKER = re.compile(r'"records"\s*:\s*\[')


class CrosswalkError(RuntimeError):
    """Raised for an invalid immutable input or an unsafe output request."""


@dataclass(frozen=True)
class ProofAtlasTarget:
    """A private-in-memory view of one source record.

    ``exact_target`` is intentionally never serialised to a release artifact.
    """

    index: int
    problem_id: str
    rank: int
    title: str
    exact_target: str
    release_status: str | None
    formal_statement_url: str | None
    title_key: str
    title_tokens: tuple[str, ...]
    title_signal: frozenset[str]
    statement_tokens: tuple[str, ...]
    statement_signal: frozenset[str]
    title_sha256: str
    statement_sha256: str


@dataclass(frozen=True)
class AtlasRecord:
    """A minimal transient v1.7 record representation.

    The original OPDP statement remains in the streaming input and is not
    retained after scoring this individual candidate.
    """

    record_index: int
    problem_id: int
    problem_number: str
    title: str
    statement: str
    text_mode: str | None
    source_excerpt_only: bool
    catalog_status: str | None
    source_collection_label: str | None


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


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def require_string(mapping: dict[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CrosswalkError(f"{context}: {key} must be a nonempty string")
    return value


def require_int(value: Any, context: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise CrosswalkError(f"{context}: expected integer")
    return value


def normalize_text(value: str) -> str:
    """Normalize presentation only, never mathematical content or scope."""

    text = html.unescape(value)
    text = unicodedata.normalize("NFKC", text)
    text = (
        text.replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .replace("\ufeff", "")
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
    text = MULTISPACE.sub(" ", text).strip().casefold()
    return text


def tokenise(value: str) -> tuple[str, ...]:
    return tuple(TOKEN_RE.findall(normalize_text(value)))


def signal_tokens(tokens: tuple[str, ...]) -> frozenset[str]:
    return frozenset(token for token in tokens if token not in STOPWORDS)


def title_compare_key(tokens: tuple[str, ...]) -> str:
    """Compare titles with only a leading 'the' and terminal 'problem' folded.

    Removing the generic terminal word fixes a common display variation such
    as ``P versus NP`` versus ``P versus NP Problem`` without conflating
    conjectures or changing mathematical qualification terms.
    """

    result = list(tokens)
    if result[:1] == ["the"]:
        result = result[1:]
    if len(result) >= 3 and result[-1:] == ["problem"]:
        result = result[:-1]
    return " ".join(result)


def rounded(value: float, digits: int = 6) -> float:
    return round(value, digits)


def text_metrics(reference: frozenset[str], candidate: frozenset[str]) -> dict[str, float | int]:
    """Set-based metrics used only with title evidence, never alone."""

    if not reference or not candidate:
        return {
            "shared_signal_tokens": 0,
            "reference_signal_coverage": 0.0,
            "candidate_signal_coverage": 0.0,
            "signal_jaccard": 0.0,
            "signal_f1": 0.0,
        }
    shared = len(reference & candidate)
    reference_coverage = shared / len(reference)
    candidate_coverage = shared / len(candidate)
    union = len(reference | candidate)
    f1 = 0.0
    if reference_coverage + candidate_coverage:
        f1 = 2 * reference_coverage * candidate_coverage / (
            reference_coverage + candidate_coverage
        )
    return {
        "shared_signal_tokens": shared,
        "reference_signal_coverage": rounded(reference_coverage),
        "candidate_signal_coverage": rounded(candidate_coverage),
        "signal_jaccard": rounded(shared / union),
        "signal_f1": rounded(f1),
    }


def longest_common_token_run(
    first: tuple[str, ...], second: tuple[str, ...], maximum_tokens: int = 900
) -> int:
    """Return a bounded exact contiguous token-run length.

    The implementation is sparse rather than an O(n*m) matrix.  The cap is
    a deterministically disclosed guard against unusually large catalog text;
    it does not change titles and never fabricates a semantic correspondence.
    """

    first = first[:maximum_tokens]
    second = second[:maximum_tokens]
    positions: dict[str, list[int]] = defaultdict(list)
    for position, token in enumerate(second):
        positions[token].append(position)
    previous: dict[int, int] = {}
    best = 0
    for token in first:
        current: dict[int, int] = {}
        for position in positions.get(token, []):
            length = previous.get(position - 1, 0) + 1
            current[position] = length
            if length > best:
                best = length
        previous = current
    return best


def atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", delete=False, dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    ) as handle:
        temporary = Path(handle.name)
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_new_snapshot(path: Path, body: bytes) -> None:
    """Create an immutable raw snapshot and reject any accidental replacement."""

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise CrosswalkError(
            f"refusing to overwrite existing raw snapshot: {path}; use it via --proofatlas-json "
            "or choose a new snapshot path"
        ) from exc


def validate_proofatlas_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"proofatlas.ai", "www.proofatlas.ai"}:
        raise CrosswalkError(
            "--proofatlas-url must be an HTTPS URL on proofatlas.ai or www.proofatlas.ai"
        )


def fetch_snapshot(url: str, destination: Path, timeout_seconds: int) -> dict[str, Any]:
    """Fetch one bounded JSON document after the CLI explicitly authorises it."""

    validate_proofatlas_url(url)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            final_url = response.geturl()
            validate_proofatlas_url(final_url)
            body = response.read(MAX_FETCH_BYTES + 1)
            content_type = response.headers.get("Content-Type")
            status = getattr(response, "status", None)
    except OSError as exc:
        raise CrosswalkError(f"ProofAtlas fetch failed: {exc}") from exc
    if len(body) > MAX_FETCH_BYTES:
        raise CrosswalkError(
            f"ProofAtlas response exceeds immutable {MAX_FETCH_BYTES:,}-byte safety limit"
        )
    write_new_snapshot(destination, body)
    return {
        "retrieval_mode": "explicit_network_fetch",
        "requested_url": url,
        "final_url": final_url,
        "http_status": status,
        "content_type": content_type,
        "retrieved_at_utc": utc_now(),
    }


def load_proofatlas_targets(path: Path) -> tuple[list[ProofAtlasTarget], dict[str, Any]]:
    if not path.is_file():
        raise CrosswalkError(f"ProofAtlas snapshot does not exist: {path}")
    try:
        body = path.read_bytes()
        payload = json.loads(body.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CrosswalkError(f"cannot parse ProofAtlas snapshot {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CrosswalkError(f"{path}: expected a JSON object")
    schema_version = payload.get("schemaVersion")
    if schema_version != TOP500_SCHEMA:
        raise CrosswalkError(
            f"{path}: expected schemaVersion {TOP500_SCHEMA!r}, got {schema_version!r}"
        )
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        raise CrosswalkError(f"{path}: records must be an array")
    claimed_count = payload.get("recordCount")
    if claimed_count != len(raw_records):
        raise CrosswalkError(
            f"{path}: recordCount {claimed_count!r} disagrees with {len(raw_records):,} records"
        )
    if len(raw_records) != 500:
        raise CrosswalkError(f"{path}: expected ProofAtlas Top 500, found {len(raw_records):,} records")

    targets: list[ProofAtlasTarget] = []
    ids: set[str] = set()
    ranks: set[int] = set()
    for index, raw in enumerate(raw_records):
        context = f"ProofAtlas record {index}"
        if not isinstance(raw, dict):
            raise CrosswalkError(f"{context}: expected an object")
        problem_id = require_string(raw, "problemId", context)
        title = require_string(raw, "canonicalTitle", context)
        exact_target = require_string(raw, "exactTarget", context)
        rank = require_int(raw.get("releaseRank"), f"{context}.releaseRank")
        if problem_id in ids:
            raise CrosswalkError(f"{context}: duplicate problemId {problem_id!r}")
        if rank in ranks:
            raise CrosswalkError(f"{context}: duplicate releaseRank {rank}")
        ids.add(problem_id)
        ranks.add(rank)
        formal_source = raw.get("formalStatementSource")
        formal_url = None
        if isinstance(formal_source, dict) and isinstance(formal_source.get("url"), str):
            formal_url = formal_source["url"]
        title_tokens = tokenise(title)
        statement_tokens = tokenise(exact_target)
        if not title_tokens or not statement_tokens:
            raise CrosswalkError(f"{context}: title/target tokenisation unexpectedly empty")
        status = raw.get("releaseStatus")
        targets.append(
            ProofAtlasTarget(
                index=index,
                problem_id=problem_id,
                rank=rank,
                title=title,
                exact_target=exact_target,
                release_status=status if isinstance(status, str) else None,
                formal_statement_url=formal_url,
                title_key=title_compare_key(title_tokens),
                title_tokens=title_tokens,
                title_signal=signal_tokens(title_tokens),
                statement_tokens=statement_tokens,
                statement_signal=signal_tokens(statement_tokens),
                title_sha256=sha256_text(title),
                statement_sha256=sha256_text(exact_target),
            )
        )
    if ranks != set(range(1, 501)):
        raise CrosswalkError(f"{path}: releaseRank values are not exactly 1 through 500")
    metadata = {
        "schema_version": schema_version,
        "publication_id": payload.get("publicationId"),
        "release_id": payload.get("releaseId"),
        "release_version": payload.get("releaseVersion"),
        "published_at": payload.get("publishedAt"),
        "edition_date": payload.get("editionDate"),
        "record_count": len(targets),
        "snapshot_sha256": sha256_bytes(body),
        "snapshot_bytes": len(body),
    }
    return targets, metadata


def iter_v17_records(path: Path) -> Iterator[dict[str, Any]]:
    """Stream only the top-level ``records`` array from a large v1.7 JSON file."""

    if not path.is_file():
        raise CrosswalkError(f"OPDP v1.7 export does not exist: {path}")
    opener = gzip.open if path.suffix.lower() == ".gz" else open
    decoder = json.JSONDecoder()
    with opener(path, "rt", encoding="utf-8") as handle:  # type: ignore[arg-type]
        buffer = ""
        marker = RECORDS_MARKER.search(buffer)
        while marker is None:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                raise CrosswalkError(f"{path}: did not find top-level records array")
            buffer += chunk
            # The header is small.  Retaining an unexpectedly huge header is
            # an input-integrity error rather than an unbounded-memory path.
            if len(buffer) > 16 * 1024 * 1024:
                raise CrosswalkError(f"{path}: records marker absent from first 16 MiB")
            marker = RECORDS_MARKER.search(buffer)
        buffer = buffer[marker.end() :]
        while True:
            buffer = buffer.lstrip()
            if buffer.startswith(","):
                buffer = buffer[1:]
                continue
            if buffer.startswith("]"):
                return
            if not buffer:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    raise CrosswalkError(f"{path}: truncated records array")
                buffer = chunk
                continue
            try:
                item, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    raise CrosswalkError(f"{path}: invalid or truncated JSON record")
                buffer += chunk
                if len(buffer) > 32 * 1024 * 1024:
                    raise CrosswalkError(f"{path}: a single record exceeds 32 MiB")
                continue
            if not isinstance(item, dict):
                raise CrosswalkError(f"{path}: records array contains non-object value")
            buffer = buffer[end:]
            yield item


def atlas_record_from_raw(raw: dict[str, Any], record_index: int) -> AtlasRecord | None:
    """Extract only comparison inputs from one v1.7 record."""

    problem_id = raw.get("problem_id")
    problem_number = raw.get("problem_number")
    title = raw.get("title")
    source_text = raw.get("source_text")
    if not isinstance(problem_id, int) or isinstance(problem_id, bool):
        raise CrosswalkError(f"v1.7 record {record_index}: problem_id must be an integer")
    if not isinstance(problem_number, str) or not isinstance(title, str):
        raise CrosswalkError(f"v1.7 record {record_index}: missing string identifier/title")
    if not isinstance(source_text, dict):
        return None
    statement = source_text.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        return None
    text_mode = source_text.get("text_mode")
    if not isinstance(text_mode, str):
        text_mode = None
    flags = raw.get("flags")
    source_excerpt_only = (
        text_mode == "mathdb_public_list_excerpt"
        or (isinstance(flags, list) and "source_excerpt_only" in flags)
    )
    provenance = raw.get("provenance")
    source_collection_label = None
    if isinstance(provenance, dict) and isinstance(provenance.get("source_collection_label"), str):
        source_collection_label = provenance["source_collection_label"]
    catalog_status = raw.get("catalog_status")
    return AtlasRecord(
        record_index=record_index,
        problem_id=problem_id,
        problem_number=problem_number,
        title=title,
        statement=statement,
        text_mode=text_mode,
        source_excerpt_only=source_excerpt_only,
        catalog_status=catalog_status if isinstance(catalog_status, str) else None,
        source_collection_label=source_collection_label,
    )


def build_target_retrieval_index(
    targets: list[ProofAtlasTarget],
) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    by_title: dict[str, list[int]] = defaultdict(list)
    token_counts: Counter[str] = Counter()
    for target in targets:
        by_title[target.title_key].append(target.index)
        token_counts.update(target.title_signal)
    by_token: dict[str, list[int]] = defaultdict(list)
    for target in targets:
        for token in target.title_signal:
            # A short rare label such as NP or ETH is useful, but generic high
            # frequency title words are not permitted to retrieve candidates.
            if len(token) >= 2 and token_counts[token] <= 35:
                by_token[token].append(target.index)
    return by_title, by_token


def candidate_target_indices(
    title_tokens: tuple[str, ...],
    by_title: dict[str, list[int]],
    by_token: dict[str, list[int]],
) -> set[int]:
    result: set[int] = set(by_title.get(title_compare_key(title_tokens), []))
    for token in signal_tokens(title_tokens):
        result.update(by_token.get(token, []))
    return result


def comparison_for(target: ProofAtlasTarget, record: AtlasRecord) -> dict[str, Any]:
    atlas_title_tokens = tokenise(record.title)
    atlas_statement_tokens = tokenise(record.statement)
    atlas_title_signal = signal_tokens(atlas_title_tokens)
    atlas_statement_signal = signal_tokens(atlas_statement_tokens)
    title = text_metrics(target.title_signal, atlas_title_signal)
    statement = text_metrics(target.statement_signal, atlas_statement_signal)
    title_exact = target.title_key == title_compare_key(atlas_title_tokens)
    run = longest_common_token_run(target.statement_tokens, atlas_statement_tokens)
    title_shared = int(title["shared_signal_tokens"])
    statement_shared = int(statement["shared_signal_tokens"])

    # Candidate retrieval may share a single short token.  It cannot become a
    # viable identity candidate absent a second title feature or a long,
    # specific anchor.  This avoids treating topical overlap as identity.
    shared_title_tokens = target.title_signal & atlas_title_signal
    has_long_title_anchor = any(len(token) >= 9 for token in shared_title_tokens)
    strong_title = title_exact or (
        title_shared >= 2
        and float(title["reference_signal_coverage"]) >= 0.60
        and float(title["signal_jaccard"]) >= 0.35
    ) or (
        title_shared >= 1
        and has_long_title_anchor
        and float(title["reference_signal_coverage"]) >= 0.50
        and float(title["signal_jaccard"]) >= 0.30
    )
    statement_supported = (
        statement_shared >= 3
        and float(statement["reference_signal_coverage"]) >= 0.24
        and (run >= 3 or float(statement["signal_jaccard"]) >= 0.12)
    ) or (
        statement_shared >= 5 and float(statement["reference_signal_coverage"]) >= 0.42
    )
    strong = bool(strong_title and statement_supported)
    exact = bool(
        strong
        and not record.source_excerpt_only
        and title_exact
        and float(statement["reference_signal_coverage"]) >= 0.58
        and run >= 3
    )
    quality = (
        0.50 * float(title["signal_f1"])
        + 0.40 * float(statement["reference_signal_coverage"])
        + 0.10 * min(run / 8.0, 1.0)
    )
    return {
        "title_exact_after_fixed_presentation_normalization": title_exact,
        "title": title,
        "statement": {
            **statement,
            "longest_exact_token_run_capped_at_900": run,
            "source_text_complete_for_identity_evidence": not record.source_excerpt_only,
        },
        "strong_title_evidence": strong_title,
        "strong_statement_evidence": statement_supported,
        "strong_identity_candidate": strong,
        "exact_identity_candidate": exact,
        "composite_quality": rounded(quality),
    }


def candidate_output(record: AtlasRecord, comparison: dict[str, Any]) -> dict[str, Any]:
    """Return a public-safe candidate: no source statement/excerpt is copied."""

    return {
        "atlas_record_index": record.record_index,
        "problem_id": record.problem_id,
        "problem_number": record.problem_number,
        "title": record.title,
        "title_sha256": sha256_text(record.title),
        "catalog_status": record.catalog_status,
        "source_collection_label": record.source_collection_label,
        "source_text_mode": record.text_mode,
        "source_statement_completeness": (
            "excerpt_not_guaranteed_complete" if record.source_excerpt_only else "frozen_record_statement"
        ),
        "comparison": comparison,
    }


def retain_candidate(
    retained: list[dict[str, Any]], candidate: dict[str, Any], limit: int = 4
) -> None:
    retained.append(candidate)
    retained.sort(
        key=lambda row: (
            -float(row["comparison"]["composite_quality"]),
            -int(row["comparison"]["strong_identity_candidate"]),
            int(row["problem_id"]),
        )
    )
    del retained[limit:]


def decision_for(candidates: list[dict[str, Any]]) -> tuple[str, str, list[dict[str, Any]]]:
    viable = [row for row in candidates if row["comparison"]["strong_identity_candidate"]]
    viable.sort(
        key=lambda row: (-float(row["comparison"]["composite_quality"]), int(row["problem_id"])))
    if not viable:
        return (
            "no_strong_match",
            "No candidate passed both the title and statement evidence thresholds. This is an "
            "abstention, not evidence that the ProofAtlas item is absent from OPDP; incomplete "
            "MathDB excerpts make absence-of-match especially non-diagnostic.",
            candidates[:1],
        )
    primary = viable[0]
    if len(viable) > 1 and (
        float(primary["comparison"]["composite_quality"])
        - float(viable[1]["comparison"]["composite_quality"])
        <= 0.03
    ):
        return (
            "ambiguous",
            "More than one OPDP record has similarly strong title-plus-statement evidence; "
            "a human scope review is required before treating any one record as the match.",
            viable[:3],
        )
    if primary["comparison"]["exact_identity_candidate"]:
        return (
            "exact_match",
            "Unique candidate with exact fixed-normalized title agreement and strong complete-statement "
            "token evidence. This is an identity crosswalk result, not a current-status verification.",
            [primary],
        )
    if primary["source_statement_completeness"] == "excerpt_not_guaranteed_complete":
        reason = (
            "Unique candidate with strong title-plus-excerpt agreement. The OPDP text is a MathDB "
            "excerpt, so this is deliberately not promoted to exact match or source-statement recovery."
        )
    else:
        reason = (
            "Unique candidate with strong title-plus-statement agreement but not enough evidence for the "
            "stricter exact-match gate. This is not a current-status verification."
        )
    return "strong_match", reason, [primary]


def build_crosswalk(
    *,
    targets: list[ProofAtlasTarget],
    proofatlas_metadata: dict[str, Any],
    proofatlas_snapshot_path: Path,
    atlas_path: Path,
    output_path: Path,
    summary_path: Path,
    retrieval_metadata: dict[str, Any],
    max_atlas_records: int | None = None,
) -> dict[str, Any]:
    """Stream OPDP once, then write deterministic public-safe artifacts."""

    if output_path.resolve() == atlas_path.resolve() or summary_path.resolve() == atlas_path.resolve():
        raise CrosswalkError("refusing to use the OPDP payload itself as a crosswalk output path")
    if output_path.resolve() == summary_path.resolve():
        raise CrosswalkError("crosswalk JSONL and inventory manifest paths must differ")
    by_title, by_token = build_target_retrieval_index(targets)
    target_by_index = {target.index: target for target in targets}
    retained: dict[int, list[dict[str, Any]]] = {target.index: [] for target in targets}
    scanned = 0
    compared_pairs = 0
    excerpt_rows = 0
    for record_index, raw in enumerate(iter_v17_records(atlas_path)):
        if max_atlas_records is not None and scanned >= max_atlas_records:
            break
        scanned += 1
        record = atlas_record_from_raw(raw, record_index)
        if record is None:
            continue
        if record.source_excerpt_only:
            excerpt_rows += 1
        title_tokens = tokenise(record.title)
        for target_index in candidate_target_indices(title_tokens, by_title, by_token):
            target = target_by_index[target_index]
            comparison = comparison_for(target, record)
            compared_pairs += 1
            # Retain a weak candidate only when there is some non-generic title
            # signal.  This makes the abstention auditable without publishing
            # statement prose or flooding the public output with topical noise.
            title_shared = int(comparison["title"]["shared_signal_tokens"])
            if comparison["strong_identity_candidate"] or title_shared >= 2 or comparison[
                "title_exact_after_fixed_presentation_normalization"
            ]:
                retain_candidate(retained[target_index], candidate_output(record, comparison))
        if scanned % 10_000 == 0:
            log(f"compared {scanned:,} streamed OPDP records")

    rows: list[dict[str, Any]] = []
    decisions: Counter[str] = Counter()
    match_modes: Counter[str] = Counter()
    for target in sorted(targets, key=lambda row: row.rank):
        decision, reason, selected = decision_for(retained[target.index])
        decisions[decision] += 1
        if selected and decision in {"exact_match", "strong_match", "ambiguous"}:
            for candidate in selected:
                match_modes[candidate["source_text_mode"] or "unknown"] += 1
        row = {
            "schema": CROSSWALK_SCHEMA,
            "proofatlas": {
                "problem_id": target.problem_id,
                "release_rank": target.rank,
                "canonical_title": target.title,
                "canonical_title_sha256": target.title_sha256,
                "exact_target_sha256": target.statement_sha256,
                "release_status_as_published": target.release_status,
                "formal_statement_source_url": target.formal_statement_url,
            },
            "decision": decision,
            "decision_reason": reason,
            "newness_inference": (
                "identity_candidate_already_present_in_frozen_opdp"
                if decision in {"exact_match", "strong_match"}
                else "not_determined_by_this_crosswalk"
            ),
            "candidate_count_retained": len(retained[target.index]),
            "selected_candidates": selected,
            "non_destructive_contract": {
                "proofatlas_exact_target_retained": False,
                "opdp_statement_or_excerpt_retained": False,
                "opdp_payload_modified": False,
                "opdp_records_appended": False,
                "open_status_verified": False,
                "recovery_label_assigned": False,
                "opdp_dimensions_recalculated": False,
            },
        }
        rows.append(row)
    output_body = b"".join(canonical_json_bytes(row) + b"\n" for row in rows)
    atomic_write(output_path, output_body)

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "purpose": (
            "A conservative title-plus-statement identity crosswalk between a frozen ProofAtlas "
            "Top 500 release and frozen OPDP v1.7. It is not a corpus append, statement recovery, "
            "current-openness review, or OPDP rescore."
        ),
        "non_destructive_contract": {
            "network_access_used": retrieval_metadata.get("retrieval_mode") == "explicit_network_fetch",
            "raw_proofatlas_snapshot_publicly_retained": False,
            "proofatlas_exact_targets_retained_in_public_sidecars": False,
            "opdp_statements_or_mathdb_excerpts_retained_in_public_sidecars": False,
            "opdp_payload_modified": False,
            "opdp_records_appended": False,
            "open_status_verified": False,
            "recovery_labels_assigned": False,
            "opdp_dimensions_recalculated": False,
        },
        "inputs": {
            "proofatlas_top500": {
                **proofatlas_metadata,
                "snapshot_path": str(proofatlas_snapshot_path).replace("\\", "/"),
                "raw_snapshot_retention": "local_ignored_snapshot_not_a_release_artifact",
                "retrieval": retrieval_metadata,
            },
            "opdp_v1_7": {
                "path": str(atlas_path).replace("\\", "/"),
                "sha256": sha256_file(atlas_path),
                "records_streamed": scanned,
                "scope": "complete" if max_atlas_records is None else "sample_not_for_release",
                "mathdb_excerpt_rows_seen": excerpt_rows,
            },
        },
        "comparison_policy": {
            "normalizer_version": NORMALIZER_VERSION,
            "required_evidence": "title evidence plus statement/excerpt token evidence",
            "exact_match_gate": (
                "unique candidate; exact fixed-normalized title; complete frozen OPDP statement; "
                "strong target-token coverage and contiguous token-run evidence"
            ),
            "strong_match_gate": (
                "unique candidate; strong title evidence; and nontrivial statement/excerpt evidence. "
                "MathDB excerpts can qualify only as strong, never exact."
            ),
            "ambiguous_gate": "two or more strong candidates within 0.03 composite-quality points",
            "no_strong_match_interpretation": (
                "abstention only, never evidence of corpus absence or newness; incomplete MathDB "
                "excerpts make non-match especially non-diagnostic"
            ),
            "operations": [
                "HTML entity decoding",
                "Unicode NFKC and fixed typography folding",
                "fixed TeX layout-command removal",
                "non-layout TeX command names exposed as lexical tokens",
                "Unicode word-token comparison",
                "fixed title folding of one leading 'the' and terminal generic 'problem'",
            ],
            "non_operations": [
                "no macro expansion",
                "no symbolic simplification",
                "no notation inference",
                "no translation",
                "no statement reconstruction",
                "no current-status inference",
            ],
        },
        "results": {
            "proofatlas_targets": len(targets),
            "opdp_candidate_pairs_compared": compared_pairs,
            "decisions": dict(sorted(decisions.items())),
            "selected_candidate_text_modes": dict(sorted(match_modes.items())),
            "no_strong_match_is_not_newness_evidence": decisions["no_strong_match"],
        },
        "output": {
            "path": str(output_path).replace("\\", "/"),
            "sha256": sha256_bytes(output_body),
            "record_count": len(rows),
        },
    }
    atomic_write(summary_path, canonical_json_bytes(manifest) + b"\n")
    return manifest


def self_test() -> None:
    """Fast deterministic guards for normalisation and threshold primitives."""

    if normalize_text("The Riemann\\, Hypothesis") != "the riemann hypothesis":
        raise CrosswalkError("self-test: presentation normalizer")
    if title_compare_key(tokenise("The P versus NP Problem")) != "p versus np":
        raise CrosswalkError("self-test: title compare key")
    if longest_common_token_run(("a", "b", "c"), ("x", "a", "b", "c", "z")) != 3:
        raise CrosswalkError("self-test: contiguous token run")
    metric = text_metrics(frozenset({"riemann", "hypothesis"}), frozenset({"riemann"}))
    if metric["reference_signal_coverage"] != 0.5:
        raise CrosswalkError("self-test: metric coverage")


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=False)
    source.add_argument(
        "--proofatlas-json",
        type=Path,
        help="previously frozen local ProofAtlas Top 500 JSON snapshot; no network is used",
    )
    source.add_argument(
        "--fetch-proofatlas",
        action="store_true",
        help="explicitly fetch the canonical ProofAtlas Top 500 JSON into --raw-snapshot",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="required alongside --fetch-proofatlas; otherwise no network access is possible",
    )
    parser.add_argument(
        "--proofatlas-url",
        default=DEFAULT_PROOFATLAS_URL,
        help="canonical HTTPS ProofAtlas Top 500 JSON endpoint",
    )
    parser.add_argument(
        "--raw-snapshot",
        type=Path,
        help="new ignored local snapshot path required with --fetch-proofatlas",
    )
    parser.add_argument(
        "--timeout-seconds", type=int, default=30, help="network timeout for explicit fetch")
    parser.add_argument(
        "--atlas-v17",
        type=Path,
        default=Path("data/Ulam_MathDB_OPDP_Assessments_v1.7.json.gz"),
        help="frozen OPDP v1.7 export to stream without mutation",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/PROOFATLAS_TOP500_CROSSWALK_v1.jsonl"),
        help="public-safe JSONL crosswalk output",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        default=Path("data/PROOFATLAS_TOP500_INVENTORY_v1.json"),
        help="public hash/metadata manifest and aggregate results",
    )
    parser.add_argument(
        "--max-atlas-records",
        type=int,
        help="test-only sample cap; output is explicitly marked non-release",
    )
    parser.add_argument("--self-test", action="store_true", help="run deterministic checks and exit")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    arguments = parse_arguments(argv)
    try:
        self_test()
        if arguments.self_test:
            print("self-test passed")
            return 0
        if arguments.timeout_seconds <= 0:
            raise CrosswalkError("--timeout-seconds must be positive")
        if arguments.max_atlas_records is not None and arguments.max_atlas_records <= 0:
            raise CrosswalkError("--max-atlas-records must be positive when provided")
        if arguments.fetch_proofatlas:
            if not arguments.allow_network:
                raise CrosswalkError("--fetch-proofatlas requires explicit --allow-network")
            if arguments.raw_snapshot is None:
                raise CrosswalkError("--fetch-proofatlas requires a new --raw-snapshot path")
            retrieval_metadata = fetch_snapshot(
                arguments.proofatlas_url, arguments.raw_snapshot, arguments.timeout_seconds
            )
            proofatlas_path = arguments.raw_snapshot
        else:
            if arguments.allow_network:
                raise CrosswalkError("--allow-network is meaningful only with --fetch-proofatlas")
            if arguments.proofatlas_json is None:
                raise CrosswalkError(
                    "provide --proofatlas-json or explicitly request --fetch-proofatlas --allow-network"
                )
            proofatlas_path = arguments.proofatlas_json
            retrieval_metadata = {"retrieval_mode": "local_frozen_snapshot"}
        targets, proofatlas_metadata = load_proofatlas_targets(proofatlas_path)
        manifest = build_crosswalk(
            targets=targets,
            proofatlas_metadata=proofatlas_metadata,
            proofatlas_snapshot_path=proofatlas_path,
            atlas_path=arguments.atlas_v17,
            output_path=arguments.output,
            summary_path=arguments.summary,
            retrieval_metadata=retrieval_metadata,
            max_atlas_records=arguments.max_atlas_records,
        )
    except CrosswalkError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    results = manifest["results"]
    print(
        "wrote {targets:,} Top 500 crosswalk rows: {exact} exact, {strong} strong, "
        "{ambiguous} ambiguous, {unmatched} no-strong-match".format(
            targets=results["proofatlas_targets"],
            exact=results["decisions"].get("exact_match", 0),
            strong=results["decisions"].get("strong_match", 0),
            ambiguous=results["decisions"].get("ambiguous", 0),
            unmatched=results["decisions"].get("no_strong_match", 0),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
