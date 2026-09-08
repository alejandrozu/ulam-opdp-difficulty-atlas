#!/usr/bin/env python3
"""Acquire a reproducible, resumable public MathDB snapshot.

The script freezes the sitemap-described problem inventory, then retrieves the
full public JSON representation of every inventory item from
``/api/posts/{number}``.  It deliberately uses only Python's standard library.

The output directory is a snapshot workspace, not a cache shared between
snapshots.  Re-running the command with the same arguments and output directory
resumes a partial details crawl.  Use a new directory to take a newer snapshot.

Example (the default aggregate rate is 1.5 requests per second)::

    python scripts/acquire_mathdb_snapshot.py \
      --output-dir ../mathdb-snapshot-2026-09-08

For a non-destructive smoke test, freeze the full inventory but fetch only the
first two details records::

    python scripts/acquire_mathdb_snapshot.py \
      --output-dir ../mathdb-snapshot-smoke --limit 2
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import dataclasses
import datetime as dt
import email.utils
import hashlib
import json
import os
import platform
import random
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence


TOOL_NAME = "OPDP MathDB snapshot acquirer"
TOOL_VERSION = "1.0.0"
MANIFEST_SCHEMA = "opdp.mathdb.snapshot-manifest.v1"
INVENTORY_SCHEMA = "opdp.mathdb.sitemap-inventory.v1"
DETAIL_SCHEMA = "opdp.mathdb.problem-detail.v1"
DEFAULT_BASE_URL = "https://mathdb.com"
DEFAULT_USER_AGENT = (
    "OPDP-MathDB-Snapshot/1.0 "
    "(+https://github.com/alejandrozu/ulam-opdp-difficulty-atlas)"
)
DEFAULT_RATE = 1.5
MAX_ALLOWED_RATE = 8.0
DEFAULT_WORKERS = 4
MAX_ALLOWED_WORKERS = 8
DEFAULT_BATCH_SIZE = 96
DEFAULT_TIMEOUT = 45.0
DEFAULT_RETRIES = 6
DEFAULT_ROBOTS_REFRESH_MINUTES = 60.0
RETRIABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}
PROBLEM_PATH_RE = re.compile(r"^/p/(?P<number>[0-9]+)(?:/(?P<slug>[^/?#]+))?/?$")
PROBLEM_SITEMAP_RE = re.compile(r"^sitemap-problems-[0-9]+\.xml$")
SAFE_RESPONSE_HEADERS = {
    "cache-control",
    "cf-cache-status",
    "cf-ray",
    "content-length",
    "content-type",
    "date",
    "etag",
    "last-modified",
    "retry-after",
    "server",
    "x-mathdb-release",
}


class SnapshotError(RuntimeError):
    """A fatal acquisition or validation error."""


@dataclasses.dataclass(frozen=True)
class FetchResult:
    requested_url: str
    final_url: str
    status: int
    body: bytes
    headers: dict[str, str]
    retrieved_at_utc: str
    attempts: int


class AggregateRateLimiter:
    """Thread-safe fixed-interval limiter shared by every HTTP worker."""

    def __init__(self, requests_per_second: float) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self._interval = 1.0 / requests_per_second
        self._lock = threading.Lock()
        self._next_slot = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self._interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)


class HttpClient:
    """Small GET-only client with bounded retries and aggregate rate limiting."""

    def __init__(
        self,
        *,
        user_agent: str,
        rate: float,
        timeout: float,
        max_retries: int,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max_retries
        self.rate_limiter = AggregateRateLimiter(rate)

    def get(self, url: str, *, accept: str = "*/*") -> FetchResult:
        last_error: BaseException | None = None
        for attempt in range(1, self.max_retries + 2):
            self.rate_limiter.wait()
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": accept,
                    # Avoid compression ambiguity: the stored response hash is
                    # for the exact decoded HTTP entity body returned here.
                    "Accept-Encoding": "identity",
                },
                method="GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read()
                    result = FetchResult(
                        requested_url=url,
                        final_url=response.geturl(),
                        status=int(response.status),
                        body=body,
                        headers=_safe_headers(response.headers),
                        retrieved_at_utc=utc_now(),
                        attempts=attempt,
                    )
                    if result.status != 200:
                        raise SnapshotError(f"unexpected HTTP {result.status} for {url}")
                    return result
            except urllib.error.HTTPError as exc:
                last_error = exc
                retry_after_value = exc.headers.get("Retry-After")
                retry_after_seconds = _retry_after_seconds(retry_after_value)
                if exc.code == 429 and retry_after_seconds is not None and retry_after_seconds > 60:
                    excerpt = _read_error_excerpt(exc)
                    raise SnapshotError(
                        f"HTTP 429 quota response for {url}; server Retry-After="
                        f"{retry_after_value!r}. The run stopped without waiting or "
                        "attempting to evade the server limit"
                        + (f": {excerpt}" if excerpt else "")
                    ) from exc
                if exc.code not in RETRIABLE_HTTP_STATUSES or attempt > self.max_retries:
                    excerpt = _read_error_excerpt(exc)
                    raise SnapshotError(
                        f"HTTP {exc.code} for {url} after {attempt} attempt(s)"
                        + (f": {excerpt}" if excerpt else "")
                    ) from exc
                delay = _retry_delay(attempt, retry_after_value)
                log(
                    f"retry {attempt}/{self.max_retries} for HTTP {exc.code} "
                    f"after {delay:.1f}s: {url}"
                )
                time.sleep(delay)
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
                last_error = exc
                if attempt > self.max_retries:
                    raise SnapshotError(
                        f"network failure for {url} after {attempt} attempt(s): {exc}"
                    ) from exc
                delay = _retry_delay(attempt, None)
                log(
                    f"retry {attempt}/{self.max_retries} for network error "
                    f"after {delay:.1f}s: {url} ({exc})"
                )
                time.sleep(delay)
        raise SnapshotError(f"request failed for {url}: {last_error}")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def log(message: str) -> None:
    print(f"[{utc_now()}] {message}", file=sys.stderr, flush=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def json_line(value: Any) -> bytes:
    return canonical_json_bytes(value) + b"\n"


def _safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    return {
        key: normalized[key]
        for key in sorted(SAFE_RESPONSE_HEADERS)
        if key in normalized
    }


def _read_error_excerpt(exc: urllib.error.HTTPError) -> str:
    try:
        return exc.read(512).decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    retry_after_seconds = _retry_after_seconds(retry_after)
    if retry_after_seconds is not None:
        return min(300.0, retry_after_seconds)
    # Small jitter keeps simultaneous retrying clients from synchronizing.
    return min(60.0, (2 ** (attempt - 1)) + random.uniform(0.0, 0.75))


def _retry_after_seconds(retry_after: str | None) -> float | None:
    if not retry_after:
        return None
    with contextlib.suppress(ValueError):
        return max(0.0, float(retry_after))
    with contextlib.suppress(TypeError, ValueError, OverflowError):
        target = email.utils.parsedate_to_datetime(retry_after)
        if target.tzinfo is None:
            target = target.replace(tzinfo=dt.timezone.utc)
        return max(0.0, (target - dt.datetime.now(dt.timezone.utc)).total_seconds())
    return None


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_write_json(path: Path, value: Any) -> None:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    atomic_write_bytes(path, payload)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def relative_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def response_metadata(result: FetchResult) -> dict[str, Any]:
    return {
        "requested_url": result.requested_url,
        "final_url": result.final_url,
        "status": result.status,
        "retrieved_at_utc": result.retrieved_at_utc,
        "attempts": result.attempts,
        "headers": result.headers,
        "body_bytes": len(result.body),
        "body_sha256": sha256_bytes(result.body),
    }


def ensure_same_origin(url: str, base_url: str, *, label: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    base = urllib.parse.urlsplit(base_url)
    if parsed.scheme.lower() != base.scheme.lower() or parsed.netloc.lower() != base.netloc.lower():
        raise SnapshotError(f"{label} escaped the configured origin: {url}")


def parse_robots(result: FetchResult, *, user_agent: str) -> urllib.robotparser.RobotFileParser:
    try:
        text = result.body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SnapshotError("robots.txt was not valid UTF-8") from exc
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(result.final_url)
    parser.parse(text.splitlines())
    # Passing the full identifying UA is intentional; wildcard rules still
    # apply and any future crawler-specific rule can match its product token.
    if not user_agent.strip():
        raise SnapshotError("an identifying User-Agent is required")
    return parser


def assert_robots_allowed(
    parser: urllib.robotparser.RobotFileParser,
    *,
    user_agent: str,
    urls: Iterable[str],
) -> None:
    denied = [url for url in urls if not parser.can_fetch(user_agent, url)]
    if denied:
        raise SnapshotError(
            "robots.txt disallows required public snapshot URL(s): " + ", ".join(denied[:5])
        )


def parse_sitemap_index(body: bytes, *, base_url: str) -> list[str]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise SnapshotError(f"invalid sitemap index XML: {exc}") from exc
    if _local_name(root.tag) != "sitemapindex":
        raise SnapshotError(f"expected sitemapindex root, found {_local_name(root.tag)!r}")
    locations: list[str] = []
    for element in root.iter():
        if _local_name(element.tag) != "loc" or not element.text:
            continue
        location = element.text.strip()
        name = Path(urllib.parse.urlsplit(location).path).name
        if PROBLEM_SITEMAP_RE.fullmatch(name):
            ensure_same_origin(location, base_url, label="problem sitemap")
            locations.append(location)
    if not locations:
        raise SnapshotError("sitemap index did not contain any problem sitemap shards")
    if len(locations) != len(set(locations)):
        raise SnapshotError("sitemap index contains duplicate problem sitemap shard URLs")
    return locations


def parse_problem_sitemap(
    body: bytes,
    *,
    sitemap_url: str,
    base_url: str,
) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise SnapshotError(f"invalid problem sitemap XML at {sitemap_url}: {exc}") from exc
    if _local_name(root.tag) != "urlset":
        raise SnapshotError(
            f"expected urlset root in {sitemap_url}, found {_local_name(root.tag)!r}"
        )
    entries: list[dict[str, Any]] = []
    shard_name = Path(urllib.parse.urlsplit(sitemap_url).path).name
    for url_element in [element for element in root if _local_name(element.tag) == "url"]:
        fields: dict[str, str] = {}
        for child in url_element:
            if child.text:
                fields[_local_name(child.tag)] = child.text.strip()
        location = fields.get("loc")
        if not location:
            raise SnapshotError(f"problem sitemap entry without loc in {sitemap_url}")
        ensure_same_origin(location, base_url, label="problem page")
        parsed = urllib.parse.urlsplit(location)
        match = PROBLEM_PATH_RE.fullmatch(parsed.path)
        if not match:
            raise SnapshotError(f"unrecognized MathDB problem URL in sitemap: {location}")
        entries.append(
            {
                "schema": INVENTORY_SCHEMA,
                "number": int(match.group("number")),
                "url": location,
                "slug": urllib.parse.unquote(match.group("slug") or ""),
                "lastmod": fields.get("lastmod"),
                "changefreq": fields.get("changefreq"),
                "priority": fields.get("priority"),
                "sitemap_url": sitemap_url,
                "sitemap_file": shard_name,
            }
        )
    return entries


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def validate_inventory(entries: Sequence[dict[str, Any]]) -> None:
    if not entries:
        raise SnapshotError("problem inventory is empty")
    numbers = [int(entry["number"]) for entry in entries]
    urls = [str(entry["url"]) for entry in entries]
    duplicate_numbers = [number for number, count in Counter(numbers).items() if count > 1]
    duplicate_urls = [url for url, count in Counter(urls).items() if count > 1]
    if duplicate_numbers:
        raise SnapshotError(
            f"problem inventory has duplicate numbers, including {duplicate_numbers[:10]}"
        )
    if duplicate_urls:
        raise SnapshotError(
            f"problem inventory has duplicate URLs, including {duplicate_urls[:3]}"
        )


def write_inventory(path: Path, entries: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        for entry in entries:
            handle.write(json_line(entry))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_inventory(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            try:
                value = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SnapshotError(
                    f"invalid inventory JSONL at {path}:{line_number}: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise SnapshotError(f"non-object inventory line at {path}:{line_number}")
            entries.append(value)
    validate_inventory(entries)
    if entries != sorted(entries, key=lambda entry: int(entry["number"])):
        raise SnapshotError("inventory JSONL is not sorted by MathDB problem number")
    return entries


def acquire_initial_snapshot(
    *,
    output_dir: Path,
    base_url: str,
    user_agent: str,
    client: HttpClient,
    manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], urllib.robotparser.RobotFileParser]:
    policy_dir = output_dir / "policy"
    sitemap_dir = output_dir / "sitemaps"
    metadata_dir = output_dir / "metadata"

    robots_url = urllib.parse.urljoin(base_url + "/", "robots.txt")
    terms_url = urllib.parse.urljoin(base_url + "/", "terms")
    sitemap_index_url = urllib.parse.urljoin(base_url + "/", "sitemap.xml")
    api_probe_url = urllib.parse.urljoin(base_url + "/", "api/posts?limit=1&offset=0")
    api_example_url = urllib.parse.urljoin(base_url + "/", "api/posts/1")

    robots = client.get(robots_url, accept="text/plain")
    ensure_same_origin(robots.final_url, base_url, label="robots.txt redirect")
    atomic_write_bytes(policy_dir / "robots_at_start.txt", robots.body)
    robots_parser = parse_robots(robots, user_agent=user_agent)
    assert_robots_allowed(
        robots_parser,
        user_agent=user_agent,
        urls=[terms_url, sitemap_index_url, api_probe_url, api_example_url],
    )

    terms = client.get(terms_url, accept="text/html")
    ensure_same_origin(terms.final_url, base_url, label="terms redirect")
    atomic_write_bytes(policy_dir / "terms_at_start.html", terms.body)

    sitemap_index = client.get(sitemap_index_url, accept="application/xml,text/xml")
    ensure_same_origin(sitemap_index.final_url, base_url, label="sitemap index redirect")
    atomic_write_bytes(sitemap_dir / "sitemap.xml", sitemap_index.body)
    problem_sitemaps = parse_sitemap_index(sitemap_index.body, base_url=base_url)
    assert_robots_allowed(
        robots_parser,
        user_agent=user_agent,
        urls=problem_sitemaps,
    )

    entries: list[dict[str, Any]] = []
    shard_metadata: list[dict[str, Any]] = []
    for index, sitemap_url in enumerate(problem_sitemaps, start=1):
        log(f"fetching problem sitemap {index}/{len(problem_sitemaps)}: {sitemap_url}")
        response = client.get(sitemap_url, accept="application/xml,text/xml")
        ensure_same_origin(response.final_url, base_url, label="problem sitemap redirect")
        filename = Path(urllib.parse.urlsplit(sitemap_url).path).name
        atomic_write_bytes(sitemap_dir / filename, response.body)
        shard_entries = parse_problem_sitemap(
            response.body,
            sitemap_url=sitemap_url,
            base_url=base_url,
        )
        entries.extend(shard_entries)
        metadata = response_metadata(response)
        metadata["file"] = relative_path(sitemap_dir / filename, output_dir)
        metadata["problem_count"] = len(shard_entries)
        shard_metadata.append(metadata)

    entries.sort(key=lambda entry: int(entry["number"]))
    validate_inventory(entries)
    inventory_path = output_dir / "mathdb_sitemap_inventory.jsonl"
    write_inventory(inventory_path, entries)

    probe = client.get(api_probe_url, accept="application/json")
    ensure_same_origin(probe.final_url, base_url, label="API probe redirect")
    atomic_write_bytes(metadata_dir / "api_posts_probe.json", probe.body)
    try:
        probe_json = json.loads(probe.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"API count probe did not return valid JSON: {exc}") from exc
    reported_total = probe_json.get("total") if isinstance(probe_json, dict) else None
    if not isinstance(reported_total, int):
        reported_total = None

    initial_releases = _release_counter(
        [robots, terms, sitemap_index, probe]
        + [
            FetchResult(
                requested_url=metadata["requested_url"],
                final_url=metadata["final_url"],
                status=metadata["status"],
                body=b"",
                headers=metadata["headers"],
                retrieved_at_utc=metadata["retrieved_at_utc"],
                attempts=metadata["attempts"],
            )
            for metadata in shard_metadata
        ]
    )
    manifest["policy"] = {
        **manifest["policy"],
        "robots_url": robots_url,
        "terms_url": terms_url,
        "robots_allowed_required_routes": True,
        "robots_at_start": {
            **response_metadata(robots),
            "file": relative_path(policy_dir / "robots_at_start.txt", output_dir),
        },
        "terms_at_start": {
            **response_metadata(terms),
            "file": relative_path(policy_dir / "terms_at_start.html", output_dir),
        },
        "robots_checks": [
            {
                **response_metadata(robots),
                "allowed_required_routes": True,
            }
        ],
    }
    manifest["inventory"] = {
        "status": "complete",
        "frozen_at_utc": utc_now(),
        "sitemap_index": {
            **response_metadata(sitemap_index),
            "file": relative_path(sitemap_dir / "sitemap.xml", output_dir),
        },
        "problem_sitemaps": shard_metadata,
        "path": relative_path(inventory_path, output_dir),
        "sha256": sha256_file(inventory_path),
        "count": len(entries),
        "minimum_number": min(int(entry["number"]) for entry in entries),
        "maximum_number": max(int(entry["number"]) for entry in entries),
        "api_reported_total_at_inventory_time": reported_total,
        "api_reported_total_difference": (
            reported_total - len(entries) if reported_total is not None else None
        ),
        "api_probe": {
            **response_metadata(probe),
            "file": relative_path(metadata_dir / "api_posts_probe.json", output_dir),
        },
        "release_header_counts": dict(sorted(initial_releases.items())),
    }
    manifest["status"] = "inventory_complete"
    manifest["updated_at_utc"] = utc_now()
    return entries, robots_parser


def _release_counter(results: Iterable[FetchResult]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for result in results:
        counts[result.headers.get("x-mathdb-release", "<missing>")] += 1
    return counts


def verify_frozen_inventory(
    *, output_dir: Path, manifest: Mapping[str, Any]
) -> list[dict[str, Any]]:
    inventory = manifest.get("inventory")
    if not isinstance(inventory, dict) or inventory.get("status") != "complete":
        raise SnapshotError("manifest does not contain a complete frozen inventory")
    path_value = inventory.get("path")
    expected_hash = inventory.get("sha256")
    if not isinstance(path_value, str) or not isinstance(expected_hash, str):
        raise SnapshotError("manifest inventory path/hash is missing")
    inventory_path = output_dir / path_value
    if not inventory_path.is_file():
        raise SnapshotError(f"frozen inventory file is missing: {inventory_path}")
    actual_hash = sha256_file(inventory_path)
    if actual_hash != expected_hash:
        raise SnapshotError(
            f"frozen inventory hash mismatch: expected {expected_hash}, got {actual_hash}"
        )
    entries = read_inventory(inventory_path)
    if len(entries) != inventory.get("count"):
        raise SnapshotError(
            f"frozen inventory count mismatch: manifest={inventory.get('count')}, "
            f"file={len(entries)}"
        )
    return entries


def check_live_robots(
    *,
    output_dir: Path,
    base_url: str,
    user_agent: str,
    client: HttpClient,
    manifest: dict[str, Any],
    required_urls: Sequence[str],
) -> urllib.robotparser.RobotFileParser:
    robots_url = urllib.parse.urljoin(base_url + "/", "robots.txt")
    response = client.get(robots_url, accept="text/plain")
    ensure_same_origin(response.final_url, base_url, label="robots.txt redirect")
    parser = parse_robots(response, user_agent=user_agent)
    assert_robots_allowed(parser, user_agent=user_agent, urls=required_urls)
    check = {
        **response_metadata(response),
        "allowed_required_routes": True,
    }
    policy = manifest.setdefault("policy", {})
    checks = policy.setdefault("robots_checks", [])
    if not isinstance(checks, list):
        raise SnapshotError("manifest policy.robots_checks is not a list")
    checks.append(check)

    start_info = policy.get("robots_at_start")
    start_hash = start_info.get("body_sha256") if isinstance(start_info, dict) else None
    if start_hash and start_hash != check["body_sha256"]:
        changed_path = output_dir / "policy" / (
            "robots_changed_" + utc_now().replace(":", "-") + ".txt"
        )
        atomic_write_bytes(changed_path, response.body)
        check["changed_file"] = relative_path(changed_path, output_dir)
        log("robots.txt changed since snapshot start; the current policy still allows the crawl")
    manifest["updated_at_utc"] = utc_now()
    return parser


def fetch_problem_detail(
    entry: Mapping[str, Any],
    *,
    base_url: str,
    client: HttpClient,
) -> dict[str, Any]:
    number = int(entry["number"])
    api_url = urllib.parse.urljoin(base_url + "/", f"api/posts/{number}")
    result = client.get(api_url, accept="application/json")
    ensure_same_origin(result.final_url, base_url, label="problem API redirect")
    content_type = result.headers.get("content-type", "")
    if content_type and "json" not in content_type.lower():
        raise SnapshotError(
            f"problem {number} returned unexpected Content-Type {content_type!r}"
        )
    try:
        problem = json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"problem {number} returned invalid UTF-8 JSON: {exc}") from exc
    if not isinstance(problem, dict):
        raise SnapshotError(f"problem {number} API response is not a JSON object")
    if problem.get("number") != number:
        raise SnapshotError(
            f"problem number mismatch: requested {number}, response has {problem.get('number')!r}"
        )
    canonical_problem = canonical_json_bytes(problem)
    return {
        "schema": DETAIL_SCHEMA,
        "snapshot": {
            "inventory_number": number,
            "inventory_url": entry["url"],
            "inventory_lastmod": entry.get("lastmod"),
            "inventory_sitemap_file": entry["sitemap_file"],
            "api_url": api_url,
            "final_url": result.final_url,
            "retrieved_at_utc": result.retrieved_at_utc,
            "http_status": result.status,
            "attempts": result.attempts,
            "response_headers": result.headers,
            "release": result.headers.get("x-mathdb-release"),
            "raw_response_bytes": len(result.body),
            "raw_response_sha256": sha256_bytes(result.body),
            "canonical_problem_sha256": sha256_bytes(canonical_problem),
        },
        "problem": problem,
    }


def scan_details_prefix(
    path: Path,
    *,
    expected_numbers: Sequence[int],
) -> tuple[int, Counter[str], str | None, str | None]:
    if not path.exists():
        return 0, Counter(), None, None
    expected_index = 0
    release_counts: Counter[str] = Counter()
    first_retrieved: str | None = None
    last_retrieved: str | None = None
    seen_problem_ids: set[str] = set()
    size = path.stat().st_size
    truncate_at: int | None = None
    add_newline = False
    with path.open("rb") as handle:
        while True:
            start = handle.tell()
            raw_line = handle.readline()
            if not raw_line:
                break
            end = handle.tell()
            try:
                envelope = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                if end == size:
                    truncate_at = start
                    log(
                        f"discarding an incomplete final details line at byte {start}: {exc}"
                    )
                    break
                raise SnapshotError(
                    f"corrupt non-final details JSONL line beginning at byte {start}: {exc}"
                ) from exc
            if expected_index >= len(expected_numbers):
                raise SnapshotError("details JSONL has more records than the selected inventory")
            if not isinstance(envelope, dict) or not isinstance(envelope.get("problem"), dict):
                raise SnapshotError(f"invalid details envelope beginning at byte {start}")
            if envelope.get("schema") != DETAIL_SCHEMA:
                raise SnapshotError(f"unexpected details schema beginning at byte {start}")
            number = envelope["problem"].get("number")
            expected = expected_numbers[expected_index]
            if number != expected:
                raise SnapshotError(
                    f"details JSONL is not the expected inventory prefix at record "
                    f"{expected_index + 1}: expected {expected}, found {number!r}"
                )
            problem_id = envelope["problem"].get("id")
            if not isinstance(problem_id, str) or not problem_id:
                raise SnapshotError(f"missing stable MathDB id for problem {number}")
            if problem_id in seen_problem_ids:
                raise SnapshotError(f"duplicate MathDB problem id in details JSONL: {problem_id}")
            seen_problem_ids.add(problem_id)
            snapshot = envelope.get("snapshot")
            if not isinstance(snapshot, dict):
                raise SnapshotError(f"missing details snapshot metadata for problem {number}")
            expected_canonical_hash = snapshot.get("canonical_problem_sha256")
            actual_canonical_hash = sha256_bytes(canonical_json_bytes(envelope["problem"]))
            if expected_canonical_hash != actual_canonical_hash:
                raise SnapshotError(
                    f"canonical problem hash mismatch in resumed details for problem {number}"
                )
            release_counts[str(snapshot.get("release") or "<missing>")] += 1
            retrieved = snapshot.get("retrieved_at_utc")
            if isinstance(retrieved, str):
                first_retrieved = first_retrieved or retrieved
                last_retrieved = retrieved
            expected_index += 1
            if not raw_line.endswith(b"\n"):
                add_newline = True

    if truncate_at is not None:
        with path.open("r+b") as handle:
            handle.truncate(truncate_at)
            handle.flush()
            os.fsync(handle.fileno())
    elif add_newline:
        with path.open("ab") as handle:
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
    return expected_index, release_counts, first_retrieved, last_retrieved


def update_details_manifest(
    manifest: dict[str, Any],
    *,
    output_dir: Path,
    partial_path: Path,
    final_path: Path,
    scope: str,
    sample_limit: int | None,
    expected: int,
    completed: int,
    release_counts: Counter[str],
    first_retrieved: str | None,
    last_retrieved: str | None,
) -> None:
    manifest["details"] = {
        "scope": scope,
        "sample_limit": sample_limit,
        "selected_count": expected,
        "inventory_count": manifest["inventory"]["count"],
        "completed_count": completed,
        "remaining_count": expected - completed,
        "partial_path": relative_path(partial_path, output_dir),
        "final_path": relative_path(final_path, output_dir),
        "api_release_header_counts": dict(sorted(release_counts.items())),
        "first_retrieved_at_utc": first_retrieved,
        "last_retrieved_at_utc": last_retrieved,
    }
    manifest["updated_at_utc"] = utc_now()


def acquire_details(
    *,
    output_dir: Path,
    base_url: str,
    user_agent: str,
    client: HttpClient,
    manifest: dict[str, Any],
    inventory: Sequence[dict[str, Any]],
    sample_limit: int | None,
    workers: int,
    batch_size: int,
    robots_refresh_minutes: float,
) -> None:
    selected = list(inventory if sample_limit is None else inventory[:sample_limit])
    scope = "full" if sample_limit is None else "sample"
    expected_numbers = [int(entry["number"]) for entry in selected]
    partial_path = output_dir / "mathdb_problem_details.jsonl.partial"
    final_path = output_dir / "mathdb_problem_details.jsonl"

    if final_path.exists() and not partial_path.exists():
        scan_path = final_path
    elif final_path.exists() and partial_path.exists():
        raise SnapshotError("both final and partial details files exist; refusing an ambiguous resume")
    else:
        scan_path = partial_path

    completed, release_counts, first_retrieved, last_retrieved = scan_details_prefix(
        scan_path,
        expected_numbers=expected_numbers,
    )
    update_details_manifest(
        manifest,
        output_dir=output_dir,
        partial_path=partial_path,
        final_path=final_path,
        scope=scope,
        sample_limit=sample_limit,
        expected=len(selected),
        completed=completed,
        release_counts=release_counts,
        first_retrieved=first_retrieved,
        last_retrieved=last_retrieved,
    )

    if completed == len(selected) and final_path.exists():
        finish_details_manifest(
            manifest,
            final_path=final_path,
            scope=scope,
            expected_numbers=expected_numbers,
        )
        return

    if final_path.exists():
        raise SnapshotError("incomplete final details file cannot be resumed safely")

    api_probe_url = urllib.parse.urljoin(base_url + "/", "api/posts/1")
    robots_parser = check_live_robots(
        output_dir=output_dir,
        base_url=base_url,
        user_agent=user_agent,
        client=client,
        manifest=manifest,
        required_urls=[api_probe_url],
    )
    manifest["status"] = "acquiring_details"
    atomic_write_json(output_dir / "mathdb_snapshot_manifest.json", manifest)

    started_monotonic = time.monotonic()
    last_robots_check = started_monotonic
    pending = selected[completed:]
    log(
        f"details scope={scope}; inventory={len(inventory):,}; selected={len(selected):,}; "
        f"already complete={completed:,}; pending={len(pending):,}"
    )

    partial_path.parent.mkdir(parents=True, exist_ok=True)
    with partial_path.open("ab") as output_handle, concurrent.futures.ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="mathdb",
    ) as executor:
        for batch_start in range(0, len(pending), batch_size):
            now = time.monotonic()
            if now - last_robots_check >= robots_refresh_minutes * 60.0:
                robots_parser = check_live_robots(
                    output_dir=output_dir,
                    base_url=base_url,
                    user_agent=user_agent,
                    client=client,
                    manifest=manifest,
                    required_urls=[api_probe_url],
                )
                last_robots_check = time.monotonic()

            batch = pending[batch_start : batch_start + batch_size]
            batch_urls = [
                urllib.parse.urljoin(base_url + "/", f"api/posts/{int(entry['number'])}")
                for entry in batch
            ]
            assert_robots_allowed(
                robots_parser,
                user_agent=user_agent,
                urls=batch_urls,
            )
            futures = [
                executor.submit(
                    fetch_problem_detail,
                    entry,
                    base_url=base_url,
                    client=client,
                )
                for entry in batch
            ]
            envelopes: list[dict[str, Any]] = []
            try:
                # Preserve strict inventory order in JSONL even though requests
                # complete concurrently.  No part of a failed batch is written.
                for future in futures:
                    envelopes.append(future.result())
            except BaseException:
                for future in futures:
                    future.cancel()
                raise

            for envelope in envelopes:
                output_handle.write(json_line(envelope))
                snapshot = envelope["snapshot"]
                release_counts[str(snapshot.get("release") or "<missing>")] += 1
                retrieved = snapshot.get("retrieved_at_utc")
                if isinstance(retrieved, str):
                    first_retrieved = first_retrieved or retrieved
                    last_retrieved = retrieved
            output_handle.flush()
            os.fsync(output_handle.fileno())

            completed += len(envelopes)
            update_details_manifest(
                manifest,
                output_dir=output_dir,
                partial_path=partial_path,
                final_path=final_path,
                scope=scope,
                sample_limit=sample_limit,
                expected=len(selected),
                completed=completed,
                release_counts=release_counts,
                first_retrieved=first_retrieved,
                last_retrieved=last_retrieved,
            )
            atomic_write_json(output_dir / "mathdb_snapshot_manifest.json", manifest)

            elapsed = max(0.001, time.monotonic() - started_monotonic)
            acquired_this_run = completed - (len(selected) - len(pending))
            observed_rate = acquired_this_run / elapsed
            eta_seconds = (
                (len(selected) - completed) / observed_rate if observed_rate > 0 else float("inf")
            )
            log(
                f"details {completed:,}/{len(selected):,} "
                f"({100.0 * completed / max(1, len(selected)):.2f}%); "
                f"observed {observed_rate:.2f}/s; ETA {_format_duration(eta_seconds)}"
            )

    os.replace(partial_path, final_path)
    finish_details_manifest(
        manifest,
        final_path=final_path,
        scope=scope,
        expected_numbers=expected_numbers,
    )


def finish_details_manifest(
    manifest: dict[str, Any],
    *,
    final_path: Path,
    scope: str,
    expected_numbers: Sequence[int],
) -> None:
    if not final_path.is_file():
        raise SnapshotError(f"final details file is missing: {final_path}")
    details = manifest["details"]
    if details["completed_count"] != details["selected_count"]:
        raise SnapshotError("cannot finalize an incomplete details acquisition")
    log("running post-acquisition completeness and canonical-hash validation")
    (
        verified_count,
        verified_release_counts,
        verified_first_retrieved,
        verified_last_retrieved,
    ) = scan_details_prefix(final_path, expected_numbers=expected_numbers)
    if verified_count != len(expected_numbers):
        raise SnapshotError(
            f"post-run completeness failure: expected {len(expected_numbers)} records, "
            f"validated {verified_count}"
        )
    if verified_release_counts != Counter(details["api_release_header_counts"]):
        raise SnapshotError("post-run release-header counts differ from acquisition checkpoints")
    details["sha256"] = sha256_file(final_path)
    details["bytes"] = final_path.stat().st_size
    details["completed_at_utc"] = utc_now()
    details["post_run_validation"] = {
        "status": "passed",
        "validated_count": verified_count,
        "inventory_order_exact": True,
        "unique_problem_ids": True,
        "canonical_problem_hashes_valid": True,
        "first_retrieved_at_utc": verified_first_retrieved,
        "last_retrieved_at_utc": verified_last_retrieved,
        "validated_at_utc": utc_now(),
    }

    inventory_releases = set(
        manifest.get("inventory", {}).get("release_header_counts", {}).keys()
    ) - {"<missing>"}
    detail_releases = set(details.get("api_release_header_counts", {}).keys()) - {
        "<missing>"
    }
    manifest["consistency"] = {
        "inventory_release_headers": sorted(inventory_releases),
        "detail_release_headers": sorted(detail_releases),
        "single_release_across_inventory_and_details": (
            len(inventory_releases | detail_releases) == 1
            and "<missing>"
            not in set(manifest.get("inventory", {}).get("release_header_counts", {}))
            and "<missing>" not in set(details.get("api_release_header_counts", {}))
        ),
        "note": (
            "MathDB is live and the sitemap/API do not expose a transactional snapshot. "
            "Every response release header and retrieval time is therefore retained."
        ),
    }
    manifest["status"] = "complete" if scope == "full" else "complete_sample"
    manifest["completed_at_utc"] = utc_now()
    manifest["updated_at_utc"] = manifest["completed_at_utc"]


def _format_duration(seconds: float) -> str:
    if seconds == float("inf"):
        return "unknown"
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def base_manifest(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "status": "initializing",
        "created_at_utc": utc_now(),
        "updated_at_utc": utc_now(),
        "base_url": args.base_url,
        "tool": {
            "name": TOOL_NAME,
            "version": TOOL_VERSION,
            "python_version": platform.python_version(),
            "platform": platform.platform(),
        },
        "policy": {
            "user_agent": args.user_agent,
            "aggregate_requests_per_second": args.rate,
            "workers": args.workers,
            "timeout_seconds": args.timeout,
            "maximum_retries": args.retries,
            "robots_refresh_minutes": args.robots_refresh_minutes,
        },
        "selection": {
            "scope": "full" if args.limit is None else "sample",
            "limit": args.limit,
        },
    }


def validate_resume_manifest(manifest: Mapping[str, Any], args: argparse.Namespace) -> None:
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise SnapshotError(
            f"unsupported manifest schema: {manifest.get('schema')!r}"
        )
    if manifest.get("base_url") != args.base_url:
        raise SnapshotError(
            f"resume base URL mismatch: manifest={manifest.get('base_url')!r}, "
            f"argument={args.base_url!r}"
        )
    policy = manifest.get("policy")
    if not isinstance(policy, dict) or policy.get("user_agent") != args.user_agent:
        raise SnapshotError(
            "resume User-Agent mismatch; use the same identifying User-Agent or a new output directory"
        )
    selection = manifest.get("selection")
    expected_scope = "full" if args.limit is None else "sample"
    if not isinstance(selection, dict) or selection.get("scope") != expected_scope:
        raise SnapshotError("resume scope mismatch; use a new output directory")
    if selection.get("limit") != args.limit:
        raise SnapshotError("resume sample limit mismatch; use a new output directory")


@contextlib.contextmanager
def output_lock(output_dir: Path, *, break_lock: bool) -> Iterator[None]:
    lock_path = output_dir / ".acquire_mathdb_snapshot.lock"
    output_dir.mkdir(parents=True, exist_ok=True)
    if break_lock and lock_path.exists():
        lock_path.unlink()
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        try:
            owner = lock_path.read_text(encoding="utf-8").strip()
        except OSError:
            owner = "unreadable"
        raise SnapshotError(
            f"snapshot directory is locked ({owner}); if the prior process is gone, "
            "re-run with --break-lock"
        ) from exc
    try:
        payload = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "started_at_utc": utc_now(),
        }
        os.write(descriptor, canonical_json_bytes(payload) + b"\n")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            lock_path.unlink()


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze MathDB's public problem sitemaps and acquire every corresponding "
            "full /api/posts/{number} record into resumable JSONL."
        )
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="dedicated snapshot directory (reuse it only to resume the same snapshot)",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"MathDB origin (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--user-agent",
        default=DEFAULT_USER_AGENT,
        help="accurately identifying crawler User-Agent, including a contact URL",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=DEFAULT_RATE,
        help=(
            f"aggregate requests/second across all workers (default: {DEFAULT_RATE}; "
            f"hard maximum: {MAX_ALLOWED_RATE})"
        ),
    )
    parser.add_argument(
        "--workers",
        type=positive_int,
        default=DEFAULT_WORKERS,
        help=(
            f"bounded detail-request worker count (default: {DEFAULT_WORKERS}; "
            f"hard maximum: {MAX_ALLOWED_WORKERS})"
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=positive_int,
        default=DEFAULT_BATCH_SIZE,
        help=f"ordered, fsynced checkpoint size (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"per-request timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"retries after the initial request (default: {DEFAULT_RETRIES})",
    )
    parser.add_argument(
        "--robots-refresh-minutes",
        type=float,
        default=DEFAULT_ROBOTS_REFRESH_MINUTES,
        help=(
            "re-check robots.txt during a long crawl at this interval "
            f"(default: {DEFAULT_ROBOTS_REFRESH_MINUTES})"
        ),
    )
    parser.add_argument(
        "--limit",
        type=positive_int,
        default=None,
        help=(
            "fetch only the first N details after freezing the complete inventory; "
            "intended solely for smoke tests"
        ),
    )
    parser.add_argument(
        "--break-lock",
        action="store_true",
        help="remove a stale acquisition lock after independently confirming no run is active",
    )
    args = parser.parse_args(argv)

    args.base_url = args.base_url.rstrip("/")
    parsed_base = urllib.parse.urlsplit(args.base_url)
    if parsed_base.scheme != "https" or not parsed_base.netloc or parsed_base.path:
        parser.error("--base-url must be an HTTPS origin with no path")
    if not args.user_agent.strip() or "python" in args.user_agent.lower():
        parser.error("--user-agent must accurately identify this crawler")
    if not (0 < args.rate <= MAX_ALLOWED_RATE):
        parser.error(f"--rate must be > 0 and <= {MAX_ALLOWED_RATE}")
    if args.workers > MAX_ALLOWED_WORKERS:
        parser.error(f"--workers must be <= {MAX_ALLOWED_WORKERS}")
    if args.batch_size > 1000:
        parser.error("--batch-size must be <= 1000")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if not (0 <= args.retries <= 12):
        parser.error("--retries must be between 0 and 12")
    if args.robots_refresh_minutes <= 0:
        parser.error("--robots-refresh-minutes must be positive")
    return args


def run(args: argparse.Namespace) -> None:
    output_dir = args.output_dir.resolve()
    manifest_path = output_dir / "mathdb_snapshot_manifest.json"
    client = HttpClient(
        user_agent=args.user_agent,
        rate=args.rate,
        timeout=args.timeout,
        max_retries=args.retries,
    )

    with output_lock(output_dir, break_lock=args.break_lock):
        if manifest_path.exists():
            manifest = read_json(manifest_path)
            if not isinstance(manifest, dict):
                raise SnapshotError("snapshot manifest is not a JSON object")
            validate_resume_manifest(manifest, args)
            inventory_info = manifest.get("inventory")
            if isinstance(inventory_info, dict) and inventory_info.get("status") == "complete":
                inventory = verify_frozen_inventory(output_dir=output_dir, manifest=manifest)
                log(
                    f"resuming frozen inventory of {len(inventory):,} problems from {output_dir}"
                )
            else:
                log("prior run stopped before freezing inventory; restarting the inventory stage")
                inventory, _ = acquire_initial_snapshot(
                    output_dir=output_dir,
                    base_url=args.base_url,
                    user_agent=args.user_agent,
                    client=client,
                    manifest=manifest,
                )
                atomic_write_json(manifest_path, manifest)
        else:
            manifest = base_manifest(args)
            atomic_write_json(manifest_path, manifest)
            log(f"starting new MathDB snapshot in {output_dir}")
            inventory, _ = acquire_initial_snapshot(
                output_dir=output_dir,
                base_url=args.base_url,
                user_agent=args.user_agent,
                client=client,
                manifest=manifest,
            )
            atomic_write_json(manifest_path, manifest)
            log(f"froze exact sitemap inventory: {len(inventory):,} problems")

        try:
            acquire_details(
                output_dir=output_dir,
                base_url=args.base_url,
                user_agent=args.user_agent,
                client=client,
                manifest=manifest,
                inventory=inventory,
                sample_limit=args.limit,
                workers=args.workers,
                batch_size=args.batch_size,
                robots_refresh_minutes=args.robots_refresh_minutes,
            )
        except KeyboardInterrupt:
            manifest["status"] = "interrupted"
            manifest["updated_at_utc"] = utc_now()
            manifest["last_error"] = "interrupted by operator"
            atomic_write_json(manifest_path, manifest)
            raise
        except BaseException as exc:
            manifest["status"] = "failed"
            manifest["updated_at_utc"] = utc_now()
            manifest["last_error"] = f"{type(exc).__name__}: {exc}"
            atomic_write_json(manifest_path, manifest)
            raise
        else:
            manifest.pop("last_error", None)
            atomic_write_json(manifest_path, manifest)
            log(
                f"snapshot {manifest['status']}: {manifest['details']['completed_count']:,} "
                f"details; SHA-256 {manifest['details']['sha256']}"
            )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run(args)
    except KeyboardInterrupt:
        log("interrupted; progress was checkpointed and the same command can resume")
        return 130
    except SnapshotError as exc:
        log(f"fatal: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
