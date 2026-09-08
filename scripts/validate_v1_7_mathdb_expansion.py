#!/usr/bin/env python3
"""Stream-validate the append-only OPDP v1.7 MathDB expansion.

The release payload is a gzip-compressed, monolithic JSON object whose
``records`` member is an array.  This validator parses the small top-level
metadata normally but decodes records one at a time.  It never constructs the
complete uncompressed payload or a complete records list/string.

The validator is intentionally independent of the release builder.  It checks
the immutable v1.6 prefix, the frozen MathDB snapshot, the reversible numeric
ID projection, the OPDP record contract and formulas, and the cached top-level
summary using only the Python standard library.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import math
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, Iterable, Iterator, TextIO


DEFAULT_EXPECTED_RECORDS = 102_563
DEFAULT_BASE_RECORDS = 15_458
DEFAULT_MATHDB_ID_OFFSET = 40_000_000
DEFAULT_MAX_VALUE_CHARS = 64 * 1024 * 1024
EXPECTED_RELEASE_VERSION = "1.7.0"
MATHDB_MANIFEST_SCHEMA = "opdp.mathdb.snapshot-manifest.v1"
MATHDB_CATALOG_MANIFEST_SCHEMA = "opdp.mathdb.catalog-snapshot-manifest.v1"
MATHDB_DETAIL_SCHEMA = "opdp.mathdb.problem-detail.v1"
MATHDB_SUMMARY_SCHEMA = "opdp.mathdb.problem-summary.v1"
MATHDB_INVENTORY_SCHEMA = "opdp.mathdb.sitemap-inventory.v1"

RECORD_FIELD_ORDER = [
    "problem_id",
    "difficulty_label",
    "explanation",
    "problem_number",
    "title",
    "catalog_status",
    "assessment_gate",
    "classification",
    "intrinsic_difficulty",
    "ai_assessment",
    "human_attention",
    "tractability",
    "verification",
    "formalization",
    "prerequisites",
    "tool_leverage",
    "recommended_approach",
    "evidence",
    "rationales",
    "tags",
    "review",
    "flags",
    "source_text",
    "provenance",
    "source_record",
    "implementation",
]

RATIONALE_FIELDS = [
    "intrinsic_difficulty",
    "intrinsic_factors",
    "ai_assessment",
    "tool_leverage",
    "human_attention",
    "tractability",
    "verification",
    "formalization",
    "prerequisites",
    "status_and_data",
]

ASSESSMENT_GATES = {
    "verified_open",
    "source_claimed_open",
    "status_unclear",
    "solved",
    "ill_posed",
}
CATALOG_STATUSES = {"open", "partially_solved", "solved"}
SCOPES = {"atomic", "family", "compound", "programmatic"}
ANSWER_TYPES = {
    "proof_disproof",
    "bound",
    "existence",
    "algorithm",
    "classification",
    "construction",
    "exact_value",
    "programmatic",
    "experimental",
}
AI_FITS = {"ai_favored", "ai_neutral_mixed", "ai_hostile"}
CONFIDENCE_STATUSES = {
    "assigned",
    "not_separately_assigned",
    "not_applicable",
    "unknown",
}
AI_PREDICTORS = [
    "formal_fit",
    "verification_feedback",
    "tool_fit",
    "context_load",
    "reasoning_horizon",
    "tacit_experimental_insight",
]


class ValidationError(RuntimeError):
    """Raised when a stream is structurally invalid and cannot be continued."""


class DuplicateKeyError(ValidationError):
    """Raised by the strict decoder for duplicate JSON object keys."""


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def reject_nonfinite_constant(value: str) -> Any:
    raise ValidationError(f"non-finite JSON number {value!r} is prohibited")


STRICT_DECODER = json.JSONDecoder(
    object_pairs_hook=reject_duplicate_keys,
    parse_constant=reject_nonfinite_constant,
)


@contextmanager
def open_text_auto(path: Path, *, require_gzip: bool = False) -> Generator[TextIO, None, None]:
    """Open UTF-8 text, detecting gzip by magic bytes rather than filename."""

    raw = path.open("rb")
    try:
        magic = raw.read(2)
        raw.seek(0)
        is_gzip = magic == b"\x1f\x8b"
        if require_gzip and not is_gzip:
            raise ValidationError(f"expected gzip input: {path}")
        binary: io.BufferedIOBase | gzip.GzipFile
        binary = gzip.GzipFile(fileobj=raw, mode="rb") if is_gzip else raw
        text = io.TextIOWrapper(binary, encoding="utf-8", errors="strict", newline="")
        try:
            yield text
        finally:
            text.close()
    except Exception:
        if not raw.closed:
            raw.close()
        raise


class IncrementalJSONReader:
    """A bounded-buffer JSON reader built on ``JSONDecoder.raw_decode``."""

    def __init__(
        self,
        handle: TextIO,
        *,
        source: Path,
        chunk_chars: int = 1024 * 1024,
        max_value_chars: int = DEFAULT_MAX_VALUE_CHARS,
    ) -> None:
        self.handle = handle
        self.source = source
        self.chunk_chars = chunk_chars
        self.max_value_chars = max_value_chars
        self.buffer = ""
        self.pos = 0
        self.offset = 0
        self.eof = False
        self.max_buffer_chars = 0

    def _fill(self) -> bool:
        if self.eof:
            return False
        chunk = self.handle.read(self.chunk_chars)
        if chunk == "":
            self.eof = True
            return False
        self.buffer += chunk
        self.max_buffer_chars = max(self.max_buffer_chars, len(self.buffer) - self.pos)
        return True

    def _compact(self) -> None:
        if self.pos and (self.pos >= self.chunk_chars or self.pos == len(self.buffer)):
            self.offset += self.pos
            self.buffer = self.buffer[self.pos :]
            self.pos = 0

    def skip_ws(self) -> None:
        while True:
            while self.pos < len(self.buffer) and self.buffer[self.pos] in " \t\r\n":
                self.pos += 1
            if self.pos < len(self.buffer) or self.eof:
                self._compact()
                return
            self._compact()
            self._fill()

    def peek(self) -> str | None:
        self.skip_ws()
        while self.pos >= len(self.buffer) and not self.eof:
            self._fill()
            self.skip_ws()
        return None if self.pos >= len(self.buffer) else self.buffer[self.pos]

    def expect(self, expected: str) -> None:
        actual = self.peek()
        if actual != expected:
            where = self.offset + self.pos
            raise ValidationError(
                f"{self.source}: expected {expected!r} at character {where}, found {actual!r}"
            )
        self.pos += 1
        self._compact()

    def decode_value(self) -> Any:
        self.skip_ws()
        if self.pos >= len(self.buffer) and not self._fill():
            raise ValidationError(f"{self.source}: unexpected EOF while reading JSON value")

        # Drop already-consumed text once before accumulating an incomplete value.
        if self.pos:
            self.offset += self.pos
            self.buffer = self.buffer[self.pos :]
            self.pos = 0

        while True:
            try:
                value, end = STRICT_DECODER.raw_decode(self.buffer, 0)
            except json.JSONDecodeError as exc:
                if self.eof:
                    raise ValidationError(
                        f"{self.source}: invalid JSON near character {self.offset + exc.pos}: {exc.msg}"
                    ) from exc
                if len(self.buffer) > self.max_value_chars:
                    raise ValidationError(
                        f"{self.source}: one JSON value exceeds the configured "
                        f"{self.max_value_chars:,}-character limit"
                    ) from exc
                self._fill()
                continue

            # A number at the end of a chunk may only be a prefix.  Require one
            # delimiter character (or physical EOF) before accepting a value.
            if end == len(self.buffer) and not self.eof:
                if self._fill():
                    continue
            self.pos = end
            self.max_buffer_chars = max(self.max_buffer_chars, end)
            self._compact()
            return value

    def ensure_eof(self) -> None:
        self.skip_ws()
        while not self.eof:
            self._fill()
            self.skip_ws()
        if self.pos != len(self.buffer):
            raise ValidationError(
                f"{self.source}: trailing content at character {self.offset + self.pos}"
            )


class StreamedPayload:
    """Stream a top-level object and its records array one object at a time."""

    def __init__(self, path: Path, *, max_value_chars: int) -> None:
        self.path = path
        self.max_value_chars = max_value_chars
        self.metadata: dict[str, Any] = {}
        self.top_keys: set[str] = set()
        self._context: Any = None
        self._handle: TextIO | None = None
        self.reader: IncrementalJSONReader | None = None
        self._started = False
        self._finished = False

    def __enter__(self) -> "StreamedPayload":
        self._context = open_text_auto(self.path, require_gzip=True)
        self._handle = self._context.__enter__()
        self.reader = IncrementalJSONReader(
            self._handle,
            source=self.path,
            max_value_chars=self.max_value_chars,
        )
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        assert self._context is not None
        self._context.__exit__(exc_type, exc, traceback)

    def _read_key(self) -> str:
        assert self.reader is not None
        key = self.reader.decode_value()
        if not isinstance(key, str):
            raise ValidationError(f"{self.path}: top-level object key is not a string")
        if key in self.top_keys:
            raise DuplicateKeyError(f"{self.path}: duplicate top-level key {key!r}")
        self.top_keys.add(key)
        self.reader.expect(":")
        return key

    def start(self) -> dict[str, Any]:
        assert self.reader is not None
        if self._started:
            return self.metadata
        self.reader.expect("{")
        first = True
        while True:
            token = self.reader.peek()
            if token == "}":
                raise ValidationError(f"{self.path}: missing top-level records array")
            if not first:
                self.reader.expect(",")
            key = self._read_key()
            if key == "records":
                self.reader.expect("[")
                self._started = True
                return self.metadata
            self.metadata[key] = self.reader.decode_value()
            first = False

    def records(self) -> Iterator[dict[str, Any]]:
        assert self.reader is not None
        if not self._started:
            self.start()
        first = True
        while True:
            token = self.reader.peek()
            if token == "]":
                self.reader.expect("]")
                break
            if not first:
                self.reader.expect(",")
                if self.reader.peek() == "]":
                    raise ValidationError(f"{self.path}: trailing comma in records array")
            value = self.reader.decode_value()
            if not isinstance(value, dict):
                raise ValidationError(f"{self.path}: records array contains a non-object value")
            yield value
            first = False

        while True:
            token = self.reader.peek()
            if token == "}":
                self.reader.expect("}")
                break
            self.reader.expect(",")
            key = self._read_key()
            if key == "records":
                raise DuplicateKeyError(f"{self.path}: duplicate top-level records key")
            self.metadata[key] = self.reader.decode_value()
        self.reader.ensure_eof()
        self._finished = True


def strict_json_line(text: str, *, source: Path, line_number: int) -> Any:
    try:
        value = STRICT_DECODER.decode(text)
    except (json.JSONDecodeError, DuplicateKeyError, ValidationError) as exc:
        raise ValidationError(f"{source}:{line_number}: {exc}") from exc
    return value


def json_pointer(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise ValidationError(f"JSON pointer must start with '/': {pointer!r}")
    current = value
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise KeyError(pointer)
    return current


def canonical_source_id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValidationError(
            f"MathDB IDs must be JSON strings or integers, found {type(value).__name__}"
        )
    kind = "s" if isinstance(value, str) else "i"
    return f"{kind}:{json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def first_integer(value: Any, names: Iterable[str]) -> int | None:
    """Mirror the builder's integer coercion for MathDB number fields."""

    if not isinstance(value, dict):
        return None
    for name in names:
        candidate = value.get(name)
        if isinstance(candidate, bool) or candidate is None:
            continue
        if isinstance(candidate, int):
            return candidate
        if isinstance(candidate, float) and math.isfinite(candidate) and candidate.is_integer():
            return int(candidate)
        if isinstance(candidate, str):
            stripped = candidate.strip()
            if stripped and stripped.lstrip("-").isdigit():
                return int(stripped)
    return None


def unwrap_snapshot_row(
    row: dict[str, Any], *, source_pointer: str | None = None
) -> tuple[dict[str, Any], dict[str, Any] | None, Any]:
    """Return (lossless problem, envelope metadata, envelope schema).

    Acquisition output uses ``{schema, snapshot, problem}`` envelopes, while
    fixture and legacy snapshots may contain the native problem object directly.
    An explicit JSON pointer wins when supplied.
    """

    if source_pointer:
        try:
            problem = json_pointer(row, source_pointer)
        except KeyError as exc:
            raise ValidationError(
                f"snapshot row has no source object at {source_pointer}"
            ) from exc
        if not isinstance(problem, dict):
            raise ValidationError(
                f"snapshot source object at {source_pointer} is not an object"
            )
        metadata = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else None
        return problem, metadata, row.get("schema")
    if "problem" in row:
        problem = row.get("problem")
        if not isinstance(problem, dict):
            raise ValidationError("snapshot envelope.problem must be an object")
        metadata = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else None
        return problem, metadata, row.get("schema")
    return row, None, None


def display_value(value: Any, limit: int = 180) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def first_difference(left: Any, right: Any, path: str = "") -> str | None:
    """Return the first semantic JSON difference, ignoring object key order."""

    if isinstance(left, bool) != isinstance(right, bool):
        return f"{path or '/'}: {display_value(left)} != {display_value(right)}"
    if isinstance(left, (int, float)) and not isinstance(left, bool) and isinstance(
        right, (int, float)
    ) and not isinstance(right, bool):
        if left == right:
            return None
    elif type(left) is not type(right):
        return f"{path or '/'}: type {type(left).__name__} != {type(right).__name__}"

    if isinstance(left, dict):
        if set(left) != set(right):
            missing = sorted(set(right) - set(left))
            extra = sorted(set(left) - set(right))
            return f"{path or '/'}: missing keys={missing[:5]}, extra keys={extra[:5]}"
        for key in left:
            escaped = key.replace("~", "~0").replace("/", "~1")
            result = first_difference(left[key], right[key], f"{path}/{escaped}")
            if result:
                return result
        return None
    if isinstance(left, list):
        if len(left) != len(right):
            return f"{path or '/'}: array length {len(left)} != {len(right)}"
        for index, (a, b) in enumerate(zip(left, right)):
            result = first_difference(a, b, f"{path}/{index}")
            if result:
                return result
        return None
    if left != right:
        return f"{path or '/'}: {display_value(left)} != {display_value(right)}"
    return None


def manifest_get(manifest: dict[str, Any], *paths: str) -> Any:
    for dotted in paths:
        current: Any = manifest
        found = True
        for part in dotted.split("."):
            if not isinstance(current, dict) or part not in current:
                found = False
                break
            current = current[part]
        if found and current is not None:
            return current
    return None


def manifest_path(manifest: dict[str, Any], *paths: str) -> str | None:
    value = manifest_get(manifest, *paths)
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("path"), str):
        return value["path"]
    return None


def resolve_path(value: str | Path | None, *, base: Path) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def load_manifest(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    with open_text_auto(path) as handle:
        text = handle.read()
    value = strict_json_line(text, source=path, line_number=1)
    if not isinstance(value, dict):
        raise ValidationError(f"manifest must be a JSON object: {path}")
    return value


def iter_snapshot_rows(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield strict JSON objects from a JSONL/NDJSON snapshot."""

    with open_text_auto(path) as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = strict_json_line(line, source=path, line_number=line_number)
            if not isinstance(value, dict):
                raise ValidationError(f"{path}:{line_number}: snapshot row is not an object")
            yield line_number, value


class Findings:
    def __init__(self, max_examples: int) -> None:
        self.max_examples = max_examples
        self.error_count = 0
        self.errors: list[dict[str, Any]] = []

    def add(
        self,
        code: str,
        message: str,
        *,
        record_index: int | None = None,
        problem_id: Any = None,
    ) -> None:
        self.error_count += 1
        if len(self.errors) < self.max_examples:
            item: dict[str, Any] = {"code": code, "message": message}
            if record_index is not None:
                item["record_index"] = record_index
            if problem_id is not None:
                item["problem_id"] = problem_id
            self.errors.append(item)


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def in_range(value: Any, low: float, high: float) -> bool:
    return is_number(value) and low <= value <= high


def close(actual: Any, expected: float, tolerance: float = 1e-9) -> bool:
    return is_number(actual) and abs(float(actual) - expected) <= tolerance


def js_round(value: float) -> int:
    return math.floor(value + 0.5)


def round1(value: float) -> float:
    return math.floor(value * 10.0 + 0.5) / 10.0


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_get(value: Any, *keys: str, default: Any = None) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def require_object(
    value: Any,
    findings: Findings,
    code: str,
    location: str,
    *,
    index: int,
    problem_id: Any,
) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    findings.add(code, f"{location} must be an object", record_index=index, problem_id=problem_id)
    return {}


def validate_confidence(
    value: Any,
    findings: Findings,
    location: str,
    *,
    index: int,
    problem_id: Any,
) -> None:
    confidence = require_object(
        value,
        findings,
        "CONFIDENCE_SCHEMA",
        location,
        index=index,
        problem_id=problem_id,
    )
    if set(confidence) != {"level", "code", "status"}:
        findings.add(
            "CONFIDENCE_SCHEMA",
            f"{location} must contain exactly level/code/status",
            record_index=index,
            problem_id=problem_id,
        )
    status = confidence.get("status")
    level = confidence.get("level")
    code = confidence.get("code")
    if status not in CONFIDENCE_STATUSES:
        findings.add(
            "CONFIDENCE_STATUS",
            f"{location}.status={status!r} is invalid",
            record_index=index,
            problem_id=problem_id,
        )
    if status == "assigned":
        if not is_integer(level) or not 0 <= level <= 3 or code != f"C{level}":
            findings.add(
                "CONFIDENCE_VALUE",
                f"{location} assigned confidence must be level 0..3 with matching C code",
                record_index=index,
                problem_id=problem_id,
            )
    elif level is not None or code is not None:
        findings.add(
            "CONFIDENCE_NULL_SEMANTICS",
            f"{location} non-assigned confidence must have null level and code",
            record_index=index,
            problem_id=problem_id,
        )


class SummaryAccumulator:
    def __init__(self, rubric: dict[str, Any]) -> None:
        self.records_total = 0
        self.valid_problem_ids: set[int] = set()
        self.problem_numbers: Counter[str] = Counter()
        self.catalog_status_counts: Counter[str] = Counter(
            {key: 0 for key in ("open", "partially_solved", "solved")}
        )
        self.gate_counts: Counter[str] = Counter()
        self.tier_counts: Counter[str] = Counter()
        self.ai_fit_counts: Counter[str] = Counter()
        self.confidence_counts: Counter[str] = Counter()
        self.scope_counts: Counter[str] = Counter()
        self.answer_counts: Counter[str] = Counter()
        self.human_counts: Counter[str] = Counter()
        self.tractability_counts: Counter[str] = Counter()
        self.priority_counts: Counter[str] = Counter()
        self.tag_counts: Counter[str] = Counter()
        self.flag_counts: Counter[str] = Counter()
        self.route_counts: Counter[str] = Counter()
        self.category_counts: Counter[str] = Counter()
        self.source_counts: Counter[str] = Counter()
        self.records_with_flags = 0
        self.records_needing_curation = 0
        self.tractability_scored = 0
        self.d_sum = 0.0
        self.ai_sum = 0.0
        self.t_sum = 0.0
        self.d_count = 0
        self.ai_count = 0
        self.control_occurrences = 0
        self.control_records = 0
        self.malformed_urls = 0
        self.fallback_urls = 0
        self.missing_set_ids = 0
        self.missing_nested_sets = 0
        self.null_tractability = 0
        self.zero_tractability = 0
        self.duplicate_number_groups: set[str] = set()
        self.duplicate_number_records = 0
        self.tag_registry = list((rubric.get("tag_registry") or {}).keys())
        self.flag_registry = list((rubric.get("flag_registry") or {}).keys())
        self.priority_registry = list((rubric.get("review_priority_registry") or {}).keys())

    def observe(self, record: dict[str, Any]) -> None:
        self.records_total += 1
        problem_id = record.get("problem_id")
        if is_integer(problem_id):
            self.valid_problem_ids.add(problem_id)
        problem_number = record.get("problem_number")
        if isinstance(problem_number, str):
            self.problem_numbers[problem_number] += 1

        def add(counter: Counter[str], value: Any) -> None:
            if isinstance(value, str):
                counter[value] += 1

        add(self.catalog_status_counts, record.get("catalog_status"))
        add(self.gate_counts, safe_get(record, "assessment_gate", "value"))
        add(self.tier_counts, safe_get(record, "intrinsic_difficulty", "tier_code"))
        add(self.ai_fit_counts, safe_get(record, "ai_assessment", "fit"))
        add(
            self.confidence_counts,
            safe_get(record, "intrinsic_difficulty", "confidence", "code"),
        )
        add(self.scope_counts, safe_get(record, "classification", "scope"))
        add(self.answer_counts, safe_get(record, "classification", "answer_type"))

        h = safe_get(record, "human_attention", "effort", "score")
        if is_integer(h):
            self.human_counts[f"H{h}"] += 1
        t = safe_get(record, "tractability", "score")
        if t is None:
            self.tractability_counts["not_applicable"] += 1
            self.null_tractability += 1
        elif is_integer(t):
            self.tractability_counts[f"T{t}"] += 1
            self.tractability_scored += 1
            self.t_sum += t
            if t == 0:
                self.zero_tractability += 1
        add(self.priority_counts, safe_get(record, "review", "priority_code"))
        add(self.route_counts, safe_get(record, "recommended_approach", "route_code"))

        tags = record.get("tags")
        if isinstance(tags, list):
            for tag in tags:
                add(self.tag_counts, tag)
            if "needs_curation" in tags:
                self.records_needing_curation += 1
        flags = record.get("flags")
        if isinstance(flags, list):
            for flag in flags:
                add(self.flag_counts, flag)
            if flags:
                self.records_with_flags += 1

        add(self.category_counts, safe_get(record, "classification", "category", "label"))
        add(
            self.source_counts,
            safe_get(record, "classification", "source_collection", "label"),
        )

        d = safe_get(record, "intrinsic_difficulty", "score")
        if is_number(d):
            self.d_sum += float(d)
            self.d_count += 1
        ai = safe_get(record, "ai_assessment", "difficulty_score")
        if is_number(ai):
            self.ai_sum += float(ai)
            self.ai_count += 1

        controls = safe_get(
            record,
            "source_text",
            "transport_qa",
            "forbidden_control_character_count",
            default=0,
        )
        if is_integer(controls) and controls >= 0:
            self.control_occurrences += controls
            self.control_records += int(controls > 0)
        qa_flags = safe_get(record, "provenance", "source_url_qa_flags", default=[])
        if isinstance(qa_flags, list) and "duplicated_scheme" in qa_flags:
            self.malformed_urls += 1
        source_url_kind = safe_get(record, "provenance", "source_url_kind")
        if isinstance(source_url_kind, str) and "fallback" in source_url_kind:
            self.fallback_urls += 1
        source_collection = safe_get(record, "classification", "source_collection", default={})
        if isinstance(source_collection, dict):
            if source_collection.get("id") is None:
                self.missing_set_ids += 1
            if source_collection.get("id") is not None and not source_collection.get(
                "nested_object_present"
            ):
                self.missing_nested_sets += 1
        collision_peers = safe_get(
            record,
            "review",
            "identity_collisions",
            "problem_number_peer_ids",
            default=[],
        )
        if isinstance(collision_peers, list) and collision_peers:
            self.duplicate_number_records += 1
            if isinstance(problem_number, str):
                self.duplicate_number_groups.add(problem_number)

    @staticmethod
    def _with_zeros(keys: Iterable[str], counter: Counter[str]) -> dict[str, int]:
        return {key: counter.get(key, 0) for key in keys}

    def result(self) -> dict[str, Any]:
        duplicate_groups = len(self.duplicate_number_groups)
        duplicate_records = self.duplicate_number_records
        return {
            "records_total": self.records_total,
            "catalog_status_counts": dict(self.catalog_status_counts),
            "assessment_gate_counts": self._with_zeros(
                ["verified_open", "source_claimed_open", "status_unclear", "solved", "ill_posed"],
                self.gate_counts,
            ),
            "difficulty_tier_counts": self._with_zeros(
                ["T1", "T2", "T3", "T4", "T5"], self.tier_counts
            ),
            "ai_fit_counts": self._with_zeros(
                ["ai_favored", "ai_neutral_mixed", "ai_hostile"], self.ai_fit_counts
            ),
            "difficulty_confidence_counts": self._with_zeros(
                ["C0", "C1", "C2", "C3"], self.confidence_counts
            ),
            "scope_counts": self._with_zeros(
                ["atomic", "family", "compound", "programmatic"], self.scope_counts
            ),
            "answer_type_counts": self._with_zeros(
                [
                    "proof_disproof",
                    "bound",
                    "existence",
                    "algorithm",
                    "classification",
                    "construction",
                    "exact_value",
                    "programmatic",
                    "experimental",
                ],
                self.answer_counts,
            ),
            "human_effort_band_counts": self._with_zeros(
                [f"H{i}" for i in range(11)], self.human_counts
            ),
            "tractability_band_counts": self._with_zeros(
                [*[f"T{i}" for i in range(11)], "not_applicable"],
                self.tractability_counts,
            ),
            "review_priority_counts": self._with_zeros(
                self.priority_registry, self.priority_counts
            ),
            "tag_counts": self._with_zeros(self.tag_registry, self.tag_counts),
            "flag_counts": dict(self.flag_counts),
            "category_counts": dict(
                sorted(self.category_counts.items(), key=lambda item: item[0].casefold())
            ),
            "source_collection_counts": dict(
                sorted(self.source_counts.items(), key=lambda item: item[0].casefold())
            ),
            "records_with_flags": self.records_with_flags,
            "records_needing_curation": self.records_needing_curation,
            "tractability_scored_records": self.tractability_scored,
            "means": {
                "intrinsic_difficulty": round1(self.d_sum / self.records_total)
                if self.records_total
                else None,
                "ai_difficulty": round1(self.ai_sum / self.records_total)
                if self.records_total
                else None,
                "tractability": round1(self.t_sum / self.tractability_scored)
                if self.tractability_scored
                else None,
            },
            "corpus_audit": {
                "unique_problem_ids": len(self.valid_problem_ids),
                "unique_problem_numbers": len(self.problem_numbers),
                "duplicate_problem_number_groups": duplicate_groups,
                "records_in_duplicate_problem_number_groups": duplicate_records,
                "surplus_problem_number_records": duplicate_records - duplicate_groups,
                "source_url_fallback_records": self.fallback_urls,
                "malformed_duplicated_scheme_source_urls": self.malformed_urls,
                "forbidden_control_character_occurrences": self.control_occurrences,
                "records_with_forbidden_control_characters": self.control_records,
                "missing_set_id_records": self.missing_set_ids,
                "set_id_present_but_nested_set_missing_records": self.missing_nested_sets,
                "intentional_null_tractability_records": self.null_tractability,
                "valid_zero_tractability_records": self.zero_tractability,
            },
            "route_counts": dict(self.route_counts),
            "summary_semantics": (
                "Cached convenience values recomputable from records; means use the stated "
                "denominator and are rounded to one decimal."
            ),
        }


class RecordValidator:
    def __init__(self, metadata: dict[str, Any], findings: Findings) -> None:
        self.metadata = metadata
        self.findings = findings
        self.schema = metadata.get("schema") if isinstance(metadata.get("schema"), dict) else {}
        self.rubric = metadata.get("rubric") if isinstance(metadata.get("rubric"), dict) else {}
        self.rule_version = safe_get(metadata, "assessment_release", "rule_version")
        self.record_schema_version = self.schema.get("record_schema_version")
        self.tag_codes = set((self.rubric.get("tag_registry") or {}).keys())
        self.flag_codes = set((self.rubric.get("flag_registry") or {}).keys())
        self.priority_registry = self.rubric.get("review_priority_registry") or {}
        self.route_registry = self.rubric.get("route_registry") or {}
        self.tiers = {
            row.get("code"): row
            for row in safe_get(self.rubric, "intrinsic_difficulty", "tiers", default=[])
            if isinstance(row, dict) and isinstance(row.get("code"), str)
        }
        self.hours = {
            row.get("code"): row
            for row in safe_get(self.rubric, "human_attention", "effort_bands", default=[])
            if isinstance(row, dict) and isinstance(row.get("code"), str)
        }
        self.probabilities = {
            row.get("code"): row
            for row in safe_get(self.rubric, "tractability", "probability_bands", default=[])
            if isinstance(row, dict) and isinstance(row.get("code"), str)
        }
        self.protocol_ids = set(
            metadata.get("protocols", {}).keys()
            if isinstance(metadata.get("protocols"), dict)
            else []
        )

    def issue(self, code: str, message: str, index: int, problem_id: Any) -> None:
        self.findings.add(code, message, record_index=index, problem_id=problem_id)

    def validate(self, record: dict[str, Any], index: int) -> None:
        problem_id = record.get("problem_id")
        if list(record.keys()) != RECORD_FIELD_ORDER:
            self.issue(
                "RECORD_FIELD_ORDER",
                f"record fields differ from the required 26-field order: {list(record.keys())}",
                index,
                problem_id,
            )
        if not is_integer(problem_id):
            self.issue("PROBLEM_ID_TYPE", "problem_id must be an integer", index, problem_id)
        for key in ("difficulty_label", "explanation", "problem_number", "title"):
            if not isinstance(record.get(key), str) or not record.get(key):
                self.issue("REQUIRED_STRING", f"{key} must be a non-empty string", index, problem_id)
        if record.get("catalog_status") not in CATALOG_STATUSES:
            self.issue(
                "CATALOG_STATUS_ENUM",
                f"invalid catalog_status={record.get('catalog_status')!r}",
                index,
                problem_id,
            )

        gate = require_object(
            record.get("assessment_gate"),
            self.findings,
            "ASSESSMENT_GATE_SCHEMA",
            "assessment_gate",
            index=index,
            problem_id=problem_id,
        )
        gate_value = gate.get("value")
        if gate_value not in ASSESSMENT_GATES:
            self.issue("ASSESSMENT_GATE_ENUM", f"invalid gate={gate_value!r}", index, problem_id)
        validate_confidence(
            gate.get("confidence"),
            self.findings,
            "assessment_gate.confidence",
            index=index,
            problem_id=problem_id,
        )

        classification = require_object(
            record.get("classification"),
            self.findings,
            "CLASSIFICATION_SCHEMA",
            "classification",
            index=index,
            problem_id=problem_id,
        )
        scope = classification.get("scope")
        if scope not in SCOPES:
            self.issue("SCOPE_ENUM", f"invalid scope={scope!r}", index, problem_id)
        answer_type = classification.get("answer_type")
        if answer_type not in ANSWER_TYPES:
            self.issue("ANSWER_TYPE_ENUM", f"invalid answer_type={answer_type!r}", index, problem_id)
        if not in_range(classification.get("ambiguity_score"), 0, 10):
            self.issue("AMBIGUITY_RANGE", "ambiguity_score must be in 0..10", index, problem_id)
        objects = classification.get("mathematical_objects")
        if not isinstance(objects, list) or any(not isinstance(value, str) for value in objects):
            self.issue(
                "MATHEMATICAL_OBJECTS_SCHEMA",
                "mathematical_objects must be an array of strings",
                index,
                problem_id,
            )
        for location in ("category", "source_collection", "legacy_difficulty"):
            if not isinstance(classification.get(location), dict):
                self.issue(
                    "CLASSIFICATION_SCHEMA",
                    f"classification.{location} must be an object",
                    index,
                    problem_id,
                )

        intrinsic = require_object(
            record.get("intrinsic_difficulty"),
            self.findings,
            "INTRINSIC_SCHEMA",
            "intrinsic_difficulty",
            index=index,
            problem_id=problem_id,
        )
        d_score = intrinsic.get("score")
        if not in_range(d_score, 0, 10):
            self.issue("D_RANGE", "intrinsic score must be in 0..10", index, problem_id)
        validate_confidence(
            intrinsic.get("confidence"),
            self.findings,
            "intrinsic_difficulty.confidence",
            index=index,
            problem_id=problem_id,
        )
        factors = require_object(
            intrinsic.get("factors"),
            self.findings,
            "D_FACTORS_SCHEMA",
            "intrinsic_difficulty.factors",
            index=index,
            problem_id=problem_id,
        )
        factor_specs = [
            ("conceptual_gap", "CG", 0.30),
            ("route_gap", "RG", 0.20),
            ("technical_depth", "TD", 0.20),
            ("known_barrier", "KB", 0.20),
            ("search_scale", "SS", 0.10),
        ]
        weighted = 0.0
        factors_valid = set(factors) == {row[0] for row in factor_specs}
        if not factors_valid:
            self.issue("D_FACTORS_SCHEMA", "factor keys are incomplete or unexpected", index, problem_id)
        for name, code, weight in factor_specs:
            factor = factors.get(name)
            if not isinstance(factor, dict):
                factors_valid = False
                self.issue("D_FACTOR_SCHEMA", f"missing factor {name}", index, problem_id)
                continue
            score = factor.get("score")
            if factor.get("code") != code or not close(factor.get("weight"), weight) or not in_range(score, 0, 4):
                factors_valid = False
                self.issue(
                    "D_FACTOR_VALUE",
                    f"{name} must have code={code}, weight={weight}, score in 0..4",
                    index,
                    problem_id,
                )
            else:
                weighted += float(score) * weight
        if factors_valid and is_number(d_score):
            expected_d = round1(clamp(2.5 * weighted, 0, 10))
            if record.get("catalog_status") == "solved":
                expected_d = min(expected_d, 8.5)
            if not close(d_score, expected_d):
                self.issue(
                    "D_RECOMPUTATION",
                    f"stored D={d_score} but formula gives {expected_d}",
                    index,
                    problem_id,
                )
        tier_code = intrinsic.get("tier_code")
        tier = self.tiers.get(tier_code)
        if not tier:
            self.issue("DIFFICULTY_TIER_ENUM", f"invalid tier={tier_code!r}", index, problem_id)
        elif is_number(d_score):
            low, high = tier.get("low"), tier.get("high")
            inclusive = bool(tier.get("high_inclusive"))
            in_tier = is_number(low) and is_number(high) and float(d_score) >= float(low) and (
                float(d_score) <= float(high) if inclusive else float(d_score) < float(high)
            )
            if not in_tier:
                self.issue("DIFFICULTY_TIER_RANGE", "D does not fall in its tier", index, problem_id)
            if intrinsic.get("tier_label") != tier.get("label") or record.get(
                "difficulty_label"
            ) != tier.get("difficulty_label"):
                self.issue(
                    "DIFFICULTY_TIER_LABEL",
                    "difficulty labels do not match the tier registry",
                    index,
                    problem_id,
                )
        d_range = intrinsic.get("range")
        if isinstance(d_range, dict) and in_range(d_range.get("low"), 0, 10) and in_range(
            d_range.get("high"), 0, 10
        ):
            if is_number(d_score) and not (
                float(d_range["low"]) <= float(d_score) <= float(d_range["high"])
            ):
                self.issue("D_INTERVAL", "D is outside its interval", index, problem_id)
        else:
            self.issue("D_INTERVAL", "invalid D interval", index, problem_id)
        d_confidence = safe_get(intrinsic, "confidence", "level")
        excerpt_only = isinstance(record.get("flags"), list) and (
            "source_excerpt_only" in record["flags"]
        )
        if is_integer(d_confidence) and is_number(d_score):
            d_width = {0: 2.5, 1: 1.8, 2: 1.2, 3: 0.8}.get(d_confidence)
            if d_width is not None:
                d_width += 0.5 if excerpt_only else 0.0
                d_width += 0.4 if scope == "compound" else 0.0
                expected_d_range = {
                    "low": round1(clamp(float(d_score) - d_width, 0, 10)),
                    "high": round1(clamp(float(d_score) + d_width, 0, 10)),
                }
                if d_range != expected_d_range:
                    self.issue(
                        "D_INTERVAL_RECOMPUTATION",
                        f"stored D interval {d_range!r}, expected {expected_d_range!r}",
                        index,
                        problem_id,
                    )

        ai = require_object(
            record.get("ai_assessment"),
            self.findings,
            "AI_SCHEMA",
            "ai_assessment",
            index=index,
            problem_id=problem_id,
        )
        predictors = ai.get("predictors")
        predictors_valid = isinstance(predictors, dict) and list(predictors.keys()) == AI_PREDICTORS
        if not predictors_valid:
            self.issue("AI_PREDICTOR_SCHEMA", "AI predictor keys/order are invalid", index, problem_id)
        elif any(
            not is_integer(predictors[name]) or predictors[name] not in {-1, 0, 1}
            for name in AI_PREDICTORS
        ):
            predictors_valid = False
            self.issue("AI_PREDICTOR_RANGE", "AI predictors must be -1, 0, or 1", index, problem_id)
        relative = ai.get("relative_adjustment")
        ai_score = ai.get("difficulty_score")
        if not in_range(relative, -3, 3):
            self.issue("AI_RELATIVE_RANGE", "AI relative adjustment must be in -3..3", index, problem_id)
        if not in_range(ai_score, 0, 10):
            self.issue("AI_DIFFICULTY_RANGE", "AI difficulty must be in 0..10", index, problem_id)
        if predictors_valid:
            expected_relative = round1(0.5 * sum(predictors[name] for name in AI_PREDICTORS))
            if not close(relative, expected_relative):
                self.issue(
                    "AI_RELATIVE_RECOMPUTATION",
                    f"stored relative={relative}, expected={expected_relative}",
                    index,
                    problem_id,
                )
            if is_number(d_score):
                expected_ai = round1(clamp(float(d_score) + expected_relative, 0, 10))
                if not close(ai_score, expected_ai):
                    self.issue(
                        "AI_DIFFICULTY_RECOMPUTATION",
                        f"stored AI D={ai_score}, expected={expected_ai}",
                        index,
                        problem_id,
                    )
            expected_fit = (
                "ai_favored"
                if expected_relative <= -1
                else "ai_hostile"
                if expected_relative >= 1
                else "ai_neutral_mixed"
            )
            expected_fit_label = {
                "ai_favored": "AI-favored",
                "ai_neutral_mixed": "AI-neutral/mixed",
                "ai_hostile": "AI-hostile",
            }[expected_fit]
            if ai.get("fit") != expected_fit or ai.get("fit_label") != expected_fit_label:
                self.issue("AI_FIT_RECOMPUTATION", "AI fit/label mismatch", index, problem_id)
        if ai.get("fit") not in AI_FITS:
            self.issue("AI_FIT_ENUM", f"invalid AI fit={ai.get('fit')!r}", index, problem_id)
        if ai.get("protocol_id") not in self.protocol_ids:
            self.issue("AI_PROTOCOL", "AI protocol_id is not registered", index, problem_id)
        validate_confidence(
            ai.get("confidence"),
            self.findings,
            "ai_assessment.confidence",
            index=index,
            problem_id=problem_id,
        )
        ai_range = ai.get("range")
        if not (
            isinstance(ai_range, dict)
            and in_range(ai_range.get("low"), 0, 10)
            and in_range(ai_range.get("high"), 0, 10)
            and (not is_number(ai_score) or float(ai_range["low"]) <= float(ai_score) <= float(ai_range["high"]))
        ):
            self.issue("AI_INTERVAL", "invalid AI difficulty interval", index, problem_id)
        if is_integer(d_confidence) and is_number(ai_score):
            expected_ai_confidence = 0 if excerpt_only else min(2, d_confidence)
            if safe_get(ai, "confidence", "level") != expected_ai_confidence:
                self.issue(
                    "AI_CONFIDENCE_RECOMPUTATION",
                    f"AI confidence must be C{expected_ai_confidence}",
                    index,
                    problem_id,
                )
            ai_width = {0: 3.0, 1: 2.3, 2: 1.7}[expected_ai_confidence]
            ai_width += 0.5 if excerpt_only else 0.0
            expected_ai_range = {
                "low": round1(clamp(float(ai_score) - ai_width, 0, 10)),
                "high": round1(clamp(float(ai_score) + ai_width, 0, 10)),
            }
            if ai_range != expected_ai_range:
                self.issue(
                    "AI_INTERVAL_RECOMPUTATION",
                    f"stored AI interval {ai_range!r}, expected {expected_ai_range!r}",
                    index,
                    problem_id,
                )

        human = require_object(
            record.get("human_attention"),
            self.findings,
            "HUMAN_SCHEMA",
            "human_attention",
            index=index,
            problem_id=problem_id,
        )
        effort = require_object(
            human.get("effort"),
            self.findings,
            "HUMAN_EFFORT_SCHEMA",
            "human_attention.effort",
            index=index,
            problem_id=problem_id,
        )
        h_score = effort.get("score")
        if not is_integer(h_score) or not 0 <= h_score <= 10 or effort.get("code") != f"H{h_score}":
            self.issue("HUMAN_EFFORT_RANGE", "H score/code must be H0..H10", index, problem_id)
        else:
            registered_band = self.hours.get(effort.get("code"))
            stored_band = effort.get("estimated_specialist_hours")
            expected_band = (
                {
                    "minimum": registered_band.get("minimum"),
                    "maximum": registered_band.get("maximum"),
                    "minimum_inclusive": True,
                    "maximum_inclusive": (
                        None if registered_band.get("maximum") is None else False
                    ),
                    "maximum_kind": (
                        "unbounded"
                        if registered_band.get("maximum") is None
                        else "finite"
                    ),
                    "display_label": registered_band.get("label"),
                }
                if isinstance(registered_band, dict)
                else None
            )
            if stored_band != expected_band:
                self.issue(
                    "HUMAN_EFFORT_BAND",
                    "estimated specialist-hour band does not match the H registry",
                    index,
                    problem_id,
                )
        validate_confidence(
            effort.get("confidence"),
            self.findings,
            "human_attention.effort.confidence",
            index=index,
            problem_id=problem_id,
        )
        exposure = require_object(
            human.get("exposure"),
            self.findings,
            "EXPOSURE_SCHEMA",
            "human_attention.exposure",
            index=index,
            problem_id=problem_id,
        )
        x_score = exposure.get("score")
        if not is_integer(x_score) or not 0 <= x_score <= 5 or exposure.get("code") != f"X{x_score}":
            self.issue("EXPOSURE_RANGE", "exposure score/code must be X0..X5", index, problem_id)
        validate_confidence(
            exposure.get("confidence"),
            self.findings,
            "human_attention.exposure.confidence",
            index=index,
            problem_id=problem_id,
        )

        verification = require_object(
            record.get("verification"),
            self.findings,
            "VERIFICATION_SCHEMA",
            "verification",
            index=index,
            problem_id=problem_id,
        )
        v_true = verification.get("if_true_score")
        v_false = verification.get("if_false_score")
        if not in_range(v_true, 0, 10) or not in_range(v_false, 0, 10):
            self.issue("VERIFICATION_RANGE", "verification scores must be in 0..10", index, problem_id)
        validate_confidence(
            verification.get("confidence"),
            self.findings,
            "verification.confidence",
            index=index,
            problem_id=problem_id,
        )
        formalization = require_object(
            record.get("formalization"),
            self.findings,
            "FORMALIZATION_SCHEMA",
            "formalization",
            index=index,
            problem_id=problem_id,
        )
        if not in_range(formalization.get("score"), 0, 10):
            self.issue("FORMALIZATION_RANGE", "formalization score must be in 0..10", index, problem_id)
        validate_confidence(
            formalization.get("confidence"),
            self.findings,
            "formalization.confidence",
            index=index,
            problem_id=problem_id,
        )
        prerequisites = require_object(
            record.get("prerequisites"),
            self.findings,
            "PREREQUISITES_SCHEMA",
            "prerequisites",
            index=index,
            problem_id=problem_id,
        )
        if not in_range(prerequisites.get("preparation_score"), 0, 10) or not in_range(
            prerequisites.get("breadth_score"), 0, 5
        ):
            self.issue("PREREQUISITES_RANGE", "P must be 0..10 and B 0..5", index, problem_id)
        validate_confidence(
            prerequisites.get("confidence"),
            self.findings,
            "prerequisites.confidence",
            index=index,
            problem_id=problem_id,
        )
        leverage = require_object(
            record.get("tool_leverage"),
            self.findings,
            "TOOL_LEVERAGE_SCHEMA",
            "tool_leverage",
            index=index,
            problem_id=problem_id,
        )
        if not in_range(leverage.get("score"), 0, 10):
            self.issue("TOOL_LEVERAGE_RANGE", "tool leverage must be in 0..10", index, problem_id)
        validate_confidence(
            leverage.get("confidence"),
            self.findings,
            "tool_leverage.confidence",
            index=index,
            problem_id=problem_id,
        )

        tractability = require_object(
            record.get("tractability"),
            self.findings,
            "TRACTABILITY_SCHEMA",
            "tractability",
            index=index,
            problem_id=problem_id,
        )
        t_score = tractability.get("score")
        if gate_value in {"solved", "ill_posed"}:
            if tractability.get("status") != "not_applicable" or any(
                tractability.get(key) is not None for key in ("score", "code", "progress_probability")
            ):
                self.issue(
                    "TRACTABILITY_NULL_SEMANTICS",
                    "solved/ill_posed gates require null T/code/probability",
                    index,
                    problem_id,
                )
        else:
            if not is_integer(t_score) or not 0 <= t_score <= 10 or tractability.get(
                "code"
            ) != f"T{t_score}" or tractability.get("status") != "scored":
                self.issue("TRACTABILITY_RANGE", "scored T must be T0..T10", index, problem_id)
            if all(
                is_number(value)
                for value in (ai_score, leverage.get("score"), v_true, v_false)
            ) and is_integer(h_score):
                expected_t = js_round(
                    clamp(
                        9
                        - 0.8 * float(ai_score)
                        + 0.2 * float(leverage["score"])
                        - 0.15 * ((float(v_true) + float(v_false)) / 2)
                        + (0.6 if h_score <= 4 else 0)
                        + (0.5 if record.get("catalog_status") == "partially_solved" else 0)
                        - (1.0 if gate_value in {"status_unclear", "ill_posed", "solved"} else 0),
                        0,
                        10,
                    )
                )
                if t_score != expected_t:
                    self.issue(
                        "TRACTABILITY_RECOMPUTATION",
                        f"stored T={t_score}, expected={expected_t}",
                        index,
                        problem_id,
                    )
            band = self.probabilities.get(tractability.get("code"))
            probability = tractability.get("progress_probability")
            if band and isinstance(probability, dict):
                expected_probability = {
                    "low": band.get("low"),
                    "high": band.get("high"),
                    "low_inclusive": True,
                    "high_inclusive": band.get("high_inclusive"),
                    "display_label": band.get("label"),
                }
                if probability != expected_probability:
                    self.issue(
                        "TRACTABILITY_BAND",
                        "progress probability does not match T registry",
                        index,
                        problem_id,
                    )
            else:
                self.issue("TRACTABILITY_BAND", "missing T probability band", index, problem_id)
        validate_confidence(
            tractability.get("confidence"),
            self.findings,
            "tractability.confidence",
            index=index,
            problem_id=problem_id,
        )

        route = require_object(
            record.get("recommended_approach"),
            self.findings,
            "ROUTE_SCHEMA",
            "recommended_approach",
            index=index,
            problem_id=problem_id,
        )
        route_entry = self.route_registry.get(route.get("route_code"))
        if not isinstance(route_entry, dict) or route.get("route_label") != route_entry.get("label"):
            self.issue("ROUTE_ENUM", "route code/label is not registered", index, problem_id)

        evidence = require_object(
            record.get("evidence"),
            self.findings,
            "EVIDENCE_SCHEMA",
            "evidence",
            index=index,
            problem_id=problem_id,
        )
        if not in_range(evidence.get("literature_load_score"), 0, 10) or not in_range(
            evidence.get("collection_barrier_prior"), 0, 10
        ):
            self.issue("EVIDENCE_RANGE", "literature/prior scores must be in 0..10", index, problem_id)
        codes = evidence.get("codes")
        if not isinstance(codes, list) or any(not isinstance(value, str) for value in codes):
            self.issue("EVIDENCE_CODES", "evidence.codes must be strings", index, problem_id)

        rationales = record.get("rationales")
        if not isinstance(rationales, dict) or list(rationales.keys()) != RATIONALE_FIELDS or any(
            not isinstance(rationales.get(key), str) or not rationales.get(key)
            for key in RATIONALE_FIELDS
        ):
            self.issue(
                "RATIONALE_SCHEMA",
                "all ten rationale fields must be present, ordered, and non-empty",
                index,
                problem_id,
            )

        tags = record.get("tags")
        tags_valid = isinstance(tags, list) and all(isinstance(tag, str) for tag in tags)
        if not tags_valid or len(tags) != len(set(tags)) or any(
            tag not in self.tag_codes for tag in tags
        ):
            self.issue("TAG_ENUM", "tags must be unique registered strings", index, problem_id)
        flags = record.get("flags")
        flags_valid = isinstance(flags, list) and all(isinstance(flag, str) for flag in flags)
        if not flags_valid or len(flags) != len(set(flags)) or any(
            flag not in self.flag_codes for flag in flags
        ):
            self.issue("FLAG_ENUM", "flags must be unique registered strings", index, problem_id)
        priority = safe_get(record, "review", "priority_code")
        if priority not in self.priority_registry:
            self.issue("REVIEW_PRIORITY_ENUM", f"invalid review priority={priority!r}", index, problem_id)

        source_text = require_object(
            record.get("source_text"),
            self.findings,
            "SOURCE_TEXT_SCHEMA",
            "source_text",
            index=index,
            problem_id=problem_id,
        )
        for key in ("title", "statement", "background"):
            if not isinstance(source_text.get(key), str):
                self.issue("SOURCE_TEXT_SCHEMA", f"source_text.{key} must be a string", index, problem_id)
        source_record = record.get("source_record")
        if not isinstance(source_record, dict):
            self.issue("SOURCE_RECORD_SCHEMA", "source_record must be an object", index, problem_id)

        implementation = require_object(
            record.get("implementation"),
            self.findings,
            "IMPLEMENTATION_SCHEMA",
            "implementation",
            index=index,
            problem_id=problem_id,
        )
        if implementation.get("record_schema_version") != self.record_schema_version:
            self.issue("RECORD_SCHEMA_VERSION", "record schema version mismatch", index, problem_id)
        if implementation.get("rule_version") != self.rule_version:
            self.issue("RULE_VERSION", "record rule version mismatch", index, problem_id)
        if implementation.get("calculation_state") != "fresh":
            self.issue("CALCULATION_STATE", "calculation_state must be fresh", index, problem_id)
        if not isinstance(implementation.get("dataset_version"), str) or not implementation.get(
            "dataset_version"
        ):
            self.issue("DATASET_VERSION", "implementation.dataset_version is required", index, problem_id)


def validate_top_metadata(
    metadata: dict[str, Any],
    base_metadata: dict[str, Any],
    expected_records: int,
    base_records: int,
    findings: Findings,
) -> None:
    source_segments = safe_get(metadata, "dataset_snapshot", "source_segments", default=[])
    segment_counts_ok = (
        isinstance(source_segments, list)
        and len(source_segments) == 2
        and isinstance(source_segments[0], dict)
        and isinstance(source_segments[1], dict)
        and source_segments[0].get("role") == "compatibility_base"
        and source_segments[0].get("record_count") == base_records
        and source_segments[1].get("role") == "mathdb_additions"
        and source_segments[1].get("record_count") == expected_records - base_records
    )
    checks = [
        (metadata.get("format_name") == "ulam_opdp_assessment_export", "FORMAT_NAME", "unexpected format_name"),
        (
            safe_get(metadata, "dataset_snapshot", "version") == EXPECTED_RELEASE_VERSION,
            "RELEASE_VERSION",
            f"dataset snapshot version must be {EXPECTED_RELEASE_VERSION}",
        ),
        (
            metadata.get("schema_version") == base_metadata.get("schema_version") == "1.0.0",
            "SCHEMA_VERSION",
            "v1.7 must retain top-level schema 1.0.0",
        ),
        (
            safe_get(metadata, "schema", "record_schema_version")
            == safe_get(base_metadata, "schema", "record_schema_version")
            == "1.0.0",
            "RECORD_SCHEMA_VERSION",
            "v1.7 must retain record schema 1.0.0",
        ),
        (
            safe_get(metadata, "schema", "record_field_order") == RECORD_FIELD_ORDER,
            "SCHEMA_FIELD_ORDER",
            "schema.record_field_order is not canonical",
        ),
        (
            safe_get(metadata, "schema", "record_required_fields") == RECORD_FIELD_ORDER,
            "SCHEMA_REQUIRED_FIELDS",
            "schema.record_required_fields is not canonical",
        ),
        (
            safe_get(metadata, "schema", "record_prefix_required")
            == ["problem_id", "difficulty_label", "explanation"],
            "SCHEMA_PREFIX",
            "schema record prefix changed",
        ),
        (
            safe_get(metadata, "schema", "additional_record_properties_allowed") is False,
            "SCHEMA_ADDITIONAL_PROPERTIES",
            "additional record properties must be prohibited",
        ),
        (
            safe_get(metadata, "dataset_snapshot", "record_count") == expected_records,
            "SNAPSHOT_COUNT",
            "dataset snapshot record_count mismatch",
        ),
        (
            segment_counts_ok,
            "SOURCE_SEGMENTS",
            "dataset source segments do not match the immutable base plus MathDB additions",
        ),
        (
            safe_get(metadata, "assessment_release", "records_assessed") == expected_records,
            "RELEASE_COUNT",
            "assessment_release.records_assessed mismatch",
        ),
        (
            safe_get(metadata, "assessment_release", "records_total") == expected_records,
            "RELEASE_COUNT",
            "assessment_release.records_total mismatch",
        ),
        (
            safe_get(metadata, "assessment_release", "compatibility", "mode") == "append_only",
            "COMPATIBILITY_MODE",
            "compatibility mode must be append_only",
        ),
        (
            safe_get(metadata, "assessment_release", "compatibility", "base_record_count")
            == base_records,
            "COMPATIBILITY_BASE_COUNT",
            "compatibility base count mismatch",
        ),
        (
            safe_get(metadata, "assessment_release", "compatibility", "added_record_count")
            == expected_records - base_records,
            "COMPATIBILITY_ADDED_COUNT",
            "compatibility added count mismatch",
        ),
        (
            safe_get(metadata, "assessment_release", "compatibility", "base_export_id")
            == base_metadata.get("export_id"),
            "COMPATIBILITY_BASE_ID",
            "compatibility base_export_id does not name v1.6",
        ),
        (
            safe_get(metadata, "assessment_release", "rule_version")
            == safe_get(base_metadata, "assessment_release", "rule_version"),
            "RULE_VERSION",
            "append-only release changed the OPDP rule version",
        ),
    ]
    for passed, code, message in checks:
        if not passed:
            findings.add(code, message)


def validate_release(args: argparse.Namespace) -> dict[str, Any]:
    findings = Findings(args.max_error_examples)
    manifest_file = args.manifest.resolve() if args.manifest else None
    manifest = load_manifest(manifest_file)
    manifest_base = manifest_file.parent if manifest_file else Path.cwd()

    def input_path(cli_value: Path | None, *manifest_locations: str) -> Path | None:
        if cli_value is not None:
            return cli_value.resolve()
        return resolve_path(manifest_path(manifest, *manifest_locations), base=manifest_base)

    final_path = input_path(
        args.input,
        "input",
        "payload",
        "payload.path",
        "output",
        "output.path",
        "outputs.output_path",
        "opdp.path",
    )
    snapshot_path = input_path(
        args.snapshot,
        "catalog.output_path",
        "catalog.final_path",
        "catalog.path",
        "details.final_path",
        "snapshot",
        "snapshot.path",
        "mathdb_snapshot",
        "mathdb_snapshot.path",
    )
    inventory_path = input_path(
        args.inventory,
        "inventory.path",
        "sitemap_inventory.path",
        "mathdb_inventory.path",
    )
    default_base = Path(__file__).resolve().parent.parent / "data" / "Ulam_UnsolvedMath_OPDP_Assessments_v1.6.json.gz"
    base_path = input_path(
        args.base,
        "base",
        "base.path",
        "base_payload",
        "base_payload.path",
    )
    base_path = base_path or default_base
    if final_path is None or snapshot_path is None or inventory_path is None:
        raise ValidationError("input, snapshot, and sitemap inventory paths are required")
    for path in (final_path, snapshot_path, inventory_path, base_path):
        if not path.is_file():
            raise ValidationError(f"file does not exist: {path}")

    manifest_total = manifest_get(
        manifest,
        "expected_records",
        "records_total",
        "total_records",
        "counts.total_records",
        "counts.records_total",
        "opdp.record_count",
        "release.record_count",
        "outputs.records_total",
        "dataset_snapshot.record_count",
        "assessment_release.records_total",
    )
    manifest_base_count = manifest_get(
        manifest,
        "base_record_count",
        "counts.base_records",
        "base.record_count",
        "inputs.base_count",
    )
    def count_value(value: Any, label: str) -> int:
        if isinstance(value, bool):
            raise ValidationError(f"{label} must be a nonnegative integer")
        try:
            result = int(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"{label} must be a nonnegative integer") from exc
        if result < 0 or (isinstance(value, float) and not value.is_integer()):
            raise ValidationError(f"{label} must be a nonnegative integer")
        return result

    expected_base_records = args.base_records if args.base_records is not None else (
        count_value(manifest_base_count, "manifest base count")
        if manifest_base_count is not None
        else DEFAULT_BASE_RECORDS
    )
    manifest_additions = manifest_get(
        manifest,
        "catalog.completed_count",
        "catalog.selected_count",
        "details.selected_count",
        "expected_mathdb_records",
        "mathdb_record_count",
        "counts.mathdb_records",
        "counts.added_records",
        "inputs.mathdb_count",
        "outputs.additions",
    )
    if args.expected_records is not None:
        expected_records = args.expected_records
    elif manifest_total is not None:
        expected_records = count_value(manifest_total, "manifest total count")
    elif manifest_additions is not None:
        expected_records = expected_base_records + count_value(
            manifest_additions, "manifest MathDB count"
        )
    else:
        expected_records = DEFAULT_EXPECTED_RECORDS
    expected_additions = expected_records - expected_base_records
    if expected_base_records <= 0 or expected_additions <= 0:
        raise ValidationError("expected total must exceed the base record count")

    if manifest.get("schema") == MATHDB_CATALOG_MANIFEST_SCHEMA:
        if manifest.get("status") != "complete":
            findings.add(
                "CATALOG_MANIFEST_STATUS",
                f"catalog manifest is not complete: {manifest.get('status')!r}",
            )
        inventory_manifest = manifest.get("inventory")
        if not isinstance(inventory_manifest, dict):
            findings.add(
                "CATALOG_MANIFEST_INVENTORY",
                "catalog manifest lacks frozen inventory metadata",
            )
        catalog = manifest.get("catalog")
        if not isinstance(catalog, dict) or catalog.get("status") != "complete":
            findings.add(
                "CATALOG_MANIFEST_CATALOG",
                "catalog manifest catalog.status must be complete",
            )
        elif catalog.get("completed_count") != expected_additions:
            findings.add(
                "CATALOG_MANIFEST_COUNT",
                "catalog.completed_count does not equal the expected additions",
            )
    elif manifest.get("schema") == MATHDB_MANIFEST_SCHEMA:
        inventory_manifest = manifest.get("inventory")
        if not isinstance(inventory_manifest, dict) or inventory_manifest.get(
            "status"
        ) != "complete":
            findings.add(
                "SNAPSHOT_MANIFEST_INVENTORY",
                "acquisition manifest inventory is not complete",
            )
        catalog = manifest.get("catalog")
        details = manifest.get("details")
        if isinstance(catalog, dict):
            if catalog.get("status") != "complete":
                findings.add(
                    "SNAPSHOT_MANIFEST_CATALOG",
                    f"catalog status is not complete: {catalog.get('status')!r}",
                )
            if catalog.get("completed_count") != expected_additions:
                findings.add(
                    "SNAPSHOT_MANIFEST_CATALOG_COUNT",
                    "catalog.completed_count does not equal the expected additions",
                )
        elif not isinstance(details, dict):
            findings.add(
                "SNAPSHOT_MANIFEST_SOURCE",
                "acquisition manifest has neither a completed catalog nor details section",
            )
        else:
            if details.get("completed_count") != details.get("selected_count"):
                findings.add(
                    "SNAPSHOT_MANIFEST_COMPLETENESS",
                    "details.completed_count differs from details.selected_count",
                )
            if safe_get(details, "post_run_validation", "status") != "passed":
                findings.add(
                    "SNAPSHOT_MANIFEST_VALIDATION",
                    "acquisition post-run validation did not pass",
                )

    id_offset = args.id_offset
    if not is_integer(id_offset) or id_offset < 1:
        raise ValidationError("--id-offset must be a positive integer")
    snapshot_source_pointer = args.snapshot_source_pointer or manifest_get(
        manifest, "snapshot.source_pointer", "mathdb_snapshot.source_pointer"
    )
    required_snapshot_schema = {
        "summary": MATHDB_SUMMARY_SCHEMA,
        "detail": MATHDB_DETAIL_SCHEMA,
    }.get(args.snapshot_schema)
    if args.snapshot_schema == "auto" and isinstance(manifest.get("catalog"), dict):
        required_snapshot_schema = MATHDB_SUMMARY_SCHEMA
    expected_snapshot_sha = manifest_get(
        manifest,
        "catalog.sha256",
        "details.sha256",
        "snapshot.sha256",
        "mathdb_snapshot.sha256",
    )
    actual_snapshot_sha = sha256_file(snapshot_path)
    if isinstance(expected_snapshot_sha, str) and (
        actual_snapshot_sha.casefold() != expected_snapshot_sha.casefold()
    ):
        findings.add(
            "SNAPSHOT_SHA256",
            f"snapshot SHA-256 {actual_snapshot_sha} != manifest {expected_snapshot_sha}",
        )
    expected_inventory_sha = manifest_get(
        manifest,
        "inventory.sha256",
        "sitemap_inventory.sha256",
        "mathdb_inventory.sha256",
    )
    actual_inventory_sha = sha256_file(inventory_path)
    if isinstance(expected_inventory_sha, str) and (
        actual_inventory_sha.casefold() != expected_inventory_sha.casefold()
    ):
        findings.add(
            "INVENTORY_SHA256",
            f"inventory SHA-256 {actual_inventory_sha} != manifest {expected_inventory_sha}",
        )
    manifest_inventory_count = manifest_get(
        manifest,
        "inventory.count",
        "sitemap_inventory.count",
        "mathdb_inventory.count",
    )
    if manifest_inventory_count is not None and count_value(
        manifest_inventory_count, "manifest inventory count"
    ) != expected_additions:
        findings.add(
            "MANIFEST_INVENTORY_COUNT",
            "manifest inventory count differs from the expected additions",
        )
    if manifest_additions is not None and count_value(
        manifest_additions, "manifest MathDB count"
    ) != expected_additions:
        selected_additions = count_value(manifest_additions, "manifest MathDB count")
        findings.add(
            "MANIFEST_ADDITION_COUNT",
            f"manifest selects {selected_additions:,} additions but expected total implies "
            f"{expected_additions:,}",
        )

    seen_new_problem_ids: set[int] = set()
    seen_snapshot_numbers: set[int] = set()
    seen_snapshot_source_ids: set[str] = set()
    seen_inventory_numbers: set[int] = set()
    snapshot_count = 0
    inventory_count = 0
    inventory_mapping_matches = 0
    snapshot_source_matches = 0
    snapshot_metadata_matches = 0
    mathdb_projection_matches = 0
    final_count = 0
    base_compared = 0
    base_mismatches = 0
    previous_id: int | None = None
    ascending_ids = True
    unique_ids = True
    seen_final_ids: set[int] = set()
    max_final_buffer = 0
    max_base_buffer = 0
    snapshot_rows_iter = iter_snapshot_rows(snapshot_path)
    inventory_rows_iter = iter_snapshot_rows(inventory_path)
    previous_snapshot_number: int | None = None
    previous_inventory_number: int | None = None
    snapshot_short_reported = False
    inventory_short_reported = False

    with StreamedPayload(base_path, max_value_chars=args.max_value_chars) as base_payload:
        base_metadata = dict(base_payload.start())
        base_records_iter = base_payload.records()
        with StreamedPayload(final_path, max_value_chars=args.max_value_chars) as final_payload:
            final_metadata = dict(final_payload.start())
            validate_top_metadata(
                final_metadata,
                base_metadata,
                expected_records,
                expected_base_records,
                findings,
            )
            payload_snapshot_sha = safe_get(final_metadata, "dataset_snapshot", "sha256")
            if not isinstance(payload_snapshot_sha, str) or (
                payload_snapshot_sha.casefold() != actual_snapshot_sha.casefold()
            ):
                findings.add(
                    "PAYLOAD_SNAPSHOT_SHA256",
                    "dataset_snapshot.sha256 does not identify the exact MathDB JSONL input",
                )
            record_validator = RecordValidator(final_metadata, findings)
            accumulator = SummaryAccumulator(record_validator.rubric)

            for index, record in enumerate(final_payload.records()):
                final_count += 1
                problem_id = record.get("problem_id")
                if not is_integer(problem_id):
                    findings.add(
                        "PROBLEM_ID_TYPE",
                        "problem_id must be an integer",
                        record_index=index,
                        problem_id=problem_id,
                    )
                if is_integer(problem_id):
                    if problem_id in seen_final_ids:
                        unique_ids = False
                        findings.add(
                            "PROBLEM_ID_DUPLICATE",
                            f"problem_id {problem_id} occurs more than once",
                            record_index=index,
                            problem_id=problem_id,
                        )
                    seen_final_ids.add(problem_id)
                    if previous_id is not None and problem_id <= previous_id:
                        ascending_ids = False
                        findings.add(
                            "PROBLEM_ID_ORDER",
                            f"problem_id {problem_id} is not greater than prior ID {previous_id}",
                            record_index=index,
                            problem_id=problem_id,
                        )
                    previous_id = problem_id

                if index < expected_base_records:
                    try:
                        base_record = next(base_records_iter)
                    except StopIteration:
                        findings.add(
                            "BASE_TOO_SHORT",
                            f"v1.6 base ended before record {index}",
                            record_index=index,
                            problem_id=problem_id,
                        )
                    else:
                        base_compared += 1
                        difference = first_difference(record, base_record)
                        if difference:
                            base_mismatches += 1
                            findings.add(
                                "BASE_RECORD_CHANGED",
                                difference,
                                record_index=index,
                                problem_id=problem_id,
                            )
                else:
                    if is_integer(problem_id):
                        seen_new_problem_ids.add(problem_id)
                    inventory_number: int | None = None
                    inventory_row: dict[str, Any] | None = None
                    try:
                        inventory_line, inventory_row = next(inventory_rows_iter)
                    except StopIteration:
                        if not inventory_short_reported:
                            findings.add(
                                "INVENTORY_TOO_SHORT",
                                "sitemap inventory ended before the final payload's additions",
                                record_index=index,
                                problem_id=problem_id,
                            )
                            inventory_short_reported = True
                    else:
                        inventory_count += 1
                        if inventory_row.get("schema") != MATHDB_INVENTORY_SCHEMA:
                            findings.add(
                                "INVENTORY_SCHEMA",
                                f"inventory line {inventory_line} has schema "
                                f"{inventory_row.get('schema')!r}",
                                record_index=index,
                                problem_id=problem_id,
                            )
                        inventory_number = first_integer(inventory_row, ("number",))
                        if inventory_number is None or inventory_number < 0:
                            findings.add(
                                "INVENTORY_NUMBER",
                                f"inventory line {inventory_line} lacks a nonnegative number",
                                record_index=index,
                                problem_id=problem_id,
                            )
                        else:
                            if inventory_number in seen_inventory_numbers:
                                findings.add(
                                    "INVENTORY_NUMBER_DUPLICATE",
                                    f"MathDB inventory number {inventory_number} is duplicated",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                            seen_inventory_numbers.add(inventory_number)
                            if (
                                previous_inventory_number is not None
                                and inventory_number <= previous_inventory_number
                            ):
                                findings.add(
                                    "INVENTORY_NUMBER_ORDER",
                                    f"inventory number {inventory_number} is not strictly greater "
                                    f"than {previous_inventory_number}",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                            previous_inventory_number = inventory_number
                    try:
                        snapshot_line, snapshot_row = next(snapshot_rows_iter)
                    except StopIteration:
                        if not snapshot_short_reported:
                            findings.add(
                                "SNAPSHOT_TOO_SHORT",
                                "MathDB snapshot ended before the final payload's additions",
                                record_index=index,
                                problem_id=problem_id,
                            )
                            snapshot_short_reported = True
                    else:
                        snapshot_count += 1
                        snapshot_problem, snapshot_metadata, envelope_schema = unwrap_snapshot_row(
                            snapshot_row, source_pointer=snapshot_source_pointer
                        )
                        if "problem" in snapshot_row:
                            if envelope_schema not in {
                                MATHDB_DETAIL_SCHEMA,
                                MATHDB_SUMMARY_SCHEMA,
                            }:
                                findings.add(
                                    "SNAPSHOT_ENVELOPE_SCHEMA",
                                    f"snapshot line {snapshot_line} has schema "
                                    f"{envelope_schema!r}; expected the detail or summary schema",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                            if (
                                required_snapshot_schema is not None
                                and envelope_schema != required_snapshot_schema
                            ):
                                findings.add(
                                    "REQUIRED_SNAPSHOT_ENVELOPE_SCHEMA",
                                    f"snapshot line {snapshot_line} has schema "
                                    f"{envelope_schema!r}, expected {required_snapshot_schema!r}",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                            if snapshot_metadata is None:
                                findings.add(
                                    "SNAPSHOT_ENVELOPE_METADATA",
                                    f"snapshot line {snapshot_line} lacks a snapshot object",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                        native_statement = snapshot_problem.get("statement")
                        native_excerpt = snapshot_problem.get("excerpt")
                        statement_present = isinstance(native_statement, str) and bool(
                            native_statement.strip()
                        )
                        excerpt_present = isinstance(native_excerpt, str) and bool(
                            native_excerpt.strip()
                        )
                        if envelope_schema == MATHDB_SUMMARY_SCHEMA:
                            assessed_text = native_excerpt if excerpt_present else None
                            expected_text_mode = "mathdb_public_list_excerpt"
                            expected_input_mode = (
                                "copied_mathdb_public_list_excerpt_plus_stored_editorial_inputs"
                            )
                            if not excerpt_present:
                                findings.add(
                                    "SUMMARY_EXCERPT_MISSING",
                                    "summary envelope problem.excerpt must be nonempty",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                            if not isinstance(snapshot_metadata, dict) or snapshot_metadata.get(
                                "text_basis"
                            ) != "mathdb_public_list_excerpt":
                                findings.add(
                                    "SUMMARY_TEXT_BASIS",
                                    "summary envelope snapshot.text_basis must be "
                                    "mathdb_public_list_excerpt",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                            if not isinstance(snapshot_metadata, dict) or snapshot_metadata.get(
                                "statement_completeness"
                            ) != "source_provided_excerpt_not_guaranteed_complete":
                                findings.add(
                                    "SUMMARY_STATEMENT_COMPLETENESS",
                                    "summary envelope must mark the excerpt as not guaranteed complete",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                        else:
                            assessed_text = (
                                native_statement
                                if statement_present
                                else native_excerpt
                                if excerpt_present
                                else None
                            )
                            expected_text_mode = (
                                "mathdb_native_field_projection"
                                if statement_present
                                else "mathdb_public_list_excerpt"
                            )
                            expected_input_mode = (
                                "copied_mathdb_source_plus_stored_editorial_inputs"
                                if statement_present
                                else "copied_mathdb_public_list_excerpt_plus_stored_editorial_inputs"
                            )
                        if assessed_text is None:
                            findings.add(
                                "MATHDB_ASSESSED_TEXT_MISSING",
                                "MathDB problem must provide a nonempty statement or excerpt",
                                record_index=index,
                                problem_id=problem_id,
                            )
                        source_text = record.get("source_text")
                        if not isinstance(source_text, dict):
                            source_text = {}
                        if assessed_text is not None and source_text.get(
                            "statement"
                        ) != assessed_text:
                            findings.add(
                                "SOURCE_TEXT_ASSESSED_TEXT",
                                "source_text.statement is not the exact native assessed text",
                                record_index=index,
                                problem_id=problem_id,
                            )
                        if source_text.get("text_mode") != expected_text_mode:
                            findings.add(
                                "SOURCE_TEXT_MODE",
                                f"source_text.text_mode must be {expected_text_mode!r} for "
                                f"envelope schema {envelope_schema!r}",
                                record_index=index,
                                problem_id=problem_id,
                            )
                        native_number = first_integer(
                            snapshot_problem,
                            ("number", "problem_number", "post_number"),
                        )
                        if native_number is None or native_number < 0:
                            findings.add(
                                "SNAPSHOT_NUMBER",
                                f"snapshot line {snapshot_line} has no nonnegative integer number",
                                record_index=index,
                                problem_id=problem_id,
                            )
                        else:
                            if native_number in seen_snapshot_numbers:
                                findings.add(
                                    "SNAPSHOT_NUMBER_DUPLICATE",
                                    f"MathDB number {native_number} appears more than once",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                            seen_snapshot_numbers.add(native_number)
                            if (
                                previous_snapshot_number is not None
                                and native_number <= previous_snapshot_number
                            ):
                                findings.add(
                                    "SNAPSHOT_NUMBER_ORDER",
                                    f"MathDB number {native_number} is not strictly greater than "
                                    f"{previous_snapshot_number}",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                            previous_snapshot_number = native_number
                            if inventory_number != native_number:
                                findings.add(
                                    "SNAPSHOT_INVENTORY_MAPPING",
                                    f"snapshot problem number {native_number} does not match "
                                    f"inventory number {inventory_number!r}",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                            else:
                                inventory_mapping_matches += 1
                            if isinstance(snapshot_metadata, dict) and isinstance(
                                inventory_row, dict
                            ):
                                snapshot_inventory_fields = {
                                    "inventory_url": inventory_row.get("url"),
                                    "inventory_lastmod": inventory_row.get("lastmod"),
                                    "inventory_sitemap_file": inventory_row.get(
                                        "sitemap_file"
                                    ),
                                }
                                for field, expected_value in snapshot_inventory_fields.items():
                                    if snapshot_metadata.get(field) != expected_value:
                                        findings.add(
                                            "SNAPSHOT_INVENTORY_PROVENANCE",
                                            f"snapshot.{field} differs from the frozen inventory",
                                            record_index=index,
                                            problem_id=problem_id,
                                        )
                            expected_problem_id = id_offset + native_number
                            if not -(2**53 - 1) <= expected_problem_id <= 2**53 - 1:
                                findings.add(
                                    "MATHDB_ID_SAFE_INTEGER",
                                    f"derived problem_id {expected_problem_id} is outside the safe integer range",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                            if problem_id != expected_problem_id:
                                findings.add(
                                    "MATHDB_ID_MAPPING",
                                    f"problem_id must be {id_offset} + {native_number} = "
                                    f"{expected_problem_id}",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                            expected_problem_number = f"MATHDB-{native_number}"
                            if record.get("problem_number") != expected_problem_number:
                                findings.add(
                                    "MATHDB_PROBLEM_NUMBER_MAPPING",
                                    f"problem_number must be MATHDB-{native_number}",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                            if (
                                problem_id == expected_problem_id
                                and record.get("problem_number") == expected_problem_number
                            ):
                                mathdb_projection_matches += 1

                        source_id = snapshot_problem.get("id")
                        try:
                            source_id_key = canonical_source_id(source_id)
                        except ValidationError as exc:
                            findings.add(
                                "SNAPSHOT_SOURCE_ID",
                                f"snapshot line {snapshot_line}: {exc}",
                                record_index=index,
                                problem_id=problem_id,
                            )
                        else:
                            if source_id_key in seen_snapshot_source_ids:
                                findings.add(
                                    "SNAPSHOT_SOURCE_ID_DUPLICATE",
                                    f"MathDB source id {source_id!r} appears more than once",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                            seen_snapshot_source_ids.add(source_id_key)

                        source_difference = first_difference(
                            record.get("source_record"), snapshot_problem
                        )
                        if source_difference:
                            findings.add(
                                "SOURCE_RECORD_NOT_SNAPSHOT_EQUAL",
                                source_difference,
                                record_index=index,
                                problem_id=problem_id,
                            )
                        else:
                            snapshot_source_matches += 1

                        provenance = record.get("provenance")
                        implementation = record.get("implementation")
                        if not isinstance(provenance, dict):
                            provenance = {}
                        if not isinstance(implementation, dict):
                            implementation = {}
                        if implementation.get("input_mode") != expected_input_mode:
                            findings.add(
                                "IMPLEMENTATION_INPUT_MODE",
                                f"implementation.input_mode must be {expected_input_mode!r}",
                                record_index=index,
                                problem_id=problem_id,
                            )
                        if envelope_schema == MATHDB_SUMMARY_SCHEMA and (
                            not isinstance(record.get("flags"), list)
                            or "source_excerpt_only" not in record["flags"]
                        ):
                            findings.add(
                                "SUMMARY_EXCERPT_FLAG",
                                "summary-derived records must carry source_excerpt_only",
                                record_index=index,
                                problem_id=problem_id,
                            )
                        if envelope_schema == MATHDB_SUMMARY_SCHEMA:
                            assigned_confidences = (
                                (
                                    "assessment_gate.confidence",
                                    safe_get(record, "assessment_gate", "confidence"),
                                ),
                                (
                                    "intrinsic_difficulty.confidence",
                                    safe_get(record, "intrinsic_difficulty", "confidence"),
                                ),
                                (
                                    "ai_assessment.confidence",
                                    safe_get(record, "ai_assessment", "confidence"),
                                ),
                                (
                                    "human_attention.effort.confidence",
                                    safe_get(
                                        record,
                                        "human_attention",
                                        "effort",
                                        "confidence",
                                    ),
                                ),
                                (
                                    "verification.confidence",
                                    safe_get(record, "verification", "confidence"),
                                ),
                                (
                                    "formalization.confidence",
                                    safe_get(record, "formalization", "confidence"),
                                ),
                                (
                                    "prerequisites.confidence",
                                    safe_get(record, "prerequisites", "confidence"),
                                ),
                            )
                            expected_c0 = {
                                "level": 0,
                                "code": "C0",
                                "status": "assigned",
                            }
                            for confidence_path, confidence_value in assigned_confidences:
                                if confidence_value != expected_c0:
                                    findings.add(
                                        "SUMMARY_ASSIGNED_CONFIDENCE_NOT_C0",
                                        f"{confidence_path} must be assigned C0 for "
                                        "an excerpt-only summary record",
                                        record_index=index,
                                        problem_id=problem_id,
                                    )
                            if (
                                safe_get(record, "tractability", "status") == "scored"
                                and safe_get(record, "tractability", "confidence")
                                != expected_c0
                            ):
                                findings.add(
                                    "SUMMARY_TRACTABILITY_CONFIDENCE_NOT_C0",
                                    "tractability.confidence must be assigned C0 when an "
                                    "excerpt-only summary record is scored",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                            expected_completeness = (
                                snapshot_metadata.get("statement_completeness")
                                if isinstance(snapshot_metadata, dict)
                                else None
                            )
                            if provenance.get(
                                "mathdb_text_basis"
                            ) != "mathdb_public_list_excerpt" or provenance.get(
                                "statement_completeness"
                            ) != expected_completeness:
                                findings.add(
                                    "SUMMARY_PROVENANCE_TEXT_BASIS",
                                    "summary-derived provenance does not preserve its text basis "
                                    "and completeness marker",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                            evidence_codes = safe_get(
                                record, "evidence", "codes", default=[]
                            )
                            if not isinstance(evidence_codes, list) or (
                                "text:public_list_excerpt" not in evidence_codes
                            ):
                                findings.add(
                                    "SUMMARY_EVIDENCE_TEXT_BASIS",
                                    "summary-derived evidence must identify the public-list excerpt",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                        provenance_snapshot_difference = first_difference(
                            provenance.get("mathdb_snapshot"), snapshot_metadata
                        )
                        implementation_snapshot_difference = first_difference(
                            implementation.get("source_snapshot"), snapshot_metadata
                        )
                        envelope_schema_matches = (
                            implementation.get("source_envelope_schema") == envelope_schema
                        )
                        if provenance_snapshot_difference:
                            findings.add(
                                "PROVENANCE_SNAPSHOT_MISMATCH",
                                "provenance.mathdb_snapshot is not the envelope snapshot object",
                                record_index=index,
                                problem_id=problem_id,
                            )
                        if implementation_snapshot_difference:
                            findings.add(
                                "IMPLEMENTATION_SNAPSHOT_MISMATCH",
                                "implementation.source_snapshot is not the envelope snapshot object",
                                record_index=index,
                                problem_id=problem_id,
                            )
                        if not envelope_schema_matches:
                            findings.add(
                                "SOURCE_ENVELOPE_SCHEMA_MISMATCH",
                                "implementation.source_envelope_schema differs from the snapshot row",
                                record_index=index,
                                problem_id=problem_id,
                            )
                        if (
                            not provenance_snapshot_difference
                            and not implementation_snapshot_difference
                            and envelope_schema_matches
                        ):
                            snapshot_metadata_matches += 1
                        if native_number is not None and provenance.get(
                            "mathdb_number"
                        ) != native_number:
                            findings.add(
                                "PROVENANCE_MATHDB_NUMBER",
                                "provenance.mathdb_number differs from the native number",
                                record_index=index,
                                problem_id=problem_id,
                            )
                        if provenance.get("mathdb_id") != source_id:
                            findings.add(
                                "PROVENANCE_MATHDB_ID",
                                "provenance.mathdb_id differs from source_record.id",
                                record_index=index,
                                problem_id=problem_id,
                            )
                        engagement = provenance.get("engagement")
                        if not isinstance(engagement, dict) or engagement.get(
                            "excluded_from_scoring"
                        ) is not True:
                            findings.add(
                                "MATHDB_ENGAGEMENT_SCORING",
                                "MathDB engagement must be explicitly excluded from scoring",
                                record_index=index,
                                problem_id=problem_id,
                            )
                        dataset_sha = implementation.get("dataset_sha256")
                        if not isinstance(dataset_sha, str) or (
                            dataset_sha.casefold() != actual_snapshot_sha.casefold()
                        ):
                            findings.add(
                                "IMPLEMENTATION_SNAPSHOT_SHA256",
                                "implementation.dataset_sha256 does not identify the snapshot JSONL",
                                record_index=index,
                                problem_id=problem_id,
                            )

                        if snapshot_metadata is not None:
                            stored_problem_hash = snapshot_metadata.get(
                                "canonical_problem_sha256"
                            ) or snapshot_metadata.get(
                                "problem_canonical_sha256"
                            )
                            if envelope_schema in {
                                MATHDB_DETAIL_SCHEMA,
                                MATHDB_SUMMARY_SCHEMA,
                            } and not isinstance(
                                stored_problem_hash, str
                            ):
                                findings.add(
                                    "SNAPSHOT_CANONICAL_PROBLEM_SHA256",
                                    "snapshot lacks its canonical problem SHA-256",
                                    record_index=index,
                                    problem_id=problem_id,
                                )
                            elif isinstance(stored_problem_hash, str) and (
                                stored_problem_hash.casefold()
                                != canonical_digest(snapshot_problem).casefold()
                            ):
                                findings.add(
                                    "SNAPSHOT_CANONICAL_PROBLEM_SHA256",
                                    "snapshot canonical problem hash is invalid",
                                    record_index=index,
                                    problem_id=problem_id,
                                )

                record_validator.validate(record, index)
                accumulator.observe(record)

            final_metadata = final_payload.metadata
            assert final_payload.reader is not None
            max_final_buffer = final_payload.reader.max_buffer_chars

        # Exhaust the base stream so its actual count, JSON terminator, and gzip
        # CRC are checked even when the candidate payload ended early.
        remaining_base_records = sum(1 for _ in base_records_iter)
        actual_base_records = base_compared + remaining_base_records
        if actual_base_records != expected_base_records:
            findings.add(
                "BASE_RECORD_COUNT",
                f"base contains {actual_base_records:,} records; expected "
                f"{expected_base_records:,}",
            )
        assert base_payload.reader is not None
        max_base_buffer = base_payload.reader.max_buffer_chars

    # Drain any snapshot tail to validate every JSONL line and prove exact
    # one-to-one coverage rather than accepting a matching prefix.
    unrepresented_snapshot_rows = 0
    for _line_number, _row in snapshot_rows_iter:
        snapshot_count += 1
        unrepresented_snapshot_rows += 1
    if unrepresented_snapshot_rows:
        findings.add(
            "SNAPSHOT_ROWS_MISSING_FROM_PAYLOAD",
            f"{unrepresented_snapshot_rows:,} trailing snapshot rows have no payload record",
        )
    unrepresented_inventory_rows = 0
    for _line_number, _row in inventory_rows_iter:
        inventory_count += 1
        unrepresented_inventory_rows += 1
    if unrepresented_inventory_rows:
        findings.add(
            "INVENTORY_ROWS_MISSING_FROM_PAYLOAD",
            f"{unrepresented_inventory_rows:,} trailing inventory rows have no payload record",
        )

    if final_count != expected_records:
        findings.add(
            "FINAL_RECORD_COUNT",
            f"final payload has {final_count:,} records; expected {expected_records:,}",
        )
    if base_compared != expected_base_records:
        findings.add(
            "BASE_COMPARISON_COUNT",
            f"compared {base_compared:,} base records; expected {expected_base_records:,}",
        )
    observed_additions = max(0, final_count - expected_base_records)
    if snapshot_count != expected_additions:
        findings.add(
            "SNAPSHOT_COUNT",
            f"snapshot has {snapshot_count:,} rows; expected {expected_additions:,}",
        )
    if inventory_count != expected_additions:
        findings.add(
            "INVENTORY_COUNT",
            f"sitemap inventory has {inventory_count:,} rows; expected {expected_additions:,}",
        )
    if observed_additions != expected_additions:
        findings.add(
            "MATHDB_ADDITION_COUNT",
            f"payload has {observed_additions:,} additions; expected {expected_additions:,}",
        )
    if snapshot_source_matches != expected_additions:
        findings.add(
            "SNAPSHOT_SOURCE_MATCH_COUNT",
            f"{snapshot_source_matches:,} additions contain an exact snapshot problem; "
            f"expected {expected_additions:,}",
        )
    if inventory_mapping_matches != expected_additions:
        findings.add(
            "INVENTORY_MAPPING_MATCH_COUNT",
            f"{inventory_mapping_matches:,} snapshot rows map exactly to inventory rows; "
            f"expected {expected_additions:,}",
        )

    computed_summary = accumulator.result()
    stored_summary = final_metadata.get("summary")
    if not isinstance(stored_summary, dict):
        findings.add("SUMMARY_MISSING", "top-level summary must be an object")
    else:
        difference = first_difference(stored_summary, computed_summary)
        if difference:
            findings.add("SUMMARY_RECOMPUTATION", difference)

    for registry_name, summary_name in (
        ("tag_registry", "tag_counts"),
        ("flag_registry", "flag_counts"),
        ("route_registry", "route_counts"),
    ):
        registry = record_validator.rubric.get(registry_name)
        expected_counts = computed_summary[summary_name]
        if not isinstance(registry, dict):
            findings.add("REGISTRY_SCHEMA", f"rubric.{registry_name} must be an object")
            continue
        for code, entry in registry.items():
            if not isinstance(entry, dict) or entry.get("record_count") != expected_counts.get(
                code, 0
            ):
                findings.add(
                    "REGISTRY_COUNT",
                    f"rubric.{registry_name}.{code}.record_count does not match records",
                )

    def report_path(path: Path) -> str:
        try:
            return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
        except ValueError:
            return path.name

    report = {
        "status": "pass" if findings.error_count == 0 else "fail",
        "validator": "validate_v1_7_mathdb_expansion.py",
        "streaming": True,
        "input": report_path(final_path),
        "base": report_path(base_path),
        "snapshot": report_path(snapshot_path),
        "sitemap_inventory": report_path(inventory_path),
        "mathdb_id_mapping": f"problem_id = {id_offset} + problem.number",
        "snapshot_sha256": actual_snapshot_sha,
        "sitemap_inventory_sha256": actual_inventory_sha,
        "expected_records": expected_records,
        "expected_base_records": expected_base_records,
        "expected_mathdb_records": expected_additions,
        "observed": {
            "final_records": final_count,
            "base_records_compared": base_compared,
            "base_record_mismatches": base_mismatches,
            "snapshot_rows": snapshot_count,
            "sitemap_inventory_rows": inventory_count,
            "unique_new_problem_ids_seen": len(seen_new_problem_ids),
            "unique_snapshot_numbers_seen": len(seen_snapshot_numbers),
            "unique_snapshot_source_ids_seen": len(seen_snapshot_source_ids),
            "unique_inventory_numbers_seen": len(seen_inventory_numbers),
            "snapshot_inventory_mapping_matches": inventory_mapping_matches,
            "id_projection_matches": mathdb_projection_matches,
            "exact_source_record_matches": snapshot_source_matches,
            "exact_snapshot_metadata_matches": snapshot_metadata_matches,
        },
        "bounded_memory_evidence": {
            "maximum_final_parser_buffer_characters": max_final_buffer,
            "maximum_base_parser_buffer_characters": max_base_buffer,
            "final_payload_or_records_array_materialized": False,
        },
        "checks": {
            "strict_gzip_utf8_json": True,
            "duplicate_json_keys": (
                "strictly rejected in payload, base, manifest, snapshot JSONL, "
                "and inventory JSONL"
            ),
            "ascending_unique_problem_ids": ascending_ids
            and unique_ids
            and len(seen_final_ids) == final_count,
            "base_prefix_deep_equality": base_mismatches == 0
            and base_compared == expected_base_records,
            "mathdb_id_projection": mathdb_projection_matches == expected_additions,
            "mathdb_snapshot_mapping_coverage": snapshot_count == expected_additions
            and inventory_count == expected_additions
            and observed_additions == expected_additions
            and inventory_mapping_matches == expected_additions
            and snapshot_source_matches == expected_additions
            and snapshot_metadata_matches == expected_additions
            and len(seen_snapshot_numbers) == expected_additions
            and len(seen_snapshot_source_ids) == expected_additions
            and len(seen_inventory_numbers) == expected_additions,
            "record_schema_enums_ranges_and_formulas": "validated for every decoded record",
            "ten_rationale_fields": "validated for every decoded record",
            "summary_recomputed": isinstance(stored_summary, dict)
            and first_difference(stored_summary, computed_summary) is None,
        },
        "error_count": findings.error_count,
        "error_examples": findings.errors,
        "error_examples_truncated": findings.error_count > len(findings.errors),
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stream-validate the gzip OPDP v1.7 MathDB append-only expansion "
            "without materializing the final payload."
        )
    )
    parser.add_argument("--input", type=Path, help="v1.7 monolithic JSON.GZ payload")
    parser.add_argument(
        "--base",
        type=Path,
        help="v1.6 JSON.GZ base (defaults to data/Ulam_UnsolvedMath_OPDP_Assessments_v1.6.json.gz)",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        help="frozen MathDB summary/detail envelope JSONL or JSONL.GZ",
    )
    parser.add_argument(
        "--inventory",
        type=Path,
        help="frozen MathDB sitemap inventory JSONL (manifest inventory.path by default)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="optional acquisition/release manifest supplying snapshot path and counts",
    )
    parser.add_argument(
        "--expected-records",
        type=int,
        help=f"expected final total (default {DEFAULT_EXPECTED_RECORDS:,}; manifest overrides default)",
    )
    parser.add_argument(
        "--base-records",
        type=int,
        help=f"expected immutable prefix (default {DEFAULT_BASE_RECORDS:,})",
    )
    parser.add_argument(
        "--id-offset",
        type=int,
        default=DEFAULT_MATHDB_ID_OFFSET,
        help=f"native MathDB number offset (default {DEFAULT_MATHDB_ID_OFFSET})",
    )
    parser.add_argument(
        "--snapshot-source-pointer",
        help="optional JSON pointer to the native problem object; auto-detects /problem envelopes",
    )
    parser.add_argument(
        "--snapshot-schema",
        choices=("auto", "summary", "detail"),
        default="auto",
        help="required envelope kind; auto requires summary when manifest.catalog is present",
    )
    parser.add_argument(
        "--max-value-chars",
        type=int,
        default=DEFAULT_MAX_VALUE_CHARS,
        help="maximum size of one metadata value or record, not the whole file",
    )
    parser.add_argument(
        "--max-error-examples",
        type=int,
        default=100,
        help="retain at most this many error examples while still counting all errors",
    )
    parser.add_argument("--report", type=Path, help="optional JSON validation report path")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        report = validate_release(args)
    except (ValidationError, DuplicateKeyError, OSError, UnicodeError, EOFError) as exc:
        report = {
            "status": "fail",
            "validator": "validate_v1_7_mathdb_expansion.py",
            "streaming": True,
            "fatal_error": str(exc),
        }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8", newline="\n")
    sys.stdout.write(rendered)
    return 0 if report.get("status") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
