#!/usr/bin/env python3
"""Capture hash-only evidence for named external collection sources.

This intentionally small acquirer is for the *collection inventory* phase of
MathDB statement recovery.  It retrieves only the five registry-specified
landing/index assets (and the pinned Erdős YAML), records HTTP metadata and a
SHA-256 digest, and discards response bodies.  It never queries MathDB,
downloads a complete statement corpus, or changes an OPDP release payload.

The result is useful provenance evidence, not a statement recovery: an HTML
directory or an edition announcement does not establish which individual
MathDB records came from that source.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


TOOL_NAME = "OPDP MathDB collection-source evidence capturer"
TOOL_VERSION = "1.0.0"
REGISTRY_SCHEMA = "opdp.mathdb.collection-source-registry.v1"
OUTPUT_SCHEMA = "opdp.mathdb.collection-external-evidence.v1"
DEFAULT_USER_AGENT = (
    "OPDP-Collection-Recovery/1.0 "
    "(+https://github.com/alejandrozu/ulam-opdp-difficulty-atlas)"
)


class EvidenceError(RuntimeError):
    """A malformed registry or source-acquisition failure."""


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=path.parent
    )
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


def parse_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"{path}: expected a JSON object")
    return value


def require_string(mapping: dict[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise EvidenceError(f"{context}: {key} must be a nonempty string")
    return value.strip()


def validate_registry(value: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if value.get("schema") != REGISTRY_SCHEMA:
        raise EvidenceError(
            f"registry schema must be {REGISTRY_SCHEMA!r}, found {value.get('schema')!r}"
        )
    collections = value.get("collections")
    if not isinstance(collections, list) or not collections:
        raise EvidenceError("registry.collections must be a nonempty array")
    seen_collections: set[str] = set()
    seen_sources: set[tuple[str, str]] = set()
    targets: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for index, collection in enumerate(collections):
        context = f"registry.collections[{index}]"
        if not isinstance(collection, dict):
            raise EvidenceError(f"{context} must be an object")
        key = require_string(collection, "key", context)
        if key in seen_collections:
            raise EvidenceError(f"duplicate collection key {key!r}")
        seen_collections.add(key)
        sources = collection.get("sources")
        if not isinstance(sources, list) or not sources:
            raise EvidenceError(f"{context}.sources must be a nonempty array")
        for source_index, source in enumerate(sources):
            source_context = f"{context}.sources[{source_index}]"
            if not isinstance(source, dict):
                raise EvidenceError(f"{source_context} must be an object")
            source_key = require_string(source, "key", source_context)
            require_string(source, "retrieval_url", source_context)
            require_string(source, "canonical_url", source_context)
            pair = (key, source_key)
            if pair in seen_sources:
                raise EvidenceError(f"duplicate source key {pair!r}")
            seen_sources.add(pair)
            targets.append((collection, source))
    return targets


def content_type_charset(content_type: str | None) -> str:
    if not content_type:
        return "utf-8"
    match = re.search(r"charset=([^; ]+)", content_type, re.IGNORECASE)
    return match.group(1).strip('"\'') if match else "utf-8"


def count_observation(source: dict[str, Any], body: bytes, content_type: str | None) -> dict[str, Any] | None:
    extractor = source.get("count_extractor")
    if extractor is None:
        return None
    if not isinstance(extractor, dict):
        raise EvidenceError(f"source {source.get('key')!r}: count_extractor must be an object")
    kind = require_string(extractor, "kind", f"source {source.get('key')!r}.count_extractor")
    encoding = content_type_charset(content_type)
    try:
        text = body.decode(encoding, errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    if kind == "line_prefix_count":
        prefix = require_string(extractor, "prefix", "line_prefix_count extractor")
        observed = sum(1 for line in text.splitlines() if line.startswith(prefix))
        result: dict[str, Any] = {
            "kind": kind,
            "prefix": prefix,
            "observed_count": observed,
        }
        expected = extractor.get("expected_count")
        if expected is not None:
            if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
                raise EvidenceError("line_prefix_count expected_count must be a nonnegative integer")
            result["expected_count"] = expected
            result["matches_expected"] = observed == expected
        return result
    if kind == "required_regex":
        pattern = require_string(extractor, "regex", "required_regex extractor")
        raw_flags = extractor.get("flags", "")
        if not isinstance(raw_flags, str):
            raise EvidenceError("required_regex flags must be a string")
        flags = re.IGNORECASE if "i" in raw_flags.lower() else 0
        observed = len(re.findall(pattern, text, flags))
        result = {
            "kind": kind,
            "pattern_sha256": sha256_bytes(pattern.encode("utf-8")),
            "observed_match_count": observed,
        }
        expected = extractor.get("expected_match_count")
        if expected is not None:
            if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
                raise EvidenceError("required_regex expected_match_count must be a nonnegative integer")
            result["expected_match_count"] = expected
            result["matches_expected"] = observed == expected
        claimed_count = extractor.get("claimed_count")
        if claimed_count is not None:
            if not isinstance(claimed_count, int) or isinstance(claimed_count, bool) or claimed_count < 0:
                raise EvidenceError("required_regex claimed_count must be a nonnegative integer")
            result["source_claimed_count"] = claimed_count
        return result
    raise EvidenceError(f"unsupported count_extractor kind {kind!r}")


def retrieve(
    collection: dict[str, Any],
    source: dict[str, Any],
    *,
    timeout_seconds: float,
    max_bytes: int,
    user_agent: str,
) -> dict[str, Any]:
    collection_key = require_string(collection, "key", "collection")
    source_key = require_string(source, "key", f"collection {collection_key!r} source")
    requested_url = require_string(source, "retrieval_url", f"source {source_key!r}")
    request = urllib.request.Request(
        requested_url,
        headers={"User-Agent": user_agent, "Accept": "*/*"},
        method="GET",
    )
    retrieved_at = utc_now()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", None)
            headers = response.headers
            body = response.read(max_bytes + 1)
            final_url = response.geturl()
    except urllib.error.HTTPError as exc:
        raise EvidenceError(f"{collection_key}/{source_key}: HTTP {exc.code} from {requested_url}") from exc
    except urllib.error.URLError as exc:
        raise EvidenceError(f"{collection_key}/{source_key}: network error for {requested_url}: {exc}") from exc
    if len(body) > max_bytes:
        raise EvidenceError(
            f"{collection_key}/{source_key}: response exceeds --max-bytes={max_bytes}; "
            "increase the explicit limit only after checking that this is an intended index asset"
        )
    if status is not None and not (200 <= int(status) < 300):
        raise EvidenceError(f"{collection_key}/{source_key}: unexpected HTTP status {status}")
    content_type = headers.get("Content-Type")
    observed_sha256 = sha256_bytes(body)
    expected_sha256 = source.get("content_sha256_expected")
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise EvidenceError(f"{collection_key}/{source_key}: malformed content_sha256_expected")
        if observed_sha256 != expected_sha256:
            raise EvidenceError(
                f"{collection_key}/{source_key}: immutable source digest mismatch; "
                f"expected {expected_sha256}, observed {observed_sha256}"
            )
    evidence: dict[str, Any] = {
        "schema": "opdp.mathdb.collection-external-evidence-entry.v1",
        "collection_key": collection_key,
        "source_key": source_key,
        "role": source.get("role"),
        "canonical_url": require_string(source, "canonical_url", f"source {source_key!r}"),
        "requested_url": requested_url,
        "final_url": final_url,
        "retrieved_at_utc": retrieved_at,
        "http_status": int(status) if status is not None else None,
        "content_type": content_type,
        "content_length_bytes": len(body),
        "content_sha256": observed_sha256,
        "response_headers": {
            key.lower(): headers.get(key)
            for key in ("ETag", "Last-Modified", "Date", "Content-Encoding")
            if headers.get(key) is not None
        },
        "local_raw_copy_retained": False,
        "raw_content_policy": "Hash and metadata only; this capture does not publish or retain the response body.",
        "reuse": source.get("reuse"),
    }
    if isinstance(source.get("pinned_revision"), str):
        evidence["pinned_revision"] = source["pinned_revision"]
    observation = count_observation(source, body, content_type)
    if observation is not None:
        if observation.get("matches_expected") is False:
            raise EvidenceError(
                f"{collection_key}/{source_key}: collection-count assertion did not match "
                f"the retrieved artifact ({observation})"
            )
        evidence["count_observation"] = observation
    if isinstance(source.get("statement_artifact_url"), str):
        evidence["statement_artifact_url_not_fetched"] = source["statement_artifact_url"]
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("scripts/mathdb_recovery/collection_source_registry.v1.json"),
        help="source registry JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/MATHDB_COLLECTION_EXTERNAL_EVIDENCE_v1.json"),
        help="hash-only external evidence JSON",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="required acknowledgement before any external HTTP request",
    )
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--max-bytes", type=int, default=50 * 1024 * 1024)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    arguments = parser.parse_args(argv)
    if not arguments.allow_network:
        parser.error("--allow-network is required; this command contacts only registry-specified sources")
    if arguments.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    if arguments.max_bytes < 1:
        parser.error("--max-bytes must be positive")
    if arguments.delay_seconds < 0:
        parser.error("--delay-seconds cannot be negative")

    try:
        registry = parse_json_object(arguments.registry)
        targets = validate_registry(registry)
        evidence_entries: list[dict[str, Any]] = []
        for index, (collection, source) in enumerate(targets):
            if index:
                time.sleep(arguments.delay_seconds)
            log(f"retrieving {collection['key']}/{source['key']}")
            evidence_entries.append(
                retrieve(
                    collection,
                    source,
                    timeout_seconds=arguments.timeout_seconds,
                    max_bytes=arguments.max_bytes,
                    user_agent=arguments.user_agent,
                )
            )
        payload = {
            "schema": OUTPUT_SCHEMA,
            "generated_at_utc": utc_now(),
            "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
            "registry": {
                "path": str(arguments.registry).replace("\\", "/"),
                "sha256": sha256_file(arguments.registry),
                "schema": registry["schema"],
                "registry_version": registry.get("registry_version"),
            },
            "retrieval_policy": {
                "allow_network_explicitly_required": True,
                "minimum_delay_seconds_between_requests": arguments.delay_seconds,
                "raw_response_bodies_retained": False,
                "scope": "Only URLs named in the versioned collection source registry.",
            },
            "entries": evidence_entries,
        }
        atomic_write(arguments.output, canonical_json_bytes(payload) + b"\n")
        log(f"wrote {arguments.output} ({len(evidence_entries)} source artifacts)")
        return 0
    except EvidenceError as exc:
        log(f"error: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
