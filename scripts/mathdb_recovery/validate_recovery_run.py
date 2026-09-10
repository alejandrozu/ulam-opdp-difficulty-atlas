#!/usr/bin/env python3
"""Stream-validate a MathDB recovery run and its OPDP rescore gates.

The validator is intentionally read-only with respect to canonical task shards
and the append-only ledger.  It creates only a replaceable SQLite task cache
through the sibling event writer and an OS-temporary database for this audit.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from append_recovery_event import (
    EVENT_SCHEMA,
    EVENT_TYPES,
    EventError,
    ensure_task_index,
    read_json,
    sha256_file,
    validate_event,
)


VALIDATOR_NAME = "OPDP MathDB recovery-run validator"
VALIDATOR_VERSION = "0.1.0"
MAX_EXAMPLES = 100


class ValidationError(RuntimeError):
    """A run layout or event-ledger problem."""


class Findings:
    def __init__(self, limit: int = MAX_EXAMPLES) -> None:
        self.limit = limit
        self.total = 0
        self.examples: list[dict[str, Any]] = []

    def add(self, code: str, message: str, **context: Any) -> None:
        self.total += 1
        if len(self.examples) < self.limit:
            self.examples.append({"code": code, "message": message, **context})


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".partial", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def insert_event(connection: sqlite3.Connection, event: dict[str, Any], line: int) -> None:
    payload = json.dumps(event["payload"], ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    connection.execute(
        "INSERT INTO events(event_id, task_key, task_hash, event_type, sequence, occurred_at_utc, "
        "actor_kind, supersedes_event_id, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            event["event_id"],
            event["task_key"],
            event["task_content_sha256"],
            event["event_type"],
            line,
            event["occurred_at_utc"],
            event["actor"]["kind"],
            event.get("supersedes_event_id"),
            payload,
        ),
    )


def read_ledger_into_database(
    ledger: Path, task_index: Path, connection: sqlite3.Connection, findings: Findings
) -> tuple[int, Counter[str]]:
    task_connection = sqlite3.connect(f"file:{task_index.as_posix()}?mode=ro", uri=True)
    event_count = 0
    event_counts: Counter[str] = Counter()
    try:
        if not ledger.exists():
            return event_count, event_counts
        with ledger.open("r", encoding="utf-8", newline="") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                if not raw_line.strip():
                    findings.add("LEDGER_BLANK_LINE", "blank ledger line", line=line_number)
                    continue
                try:
                    raw_event = json.loads(raw_line)
                    event = validate_event(raw_event)
                except (json.JSONDecodeError, EventError) as exc:
                    findings.add("LEDGER_INVALID_EVENT", str(exc), line=line_number)
                    continue
                if event.get("schema") != EVENT_SCHEMA or event.get("event_type") not in EVENT_TYPES:
                    findings.add("LEDGER_EVENT_SCHEMA", "unexpected event schema/type", line=line_number)
                    continue
                expected = task_connection.execute(
                    "SELECT task_hash FROM tasks WHERE task_key=?", (event["task_key"],)
                ).fetchone()
                if expected is None:
                    findings.add(
                        "EVENT_UNKNOWN_TASK",
                        "event task key is absent from canonical task collection",
                        line=line_number,
                        task_key=event["task_key"],
                    )
                    continue
                if expected[0] != event["task_content_sha256"]:
                    findings.add(
                        "EVENT_TASK_HASH_MISMATCH",
                        "event task hash does not bind to canonical task content",
                        line=line_number,
                        task_key=event["task_key"],
                    )
                    continue
                try:
                    insert_event(connection, event, line_number)
                except sqlite3.IntegrityError:
                    findings.add(
                        "DUPLICATE_EVENT_ID",
                        "event_id appears more than once in ledger",
                        line=line_number,
                        event_id=event["event_id"],
                    )
                    continue
                event_count += 1
                event_counts[event["event_type"]] += 1
        connection.commit()
    finally:
        task_connection.close()
    return event_count, event_counts


def payload(row: sqlite3.Row) -> dict[str, Any]:
    parsed = json.loads(row["payload_json"])
    if not isinstance(parsed, dict):
        raise ValidationError("stored event payload unexpectedly is not an object")
    return parsed


def latest_events(connection: sqlite3.Connection, event_type: str) -> Iterable[sqlite3.Row]:
    return connection.execute(
        "SELECT e.* FROM events AS e "
        "JOIN (SELECT task_key, MAX(sequence) AS max_sequence FROM events "
        "WHERE event_type=? GROUP BY task_key) AS latest "
        "ON e.task_key=latest.task_key AND e.sequence=latest.max_sequence "
        "WHERE e.event_type=? ORDER BY e.task_key",
        (event_type, event_type),
    )


def latest_event_for_task(connection: sqlite3.Connection, task_key: str, event_type: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM events WHERE task_key=? AND event_type=? ORDER BY sequence DESC LIMIT 1",
        (task_key, event_type),
    ).fetchone()


def validate_supersessions(connection: sqlite3.Connection, findings: Findings) -> None:
    cursor = connection.execute(
        "SELECT child.event_id AS child_id, child.task_key AS child_task, child.event_type AS child_type, "
        "parent.event_id AS parent_id, parent.task_key AS parent_task, parent.event_type AS parent_type "
        "FROM events AS child LEFT JOIN events AS parent ON child.supersedes_event_id=parent.event_id "
        "WHERE child.supersedes_event_id IS NOT NULL"
    )
    for row in cursor:
        if row["parent_id"] is None:
            findings.add(
                "MISSING_SUPERSEDED_EVENT",
                "supersedes_event_id does not exist in ledger",
                event_id=row["child_id"],
            )
        elif row["child_task"] != row["parent_task"] or row["child_type"] != row["parent_type"]:
            findings.add(
                "INVALID_SUPERSESSION",
                "an event may supersede only an event of the same task and type",
                event_id=row["child_id"],
                supersedes_event_id=row["parent_id"],
            )


def evaluate_rescore_gates(connection: sqlite3.Connection, findings: Findings) -> dict[str, int]:
    metrics = Counter()
    for decision in latest_events(connection, "rescore_decision"):
        decision_payload = payload(decision)
        if not decision_payload.get("eligible"):
            metrics["latest_not_eligible"] += 1
            continue
        metrics["latest_eligible_claims"] += 1
        recovery = latest_event_for_task(connection, decision["task_key"], "recovery_outcome")
        openness = latest_event_for_task(connection, decision["task_key"], "openness_check")
        invalid = False
        if recovery is None:
            findings.add("RESCORE_NO_RECOVERY", "eligible rescore decision has no recovery outcome", task_key=decision["task_key"], event_id=decision["event_id"])
            invalid = True
        if openness is None:
            findings.add("RESCORE_NO_OPENNESS_CHECK", "eligible rescore decision has no openness check", task_key=decision["task_key"], event_id=decision["event_id"])
            invalid = True
        basis_ids = set(decision_payload.get("basis_event_ids", []))
        if decision["actor_kind"] != "human":
            findings.add("RESCORE_NO_HUMAN_SIGNOFF", "eligible rescore decision must be made by a human curator", task_key=decision["task_key"], event_id=decision["event_id"])
            invalid = True
        if recovery is not None:
            recovery_payload = payload(recovery)
            statement = recovery_payload.get("publishable_statement")
            classification = recovery_payload.get("classification")
            if classification not in {"exact_source", "faithful_normalization"}:
                findings.add("RESCORE_UNRELIABLE_RECOVERY", "eligible decision requires exact_source or faithful_normalization", task_key=decision["task_key"], event_id=decision["event_id"])
                invalid = True
            if not isinstance(statement, dict) or statement.get("self_contained") is not True:
                findings.add("RESCORE_NOT_SELF_CONTAINED", "eligible decision requires a self-contained publishable statement", task_key=decision["task_key"], event_id=decision["event_id"])
                invalid = True
            if recovery["event_id"] not in basis_ids:
                findings.add("RESCORE_MISSING_RECOVERY_BASIS", "eligible decision must cite current recovery event", task_key=decision["task_key"], event_id=decision["event_id"])
                invalid = True
        if openness is not None:
            openness_payload = payload(openness)
            if openness_payload.get("status") != "verified_open":
                findings.add("RESCORE_OPENNESS_NOT_VERIFIED", "eligible decision requires current verified_open outcome", task_key=decision["task_key"], event_id=decision["event_id"])
                invalid = True
            if openness["event_id"] not in basis_ids:
                findings.add("RESCORE_MISSING_OPENNESS_BASIS", "eligible decision must cite current openness-check event", task_key=decision["task_key"], event_id=decision["event_id"])
                invalid = True
        if invalid:
            metrics["eligible_claims_rejected"] += 1
        else:
            metrics["eligible_for_future_rescore"] += 1
    return dict(metrics)


def summarize_latest_outcomes(connection: sqlite3.Connection, event_type: str, nested_key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in latest_events(connection, event_type):
        value = payload(row).get(nested_key)
        counts[str(value)] += 1
    return dict(sorted(counts.items()))


def validate_run(run_dir: Path) -> dict[str, Any]:
    if not run_dir.is_dir():
        raise ValidationError(f"run directory does not exist: {run_dir}")
    run_manifest_path = run_dir / "run.json"
    if not run_manifest_path.is_file():
        raise ValidationError(f"missing run manifest: {run_manifest_path}")
    run_manifest = read_json(run_manifest_path)
    if run_manifest.get("schema") != "opdp.mathdb.recovery-run.v1":
        raise ValidationError("run manifest has unexpected schema")
    findings = Findings()
    task_index = ensure_task_index(run_dir)
    task_connection = sqlite3.connect(f"file:{task_index.as_posix()}?mode=ro", uri=True)
    try:
        task_count = task_connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    finally:
        task_connection.close()
    ledger = run_dir / "recovery_events.jsonl"
    with tempfile.TemporaryDirectory(prefix="opdp-mathdb-recovery-validate-") as temporary_dir:
        event_db = Path(temporary_dir) / "events.sqlite"
        connection = sqlite3.connect(event_db)
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE events (event_id TEXT PRIMARY KEY, task_key TEXT NOT NULL, task_hash TEXT NOT NULL, "
            "event_type TEXT NOT NULL, sequence INTEGER NOT NULL, occurred_at_utc TEXT NOT NULL, "
            "actor_kind TEXT NOT NULL, supersedes_event_id TEXT, payload_json TEXT NOT NULL)"
        )
        connection.execute("CREATE INDEX events_task_type_sequence ON events(task_key, event_type, sequence)")
        event_count, event_counts = read_ledger_into_database(ledger, task_index, connection, findings)
        validate_supersessions(connection, findings)
        rescore_metrics = evaluate_rescore_gates(connection, findings)
        recovery_labels = summarize_latest_outcomes(connection, "recovery_outcome", "classification")
        openness_statuses = summarize_latest_outcomes(connection, "openness_check", "status")
        connection.close()
    report = {
        "status": "pass" if findings.total == 0 else "fail",
        "validator": {"name": VALIDATOR_NAME, "version": VALIDATOR_VERSION},
        "streaming": {
            "canonical_task_shards": True,
            "ledger": True,
            "temporary_sqlite_for_joins": True,
            "full_corpus_materialized_in_memory": False,
        },
        "run": {
            "path": str(run_dir),
            "scope": run_manifest.get("scope"),
            "catalog_snapshot_sha256": run_manifest.get("source", {}).get("catalog_snapshot_sha256"),
            "canonical_task_count": task_count,
            "task_lookup_sha256": sha256_file(task_index),
        },
        "ledger": {
            "path": str(ledger),
            "exists": ledger.exists(),
            "sha256": sha256_file(ledger) if ledger.exists() else None,
            "valid_event_lines": event_count,
            "event_counts": dict(sorted(event_counts.items())),
            "latest_recovery_classifications": recovery_labels,
            "latest_openness_statuses": openness_statuses,
            "rescore_gate": rescore_metrics,
        },
        "error_count": findings.total,
        "error_examples": findings.examples,
        "error_examples_truncated": findings.total > len(findings.examples),
    }
    return report


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Validate a resumable MathDB statement-recovery run.")
    value.add_argument("--run-dir", type=Path, required=True)
    value.add_argument("--report", type=Path, help="write the derived validation JSON atomically")
    return value


def main(argv: Iterable[str] | None = None) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        report = validate_run(args.run_dir.resolve())
    except (ValidationError, EventError, OSError, sqlite3.DatabaseError, json.JSONDecodeError) as exc:
        report = {
            "status": "fail",
            "validator": {"name": VALIDATOR_NAME, "version": VALIDATOR_VERSION},
            "fatal_error": str(exc),
        }
    if args.report:
        atomic_write_json(args.report.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
