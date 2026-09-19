#!/usr/bin/env python3
"""Assemble a review-gated, source-safe ProofAtlas v1.8 curator input.

The ProofAtlas discovery reviews intentionally keep upstream target prose in
ignored local snapshots.  This offline assembler promotes only independently
authored normalization drafts that have already passed a separate identity
review into the JSONL contract consumed by ``build_proofatlas_append_v1_8``.
It never copies an upstream target or collaboration-card statement into the
release input.

Top 500 entries use their frozen release rank as the stable namespace number.
Collaboration-directory entries use ``1,000,000 + frozen card ordinal`` so
they cannot renumber a Top 500 addition or collide with a future source.

The caller must explicitly pass ``--approve``.  This is a release-assembly
gate, not a claim that ProofAtlas's source status has been independently
verified.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import tempfile
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


CURATED_SCHEMA = "opdp.proofatlas.curated-problem.v1"
MANIFEST_SCHEMA = "opdp.proofatlas.curated-snapshot-manifest.v1"
TOP500_SCHEMA = "proofatlas.public-open-problem-ranking.v3"
TOP500_NAMESPACE_MAX = 999_999
COLLABORATION_NAMESPACE_OFFSET = 1_000_000
SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
HTTP_URL = re.compile(r"^https?://", re.I)
NORMALIZATION_MODE = "curator_authored_independent_normalization"
MIN_COPY_PROSE_CHARS = 24
MIN_COPY_PROSE_WORDS = 8


class AssemblyError(RuntimeError):
    """A curator-input contract violation."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AssemblyError(f"{context}: expected a nonempty string")
    return value.strip()


def require_http_url(value: Any, context: str) -> str:
    result = require_string(value, context)
    if not HTTP_URL.match(result):
        raise AssemblyError(f"{context}: expected an HTTP(S) URL")
    return result


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise AssemblyError(f"missing JSONL input: {path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                raise AssemblyError(f"{path}:{line_number}: blank JSONL lines are not allowed")
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AssemblyError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(value, dict):
                raise AssemblyError(f"{path}:{line_number}: expected an object")
            rows.append(value)
    return rows


def atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def load_top500(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    try:
        body = path.read_bytes()
        payload = json.loads(body.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssemblyError(f"cannot parse frozen Top 500 snapshot {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schemaVersion") != TOP500_SCHEMA:
        raise AssemblyError(f"{path}: unexpected Top 500 schema")
    records = payload.get("records")
    if not isinstance(records, list) or len(records) != 500:
        raise AssemblyError(f"{path}: expected 500 Top 500 records")
    by_id: dict[str, dict[str, Any]] = {}
    ranks: set[int] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise AssemblyError(f"Top 500 record {index}: expected object")
        source_id = require_string(record.get("problemId"), f"Top 500 record {index}.problemId")
        rank = record.get("releaseRank")
        if not isinstance(rank, int) or not (1 <= rank <= 500):
            raise AssemblyError(f"Top 500 record {source_id}: invalid releaseRank")
        if source_id in by_id or rank in ranks:
            raise AssemblyError(f"Top 500 snapshot has duplicate ID/rank: {source_id}/{rank}")
        require_string(record.get("canonicalTitle"), f"Top 500 record {source_id}.canonicalTitle")
        require_string(record.get("releaseStatus"), f"Top 500 record {source_id}.releaseStatus")
        require_string(record.get("exactTarget"), f"Top 500 record {source_id}.exactTarget")
        require_string(record.get("readerQuestion"), f"Top 500 record {source_id}.readerQuestion")
        formal = record.get("formalStatementSource")
        if not isinstance(formal, dict):
            raise AssemblyError(f"Top 500 record {source_id}: formalStatementSource missing")
        require_http_url(formal.get("url"), f"Top 500 record {source_id}.formalStatementSource.url")
        by_id[source_id] = record
        ranks.add(rank)
    metadata = {
        "source_url": "https://www.proofatlas.ai/data/open-problems/top500-v16-science-v1.json",
        "snapshot_sha256": sha256_bytes(body),
        "snapshot_bytes": len(body),
        "release_version": payload.get("releaseVersion"),
        "release_id": payload.get("releaseId"),
        "published_at": payload.get("publishedAt"),
    }
    return by_id, metadata


class CollaborationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        values = {key: value or "" for key, value in attrs}
        if "data-collaboration-workspace" in values:
            self.rows.append(values)


def load_collaboration(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    try:
        body = path.read_bytes()
        source = body.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AssemblyError(f"cannot parse frozen collaboration snapshot {path}: {exc}") from exc
    parser = CollaborationParser()
    parser.feed(source)
    parser.close()
    if len(parser.rows) != 268:
        raise AssemblyError(f"{path}: expected 268 workspace cards, found {len(parser.rows)}")
    by_slug: dict[str, dict[str, Any]] = {}
    for ordinal, values in enumerate(parser.rows, 1):
        slug = require_string(values.get("data-discovery-slug"), f"workspace {ordinal}.slug")
        title = require_string(values.get("data-discovery-sort-title"), f"workspace {slug}.title")
        status = require_string(values.get("data-discovery-status"), f"workspace {slug}.status")
        href = require_string(values.get("href"), f"workspace {slug}.href")
        card_statement = require_string(
            html.unescape(values.get("data-discovery-statement", "")), f"workspace {slug}.statement"
        )
        route = f"https://proofatlas.ai/collaboration/{slug}/"
        # The raw route is only used as an integrity check; neither card
        # statement nor other card prose is retained in this release input.
        if href.rstrip("/") not in {f"./{slug}", f"/collaboration/{slug}"}:
            raise AssemblyError(f"workspace {slug}: unexpected route {href!r}")
        if slug in by_slug:
            raise AssemblyError(f"collaboration snapshot has duplicate slug {slug!r}")
        by_slug[slug] = {
            "slug": slug,
            "ordinal": ordinal,
            "title": html.unescape(title),
            "status": status,
            "route": route,
            # Private, in-memory copy check only. No call path serializes it.
            "upstream_card_statement": card_statement,
        }
    metadata = {
        "source_url": "https://www.proofatlas.ai/collaboration/",
        "snapshot_sha256": sha256_bytes(body),
        "snapshot_bytes": len(body),
        "workspace_count": len(by_slug),
    }
    return by_slug, metadata


def one_line_statement(value: Any, context: str) -> str:
    statement = require_string(value, context)
    if "\n" in statement or "\r" in statement:
        raise AssemblyError(f"{context}: statement must be one line")
    if len(statement) > 32_700:
        raise AssemblyError(f"{context}: statement is too long")
    return statement


def normalized_prose(value: str) -> str:
    """Canonicalize prose only for an in-memory source-copy guard.

    This is not a mathematical-equivalence test. It catches exact or
    materially long reuse of a frozen upstream target/card statement while
    allowing independently authored mathematical language. Source text never
    leaves this in-memory check.
    """

    value = unicodedata.normalize("NFKC", html.unescape(value)).casefold()
    return " ".join(re.findall(r"[a-z0-9]+", value))


def contains_material_source_copy(statement: str, upstream: str) -> bool:
    statement_normalized = normalized_prose(statement)
    upstream_normalized = normalized_prose(upstream)
    if not statement_normalized or not upstream_normalized:
        return False
    if statement_normalized == upstream_normalized:
        return True
    if len(statement_normalized) >= MIN_COPY_PROSE_CHARS and statement_normalized in upstream_normalized:
        return True
    if len(upstream_normalized) >= MIN_COPY_PROSE_CHARS and upstream_normalized in statement_normalized:
        return True
    statement_words = statement_normalized.split()
    upstream_words = upstream_normalized.split()
    if min(len(statement_words), len(upstream_words)) < MIN_COPY_PROSE_WORDS:
        return False
    upstream_runs = {
        tuple(upstream_words[offset : offset + MIN_COPY_PROSE_WORDS])
        for offset in range(len(upstream_words) - MIN_COPY_PROSE_WORDS + 1)
    }
    return any(
        tuple(statement_words[offset : offset + MIN_COPY_PROSE_WORDS]) in upstream_runs
        for offset in range(len(statement_words) - MIN_COPY_PROSE_WORDS + 1)
    )


def all_frozen_upstream_prose(
    top500: dict[str, dict[str, Any]], collaboration: dict[str, dict[str, Any]] | None
) -> tuple[str, ...]:
    """Collect every frozen ProofAtlas statement for an in-memory copy check.

    A curator normalization can accidentally borrow language from a related
    ProofAtlas record rather than from the record it is attached to.  Checking
    only the source record therefore does not meet the release promise.  This
    helper deliberately returns prose only to the local assembler; neither the
    values nor their source identifiers are serialized into the curated input
    or manifest.
    """

    values: list[str] = []
    for record in top500.values():
        for field in ("exactTarget", "readerQuestion"):
            value = record.get(field)
            if isinstance(value, str) and value.strip():
                values.append(value)
    if collaboration is not None:
        for workspace in collaboration.values():
            value = workspace.get("upstream_card_statement")
            if isinstance(value, str) and value.strip():
                values.append(value)
    return tuple(values)


def reject_copied_upstream_prose(statement: str, upstream_values: Iterable[Any], context: str) -> None:
    """Reject a draft that transcribes any frozen ProofAtlas source statement."""

    for upstream in upstream_values:
        if not isinstance(upstream, str) or not upstream.strip():
            continue
        if contains_material_source_copy(statement, upstream):
            raise AssemblyError(
                f"{context}: curator normalization materially copies frozen ProofAtlas source statement prose"
            )


def top500_draft_fields(row: dict[str, Any], source_path: Path) -> tuple[str, int, str, str, str, str, str]:
    """Normalize the three reviewed Top 500 draft schemas to common fields."""

    schema = row.get("schema")
    if schema == "opdp.proofatlas.append-curation-draft.v1":
        source = row.get("source")
        if not isinstance(source, dict):
            raise AssemblyError(f"{source_path}: ambiguous draft source missing")
        identifier = require_string(source.get("problem_id"), f"{source_path}: source.problem_id")
        rank = source.get("release_rank")
        status = require_string(source.get("source_status_as_published"), f"{source_path}: source status")
        url = require_http_url(source.get("formal_source_url"), f"{source_path}: formal source URL")
        statement = one_line_statement(row.get("proposed_statement"), f"{source_path}: proposed_statement")
        provenance = row.get("statement_provenance")
    elif schema == "opdp.proofatlas-curator-normalization-draft.v1":
        identifier = require_string(row.get("proofatlas_problem_id"), f"{source_path}: proofatlas_problem_id")
        rank = row.get("release_rank")
        status = require_string(row.get("source_published_status"), f"{source_path}: source_published_status")
        url = require_http_url(row.get("formal_statement_source_url"), f"{source_path}: formal_statement_source_url")
        statement = one_line_statement(row.get("curator_authored_independent_normalization"), f"{source_path}: normalization")
        # The first low-rank review tranche predates the common provenance
        # envelope.  Its two explicit source-safety declarations are the
        # attestation: a curator-authored normalization and a positive
        # no-copy flag.  Accept that legacy contract only when both exact
        # review fields are present; other instances of this schema must
        # supply the later explicit attestation below.
        legacy_attestation = (
            row.get("source_text_not_copied") is True
            and row.get("draft_status") == "curator_authored_normalization_pending_source_and_status_review"
        )
        if legacy_attestation:
            attestation = (
                "The frozen low-rank review draft explicitly declares this a curator-authored independent "
                "normalization and declares source_text_not_copied=true; it remains a source-published, "
                "not independently verified, status record."
            )
        else:
            attestation = require_string(
                row.get("curator_authorship_attestation"), f"{source_path}: curator_authorship_attestation"
            )
        provenance = {
            "mode": NORMALIZATION_MODE,
            "attestation": attestation,
        }
    elif schema == "opdp.proofatlas-top500-append-draft.v1":
        source = row.get("proofatlas")
        if not isinstance(source, dict):
            raise AssemblyError(f"{source_path}: high-rank draft proofatlas missing")
        identifier = require_string(source.get("problem_id"), f"{source_path}: proofatlas.problem_id")
        rank = source.get("release_rank")
        status = require_string(source.get("release_status_as_published"), f"{source_path}: release status")
        url = require_http_url(source.get("formal_statement_source_url"), f"{source_path}: formal source URL")
        statement = one_line_statement(row.get("curator_authored_independent_normalization"), f"{source_path}: normalization")
        provenance = row.get("statement_provenance")
    else:
        raise AssemblyError(f"{source_path}: unsupported Top 500 draft schema {schema!r}")
    if not isinstance(rank, int) or not (1 <= rank <= TOP500_NAMESPACE_MAX):
        raise AssemblyError(f"{source_path}: release rank must be an integer in [1, {TOP500_NAMESPACE_MAX}]")
    if not isinstance(provenance, dict) or provenance.get("mode") != NORMALIZATION_MODE:
        raise AssemblyError(f"{source_path}: draft lacks curator-authored normalization provenance")
    attestation = require_string(provenance.get("attestation"), f"{source_path}: normalization attestation")
    return identifier, rank, status, url, statement, attestation, str(schema)


def collaboration_draft_fields(row: dict[str, Any], source_path: Path) -> tuple[str, str, str, str, str, str]:
    schema = row.get("schema")
    if schema == "opdp.proofatlas.collaboration-append-curation-draft.v1":
        source = row.get("source")
        if not isinstance(source, dict):
            raise AssemblyError(f"{source_path}: collaboration draft source missing")
        identifier = require_string(source.get("source_id"), f"{source_path}: source.source_id")
        route = require_http_url(source.get("source_route"), f"{source_path}: source.source_route")
        status = require_string(source.get("source_status_as_published"), f"{source_path}: source status")
        statement = one_line_statement(row.get("proposed_statement"), f"{source_path}: proposed_statement")
        provenance = row.get("statement_provenance")
        if not isinstance(provenance, dict) or provenance.get("mode") != NORMALIZATION_MODE:
            raise AssemblyError(f"{source_path}: collaboration draft lacks independent normalization provenance")
        attestation = require_string(provenance.get("attestation"), f"{source_path}: normalization attestation")
    elif schema == "opdp.private-proofatlas-collaboration-curator-normalization-draft.v1":
        # This is an ignored local handoff produced by the fixed-partition
        # reviewer.  It contains an independently authored normalization, not
        # source-card prose.  The final public input preserves only that
        # normalization, the source locator, and its provenance caveat.
        review_key = require_string(row.get("review_key"), f"{source_path}: review_key")
        prefix = "proofatlas-collaboration:"
        if not review_key.startswith(prefix) or not review_key[len(prefix) :]:
            raise AssemblyError(f"{source_path}: malformed collaboration review_key")
        identifier = review_key[len(prefix) :]
        route = require_http_url(row.get("formal_source_url"), f"{source_path}: formal_source_url")
        status = require_string(row.get("source_status_as_published"), f"{source_path}: source_status_as_published")
        statement = one_line_statement(row.get("normalization"), f"{source_path}: normalization")
        if row.get("normalization_provenance") != "curator_authored_independent_paraphrase_draft":
            raise AssemblyError(f"{source_path}: unsupported private normalization provenance")
        attestation = require_string(row.get("curator_authorship_attestation"), f"{source_path}: curator_authorship_attestation")
    else:
        raise AssemblyError(f"{source_path}: unsupported collaboration draft schema {schema!r}")
    return identifier, route, status, statement, attestation, str(schema)


def build_snapshot(top500: dict[str, Any], collaboration: dict[str, Any] | None) -> dict[str, Any]:
    sources: list[dict[str, Any]] = [{"kind": "top500_v16", **top500}]
    if collaboration is not None:
        sources.append({"kind": "collaboration_directory", **collaboration})
    return {
        "snapshot_id": "proofatlas-public-site-2026-09-19-v1",
        "curation_status": "approved_for_opdp_append",
        "curation_basis": "Repository-maintainer-authorized source/identity audit; not an independent current-status proof.",
        "source_name": "ProofAtlas",
        "source_url": "https://www.proofatlas.ai/",
        "retrieved_at": "2026-09-19",
        "declared_license": None,
        "terms_url": None,
        "statement_provenance": {
            "mode": NORMALIZATION_MODE,
            "attestation": "Every problem.statement in this frozen input is an independently authored OPDP curator normalization. It does not copy ProofAtlas target/card prose or linked-source prose; ProofAtlas URLs and hashes provide attribution and locators.",
        },
        "frozen_source_artifacts": sources,
    }


def make_top500_entry(
    *, snapshot: dict[str, Any], source: dict[str, Any], status: str, url: str, statement: str, draft_schema: str, attestation: str
) -> dict[str, Any]:
    identifier = require_string(source.get("problemId"), "Top 500 source ID")
    rank = source.get("releaseRank")
    if not isinstance(rank, int):
        raise AssemblyError(f"Top 500 {identifier}: missing rank")
    title = require_string(source.get("canonicalTitle"), f"Top 500 {identifier}.title")
    raw_status = require_string(source.get("releaseStatus"), f"Top 500 {identifier}.status")
    if raw_status != status:
        raise AssemblyError(f"Top 500 {identifier}: draft status differs from frozen source")
    formal = source.get("formalStatementSource")
    raw_url = formal.get("url") if isinstance(formal, dict) else None
    if raw_url != url:
        raise AssemblyError(f"Top 500 {identifier}: draft formal URL differs from frozen source")
    source_id = f"top500--{identifier}"
    if not SOURCE_ID.fullmatch(source_id):
        raise AssemblyError(f"Top 500 {identifier}: generated source ID is invalid")
    return {
        "schema": CURATED_SCHEMA,
        "snapshot": snapshot,
        "problem": {
            "namespace_number": rank,
            "source_id": source_id,
            "title": title,
            "statement": statement,
            "background": "A curator-authored normalization of a ProofAtlas Top 500 v16 target; source status is retained only as published and is not independently verified.",
            "statement_completeness": "full_statement",
            "source_url": url,
            "status": raw_status,
            "attribution": {
                "source_name": "ProofAtlas",
                "source_catalog": "Top 500 v16",
                "catalog_url": "https://www.proofatlas.ai/data/open-problems/top500-v16-science-v1.json",
                "source_problem_id": identifier,
                "source_release_rank": rank,
                "formal_statement_source_url": url,
            },
            "curation": {
                "identity_review": "materially distinct target accepted for guarded append",
                "draft_schema": draft_schema,
                "draft_attestation": attestation,
                "source_status_independently_verified": False,
            },
        },
    }


def make_collaboration_entry(
    *, snapshot: dict[str, Any], workspace: dict[str, Any], statement: str, draft_schema: str, attestation: str
) -> dict[str, Any]:
    slug = require_string(workspace.get("slug"), "collaboration source ID")
    ordinal = workspace.get("ordinal")
    if not isinstance(ordinal, int):
        raise AssemblyError(f"collaboration {slug}: missing ordinal")
    source_id = f"collaboration--{slug}"
    if not SOURCE_ID.fullmatch(source_id):
        raise AssemblyError(f"collaboration {slug}: generated source ID is invalid")
    return {
        "schema": CURATED_SCHEMA,
        "snapshot": snapshot,
        "problem": {
            "namespace_number": COLLABORATION_NAMESPACE_OFFSET + ordinal,
            "source_id": source_id,
            "title": workspace["title"],
            "statement": statement,
            "background": "A curator-authored normalization of a ProofAtlas collaboration-directory target; source status is retained only as published and is not independently verified.",
            "statement_completeness": "full_statement",
            "source_url": workspace["route"],
            "status": workspace["status"],
            "attribution": {
                "source_name": "ProofAtlas",
                "source_catalog": "Collaboration directory",
                "catalog_url": "https://www.proofatlas.ai/collaboration/",
                "source_workspace_slug": slug,
                "source_workspace_ordinal": ordinal,
                "workspace_url": workspace["route"],
            },
            "curation": {
                "identity_review": "materially distinct target accepted for guarded append",
                "draft_schema": draft_schema,
                "draft_attestation": attestation,
                "source_status_independently_verified": False,
            },
        },
    }


def assemble(
    *,
    top500_snapshot_path: Path,
    top500_drafts: Iterable[Path],
    top500_replacements: Iterable[Path] = (),
    collaboration_snapshot_path: Path | None,
    collaboration_drafts: Iterable[Path],
    collaboration_replacements: Iterable[Path] = (),
    output: Path,
    manifest: Path,
    approve: bool,
) -> dict[str, Any]:
    if not approve:
        raise AssemblyError("refusing to assemble a release input without explicit --approve")
    if output.resolve() == manifest.resolve():
        raise AssemblyError("output and manifest paths must differ")
    top500, top500_metadata = load_top500(top500_snapshot_path)
    collaboration_draft_paths = list(collaboration_drafts)
    collaboration: dict[str, dict[str, Any]] | None = None
    collaboration_metadata: dict[str, Any] | None = None
    if collaboration_draft_paths:
        if collaboration_snapshot_path is None:
            raise AssemblyError("collaboration drafts require --collaboration-snapshot")
        collaboration, collaboration_metadata = load_collaboration(collaboration_snapshot_path)
    elif collaboration_snapshot_path is not None:
        # Including the snapshot hash in the final record is harmless even
        # when this particular review produced no curated workspace addition.
        # Retain the parsed cards as well: every selected Top 500 normalization
        # must be checked against every frozen ProofAtlas card, not merely its
        # own Top 500 target/question.
        collaboration, collaboration_metadata = load_collaboration(collaboration_snapshot_path)

    snapshot = build_snapshot(top500_metadata, collaboration_metadata)
    frozen_source_prose = all_frozen_upstream_prose(top500, collaboration)
    entries: list[dict[str, Any]] = []
    top500_inputs: dict[str, tuple[int, str, str, str, str, str]] = {}
    top500_ranks: set[int] = set()
    for path in top500_drafts:
        for row in read_jsonl(path):
            identifier, rank, status, url, statement, attestation, draft_schema = top500_draft_fields(row, path)
            source = top500.get(identifier)
            if source is None:
                raise AssemblyError(f"{path}: unknown frozen Top 500 ID {identifier!r}")
            if identifier in top500_inputs or rank in top500_ranks:
                raise AssemblyError(f"{path}: duplicate Top 500 curated selection {identifier!r}/{rank}")
            if source.get("releaseRank") != rank:
                raise AssemblyError(f"{path}: release rank differs from frozen Top 500 for {identifier}")
            top500_inputs[identifier] = (rank, status, url, statement, attestation, draft_schema)
            top500_ranks.add(rank)

    for path in top500_replacements:
        for row in read_jsonl(path):
            identifier, rank, status, url, statement, attestation, draft_schema = top500_draft_fields(row, path)
            existing = top500_inputs.get(identifier)
            if existing is None:
                raise AssemblyError(f"{path}: replacement is not selected by a base Top 500 draft: {identifier!r}")
            if existing[:3] != (rank, status, url):
                raise AssemblyError(f"{path}: replacement changes frozen Top 500 identity metadata for {identifier}")
            top500_inputs[identifier] = (rank, status, url, statement, attestation, draft_schema)

    for identifier, (rank, status, url, statement, attestation, draft_schema) in sorted(
        top500_inputs.items(), key=lambda item: item[1][0]
    ):
        source = top500[identifier]
        reject_copied_upstream_prose(
            statement,
            frozen_source_prose,
            f"Top 500 {identifier}",
        )
        entries.append(make_top500_entry(snapshot=snapshot, source=source, status=status, url=url, statement=statement, draft_schema=draft_schema, attestation=attestation))

    collaboration_inputs: dict[str, tuple[str, str, str, str, str]] = {}
    if collaboration is not None:
        for path in collaboration_draft_paths:
            for row in read_jsonl(path):
                slug, route, status, statement, attestation, draft_schema = collaboration_draft_fields(row, path)
                workspace = collaboration.get(slug)
                if workspace is None:
                    raise AssemblyError(f"{path}: unknown frozen collaboration workspace {slug!r}")
                if slug in collaboration_inputs:
                    raise AssemblyError(f"{path}: duplicate collaboration curated selection {slug!r}")
                if workspace["route"] != route or workspace["status"] != status:
                    raise AssemblyError(f"{path}: route/status differs from frozen collaboration source for {slug}")
                collaboration_inputs[slug] = (route, status, statement, attestation, draft_schema)

        for path in collaboration_replacements:
            for row in read_jsonl(path):
                slug, route, status, statement, attestation, draft_schema = collaboration_draft_fields(row, path)
                existing = collaboration_inputs.get(slug)
                if existing is None:
                    raise AssemblyError(f"{path}: replacement is not selected by a base collaboration draft: {slug!r}")
                if existing[:2] != (route, status):
                    raise AssemblyError(f"{path}: replacement changes frozen collaboration identity metadata for {slug}")
                collaboration_inputs[slug] = (route, status, statement, attestation, draft_schema)

        for slug, (_route, _status, statement, attestation, draft_schema) in sorted(
            collaboration_inputs.items(), key=lambda item: int(collaboration[item[0]]["ordinal"])
        ):
            workspace = collaboration[slug]
            reject_copied_upstream_prose(
                statement,
                frozen_source_prose,
                f"collaboration {slug}",
            )
            entries.append(make_collaboration_entry(snapshot=snapshot, workspace=workspace, statement=statement, draft_schema=draft_schema, attestation=attestation))

    if not entries:
        raise AssemblyError("no reviewed curator drafts were supplied")
    entries.sort(key=lambda entry: int(entry["problem"]["namespace_number"]))
    ids = [entry["problem"]["namespace_number"] for entry in entries]
    source_ids = [entry["problem"]["source_id"] for entry in entries]
    if len(ids) != len(set(ids)) or len(source_ids) != len(set(source_ids)):
        raise AssemblyError("assembled entries contain a duplicate namespace number or source ID")
    body = b"".join(canonical_json(entry) + b"\n" for entry in entries)
    atomic_write(output, body)
    digest = sha256_bytes(body).upper()
    result = {
        "schema": MANIFEST_SCHEMA,
        "status": "complete",
        "source_file": output.name,
        "sha256": digest,
        "record_count": len(entries),
        "snapshot_id": snapshot["snapshot_id"],
        "approval": {
            "mode": "repository-maintainer-authorized automated release assembly",
            "scope": "identity-reviewed, curator-authored independent normalizations only",
            "not_claimed": ["independent current-open verification", "rights adjudication beyond curator attestation", "difficulty recalibration"],
        },
        "sources": snapshot["frozen_source_artifacts"],
        "results": {
            "top500_entries": len(top500_inputs),
            "collaboration_entries": len(collaboration_inputs),
            "namespace_minimum": min(ids),
            "namespace_maximum": max(ids),
        },
    }
    atomic_write(manifest, canonical_json(result) + b"\n")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top500-snapshot", type=Path, required=True, help="ignored frozen Top 500 v16 JSON snapshot")
    parser.add_argument("--top500-draft", type=Path, action="append", required=True, help="review-gated Top 500 normalization draft; repeatable")
    parser.add_argument("--top500-replacement-draft", type=Path, action="append", default=[], help="independent-normalization rewrite for a selected Top 500 draft; repeatable")
    parser.add_argument("--collaboration-snapshot", type=Path, help="ignored frozen collaboration directory HTML snapshot")
    parser.add_argument("--collaboration-draft", type=Path, action="append", default=[], help="review-gated collaboration normalization draft; repeatable")
    parser.add_argument("--collaboration-replacement-draft", type=Path, action="append", default=[], help="independent-normalization rewrite for a selected collaboration draft; repeatable")
    parser.add_argument("--output", type=Path, required=True, help="final curated JSONL input for build_proofatlas_append_v1_8.py")
    parser.add_argument("--manifest", type=Path, required=True, help="complete curator-input manifest")
    parser.add_argument("--approve", action="store_true", help="explicitly approve assembly into a v1.8 candidate input")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = assemble(
            top500_snapshot_path=args.top500_snapshot,
            top500_drafts=args.top500_draft,
            top500_replacements=args.top500_replacement_draft,
            collaboration_snapshot_path=args.collaboration_snapshot,
            collaboration_drafts=args.collaboration_draft,
            collaboration_replacements=args.collaboration_replacement_draft,
            output=args.output,
            manifest=args.manifest,
            approve=args.approve,
        )
    except AssemblyError as exc:
        print(f"error: {exc}")
        return 2
    print(json.dumps(result["results"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
