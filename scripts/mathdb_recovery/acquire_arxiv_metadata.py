#!/usr/bin/env python3
"""Acquire arXiv *metadata* for a frozen OPDP recovery source manifest.

This is deliberately a narrow pre-extraction stage.  It uses only the
official legacy API endpoint
``https://export.arxiv.org/api/query?id_list=...&max_results=...`` and never asks for an
e-print, PDF, TeX source, HTML rendering, or full-text archive.  The default
mode is a no-network dry run.  A real request requires ``--allow-network``;
running every source additionally requires ``--allow-full-run``.

The run directory contains immutable per-batch response envelopes.  Each
envelope preserves the exact Atom response (base64 encoded so its bytes and
digest are stable), request URL, parsed metadata, and response validation.
Failures are appended to an error ledger.  A later S3 source-extraction stage
can use the canonical identifier/version/license hints here, but it must still
perform its own S3-manifest, rights, and passage-location checks.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable, Iterator


TOOL_NAME = "OPDP arXiv metadata acquisition"
TOOL_VERSION = "0.1.0"
SOURCE_MANIFEST_SCHEMA = "opdp.mathdb.arxiv-source-manifest.v1"
SOURCE_ROW_SCHEMA = "opdp.mathdb.arxiv-source.v1"
PLAN_SCHEMA = "opdp.mathdb.arxiv-api-metadata-plan.v1"
RUN_SCHEMA = "opdp.mathdb.arxiv-api-metadata-run.v1"
RESPONSE_SCHEMA = "opdp.mathdb.arxiv-api-metadata-response.v1"
ERROR_SCHEMA = "opdp.mathdb.arxiv-api-metadata-error.v1"
INDEX_SCHEMA = "opdp.mathdb.arxiv-api-metadata-index-row.v1"
INDEX_MANIFEST_SCHEMA = "opdp.mathdb.arxiv-api-metadata-index-manifest.v1"

API_ENDPOINT = "https://export.arxiv.org/api/query"
API_HOST = "export.arxiv.org"
API_PATH = "/api/query"
MAX_BATCH_SIZE = 50
DEFAULT_BATCH_SIZE = 50
DEFAULT_MIN_INTERVAL_SECONDS = 3.0
DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_RETRIES = 2

ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"
OPENSEARCH_NS = "{http://a9.com/-/spec/opensearch/1.1/}"
MODERN_ID = re.compile(r"^\d{4}\.\d{4,5}$")
LEGACY_ID = re.compile(r"^[a-z-]+/\d{7}$")
VERSION_SUFFIX = re.compile(r"^(?P<identifier>.+?)v(?P<version>[1-9]\d*)$", re.IGNORECASE)
WITHDRAWN_MARKER = re.compile(r"\bwithdrawn\b", re.IGNORECASE)


class AcquisitionError(RuntimeError):
    """A source-manifest, output-integrity, or API acquisition failure."""


class ResponseValidationError(AcquisitionError):
    """An API response that cannot safely be retained as completed metadata."""


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def write_immutable_bytes(path: Path, value: bytes, label: str) -> bool:
    """Write once, or verify exact byte equality during a resume."""

    if path.exists():
        observed = path.read_bytes()
        if observed != value:
            raise AcquisitionError(
                f"existing immutable {label} differs: {path}. "
                "Choose a new output directory instead of mutating this run."
            )
        return False
    atomic_write_bytes(path, value)
    return True


def write_immutable_json(path: Path, value: dict[str, Any], label: str) -> tuple[str, bool]:
    encoded = canonical_json_bytes(value) + b"\n"
    return sha256_bytes(encoded), write_immutable_bytes(path, encoded, label)


def write_replaceable_json(path: Path, value: dict[str, Any]) -> tuple[str, int]:
    encoded = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    raw = encoded.encode("utf-8")
    atomic_write_bytes(path, raw)
    return sha256_bytes(raw), len(raw)


def write_replaceable_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> tuple[str, int, int]:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=path.parent)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    byte_count = 0
    row_count = 0
    try:
        with os.fdopen(descriptor, "wb") as handle:
            for row in rows:
                raw = canonical_json_bytes(row) + b"\n"
                handle.write(raw)
                digest.update(raw)
                byte_count += len(raw)
                row_count += 1
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
    return digest.hexdigest(), byte_count, row_count


def read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcquisitionError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise AcquisitionError(f"{label} must contain a JSON object: {path}")
    return value


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def require_safe_relative_path(parent: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise AcquisitionError(f"{label} is not a safe relative path")
    candidate = (parent / relative).resolve()
    if not is_within(candidate, parent):
        raise AcquisitionError(f"{label} escapes its manifest directory")
    return candidate


def normalize_arxiv_identifier(value: Any) -> tuple[str, str | None] | None:
    """Return a versionless arXiv identifier and optional vN suffix."""

    if not isinstance(value, str):
        return None
    identifier = value.strip().lower()
    if not identifier:
        return None
    parsed = urllib.parse.urlparse(identifier)
    if parsed.scheme in {"http", "https"}:
        host = (parsed.hostname or "").lower()
        parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
        if host not in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"} or len(parts) < 2:
            return None
        if parts[0].lower() != "abs":
            return None
        if re.fullmatch(r"[a-z-]+", parts[1], flags=re.IGNORECASE) and len(parts) >= 3:
            identifier = f"{parts[1]}/{parts[2]}"
        else:
            identifier = parts[1]
    identifier = identifier.removeprefix("arxiv:").removesuffix(".pdf")
    version: str | None = None
    version_match = VERSION_SUFFIX.fullmatch(identifier)
    if version_match:
        identifier = version_match.group("identifier")
        version = f"v{version_match.group('version')}"
    if not (MODERN_ID.fullmatch(identifier) or LEGACY_ID.fullmatch(identifier)):
        return None
    return identifier, version


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized or None


def element_text(parent: ET.Element, tag: str) -> str | None:
    child = parent.find(tag)
    if child is None:
        return None
    return normalize_text("".join(child.itertext()))


def parse_atom_response(raw: bytes, manifest_versions: dict[str, list[str]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse a small, trusted Atom API response without interpreting source text."""

    # ElementTree does not perform network fetches for ordinary feeds, but
    # rejecting declarations prevents an unexpected DTD/entity payload from
    # becoming part of a long-lived audit artifact.
    markup = raw.upper()
    if b"<!DOCTYPE" in markup or b"<!ENTITY" in markup:
        raise ResponseValidationError("Atom response contains a forbidden DTD/entity declaration")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ResponseValidationError(f"cannot parse Atom XML: {exc}") from exc
    if root.tag != f"{ATOM_NS}feed":
        raise ResponseValidationError("Atom response root is not an Atom feed")

    entries: list[dict[str, Any]] = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        returned_id_url = element_text(entry, f"{ATOM_NS}id")
        normalized = normalize_arxiv_identifier(returned_id_url)
        if normalized is None:
            raise ResponseValidationError(f"Atom entry has an unparseable arXiv id: {returned_id_url!r}")
        arxiv_id, returned_version = normalized
        categories = []
        for category in entry.findall(f"{ATOM_NS}category"):
            term = category.attrib.get("term")
            if isinstance(term, str) and term:
                categories.append(term)
        primary = entry.find(f"{ARXIV_NS}primary_category")
        primary_category = primary.attrib.get("term") if primary is not None else None
        authors = []
        for author in entry.findall(f"{ATOM_NS}author"):
            name = element_text(author, f"{ATOM_NS}name")
            if name is not None:
                authors.append(name)
        links: list[dict[str, str]] = []
        license_urls: list[str] = []
        for link in entry.findall(f"{ATOM_NS}link"):
            href = link.attrib.get("href")
            rel = link.attrib.get("rel")
            title = link.attrib.get("title")
            media_type = link.attrib.get("type")
            link_record = {
                key: item
                for key, item in {
                    "href": href,
                    "rel": rel,
                    "title": title,
                    "type": media_type,
                }.items()
                if isinstance(item, str) and item
            }
            if link_record:
                links.append(link_record)
            if isinstance(href, str) and (
                (isinstance(rel, str) and rel.lower() == "license")
                or (isinstance(title, str) and "license" in title.lower())
            ):
                license_urls.append(href)
        direct_license = element_text(entry, f"{ARXIV_NS}license")
        if direct_license is not None:
            license_urls.append(direct_license)
        # Preserve first-observed ordering while eliminating duplicate URLs.
        deduped_license_urls = list(dict.fromkeys(license_urls))
        title = element_text(entry, f"{ATOM_NS}title")
        summary = element_text(entry, f"{ATOM_NS}summary")
        comment = element_text(entry, f"{ARXIV_NS}comment")
        withdrawal_evidence = []
        for field_name, field_value in (("title", title), ("comment", comment), ("summary", summary)):
            if field_value and WITHDRAWN_MARKER.search(field_value):
                withdrawal_evidence.append(field_name)
        entries.append(
            {
                "schema": "opdp.mathdb.arxiv-api-metadata-record.v1",
                "arxiv_id": arxiv_id,
                "canonical_url": f"https://arxiv.org/abs/{arxiv_id}",
                "api_returned_id_url": returned_id_url,
                "title": title,
                "abstract": summary,
                "authors": authors,
                "published_at": element_text(entry, f"{ATOM_NS}published"),
                "updated_at": element_text(entry, f"{ATOM_NS}updated"),
                "categories": categories,
                "primary_category": primary_category,
                "comment": comment,
                "journal_ref": element_text(entry, f"{ARXIV_NS}journal_ref"),
                "doi": element_text(entry, f"{ARXIV_NS}doi"),
                "report_no": element_text(entry, f"{ARXIV_NS}report-no"),
                "links": links,
                "version_info": {
                    "api_returned_version": returned_version,
                    "source_manifest_observed_versions": manifest_versions.get(arxiv_id, []),
                    "version_history_available_in_api_response": False,
                    "note": "The legacy API response identifies the returned version but does not provide a complete version history.",
                },
                "license_info": {
                    "license_urls": deduped_license_urls,
                    "status": "provided_by_api_response" if deduped_license_urls else "not_provided_by_api_response",
                    "note": "Absence from this API response is not a license determination; use an authorized source/metadata channel before publication.",
                },
                "withdrawal_info": {
                    "status": "possible_withdrawal_indicator" if withdrawal_evidence else "not_indicated_by_api_response",
                    "evidence_fields": withdrawal_evidence,
                    "note": "This is a text-marker heuristic, not a current-status or withdrawal determination.",
                },
            }
        )
    feed_info = {
        "feed_id": element_text(root, f"{ATOM_NS}id"),
        "feed_updated_at": element_text(root, f"{ATOM_NS}updated"),
        "opensearch_total_results": element_text(root, f"{OPENSEARCH_NS}totalResults"),
        "opensearch_start_index": element_text(root, f"{OPENSEARCH_NS}startIndex"),
        "opensearch_items_per_page": element_text(root, f"{OPENSEARCH_NS}itemsPerPage"),
    }
    return entries, feed_info


def validate_response_entries(entries: list[dict[str, Any]], requested_ids: list[str]) -> dict[str, Any]:
    returned_ids = [entry["arxiv_id"] for entry in entries]
    duplicate_returned = sorted({identifier for identifier in returned_ids if returned_ids.count(identifier) > 1})
    requested_set = set(requested_ids)
    returned_set = set(returned_ids)
    missing = [identifier for identifier in requested_ids if identifier not in returned_set]
    unexpected = sorted(returned_set - requested_set)
    if duplicate_returned or missing or unexpected or len(returned_ids) != len(requested_ids):
        raise ResponseValidationError(
            "Atom response does not have exactly one metadata entry for every requested id: "
            f"missing={missing[:10]!r}, unexpected={unexpected[:10]!r}, duplicates={duplicate_returned[:10]!r}"
        )
    return {
        "requested_ids": requested_ids,
        "returned_ids": returned_ids,
        "missing_requested_ids": [],
        "unexpected_returned_ids": [],
        "duplicate_returned_ids": [],
        "status": "complete_exact_id_match",
    }


def build_request_url(arxiv_ids: list[str]) -> str:
    if not 1 <= len(arxiv_ids) <= MAX_BATCH_SIZE:
        raise AcquisitionError(
            f"an API request must contain between 1 and {MAX_BATCH_SIZE} identifiers"
        )
    # arXiv's legacy API defaults to ten results.  Asking for the exact batch
    # cardinality is therefore necessary to make a 50-ID id_list response
    # complete and auditable rather than silently truncating at ten entries.
    query = urllib.parse.urlencode(
        [("id_list", ",".join(arxiv_ids)), ("max_results", str(len(arxiv_ids)))],
        quote_via=urllib.parse.quote,
        safe=",/",
    )
    return f"{API_ENDPOINT}?{query}"


def batch_key(batch_index: int, arxiv_ids: list[str], request_url: str, observed_versions: dict[str, list[str]]) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {
                "index": batch_index,
                "arxiv_ids": arxiv_ids,
                "request_url": request_url,
                "observed_versions": observed_versions,
            }
        )
    )


def response_filename(batch: dict[str, Any]) -> str:
    return f"batch-{batch['index']:05d}-{batch['batch_key'][:16]}.json"


def make_plan(
    source_manifest_path: Path,
    source_manifest_sha256: str,
    source_rows_sha256: str,
    selected_rows: list[dict[str, Any]],
    batch_size: int,
    selection_kind: str,
) -> dict[str, Any]:
    selected_ids = [row["arxiv_id"] for row in selected_rows]
    batches = []
    for start in range(0, len(selected_rows), batch_size):
        rows = selected_rows[start : start + batch_size]
        ids = [row["arxiv_id"] for row in rows]
        observed_versions = {row["arxiv_id"]: row["observed_versions"] for row in rows}
        request_url = build_request_url(ids)
        index = len(batches) + 1
        key = batch_key(index, ids, request_url, observed_versions)
        batches.append(
            {
                "index": index,
                "batch_key": key,
                "arxiv_ids": ids,
                "observed_versions": observed_versions,
                "request_url": request_url,
                "response_file": f"responses/{response_filename({'index': index, 'batch_key': key})}",
            }
        )
    return {
        "schema": PLAN_SCHEMA,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "source": {
            "source_manifest_filename": source_manifest_path.name,
            "source_manifest_sha256": source_manifest_sha256,
            "arxiv_sources_sha256": source_rows_sha256,
        },
        "api": {
            "endpoint": API_ENDPOINT,
            "request_shape": "GET /api/query?id_list=<comma-separated canonical IDs>&max_results=<exact batch size>",
            "maximum_ids_per_request": MAX_BATCH_SIZE,
            "single_connection_policy": True,
            "minimum_inter_request_seconds": DEFAULT_MIN_INTERVAL_SECONDS,
            "full_text_requested": False,
        },
        "selection": {
            "kind": selection_kind,
            "source_count": len(selected_rows),
            "arxiv_ids_sha256": sha256_bytes(canonical_json_bytes(selected_ids)),
            "scope": "complete" if selection_kind.endswith("_complete") or selection_kind == "all_manifest_sources" else "sample",
        },
        "batches": batches,
    }


def read_source_rows(source_manifest_path: Path) -> tuple[dict[str, Any], str, str, list[dict[str, Any]]]:
    source_manifest_path = source_manifest_path.resolve()
    if not source_manifest_path.is_file():
        raise AcquisitionError(f"source manifest does not exist: {source_manifest_path}")
    manifest = read_json_object(source_manifest_path, "source manifest")
    if manifest.get("schema") != SOURCE_MANIFEST_SCHEMA:
        raise AcquisitionError(f"unexpected source manifest schema: {manifest.get('schema')!r}")
    outputs = manifest.get("outputs")
    if not isinstance(outputs, dict) or not isinstance(outputs.get("arxiv_sources"), dict):
        raise AcquisitionError("source manifest lacks outputs.arxiv_sources")
    source_info = outputs["arxiv_sources"]
    source_path = require_safe_relative_path(source_manifest_path.parent, source_info.get("path"), "arxiv_sources path")
    if not source_path.is_file():
        raise AcquisitionError(f"arxiv source rows file does not exist: {source_path}")
    expected_sha = source_info.get("sha256")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise AcquisitionError("source manifest has an invalid arxiv_sources SHA-256")
    observed_sha = sha256_file(source_path)
    if observed_sha != expected_sha:
        raise AcquisitionError(
            f"arxiv source rows hash mismatch: expected {expected_sha}, observed {observed_sha}"
        )
    expected_count = source_info.get("count")
    if not isinstance(expected_count, int) or expected_count < 1:
        raise AcquisitionError("source manifest has an invalid arxiv_sources count")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with source_path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise AcquisitionError(f"{source_path}:{line_number}: invalid JSON") from exc
            if not isinstance(row, dict) or row.get("schema") != SOURCE_ROW_SCHEMA:
                raise AcquisitionError(f"{source_path}:{line_number}: unexpected source row schema")
            normalized = normalize_arxiv_identifier(row.get("arxiv_id"))
            if normalized is None or normalized[1] is not None:
                raise AcquisitionError(f"{source_path}:{line_number}: invalid versionless arXiv id")
            arxiv_id = normalized[0]
            if arxiv_id in seen:
                raise AcquisitionError(f"{source_path}:{line_number}: duplicate arXiv id {arxiv_id}")
            seen.add(arxiv_id)
            raw_versions = row.get("observed_versions", [])
            if not isinstance(raw_versions, list) or any(
                not isinstance(version, str) or not re.fullmatch(r"v[1-9]\d*", version) for version in raw_versions
            ):
                raise AcquisitionError(f"{source_path}:{line_number}: invalid observed_versions")
            rows.append({"arxiv_id": arxiv_id, "observed_versions": raw_versions})
    if len(rows) != expected_count:
        raise AcquisitionError(
            f"arxiv source row count mismatch: expected {expected_count:,}, read {len(rows):,}"
        )
    summary = manifest.get("summary")
    if isinstance(summary, dict) and summary.get("unique_arxiv_sources") != len(rows):
        raise AcquisitionError("source manifest summary unique_arxiv_sources does not match source rows")
    return manifest, sha256_file(source_manifest_path), observed_sha, rows


def select_rows(rows: list[dict[str, Any]], args: argparse.Namespace) -> tuple[list[dict[str, Any]], str]:
    requested = args.arxiv_id or []
    if requested and args.max_ids is not None:
        raise AcquisitionError("--arxiv-id and --max-ids cannot be used together")
    if args.max_ids is not None and args.max_ids < 1:
        raise AcquisitionError("--max-ids must be positive")
    if requested:
        wanted: set[str] = set()
        for raw in requested:
            for candidate in raw.split(","):
                normalized = normalize_arxiv_identifier(candidate)
                if normalized is None:
                    raise AcquisitionError(f"--arxiv-id is invalid: {candidate!r}")
                wanted.add(normalized[0])
        source_map = {row["arxiv_id"]: row for row in rows}
        missing = sorted(wanted - source_map.keys())
        if missing:
            raise AcquisitionError(f"requested arXiv ids are absent from the frozen source manifest: {missing[:10]!r}")
        selected = [row for row in rows if row["arxiv_id"] in wanted]
        return selected, "explicit_ids_complete" if len(selected) == len(rows) else "explicit_ids"
    if args.max_ids is not None:
        selected = rows[: args.max_ids]
        return selected, "manifest_prefix_complete" if len(selected) == len(rows) else "manifest_prefix"
    return rows, "all_manifest_sources"


class RunLock:
    """A simple single-process lock; stale locks are never broken automatically."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.token = str(uuid.uuid4())

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise AcquisitionError(
                f"another acquisition process (or a stale lock) owns {self.path}; "
                "do not delete it unless you have verified that no process is running"
            ) from exc
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "started_at_utc": utc_now(), "token": self.token}) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            lock = read_json_object(self.path, "acquisition lock")
            if lock.get("token") == self.token:
                self.path.unlink()
        except (OSError, AcquisitionError):
            # A lock that cannot be safely identified is deliberately retained.
            pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Reject redirects so every network request stays on the declared API URL."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        fp: Any,
        code: int,
        message: str,
        headers: Any,
        newurl: str,
    ) -> None:
        raise urllib.error.HTTPError(request.full_url, code, f"redirect rejected: {newurl}", headers, fp)


class RateLimiter:
    def __init__(self, minimum_interval_seconds: float, prior_request_started_at: dt.datetime | None) -> None:
        self.minimum_interval_seconds = minimum_interval_seconds
        self.last_request_started_at = prior_request_started_at

    def before_request(self) -> str:
        if self.last_request_started_at is not None:
            elapsed = (dt.datetime.now(dt.timezone.utc) - self.last_request_started_at).total_seconds()
            # A backwards clock is treated as no elapsed time rather than as
            # permission to send an early request.
            remaining = self.minimum_interval_seconds - max(0.0, elapsed)
            if remaining > 0:
                time.sleep(remaining)
        started = dt.datetime.now(dt.timezone.utc)
        self.last_request_started_at = started
        return started.isoformat(timespec="seconds").replace("+00:00", "Z")


def restricted_headers(headers: Any) -> dict[str, str]:
    output: dict[str, str] = {}
    for name in ("Content-Type", "Content-Length", "ETag", "Last-Modified"):
        value = headers.get(name) if headers is not None else None
        if isinstance(value, str) and value:
            output[name.lower()] = value
    return output


def read_limited(handle: Any, maximum_bytes: int) -> bytes:
    chunks: list[bytes] = []
    byte_count = 0
    while True:
        chunk = handle.read(min(1024 * 1024, maximum_bytes + 1))
        if not chunk:
            break
        byte_count += len(chunk)
        if byte_count > maximum_bytes:
            raise ResponseValidationError(
                f"API response exceeds configured maximum of {maximum_bytes:,} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def check_final_url(value: str) -> None:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != API_HOST or parsed.path != API_PATH:
        raise ResponseValidationError(f"API response final URL is outside the approved endpoint: {value!r}")


def api_fetch(
    opener: urllib.request.OpenerDirector,
    request_url: str,
    timeout_seconds: float,
    maximum_bytes: int,
) -> tuple[int, str, dict[str, str], bytes]:
    request = urllib.request.Request(
        request_url,
        method="GET",
        headers={
            "Accept": "application/atom+xml, application/xml;q=0.9",
            "User-Agent": "OPDP-MathDB-metadata-recovery/0.1 (single-worker; no-full-text)",
            # Deliberately avoid a pool or a persistent keep-alive connection.
            "Connection": "close",
        },
    )
    with opener.open(request, timeout=timeout_seconds) as response:
        status = response.getcode()
        final_url = response.geturl()
        check_final_url(final_url)
        if status != 200:
            raise ResponseValidationError(f"API returned unexpected HTTP status {status}")
        body = read_limited(response, maximum_bytes)
        return status, final_url, restricted_headers(response.headers), body


def append_error(path: Path, event: dict[str, Any]) -> None:
    encoded = canonical_json_bytes(event) + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def make_error_event(
    batch: dict[str, Any],
    attempt: int,
    request_started_at_utc: str | None,
    phase: str,
    exc: BaseException,
    http_status: int | None = None,
) -> dict[str, Any]:
    return {
        "schema": ERROR_SCHEMA,
        "event_id": str(uuid.uuid4()),
        "occurred_at_utc": utc_now(),
        "phase": phase,
        "batch_index": batch["index"],
        "batch_key": batch["batch_key"],
        "attempt": attempt,
        "request_url": batch["request_url"],
        "arxiv_ids": batch["arxiv_ids"],
        "request_started_at_utc": request_started_at_utc,
        "error": {
            "class": type(exc).__name__,
            "message": str(exc)[:2000],
            "http_status": http_status,
        },
    }


def load_response_record(path: Path, batch: dict[str, Any]) -> dict[str, Any]:
    record = read_json_object(path, "immutable API response")
    if record.get("schema") != RESPONSE_SCHEMA:
        raise AcquisitionError(f"response file has unexpected schema: {path}")
    record_batch = record.get("batch")
    if not isinstance(record_batch, dict):
        raise AcquisitionError(f"response file lacks batch object: {path}")
    for key in ("index", "batch_key", "request_url", "arxiv_ids"):
        if record_batch.get(key) != batch.get(key):
            raise AcquisitionError(f"response file batch binding differs for {key}: {path}")
    response = record.get("response")
    parsed = record.get("parsed_metadata")
    if not isinstance(response, dict) or not isinstance(parsed, dict):
        raise AcquisitionError(f"response file lacks response or parsed_metadata: {path}")
    encoded_body = response.get("atom_xml_base64")
    if not isinstance(encoded_body, str):
        raise AcquisitionError(f"response file has no encoded Atom body: {path}")
    try:
        body = base64.b64decode(encoded_body.encode("ascii"), validate=True)
    except (ValueError, UnicodeError) as exc:
        raise AcquisitionError(f"response file has invalid base64 Atom body: {path}") from exc
    if sha256_bytes(body) != response.get("body_sha256") or len(body) != response.get("body_bytes"):
        raise AcquisitionError(f"response file Atom body hash/length mismatch: {path}")
    entries = parsed.get("entries")
    if not isinstance(entries, list):
        raise AcquisitionError(f"response file parsed metadata entries are invalid: {path}")
    if sha256_bytes(canonical_json_bytes(entries)) != parsed.get("entries_sha256"):
        raise AcquisitionError(f"response file parsed metadata hash mismatch: {path}")
    try:
        validate_response_entries(entries, batch["arxiv_ids"])
    except ResponseValidationError as exc:
        raise AcquisitionError(f"response file entry validation fails: {path}: {exc}") from exc
    return record


def result_response_path(output_dir: Path, batch: dict[str, Any]) -> Path:
    relative = batch.get("response_file")
    return require_safe_relative_path(output_dir, relative, "batch response file")


def make_response_record(
    batch: dict[str, Any],
    request_started_at_utc: str,
    status: int,
    final_url: str,
    headers: dict[str, str],
    body: bytes,
) -> dict[str, Any]:
    entries, feed_info = parse_atom_response(body, batch["observed_versions"])
    validation = validate_response_entries(entries, batch["arxiv_ids"])
    return {
        "schema": RESPONSE_SCHEMA,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "batch": {
            "index": batch["index"],
            "batch_key": batch["batch_key"],
            "arxiv_ids": batch["arxiv_ids"],
            "request_url": batch["request_url"],
        },
        "request": {
            "method": "GET",
            "request_started_at_utc": request_started_at_utc,
            "connection_policy": "one sequential request at a time; Connection: close",
        },
        "response": {
            "status": status,
            "final_url": final_url,
            "headers": headers,
            "body_bytes": len(body),
            "body_sha256": sha256_bytes(body),
            "atom_xml_base64": base64.b64encode(body).decode("ascii"),
        },
        "parsed_metadata": {
            "normalization": "Atom text fields are whitespace-normalized; exact XML bytes remain in response.atom_xml_base64.",
            "feed": feed_info,
            "entries": entries,
            "entries_sha256": sha256_bytes(canonical_json_bytes(entries)),
        },
        "validation": validation,
        "content_scope": {
            "raw_full_text_requested": False,
            "contains_only_legacy_api_metadata_response": True,
        },
    }


def iter_response_records(output_dir: Path, plan: dict[str, Any]) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    for batch in plan["batches"]:
        path = result_response_path(output_dir, batch)
        if path.exists():
            yield batch, load_response_record(path, batch)


def latest_prior_request_time(output_dir: Path, plan: dict[str, Any]) -> dt.datetime | None:
    timestamps: list[dt.datetime] = []
    for _, record in iter_response_records(output_dir, plan):
        timestamp = parse_utc(record.get("request", {}).get("request_started_at_utc"))
        if timestamp is not None:
            timestamps.append(timestamp)
    error_path = output_dir / "error_ledger.jsonl"
    if error_path.is_file():
        with error_path.open("r", encoding="utf-8", newline="") as handle:
            for raw_line in handle:
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError:
                    # An immutable error ledger must not be silently repaired.
                    raise AcquisitionError(f"invalid JSON in error ledger: {error_path}")
                if isinstance(event, dict):
                    timestamp = parse_utc(event.get("request_started_at_utc"))
                    if timestamp is not None:
                        timestamps.append(timestamp)
    return max(timestamps) if timestamps else None


def existing_error_attempts(error_path: Path) -> dict[str, int]:
    attempts: dict[str, int] = {}
    if not error_path.is_file():
        return attempts
    with error_path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise AcquisitionError(f"{error_path}:{line_number}: invalid error-ledger JSON") from exc
            if not isinstance(event, dict) or event.get("schema") != ERROR_SCHEMA:
                raise AcquisitionError(f"{error_path}:{line_number}: invalid error-ledger schema")
            key = event.get("batch_key")
            attempt = event.get("attempt")
            if isinstance(key, str) and isinstance(attempt, int):
                attempts[key] = max(attempts.get(key, 0), attempt)
    return attempts


def build_derived_index(output_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    metadata_by_id: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    completed_batches = 0
    for batch, record in iter_response_records(output_dir, plan):
        completed_batches += 1
        for entry in record["parsed_metadata"]["entries"]:
            identifier = entry["arxiv_id"]
            if identifier in metadata_by_id:
                raise AcquisitionError(f"duplicate ID across completed metadata batches: {identifier}")
            metadata_by_id[identifier] = (batch, entry)

    def rows() -> Iterator[dict[str, Any]]:
        for batch in plan["batches"]:
            for identifier in batch["arxiv_ids"]:
                item = metadata_by_id.get(identifier)
                if item is None:
                    continue
                response_batch, metadata = item
                yield {
                    "schema": INDEX_SCHEMA,
                    "arxiv_id": identifier,
                    "metadata": metadata,
                    "source": {
                        "response_file": response_batch["response_file"],
                        "request_url": response_batch["request_url"],
                        "batch_key": response_batch["batch_key"],
                    },
                }

    index_path = output_dir / "derived_metadata_records.jsonl"
    index_sha, index_bytes, index_rows = write_replaceable_jsonl(index_path, rows())
    manifest = {
        "schema": INDEX_MANIFEST_SCHEMA,
        "kind": "derived_replaceable_index",
        "source_plan_sha256": sha256_file(output_dir / "request_plan.json"),
        "index": {
            "path": index_path.name,
            "sha256": index_sha,
            "bytes": index_bytes,
            "metadata_record_count": index_rows,
        },
        "coverage": {
            "requested_source_count": plan["selection"]["source_count"],
            "completed_source_count": index_rows,
            "completed_batch_count": completed_batches,
            "requested_batch_count": len(plan["batches"]),
            "complete": index_rows == plan["selection"]["source_count"],
        },
        "notice": "This is a derived convenience index. Immutable evidence is held in responses/*.json.",
    }
    write_replaceable_json(output_dir / "derived_metadata_index_manifest.json", manifest)
    return manifest


def prepare_output(
    output_dir: Path,
    run_dir: Path,
    source_manifest_path: Path,
    source_manifest_sha256: str,
    source_rows_sha256: str,
    selected_rows: list[dict[str, Any]],
    selection_kind: str,
    batch_size: int,
    resume: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not is_within(output_dir, run_dir):
        raise AcquisitionError("--output-dir must be within --run-dir so acquisition artifacts remain ignored")
    if output_dir.exists() and not resume:
        # The caller takes the single-writer lock immediately before this
        # check, so the lock itself cannot make a new output directory look
        # like a pre-existing run.
        children = [
            child
            for child in output_dir.iterdir()
            if child.name not in {".DS_Store", ".acquisition.lock"}
        ]
        if children:
            raise AcquisitionError("metadata output directory is nonempty; pass --resume or choose a new output directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = make_plan(
        source_manifest_path,
        source_manifest_sha256,
        source_rows_sha256,
        selected_rows,
        batch_size,
        selection_kind,
    )
    plan_sha, _ = write_immutable_json(output_dir / "request_plan.json", plan, "metadata request plan")
    run = {
        "schema": RUN_SCHEMA,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "source": {
            "recovery_run_manifest_sha256": sha256_file(run_dir / "run.json"),
            "source_manifest_filename": source_manifest_path.name,
            "source_manifest_sha256": source_manifest_sha256,
            "arxiv_sources_sha256": source_rows_sha256,
        },
        "plan": {
            "filename": "request_plan.json",
            "sha256": plan_sha,
            "selection_scope": plan["selection"]["scope"],
            "source_count": plan["selection"]["source_count"],
            "batch_count": len(plan["batches"]),
        },
        "safety": {
            "default_mode": "dry_run_no_network",
            "approved_endpoint_only": API_ENDPOINT,
            "maximum_ids_per_request": MAX_BATCH_SIZE,
            "minimum_inter_request_seconds": DEFAULT_MIN_INTERVAL_SECONDS,
            "single_connection_policy": "single sequential worker with Connection: close",
            "raw_full_text_downloaded": False,
            "release_mutation": "prohibited by this tool",
        },
        "storage": {
            "immutable_response_files": "responses/*.json",
            "append_only_error_ledger": "error_ledger.jsonl",
            "replaceable_derived_index": "derived_metadata_records.jsonl",
        },
    }
    write_immutable_json(output_dir / "run.json", run, "metadata acquisition run manifest")
    return plan, run


def make_summary(
    output_dir: Path,
    plan: dict[str, Any],
    network_allowed: bool,
    fetched_this_invocation: int,
    failures_this_invocation: int,
) -> dict[str, Any]:
    completed_batches = 0
    completed_sources = 0
    for batch, _ in iter_response_records(output_dir, plan):
        completed_batches += 1
        completed_sources += len(batch["arxiv_ids"])
    errors = existing_error_attempts(output_dir / "error_ledger.jsonl")
    return {
        "schema": "opdp.mathdb.arxiv-api-metadata-summary.v1",
        "derived_at_utc": utc_now(),
        "network_allowed_this_invocation": network_allowed,
        "coverage": {
            "requested_source_count": plan["selection"]["source_count"],
            "requested_batch_count": len(plan["batches"]),
            "completed_source_count": completed_sources,
            "completed_batch_count": completed_batches,
            "remaining_source_count": plan["selection"]["source_count"] - completed_sources,
            "remaining_batch_count": len(plan["batches"]) - completed_batches,
            "complete": completed_batches == len(plan["batches"]),
        },
        "this_invocation": {
            "newly_fetched_batches": fetched_this_invocation,
            "failed_batches": failures_this_invocation,
            "recorded_failed_batch_attempts_total": sum(errors.values()),
        },
        "next_stage": {
            "allowed": "only_after_a_source-specific S3 bulk manifest, rights, and extraction plan are approved",
            "metadata_role": "canonical identifier/version/license hints and response provenance; not a statement recovery or openness determination",
        },
    }


def acquire(args: argparse.Namespace) -> dict[str, Any]:
    if args.batch_size < 1 or args.batch_size > MAX_BATCH_SIZE:
        raise AcquisitionError(f"--batch-size must be between 1 and {MAX_BATCH_SIZE}")
    if args.min_interval_seconds < DEFAULT_MIN_INTERVAL_SECONDS:
        raise AcquisitionError(
            f"--min-interval-seconds must be at least {DEFAULT_MIN_INTERVAL_SECONDS:g} to comply with the API policy"
        )
    if args.timeout_seconds <= 0:
        raise AcquisitionError("--timeout-seconds must be positive")
    if args.max_response_bytes < 1024:
        raise AcquisitionError("--max-response-bytes must be at least 1024")
    if args.max_retries < 0:
        raise AcquisitionError("--max-retries cannot be negative")
    run_dir = args.run_dir.resolve()
    if not (run_dir / "run.json").is_file():
        raise AcquisitionError(f"missing canonical recovery run manifest: {run_dir / 'run.json'}")
    source_manifest = args.source_manifest.resolve()
    _, source_manifest_sha, source_rows_sha, all_rows = read_source_rows(source_manifest)
    selected_rows, selection_kind = select_rows(all_rows, args)
    if not selected_rows:
        raise AcquisitionError("selection contains no arXiv identifiers")
    if len(selected_rows) == len(all_rows) and args.allow_network and not args.allow_full_run:
        raise AcquisitionError(
            "refusing a full metadata crawl without --allow-full-run in addition to --allow-network"
        )
    output_dir = args.output_dir.resolve() if args.output_dir else run_dir / "arxiv_metadata_api"
    with RunLock(output_dir / ".acquisition.lock"):
        plan, _ = prepare_output(
            output_dir,
            run_dir,
            source_manifest,
            source_manifest_sha,
            source_rows_sha,
            selected_rows,
            selection_kind,
            args.batch_size,
            args.resume,
        )
        # Always materialize a derived empty/partial index in dry-run mode too;
        # it makes the state explicit without touching any canonical record.
        if not args.allow_network:
            index = build_derived_index(output_dir, plan)
            summary = make_summary(output_dir, plan, False, 0, 0)
            write_replaceable_json(output_dir / "summary.json", summary)
            return {
                "status": "dry_run_complete_no_network",
                "run_dir": str(run_dir),
                "output_dir": str(output_dir),
                "selected_source_count": len(selected_rows),
                "planned_batch_count": len(plan["batches"]),
                "request_urls": [batch["request_url"] for batch in plan["batches"][:3]],
                "derived_index_coverage": index["coverage"],
            }

        error_path = output_dir / "error_ledger.jsonl"
        error_attempts = existing_error_attempts(error_path)
        limiter = RateLimiter(args.min_interval_seconds, latest_prior_request_time(output_dir, plan))
        opener = urllib.request.build_opener(NoRedirect())
        fetched = 0
        failed = 0
        for batch in plan["batches"]:
            response_path = result_response_path(output_dir, batch)
            if response_path.exists():
                load_response_record(response_path, batch)
                continue
            attempt_base = error_attempts.get(batch["batch_key"], 0)
            success = False
            for retry_number in range(args.max_retries + 1):
                attempt = attempt_base + retry_number + 1
                started = limiter.before_request()
                try:
                    status, final_url, headers, body = api_fetch(
                        opener,
                        batch["request_url"],
                        args.timeout_seconds,
                        args.max_response_bytes,
                    )
                    response = make_response_record(batch, started, status, final_url, headers, body)
                    write_immutable_json(response_path, response, "API response envelope")
                    fetched += 1
                    success = True
                    log(f"fetched metadata batch {batch['index']}/{len(plan['batches'])} ({len(batch['arxiv_ids'])} ids)")
                    break
                except urllib.error.HTTPError as exc:
                    append_error(
                        error_path,
                        make_error_event(batch, attempt, started, "http", exc, http_status=exc.code),
                    )
                    log(f"metadata batch {batch['index']} attempt {attempt} failed with HTTP {exc.code}: {exc}")
                except (urllib.error.URLError, TimeoutError, OSError, ResponseValidationError, ET.ParseError) as exc:
                    append_error(error_path, make_error_event(batch, attempt, started, "request_or_parse", exc))
                    log(f"metadata batch {batch['index']} attempt {attempt} failed: {exc}")
            if not success:
                failed += 1
        index = build_derived_index(output_dir, plan)
        summary = make_summary(output_dir, plan, True, fetched, failed)
        write_replaceable_json(output_dir / "summary.json", summary)
        return {
            "status": "complete" if summary["coverage"]["complete"] and failed == 0 else "partial_or_failed",
            "run_dir": str(run_dir),
            "output_dir": str(output_dir),
            "selected_source_count": len(selected_rows),
            "planned_batch_count": len(plan["batches"]),
            "newly_fetched_batch_count": fetched,
            "failed_batch_count": failed,
            "coverage": summary["coverage"],
            "derived_index_coverage": index["coverage"],
        }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Safely acquire arXiv Atom metadata for a frozen MathDB arXiv-source manifest. Default: dry run, no network."
    )
    value.add_argument("--run-dir", type=Path, required=True, help="existing MathDB recovery run directory")
    value.add_argument(
        "--source-manifest",
        type=Path,
        required=True,
        help="immutable arxiv_source_manifest.json (typically the -final manifest)",
    )
    value.add_argument(
        "--output-dir",
        type=Path,
        help="default: <run-dir>/arxiv_metadata_api; must remain beneath --run-dir",
    )
    value.add_argument("--resume", action="store_true", help="verify immutable output and continue incomplete batches")
    value.add_argument(
        "--arxiv-id",
        action="append",
        help="one arXiv identifier, or a comma-separated group; restricts to an explicit sample",
    )
    value.add_argument("--max-ids", type=int, help="restrict to the first N frozen-manifest identifiers; use for a sample")
    value.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="1..50; default: 50")
    value.add_argument(
        "--allow-network",
        action="store_true",
        help="perform sequential official API requests; omitted means dry run with no network",
    )
    value.add_argument(
        "--allow-full-run",
        action="store_true",
        help="required with --allow-network before querying every manifest identifier",
    )
    value.add_argument(
        "--min-interval-seconds",
        type=float,
        default=DEFAULT_MIN_INTERVAL_SECONDS,
        help="must be >= 3; default: 3",
    )
    value.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    value.add_argument("--max-response-bytes", type=int, default=DEFAULT_MAX_RESPONSE_BYTES)
    value.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    return value


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        report = acquire(args)
    except (AcquisitionError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
