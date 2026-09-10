#!/usr/bin/env python3
"""Deduplicate normalized arXiv source leads from immutable MathDB task shards.

This is Phase 1 only: it makes no network requests and does not download or
redistribute papers.  Its output tells an approved arXiv bulk-acquisition job
which unique source artifacts to obtain once and which MathDB tasks to match
against each artifact.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Iterator


TASK_SCHEMA = "opdp.mathdb.recovery-task.v1"
TASK_MANIFEST_SCHEMA = "opdp.mathdb.recovery-task-manifest.v1"
SOURCE_MANIFEST_SCHEMA = "opdp.mathdb.arxiv-source-manifest.v1"
SOURCE_SCHEMA = "opdp.mathdb.arxiv-source.v1"
TARGET_SCHEMA = "opdp.mathdb.arxiv-source-target.v1"
UNPARSEABLE_SCHEMA = "opdp.mathdb.arxiv-unparseable-lead.v1"
ARXIV_HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}
MODERN_ID = re.compile(r"^\d{4}\.\d{4,5}$")
LEGACY_ID = re.compile(r"^[a-z-]+/\d{7}$")
VERSION_SUFFIX = re.compile(r"^(?P<identifier>.+?)v(?P<version>[1-9]\d*)$", re.IGNORECASE)


class ManifestError(RuntimeError):
    """An invalid immutable task collection or output mismatch."""


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManifestError(f"{path} must contain a JSON object")
    return value


def task_hash(task: dict[str, Any]) -> str:
    without_hash = {key: value for key, value in task.items() if key != "task_content_sha256"}
    return hashlib.sha256(canonical_json_bytes(without_hash)).hexdigest()


def safe_shard_path(tasks_dir: Path, relative_name: Any) -> Path:
    if not isinstance(relative_name, str) or not relative_name.endswith(".jsonl"):
        raise ManifestError("canonical task manifest has an invalid shard path")
    candidate = (tasks_dir / relative_name).resolve()
    if candidate.parent != tasks_dir.resolve():
        raise ManifestError("canonical task manifest shard path escapes canonical_tasks")
    return candidate


def normalize_identifier(value: str) -> tuple[str, str | None] | None:
    identifier = value.strip().lower()
    if identifier.lower().endswith(".pdf"):
        identifier = identifier[:-4]
    identifier = identifier.removeprefix("arxiv:")
    version: str | None = None
    match = VERSION_SUFFIX.fullmatch(identifier)
    if match:
        identifier = match.group("identifier")
        version = f"v{match.group('version')}"
    if not (MODERN_ID.fullmatch(identifier) or LEGACY_ID.fullmatch(identifier)):
        return None
    return identifier, version


def path_identifier(parts: list[str]) -> str | None:
    """Extract a modern or legacy identifier from a URL path tail."""

    if not parts:
        return None
    first = parts[0].strip()
    if re.fullmatch(r"[a-z-]+", first, flags=re.IGNORECASE) and len(parts) >= 2:
        return f"{first}/{parts[1]}"
    return first


def parse_arxiv_url(value: Any) -> tuple[str, str | None] | None:
    """Return (versionless arXiv identifier, observed version) for a known URL."""

    if not isinstance(value, str):
        return None
    parsed = urllib.parse.urlparse(value.strip())
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        return None
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    identifier: str | None = None
    if hostname in ARXIV_HOSTS and len(parts) >= 2 and parts[0].lower() in {"abs", "pdf", "html", "e-print"}:
        identifier = path_identifier(parts[1:])
    elif hostname == "doi.org" and len(parts) >= 2 and parts[0].lower() == "10.48550":
        doi_suffix = "/".join(parts[1:])
        if doi_suffix.lower().startswith("arxiv."):
            identifier = doi_suffix[6:]
    elif hostname == "ar5iv.labs.arxiv.org" and len(parts) >= 2 and parts[0].lower() == "html":
        identifier = path_identifier(parts[1:])
    elif hostname == "arxiv.symmetricfunctions.com" and len(parts) >= 2 and parts[0].lower() == "paper":
        identifier = path_identifier(parts[1:])
    elif hostname == "arxiv.gg" and len(parts) >= 2 and parts[0].lower() in {"abs", "pdf"}:
        identifier = path_identifier(parts[1:])
    elif hostname == "ui.adsabs.harvard.edu" and len(parts) >= 2 and parts[0].lower() == "abs":
        identifier = path_identifier(parts[1:])
    if identifier is None:
        return None
    return normalize_identifier(identifier)


def atomic_or_identical(path: Path, write_rows: Iterator[dict[str, Any]]) -> tuple[str, int, int, bool]:
    """Stream rows to a temporary file, then atomically publish or verify bytes.

    Returns digest, bytes, row count, and whether a new immutable output was
    written.  Existing nonidentical output is refused rather than overwritten.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=path.parent)
    temporary = Path(temporary_name)
    digest = hashlib.sha256()
    byte_count = 0
    row_count = 0
    try:
        with os.fdopen(descriptor, "wb") as handle:
            for row in write_rows:
                encoded = canonical_json_bytes(row) + b"\n"
                handle.write(encoded)
                digest.update(encoded)
                byte_count += len(encoded)
                row_count += 1
            handle.flush()
            os.fsync(handle.fileno())
        value_sha = digest.hexdigest()
        if path.exists():
            if path.stat().st_size != byte_count or sha256_file(path) != value_sha:
                raise ManifestError(
                    f"existing immutable output differs: {path}. Use a new output directory "
                    "rather than rewriting a source manifest."
                )
            return value_sha, byte_count, row_count, False
        os.replace(temporary, path)
        return value_sha, byte_count, row_count, True
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def atomic_json_or_identical(path: Path, value: dict[str, Any]) -> tuple[str, bool]:
    encoded = canonical_json_bytes(value) + b"\n"
    digest = sha256_bytes(encoded)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != encoded:
            raise ManifestError(
                f"existing immutable manifest differs: {path}. Use a new output directory."
            )
        return digest, False
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
    return digest, True


def load_tasks_into_database(tasks_dir: Path, connection: sqlite3.Connection) -> tuple[int, Counter[str], int]:
    task_manifest_path = tasks_dir / "canonical_tasks_manifest.json"
    if not task_manifest_path.is_file():
        raise ManifestError(f"missing canonical task manifest: {task_manifest_path}")
    task_manifest = read_json(task_manifest_path)
    if task_manifest.get("schema") != TASK_MANIFEST_SCHEMA:
        raise ManifestError("canonical task manifest has unexpected schema")
    shards = task_manifest.get("shards")
    if not isinstance(shards, list) or not shards:
        raise ManifestError("canonical task manifest has no shards")
    connection.execute(
        "CREATE TABLE targets (arxiv_id TEXT NOT NULL, version TEXT, task_key TEXT NOT NULL, "
        "task_hash TEXT NOT NULL, mathdb_number INTEGER NOT NULL, source_field TEXT NOT NULL, "
        "declared_source_url TEXT NOT NULL, PRIMARY KEY(arxiv_id, task_key, source_field, declared_source_url))"
    )
    connection.execute("CREATE TABLE source_versions (arxiv_id TEXT NOT NULL, version TEXT, PRIMARY KEY(arxiv_id, version))")
    task_count = 0
    lead_field_counts: Counter[str] = Counter()
    unparseable = 0
    for shard in shards:
        if not isinstance(shard, dict):
            raise ManifestError("canonical task manifest has a non-object shard")
        shard_path = safe_shard_path(tasks_dir, shard.get("path"))
        if not shard_path.is_file():
            raise ManifestError(f"canonical task shard is missing: {shard_path}")
        expected_sha = shard.get("sha256")
        if sha256_file(shard_path) != expected_sha:
            raise ManifestError(f"canonical task shard hash mismatch: {shard_path}")
        shard_count = 0
        with shard_path.open("r", encoding="utf-8", newline="") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                try:
                    task = json.loads(raw_line)
                except json.JSONDecodeError as exc:
                    raise ManifestError(f"{shard_path}:{line_number}: invalid task JSON") from exc
                if not isinstance(task, dict) or task.get("schema") != TASK_SCHEMA:
                    raise ManifestError(f"{shard_path}:{line_number}: invalid task schema")
                task_key = task.get("task_key")
                task_digest = task.get("task_content_sha256")
                mathdb = task.get("mathdb")
                if not isinstance(task_key, str) or not isinstance(task_digest, str) or not isinstance(mathdb, dict):
                    raise ManifestError(f"{shard_path}:{line_number}: task identity is incomplete")
                if task_hash(task) != task_digest:
                    raise ManifestError(f"{shard_path}:{line_number}: task hash mismatch")
                number = mathdb.get("number")
                if not isinstance(number, int) or number < 1:
                    raise ManifestError(f"{shard_path}:{line_number}: task has invalid MathDB number")
                leads = task.get("source_leads")
                if not isinstance(leads, list):
                    raise ManifestError(f"{shard_path}:{line_number}: source_leads must be an array")
                for lead in leads:
                    if not isinstance(lead, dict):
                        raise ManifestError(f"{shard_path}:{line_number}: source lead must be an object")
                    url = lead.get("url")
                    parsed = parse_arxiv_url(url)
                    if parsed is None:
                        continue
                    arxiv_id, version = parsed
                    field = lead.get("field")
                    if not isinstance(field, str) or not field:
                        raise ManifestError(f"{shard_path}:{line_number}: arXiv lead has invalid field")
                    connection.execute(
                        "INSERT OR IGNORE INTO targets(arxiv_id, version, task_key, task_hash, mathdb_number, source_field, declared_source_url) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (arxiv_id, version, task_key, task_digest, number, field, url),
                    )
                    connection.execute(
                        "INSERT OR IGNORE INTO source_versions(arxiv_id, version) VALUES (?, ?)",
                        (arxiv_id, version),
                    )
                    lead_field_counts[field] += 1
                task_count += 1
                shard_count += 1
        if shard_count != shard.get("task_count"):
            raise ManifestError(f"canonical task shard count mismatch: {shard_path}")
    if task_count != task_manifest.get("record_count"):
        raise ManifestError("canonical task manifest record count does not equal shard total")
    connection.commit()
    return task_count, lead_field_counts, unparseable


def source_rows(connection: sqlite3.Connection) -> Iterator[dict[str, Any]]:
    source_cursor = connection.execute(
        "SELECT arxiv_id, COUNT(*) AS lead_count, COUNT(DISTINCT task_key) AS task_count "
        "FROM targets GROUP BY arxiv_id ORDER BY arxiv_id"
    )
    for arxiv_id, lead_count, task_count in source_cursor:
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM source_versions WHERE arxiv_id=? AND version IS NOT NULL ORDER BY CAST(SUBSTR(version, 2) AS INTEGER)",
                (arxiv_id,),
            )
        ]
        yield {
            "schema": SOURCE_SCHEMA,
            "source_key": f"arxiv:{arxiv_id}",
            "source_type": "arxiv",
            "arxiv_id": arxiv_id,
            "canonical_url": f"https://arxiv.org/abs/{arxiv_id}",
            "observed_versions": versions,
            "target_task_count": task_count,
            "source_lead_count": lead_count,
            "acquisition_policy": "acquire_once_via_approved_arxiv_bulk_channel_then_match_each_target",
        }


def target_rows(connection: sqlite3.Connection) -> Iterator[dict[str, Any]]:
    cursor = connection.execute(
        "SELECT arxiv_id, version, task_key, task_hash, mathdb_number, source_field, declared_source_url "
        "FROM targets ORDER BY arxiv_id, mathdb_number, source_field, declared_source_url"
    )
    for arxiv_id, version, task_key, task_hash, number, field, url in cursor:
        yield {
            "schema": TARGET_SCHEMA,
            "source_key": f"arxiv:{arxiv_id}",
            "arxiv_id": arxiv_id,
            "observed_version": version,
            "task_key": task_key,
            "task_content_sha256": task_hash,
            "mathdb_number": number,
            "declared_source_field": field,
            "declared_source_url": url,
        }


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = args.run_dir.resolve()
    tasks_dir = run_dir / "canonical_tasks"
    run_manifest_path = run_dir / "run.json"
    if not run_manifest_path.is_file():
        raise ManifestError(f"missing recovery run manifest: {run_manifest_path}")
    run_manifest = read_json(run_manifest_path)
    if run_manifest.get("schema") != "opdp.mathdb.recovery-run.v1":
        raise ManifestError("recovery run manifest has unexpected schema")
    task_manifest_path = tasks_dir / "canonical_tasks_manifest.json"
    task_manifest_sha = sha256_file(task_manifest_path)
    output_dir = args.output_dir.resolve() if args.output_dir else run_dir / "arxiv_source_manifest"
    if output_dir.exists() and not args.resume:
        children = [child for child in output_dir.iterdir() if child.name not in {".DS_Store"}]
        if children:
            raise ManifestError("source-manifest directory is nonempty; use --resume or choose a new output directory")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="opdp-mathdb-arxiv-source-manifest-") as temporary_dir:
        database_path = Path(temporary_dir) / "dedupe.sqlite"
        connection = sqlite3.connect(database_path)
        try:
            task_count, lead_field_counts, _ = load_tasks_into_database(tasks_dir, connection)
            unique_sources = connection.execute("SELECT COUNT(*) FROM (SELECT DISTINCT arxiv_id FROM targets)").fetchone()[0]
            target_count = connection.execute("SELECT COUNT(*) FROM targets").fetchone()[0]
            task_with_arxiv_count = connection.execute("SELECT COUNT(*) FROM (SELECT DISTINCT task_key FROM targets)").fetchone()[0]
            source_sha, source_bytes, source_rows_count, source_written = atomic_or_identical(
                output_dir / "arxiv_sources.jsonl", source_rows(connection)
            )
            target_sha, target_bytes, target_rows_count, target_written = atomic_or_identical(
                output_dir / "arxiv_targets.jsonl", target_rows(connection)
            )
        finally:
            connection.close()
    manifest = {
        "schema": SOURCE_MANIFEST_SCHEMA,
        "source": {
            "recovery_run_manifest_sha256": sha256_file(run_manifest_path),
            "canonical_task_manifest_sha256": task_manifest_sha,
            "catalog_snapshot_sha256": run_manifest.get("source", {}).get("catalog_snapshot_sha256"),
            "canonical_task_count": task_count,
        },
        "normalization": {
            "arxiv_hosts": sorted(ARXIV_HOSTS),
            "identifier_policy": "versionless modern or legacy arXiv identifier; observed vN retained separately",
            "network_requests_made": False,
        },
        "outputs": {
            "arxiv_sources": {"path": "arxiv_sources.jsonl", "sha256": source_sha, "bytes": source_bytes, "count": source_rows_count},
            "arxiv_targets": {"path": "arxiv_targets.jsonl", "sha256": target_sha, "bytes": target_bytes, "count": target_rows_count},
        },
        "summary": {
            "unique_arxiv_sources": unique_sources,
            "tasks_with_arxiv_source": task_with_arxiv_count,
            "source_to_task_lead_rows": target_count,
            "declared_source_lead_fields": dict(sorted(lead_field_counts.items())),
            "source_rows_match_unique_sources": source_rows_count == unique_sources,
            "target_rows_match_database": target_rows_count == target_count,
        },
    }
    manifest_sha, manifest_written = atomic_json_or_identical(output_dir / "arxiv_source_manifest.json", manifest)
    return {
        "status": "complete",
        "run_dir": str(run_dir),
        "output_dir": str(output_dir),
        "canonical_task_count": task_count,
        "unique_arxiv_sources": unique_sources,
        "tasks_with_arxiv_source": task_with_arxiv_count,
        "source_to_task_lead_rows": target_count,
        "arxiv_source_manifest_sha256": manifest_sha,
        "outputs_newly_written": {
            "arxiv_sources": source_written,
            "arxiv_targets": target_written,
            "manifest": manifest_written,
        },
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Create an immutable, deduplicated arXiv source manifest from MathDB recovery task shards."
    )
    value.add_argument("--run-dir", type=Path, required=True)
    value.add_argument("--output-dir", type=Path, help="default: <run-dir>/arxiv_source_manifest")
    value.add_argument("--resume", action="store_true", help="verify existing deterministic outputs and continue")
    return value


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        report = build_manifest(args)
    except (ManifestError, OSError, sqlite3.DatabaseError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
