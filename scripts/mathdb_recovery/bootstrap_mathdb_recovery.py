#!/usr/bin/env python3
"""Build an immutable, resumable canonical task index for MathDB recovery.

The v1.7 OPDP export intentionally retains only MathDB's public-list excerpt.
This tool does not alter that export or infer a fuller statement.  It binds a
future recovery ledger to the exact frozen public-catalog JSONL and emits small
atomically-written task shards that can safely be resumed after interruption.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Iterable, Iterator


TOOL_NAME = "OPDP MathDB recovery bootstrapper"
TOOL_VERSION = "0.1.0"
TASK_SCHEMA = "opdp.mathdb.recovery-task.v1"
RUN_SCHEMA = "opdp.mathdb.recovery-run.v1"
TASK_MANIFEST_SCHEMA = "opdp.mathdb.recovery-task-manifest.v1"
CATALOG_ENVELOPE_SCHEMA = "opdp.mathdb.problem-summary.v1"
CATALOG_MANIFEST_SCHEMA = "opdp.mathdb.catalog-snapshot-manifest.v1"
DEFAULT_SHARD_SIZE = 1_000
URL_FIELDS = (
    "source_url",
    "first_stated_year_source_url",
    "documented_by_source_url",
    "reference_url",
    "paper_url",
)


class BootstrapError(RuntimeError):
    """An immutable-input or output-integrity failure."""


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
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".partial", dir=path.parent
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()


def write_immutable(path: Path, value: bytes, label: str) -> bool:
    """Write once, or verify byte equality on a resumed run.

    Returns True when a new file was written.  An existing non-identical file
    is always an error: the recovery corpus must never be silently rebased.
    """

    if path.exists():
        observed = path.read_bytes()
        if observed != value:
            raise BootstrapError(
                f"existing {label} differs from the deterministic value: {path}. "
                "Use a new --run-dir rather than mutating a recovery run."
            )
        return False
    atomic_write_bytes(path, value)
    return True


def parse_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BootstrapError(f"{path} must contain a JSON object")
    return value


def valid_http_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urllib.parse.urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def recovery_route(url: str) -> str:
    hostname = (urllib.parse.urlparse(url).hostname or "").lower()
    if hostname == "arxiv.org" or hostname.endswith(".arxiv.org"):
        return "arxiv_bulk"
    if hostname in {"aimath.org", "www.aimath.org", "amathr.org", "www.amathr.org", "alglog.org"}:
        return "collection_native"
    if hostname == "github.com" and "/teorth/erdosproblems" in url.lower():
        return "collection_native"
    return "direct_source"


def source_leads(problem: dict[str, Any]) -> list[dict[str, str]]:
    leads: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for field in URL_FIELDS:
        raw_url = problem.get(field)
        if not valid_http_url(raw_url):
            continue
        url = str(raw_url).strip()
        key = (field, url)
        if key in seen:
            continue
        seen.add(key)
        leads.append({"field": field, "url": url, "route": recovery_route(url)})
    return leads


def required_string(mapping: dict[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BootstrapError(f"{context}: {key} must be a nonempty string")
    return value


def required_positive_int(mapping: dict[str, Any], key: str, context: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise BootstrapError(f"{context}: {key} must be a positive integer")
    return value


def make_task(envelope: dict[str, Any], line_number: int) -> dict[str, Any]:
    context = f"snapshot line {line_number}"
    if envelope.get("schema") != CATALOG_ENVELOPE_SCHEMA:
        raise BootstrapError(
            f"{context}: expected schema {CATALOG_ENVELOPE_SCHEMA!r}, "
            f"found {envelope.get('schema')!r}"
        )
    problem = envelope.get("problem")
    snapshot = envelope.get("snapshot")
    if not isinstance(problem, dict) or not isinstance(snapshot, dict):
        raise BootstrapError(f"{context}: envelope.problem and envelope.snapshot must be objects")
    number = required_positive_int(problem, "number", context)
    identifier = required_string(problem, "id", context)
    title = required_string(problem, "title", context)
    excerpt = required_string(problem, "excerpt", context)
    source_record_sha256 = sha256_bytes(canonical_json_bytes(problem))
    stored_problem_sha = snapshot.get("problem_canonical_sha256")
    if stored_problem_sha is not None and stored_problem_sha != source_record_sha256:
        raise BootstrapError(
            f"{context}: snapshot.problem_canonical_sha256 does not match problem"
        )
    inventory_number = snapshot.get("inventory_number")
    if inventory_number != number:
        raise BootstrapError(
            f"{context}: snapshot.inventory_number={inventory_number!r} does not match "
            f"problem.number={number}"
        )
    problem_url = snapshot.get("inventory_url")
    if not valid_http_url(problem_url):
        problem_url = f"https://mathdb.com/p/{number}"
    tags = [str(value) for value in problem.get("tags", []) if isinstance(value, str)]
    task_without_hash: dict[str, Any] = {
        "schema": TASK_SCHEMA,
        "task_key": f"mathdb:{number}",
        "mathdb": {
            "number": number,
            "uuid": identifier,
            "title": title,
            "excerpt": excerpt,
            "native_status": problem.get("status") if isinstance(problem.get("status"), str) else None,
            "problem_url": str(problem_url),
            "tags": tags,
        },
        "canonical_source": {
            "catalog_envelope_schema": CATALOG_ENVELOPE_SCHEMA,
            "snapshot_line": line_number,
            "source_record_sha256": source_record_sha256,
            "source_envelope_sha256": sha256_bytes(canonical_json_bytes(envelope)),
            "snapshot": snapshot,
        },
        "source_leads": source_leads(problem),
        "recovery_policy": {
            "initial_classification": "unresolved",
            "automatic_rescore_allowed": False,
        },
    }
    task = {
        "schema": task_without_hash["schema"],
        "task_key": task_without_hash["task_key"],
        "task_content_sha256": sha256_bytes(canonical_json_bytes(task_without_hash)),
        "mathdb": task_without_hash["mathdb"],
        "canonical_source": task_without_hash["canonical_source"],
        "source_leads": task_without_hash["source_leads"],
        "recovery_policy": task_without_hash["recovery_policy"],
    }
    return task


def read_envelopes(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    try:
        handle = path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        raise BootstrapError(f"cannot open snapshot {path}: {exc}") from exc
    with handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if not raw_line.strip():
                raise BootstrapError(f"snapshot line {line_number}: blank lines are not allowed")
            try:
                value = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise BootstrapError(f"snapshot line {line_number}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                raise BootstrapError(f"snapshot line {line_number}: JSON value must be an object")
            yield line_number, value


def prepare_run_manifest(
    snapshot: Path,
    snapshot_sha256: str,
    source_manifest: Path | None,
    source_manifest_sha256: str | None,
    source_manifest_value: dict[str, Any] | None,
    shard_size: int,
    max_records: int | None,
) -> dict[str, Any]:
    catalog: dict[str, Any] | None = None
    if source_manifest_value is not None:
        if source_manifest_value.get("schema") != CATALOG_MANIFEST_SCHEMA:
            raise BootstrapError(
                f"source manifest must have schema {CATALOG_MANIFEST_SCHEMA!r}"
            )
        catalog_value = source_manifest_value.get("catalog")
        if not isinstance(catalog_value, dict):
            raise BootstrapError("source manifest lacks a catalog object")
        catalog = catalog_value
        expected_sha = catalog.get("sha256")
        if expected_sha != snapshot_sha256:
            raise BootstrapError(
                "catalog snapshot SHA-256 does not match its source manifest: "
                f"manifest={expected_sha!r}, actual={snapshot_sha256!r}"
            )
        if max_records is None and catalog.get("status") != "complete":
            raise BootstrapError("a complete recovery run requires catalog.status='complete'")
    scope = "complete" if max_records is None else "sample"
    return {
        "schema": RUN_SCHEMA,
        "tool": {"name": TOOL_NAME, "version": TOOL_VERSION},
        "scope": scope,
        "task_schema": TASK_SCHEMA,
        "task_hash_definition": "sha256(canonical UTF-8 JSON task object with task_content_sha256 omitted)",
        "source": {
            "catalog_snapshot_filename": snapshot.name,
            "catalog_snapshot_sha256": snapshot_sha256,
            "catalog_manifest_filename": source_manifest.name if source_manifest else None,
            "catalog_manifest_sha256": source_manifest_sha256,
            "catalog_manifest_declared_count": catalog.get("completed_count") if catalog else None,
            "catalog_manifest_declared_text_basis": catalog.get("text_basis") if catalog else None,
        },
        "output": {"shard_size": shard_size, "max_records": max_records},
        "immutability": {
            "canonical_tasks": "write-once deterministic shards; existing bytes must match on resume",
            "recovery_events": "append-only; corrections are new events with supersedes_event_id",
            "release_mutation": "prohibited by this tool",
        },
    }


def make_tasks_manifest(
    run_manifest_sha: str,
    snapshot_sha: str,
    count: int,
    first_number: int | None,
    last_number: int | None,
    shards: list[dict[str, Any]],
    scope: str,
) -> dict[str, Any]:
    return {
        "schema": TASK_MANIFEST_SCHEMA,
        "task_schema": TASK_SCHEMA,
        "run_manifest_sha256": run_manifest_sha,
        "catalog_snapshot_sha256": snapshot_sha,
        "scope": scope,
        "record_count": count,
        "first_mathdb_number": first_number,
        "last_mathdb_number": last_number,
        "shards": shards,
    }


def flush_shard(
    output_dir: Path,
    ordinal_start: int,
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    if not tasks:
        raise AssertionError("cannot flush an empty task shard")
    ordinal_end = ordinal_start + len(tasks) - 1
    filename = f"task-{ordinal_start:09d}-{ordinal_end:09d}.jsonl"
    content = b"".join(canonical_json_bytes(task) + b"\n" for task in tasks)
    path = output_dir / filename
    written = write_immutable(path, content, "canonical task shard")
    if written:
        log(f"wrote {filename} ({len(tasks):,} tasks)")
    return {
        "path": filename,
        "sha256": sha256_bytes(content),
        "bytes": len(content),
        "task_count": len(tasks),
        "ordinal_start": ordinal_start,
        "ordinal_end": ordinal_end,
        "first_mathdb_number": tasks[0]["mathdb"]["number"],
        "last_mathdb_number": tasks[-1]["mathdb"]["number"],
    }


def ensure_fresh_or_resumable_run(run_dir: Path, resume: bool) -> None:
    if not run_dir.exists():
        run_dir.mkdir(parents=True)
        return
    existing = [path for path in run_dir.iterdir() if path.name not in {".DS_Store"}]
    if existing and not resume:
        raise BootstrapError(
            f"run directory is not empty: {run_dir}. Pass --resume to verify and continue it, "
            "or choose a new directory."
        )


def bootstrap(args: argparse.Namespace) -> dict[str, Any]:
    snapshot = args.snapshot.resolve()
    if not snapshot.is_file():
        raise BootstrapError(f"snapshot does not exist: {snapshot}")
    if args.shard_size < 1:
        raise BootstrapError("--shard-size must be positive")
    if args.max_records is not None and args.max_records < 1:
        raise BootstrapError("--max-records must be positive when supplied")
    run_dir = args.run_dir.resolve()
    ensure_fresh_or_resumable_run(run_dir, args.resume)
    source_manifest_path = args.source_manifest.resolve() if args.source_manifest else None
    source_manifest_value = parse_json(source_manifest_path) if source_manifest_path else None
    source_manifest_sha = sha256_file(source_manifest_path) if source_manifest_path else None
    snapshot_sha = sha256_file(snapshot)
    run_manifest = prepare_run_manifest(
        snapshot,
        snapshot_sha,
        source_manifest_path,
        source_manifest_sha,
        source_manifest_value,
        args.shard_size,
        args.max_records,
    )
    run_bytes = canonical_json_bytes(run_manifest) + b"\n"
    write_immutable(run_dir / "run.json", run_bytes, "run manifest")
    run_manifest_sha = sha256_bytes(run_bytes)

    tasks_dir = run_dir / "canonical_tasks"
    task_buffer: list[dict[str, Any]] = []
    shard_records: list[dict[str, Any]] = []
    count = 0
    previous_number = 0
    first_number: int | None = None
    last_number: int | None = None
    shard_ordinal_start = 1
    for line_number, envelope in read_envelopes(snapshot):
        task = make_task(envelope, line_number)
        number = task["mathdb"]["number"]
        if number <= previous_number:
            raise BootstrapError(
                f"snapshot line {line_number}: MathDB number {number} is not strictly "
                f"greater than previous {previous_number}"
            )
        previous_number = number
        first_number = number if first_number is None else first_number
        last_number = number
        task_buffer.append(task)
        count += 1
        if len(task_buffer) == args.shard_size:
            shard_records.append(flush_shard(tasks_dir, shard_ordinal_start, task_buffer))
            shard_ordinal_start += len(task_buffer)
            task_buffer = []
        if args.max_records is not None and count >= args.max_records:
            break
    if task_buffer:
        shard_records.append(flush_shard(tasks_dir, shard_ordinal_start, task_buffer))
    if count == 0:
        raise BootstrapError("snapshot has no catalog records")
    if args.max_records is None and source_manifest_value is not None:
        declared_count = source_manifest_value["catalog"].get("completed_count")
        if declared_count != count:
            raise BootstrapError(
                f"snapshot contains {count:,} records but source manifest declares "
                f"{declared_count!r}"
            )
    tasks_manifest = make_tasks_manifest(
        run_manifest_sha,
        snapshot_sha,
        count,
        first_number,
        last_number,
        shard_records,
        run_manifest["scope"],
    )
    tasks_bytes = canonical_json_bytes(tasks_manifest) + b"\n"
    write_immutable(tasks_dir / "canonical_tasks_manifest.json", tasks_bytes, "task manifest")
    return {
        "status": "complete",
        "run_dir": str(run_dir),
        "scope": run_manifest["scope"],
        "task_count": count,
        "shard_count": len(shard_records),
        "catalog_snapshot_sha256": snapshot_sha,
        "canonical_tasks_manifest_sha256": sha256_bytes(tasks_bytes),
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Create immutable MathDB recovery tasks from a frozen public-catalog JSONL."
    )
    value.add_argument("--snapshot", type=Path, required=True, help="complete MathDB catalog JSONL")
    value.add_argument(
        "--source-manifest",
        type=Path,
        help="optional opdp.mathdb.catalog-snapshot-manifest.v1 to bind hash/count",
    )
    value.add_argument("--run-dir", type=Path, required=True, help="new or resumable recovery run directory")
    value.add_argument("--shard-size", type=int, default=DEFAULT_SHARD_SIZE)
    value.add_argument("--max-records", type=int, help="create a non-production sample run only")
    value.add_argument("--resume", action="store_true", help="verify existing immutable outputs and continue")
    return value


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        report = bootstrap(args)
    except BootstrapError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
