#!/usr/bin/env python3
"""Acquire every MathDB public-list record against a frozen sitemap inventory.

MathDB's per-problem endpoint exposes a richer object but enforces a daily
unique-problem download quota.  The public list endpoint is the supported
complete-catalog surface and supplies a title, source-provided excerpt, tags,
status, chronology, author, and lightweight metadata for every listed item.

This script downloads the list endpoint in conservative, resumable pages,
checks the resulting number set against an already frozen sitemap inventory,
and emits one canonical envelope per problem in strict native-number order.
The original list item is retained unchanged under ``problem``.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import hashlib
import json
import os
import platform
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from acquire_mathdb_snapshot import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    SnapshotError,
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    read_inventory,
    read_json,
    sha256_bytes,
    sha256_file,
    utc_now,
)


TOOL_NAME = "OPDP MathDB public-catalog snapshot acquirer"
TOOL_VERSION = "1.0.0"
MANIFEST_SCHEMA = "opdp.mathdb.catalog-snapshot-manifest.v1"
ENVELOPE_SCHEMA = "opdp.mathdb.problem-summary.v1"
TEXT_BASIS = "mathdb_public_list_excerpt"
DEFAULT_RATE = 1.0
MAX_RATE = 4.0
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 100
SAFE_HEADERS = {
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


def log(message: str) -> None:
    print(f"[{utc_now()}] {message}", file=sys.stderr, flush=True)


def safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    return {key: normalized[key] for key in sorted(SAFE_HEADERS) if key in normalized}


def atomic_json_line_file(path: Path, rows: Iterator[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as handle:
            for row in rows:
                handle.write(canonical_json_bytes(row))
                handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


class CatalogClient:
    """Conservative single-process GET client with bounded short retries."""

    def __init__(self, *, user_agent: str, rate: float, timeout: float, retries: int):
        self.user_agent = user_agent
        self.interval = 1.0 / rate
        self.timeout = timeout
        self.retries = retries
        self.next_slot = 0.0

    def _wait(self) -> None:
        now = time.monotonic()
        delay = max(0.0, self.next_slot - now)
        if delay:
            time.sleep(delay)
        self.next_slot = max(now, self.next_slot) + self.interval

    def get(self, url: str) -> tuple[bytes, dict[str, str], str, int]:
        last_error: BaseException | None = None
        for attempt in range(1, self.retries + 2):
            self._wait()
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
                method="GET",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    if int(response.status) != 200:
                        raise SnapshotError(f"unexpected HTTP {response.status} for {url}")
                    return response.read(), safe_headers(response.headers), response.geturl(), attempt
            except urllib.error.HTTPError as exc:
                last_error = exc
                retry_after = exc.headers.get("Retry-After")
                if exc.code == 429 and retry_after:
                    raise SnapshotError(
                        f"HTTP 429 for {url}; server Retry-After={retry_after!r}. "
                        "The run stopped instead of waiting or evading the server limit."
                    ) from exc
                if exc.code not in {408, 425, 500, 502, 503, 504} or attempt > self.retries:
                    with contextlib.suppress(Exception):
                        detail = exc.read(512).decode("utf-8", errors="replace").strip()
                        if detail:
                            raise SnapshotError(f"HTTP {exc.code} for {url}: {detail}") from exc
                    raise SnapshotError(f"HTTP {exc.code} for {url}") from exc
            except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
                last_error = exc
                if attempt > self.retries:
                    raise SnapshotError(f"network failure for {url}: {exc}") from exc
            delay = min(30.0, float(2 ** (attempt - 1)))
            log(f"short retry {attempt}/{self.retries} after {delay:.0f}s: {url}")
            time.sleep(delay)
        raise SnapshotError(f"request failed for {url}: {last_error}")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", required=True, type=Path)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--rate", default=DEFAULT_RATE, type=float)
    parser.add_argument("--page-size", default=DEFAULT_PAGE_SIZE, type=positive_int)
    parser.add_argument("--timeout", default=DEFAULT_TIMEOUT, type=float)
    parser.add_argument("--retries", default=4, type=int)
    parser.add_argument("--break-lock", action="store_true")
    args = parser.parse_args(argv)
    args.base_url = args.base_url.rstrip("/")
    parsed = urllib.parse.urlsplit(args.base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path:
        parser.error("--base-url must be an HTTPS origin without a path")
    if not (0 < args.rate <= MAX_RATE):
        parser.error(f"--rate must be > 0 and <= {MAX_RATE}")
    if args.page_size > MAX_PAGE_SIZE:
        parser.error(f"--page-size must be <= {MAX_PAGE_SIZE}")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if not (0 <= args.retries <= 8):
        parser.error("--retries must be between 0 and 8")
    return args


@contextlib.contextmanager
def output_lock(snapshot_dir: Path, *, break_lock: bool) -> Iterator[None]:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_dir / ".acquire_mathdb_catalog_snapshot.lock"
    if break_lock and path.exists():
        path.unlink()
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise SnapshotError(f"catalog snapshot directory is locked: {path}") from exc
    try:
        os.write(
            descriptor,
            canonical_json_bytes(
                {"pid": os.getpid(), "hostname": socket.gethostname(), "started_at_utc": utc_now()}
            )
            + b"\n",
        )
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def load_frozen_inventory(snapshot_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    source_manifest_path = snapshot_dir / "mathdb_snapshot_manifest.json"
    inventory_path = snapshot_dir / "mathdb_sitemap_inventory.jsonl"
    if not source_manifest_path.exists() or not inventory_path.exists():
        raise SnapshotError("the detail-snapshot manifest and frozen sitemap inventory are required")
    source_manifest = read_json(source_manifest_path)
    inventory = read_inventory(inventory_path)
    expected = str(source_manifest.get("inventory", {}).get("sha256", "")).lower()
    actual = sha256_file(inventory_path).lower()
    if not expected or expected != actual:
        raise SnapshotError(f"frozen inventory hash mismatch: manifest={expected!r}, actual={actual}")
    expected_count = source_manifest.get("inventory", {}).get("count")
    if expected_count != len(inventory):
        raise SnapshotError(
            f"frozen inventory count mismatch: manifest={expected_count}, file={len(inventory)}"
        )
    return inventory, source_manifest, actual


def parse_page(
    body: bytes, *, offset: int, page_size: int, expected_inventory_count: int
) -> tuple[list[dict[str, Any]], int]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"invalid JSON in catalog page at offset {offset}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise SnapshotError(f"catalog page at offset {offset} has no items array")
    if payload.get("offset") != offset or payload.get("limit") != page_size:
        raise SnapshotError(f"catalog page pagination echo mismatch at offset {offset}")
    total = payload.get("total")
    if not isinstance(total, int) or total < expected_inventory_count:
        raise SnapshotError(
            f"catalog total fell below the frozen inventory at offset {offset}: "
            f"expected at least {expected_inventory_count}, got {total}"
        )
    expected_items = min(page_size, max(0, total - offset))
    if len(payload["items"]) != expected_items:
        raise SnapshotError(
            f"catalog page length mismatch at offset {offset}: "
            f"expected {expected_items}, got {len(payload['items'])}"
        )
    for item_index, item in enumerate(payload["items"]):
        if not isinstance(item, dict):
            raise SnapshotError(f"non-object catalog item at offset {offset}, index {item_index}")
        number = item.get("number")
        if not isinstance(number, int) or number < 0:
            raise SnapshotError(f"invalid problem number at offset {offset}, index {item_index}")
        if not isinstance(item.get("id"), str) or not item["id"].strip():
            raise SnapshotError(f"missing problem UUID for MathDB #{number}")
        if not isinstance(item.get("title"), str) or not item["title"].strip():
            raise SnapshotError(f"missing title for MathDB #{number}")
        if not isinstance(item.get("excerpt"), str) or not item["excerpt"].strip():
            raise SnapshotError(f"missing source-provided excerpt for MathDB #{number}")
    return payload["items"], total


def page_paths(page_dir: Path, offset: int) -> tuple[Path, Path]:
    stem = f"offset-{offset:08d}"
    return page_dir / f"{stem}.json", page_dir / f"{stem}.metadata.json"


def validate_cached_page(
    body_path: Path,
    metadata_path: Path,
    *,
    offset: int,
    page_size: int,
    inventory_count: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    body = body_path.read_bytes()
    metadata = read_json(metadata_path)
    if metadata.get("body_sha256") != sha256_bytes(body):
        raise SnapshotError(f"cached catalog page hash mismatch: {body_path}")
    if metadata.get("offset") != offset or metadata.get("page_size") != page_size:
        raise SnapshotError(f"cached catalog page metadata mismatch: {metadata_path}")
    items, _ = parse_page(
        body, offset=offset, page_size=page_size, expected_inventory_count=inventory_count
    )
    return items, metadata


def fetch_pages(
    *,
    snapshot_dir: Path,
    base_url: str,
    client: CatalogClient,
    inventory_count: int,
    page_size: int,
) -> list[dict[str, Any]]:
    page_dir = snapshot_dir / "catalog_pages"
    page_dir.mkdir(parents=True, exist_ok=True)
    page_metadata: list[dict[str, Any]] = []
    offsets = list(range(0, inventory_count, page_size))
    for page_index, offset in enumerate(offsets, start=1):
        body_path, metadata_path = page_paths(page_dir, offset)
        if body_path.exists() != metadata_path.exists():
            raise SnapshotError(f"incomplete cached page pair at offset {offset}")
        if body_path.exists():
            _, metadata = validate_cached_page(
                body_path,
                metadata_path,
                offset=offset,
                page_size=page_size,
                inventory_count=inventory_count,
            )
        else:
            query = urllib.parse.urlencode(
                {"limit": page_size, "offset": offset, "sort": "oldest"}
            )
            url = f"{base_url}/api/posts?{query}"
            body, headers, final_url, attempts = client.get(url)
            items, total = parse_page(
                body,
                offset=offset,
                page_size=page_size,
                expected_inventory_count=inventory_count,
            )
            metadata = {
                "offset": offset,
                "page_size": page_size,
                "item_count": len(items),
                "reported_total": total,
                "requested_url": url,
                "final_url": final_url,
                "retrieved_at_utc": utc_now(),
                "attempts": attempts,
                "headers": headers,
                "body_bytes": len(body),
                "body_sha256": sha256_bytes(body),
                "body_file": body_path.relative_to(snapshot_dir).as_posix(),
            }
            atomic_write_bytes(body_path, body)
            atomic_write_json(metadata_path, metadata)
        page_metadata.append(metadata)
        if page_index == 1 or page_index % 50 == 0 or page_index == len(offsets):
            log(f"catalog pages {page_index:,}/{len(offsets):,} complete")
    return page_metadata


def build_index(
    *,
    snapshot_dir: Path,
    page_metadata: list[dict[str, Any]],
    page_size: int,
    inventory_count: int,
    inventory_numbers: set[int],
) -> Path:
    database_path = snapshot_dir / ".mathdb_catalog_index.sqlite3"
    with contextlib.suppress(FileNotFoundError):
        database_path.unlink()
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "CREATE TABLE problems ("
            "number INTEGER PRIMARY KEY, uuid TEXT NOT NULL UNIQUE, item_json TEXT NOT NULL, "
            "page_offset INTEGER NOT NULL, page_sha256 TEXT NOT NULL, retrieved_at_utc TEXT NOT NULL, "
            "release_header TEXT)"
        )
        for metadata in page_metadata:
            offset = int(metadata["offset"])
            body_path, _ = page_paths(snapshot_dir / "catalog_pages", offset)
            items, _ = parse_page(
                body_path.read_bytes(),
                offset=offset,
                page_size=page_size,
                expected_inventory_count=inventory_count,
            )
            rows = [
                (
                    item["number"],
                    item["id"],
                    canonical_json_bytes(item).decode("utf-8"),
                    offset,
                    metadata["body_sha256"],
                    metadata["retrieved_at_utc"],
                    metadata.get("headers", {}).get("x-mathdb-release"),
                )
                for item in items
                if item["number"] in inventory_numbers
            ]
            try:
                connection.executemany("INSERT INTO problems VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            except sqlite3.IntegrityError as exc:
                raise SnapshotError(f"duplicate number or UUID in catalog pages: {exc}") from exc
        connection.commit()
        count = connection.execute("SELECT COUNT(*) FROM problems").fetchone()[0]
        if count != inventory_count:
            raise SnapshotError(f"catalog index count mismatch: expected {inventory_count}, got {count}")
    except BaseException:
        connection.close()
        with contextlib.suppress(FileNotFoundError):
            database_path.unlink()
        raise
    connection.close()
    return database_path


def write_envelopes(
    *,
    snapshot_dir: Path,
    inventory: list[dict[str, Any]],
    database_path: Path,
    output_path: Path,
) -> None:
    connection = sqlite3.connect(database_path)

    def rows() -> Iterator[dict[str, Any]]:
        try:
            for inventory_item in inventory:
                number = int(inventory_item["number"])
                found = connection.execute(
                    "SELECT item_json, page_offset, page_sha256, retrieved_at_utc, release_header "
                    "FROM problems WHERE number = ?",
                    (number,),
                ).fetchone()
                if found is None:
                    raise SnapshotError(f"MathDB #{number} appears in sitemap but not catalog pages")
                item = json.loads(found[0])
                yield {
                    "schema": ENVELOPE_SCHEMA,
                    "snapshot": {
                        "text_basis": TEXT_BASIS,
                        "statement_completeness": "source_provided_excerpt_not_guaranteed_complete",
                        "inventory_number": number,
                        "api_url": f"https://mathdb.com/api/posts?limit=100&offset={found[1]}&sort=oldest",
                        "page_offset": found[1],
                        "page_response_sha256": found[2],
                        "retrieved_at_utc": found[3],
                        "x_mathdb_release": found[4],
                        "inventory_url": inventory_item["url"],
                        "inventory_lastmod": inventory_item.get("lastmod"),
                        "inventory_sitemap_file": inventory_item.get("sitemap_file"),
                        "problem_canonical_sha256": sha256_bytes(canonical_json_bytes(item)),
                    },
                    "problem": item,
                }
        finally:
            connection.close()

    atomic_json_line_file(output_path, rows())


def validate_envelopes(
    *, output_path: Path, inventory: list[dict[str, Any]]
) -> tuple[int, str, str, collections.Counter[str]]:
    expected_numbers = [int(row["number"]) for row in inventory]
    count = 0
    previous = -1
    uuids: set[str] = set()
    release_counts: collections.Counter[str] = collections.Counter()
    first_retrieved = ""
    last_retrieved = ""
    with output_path.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            try:
                envelope = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SnapshotError(f"invalid output envelope at line {line_number}: {exc}") from exc
            if envelope.get("schema") != ENVELOPE_SCHEMA:
                raise SnapshotError(f"wrong envelope schema at line {line_number}")
            problem = envelope.get("problem")
            snapshot = envelope.get("snapshot")
            if not isinstance(problem, dict) or not isinstance(snapshot, dict):
                raise SnapshotError(f"invalid envelope shape at line {line_number}")
            number = problem.get("number")
            if number != expected_numbers[count]:
                raise SnapshotError(
                    f"output/inventory number mismatch at line {line_number}: "
                    f"expected {expected_numbers[count]}, got {number}"
                )
            if number <= previous:
                raise SnapshotError(f"output is not strictly number-sorted at line {line_number}")
            previous = number
            uuid = problem.get("id")
            if uuid in uuids:
                raise SnapshotError(f"duplicate UUID at line {line_number}: {uuid}")
            uuids.add(uuid)
            if snapshot.get("text_basis") != TEXT_BASIS:
                raise SnapshotError(f"wrong text basis at line {line_number}")
            expected_hash = sha256_bytes(canonical_json_bytes(problem))
            if snapshot.get("problem_canonical_sha256") != expected_hash:
                raise SnapshotError(f"problem canonical hash mismatch at line {line_number}")
            retrieved = str(snapshot.get("retrieved_at_utc") or "")
            if retrieved and (not first_retrieved or retrieved < first_retrieved):
                first_retrieved = retrieved
            if retrieved and (not last_retrieved or retrieved > last_retrieved):
                last_retrieved = retrieved
            release_counts[str(snapshot.get("x_mathdb_release") or "<missing>")] += 1
            count += 1
    if count != len(inventory):
        raise SnapshotError(f"output count mismatch: expected {len(inventory)}, got {count}")
    return count, first_retrieved, last_retrieved, release_counts


def run(args: argparse.Namespace) -> None:
    snapshot_dir = args.snapshot_dir.resolve()
    with output_lock(snapshot_dir, break_lock=args.break_lock):
        inventory, source_manifest, inventory_sha = load_frozen_inventory(snapshot_dir)
        inventory_count = len(inventory)
        output_path = snapshot_dir / "mathdb_problem_summaries.jsonl"
        manifest_path = snapshot_dir / "mathdb_catalog_manifest.json"
        client = CatalogClient(
            user_agent=args.user_agent,
            rate=args.rate,
            timeout=args.timeout,
            retries=args.retries,
        )
        manifest: dict[str, Any] = {
            "schema": MANIFEST_SCHEMA,
            "status": "acquiring_catalog_pages",
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
                "timeout_seconds": args.timeout,
                "maximum_short_retries": args.retries,
                "long_retry_after_policy": "stop_without_waiting_or_evasion",
                "robots_url": f"{args.base_url}/robots.txt",
                "terms_url": f"{args.base_url}/terms",
            },
            "inventory": {
                "path": "mathdb_sitemap_inventory.jsonl",
                "sha256": inventory_sha,
                "count": inventory_count,
                "minimum_number": int(inventory[0]["number"]),
                "maximum_number": int(inventory[-1]["number"]),
                "frozen_at_utc": source_manifest.get("inventory", {}).get("frozen_at_utc"),
                "source_manifest_path": "mathdb_snapshot_manifest.json",
                "source_manifest_sha256": sha256_file(snapshot_dir / "mathdb_snapshot_manifest.json"),
            },
            "catalog": {
                "endpoint": f"{args.base_url}/api/posts",
                "sort": "oldest",
                "page_size": args.page_size,
                "text_basis": TEXT_BASIS,
                "source_record_semantics": "exact parsed public-list item",
            },
        }
        atomic_write_json(manifest_path, manifest)
        page_metadata = fetch_pages(
            snapshot_dir=snapshot_dir,
            base_url=args.base_url,
            client=client,
            inventory_count=inventory_count,
            page_size=args.page_size,
        )
        manifest["status"] = "indexing_catalog"
        manifest["updated_at_utc"] = utc_now()
        manifest["catalog"]["page_count"] = len(page_metadata)
        manifest["catalog"]["page_metadata"] = page_metadata
        atomic_write_json(manifest_path, manifest)
        database_path = build_index(
            snapshot_dir=snapshot_dir,
            page_metadata=page_metadata,
            page_size=args.page_size,
            inventory_count=inventory_count,
            inventory_numbers={int(item["number"]) for item in inventory},
        )
        try:
            write_envelopes(
                snapshot_dir=snapshot_dir,
                inventory=inventory,
                database_path=database_path,
                output_path=output_path,
            )
        finally:
            with contextlib.suppress(FileNotFoundError):
                database_path.unlink()
        count, first_retrieved, last_retrieved, release_counts = validate_envelopes(
            output_path=output_path, inventory=inventory
        )
        manifest["status"] = "complete"
        manifest["updated_at_utc"] = utc_now()
        manifest["catalog"].update(
            {
                "status": "complete",
                "completed_count": count,
                "output_path": output_path.name,
                "sha256": sha256_file(output_path),
                "bytes": output_path.stat().st_size,
                "first_retrieved_at_utc": first_retrieved,
                "last_retrieved_at_utc": last_retrieved,
                "api_release_header_counts": dict(sorted(release_counts.items())),
                "validation": {
                    "strict_number_order": True,
                    "unique_problem_numbers": True,
                    "unique_problem_uuids": True,
                    "exact_sitemap_number_set": True,
                    "nonempty_title_and_excerpt": True,
                    "canonical_problem_hashes_match": True,
                },
            }
        )
        atomic_write_json(manifest_path, manifest)
        log(
            f"complete catalog snapshot: {count:,} records; "
            f"SHA-256 {manifest['catalog']['sha256']}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        run(args)
    except KeyboardInterrupt:
        log("interrupted; cached complete pages can be reused by rerunning the same command")
        return 130
    except SnapshotError as exc:
        log(f"fatal: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
