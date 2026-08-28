#!/usr/bin/env python3
"""Build the OPDP v1.6 source registry and cohort/source statistics.

The script treats published OPDP releases as immutable vintages and derives a
natural source unit for dependence-aware summaries:

* v1.2: AMR source list where recoverable; otherwise the frozen collection.
* v1.5: AIM workshop tag.
* v1.6: exact Oberwolfach Report DOI.

It intentionally does not refresh legacy records from later upstream edits.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import numpy as np


AUTHOR = "Alejandro Zarzuelo Urdiales with ChatGPT 5.6 Sol"
RELEASE_DATE = "2026-08-26"
EXPECTED_COUNTS = {"V0": 5426, "V1": 3359, "V2": 6673}
COHORTS = {
    "1.2.0": ("V0", "Original v1.2 compatibility cohort"),
    "1.5.0": ("V1", "AIM v1.5 additions"),
    "1.6.0": ("V2", "Oberwolfach v1.6 additions"),
}

DIMENSIONS: dict[str, dict[str, Any]] = {
    "D": {"label": "Intrinsic difficulty", "path": ("intrinsic_difficulty", "score"), "family": "intrinsic", "direction": "burden"},
    "CG": {"label": "Conceptual gap", "path": ("intrinsic_difficulty", "factors", "conceptual_gap", "score"), "family": "intrinsic", "direction": "burden"},
    "RG": {"label": "Route gap", "path": ("intrinsic_difficulty", "factors", "route_gap", "score"), "family": "intrinsic", "direction": "burden"},
    "TD": {"label": "Technical depth", "path": ("intrinsic_difficulty", "factors", "technical_depth", "score"), "family": "intrinsic", "direction": "burden"},
    "KB": {"label": "Known barrier", "path": ("intrinsic_difficulty", "factors", "known_barrier", "score"), "family": "intrinsic", "direction": "burden"},
    "SS": {"label": "Search scale", "path": ("intrinsic_difficulty", "factors", "search_scale", "score"), "family": "intrinsic", "direction": "burden"},
    "AI_adj": {"label": "AI-relative adjustment", "path": ("ai_assessment", "relative_adjustment"), "family": "protocol", "direction": "burden"},
    "AI_D": {"label": "AI difficulty", "path": ("ai_assessment", "difficulty_score"), "family": "protocol", "direction": "burden"},
    "H": {"label": "Human-effort prior", "path": ("human_attention", "effort", "score"), "family": "context", "direction": "context"},
    "X": {"label": "Exposure", "path": ("human_attention", "exposure", "score"), "family": "context", "direction": "context"},
    "T": {"label": "100-hour tractability", "path": ("tractability", "score"), "family": "protocol", "direction": "favorable"},
    "V_true": {"label": "Verification if true", "path": ("verification", "if_true_score"), "family": "execution", "direction": "burden"},
    "V_false": {"label": "Verification if false", "path": ("verification", "if_false_score"), "family": "execution", "direction": "burden"},
    "F": {"label": "Formalization burden", "path": ("formalization", "score"), "family": "execution", "direction": "burden"},
    "P": {"label": "Prerequisite preparation", "path": ("prerequisites", "preparation_score"), "family": "execution", "direction": "burden"},
    "B": {"label": "Breadth", "path": ("prerequisites", "breadth_score"), "family": "execution", "direction": "burden"},
    "L": {"label": "Tool leverage", "path": ("tool_leverage", "score"), "family": "execution", "direction": "favorable"},
    "ambiguity": {"label": "Ambiguity", "path": ("classification", "ambiguity_score"), "family": "context", "direction": "burden"},
    "literature": {"label": "Literature load", "path": ("evidence", "literature_load_score"), "family": "context", "direction": "context"},
    "collection_prior": {"label": "Collection-barrier prior", "path": ("evidence", "collection_barrier_prior"), "family": "context", "direction": "context"},
}

PROFILE_DISTANCE_DIMS = ["D", "CG", "RG", "TD", "KB", "SS", "AI_adj", "AI_D", "T", "V_true", "V_false", "F", "P", "B", "L"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path, help="Complete OPDP v1.6 JSON or JSON.GZ")
    parser.add_argument("--base-v15", required=True, type=Path, help="Published OPDP v1.5 JSON or JSON.GZ")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--upstream-revision", default="b9437975f3c873f635a13c48f8b022f5ba80898a")
    parser.add_argument("--upstream-sha256", default="0A11E1B86385B6095F001803E2BB9D176B3498DA0932D7AF3700E1132EF153AC")
    parser.add_argument("--bootstrap-replicates", type=int, default=4000)
    return parser.parse_args()


def load_json(path: Path) -> Any:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def uncompressed_json_sha256(path: Path) -> str:
    """Hash JSON content identically whether the transport is .json or .json.gz."""
    h = hashlib.sha256()
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def get(record: dict[str, Any], path: Iterable[str]) -> Any:
    value: Any = record
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"https?://", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "unknown"


def normalize_url(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().rstrip("/")


def background_field(background: str, label: str) -> str | None:
    match = re.search(rf"^{re.escape(label)}:\s*(.+?)\s*$", background or "", re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else None


def cohort(record: dict[str, Any]) -> tuple[str, str]:
    version = str(get(record, ("implementation", "dataset_version")) or "")
    if version not in COHORTS:
        raise ValueError(f"Unexpected implementation.dataset_version={version!r} for {record.get('problem_id')}")
    return COHORTS[version]


def source_assignment(record: dict[str, Any]) -> dict[str, Any]:
    code, label = cohort(record)
    source = record.get("source_record") or {}
    classification = get(record, ("classification", "source_collection")) or {}
    collection_label = classification.get("label") or "Unassigned"
    collection_slug = classification.get("slug") or slugify(collection_label)
    background = str(source.get("background") or "")
    raw_url = normalize_url(source.get("source_url"))
    if not raw_url:
        raw_url = normalize_url(background_field(background, "Source URL"))
    if not raw_url:
        candidate = normalize_url(get(record, ("provenance", "canonical_source_url")))
        if candidate and "unsolvedmath.com/problems/" not in candidate:
            raw_url = candidate

    natural_id: str | None
    natural_label: str
    natural_type: str
    method: str
    confidence: str
    citation = source.get("source_citation")
    source_year: int | None = None

    if collection_label == "Oberwolfach Reports Open Problems":
        doi_match = re.search(r"10\.4171/owr/\d{4}/\d+", raw_url or "", re.IGNORECASE)
        if not doi_match or not citation:
            raise ValueError(f"OWR record lacks DOI/citation: {record['problem_id']}")
        doi = doi_match.group(0).lower()
        natural_id = f"doi:{doi}"
        natural_label = str(citation)
        natural_type = "oberwolfach_report"
        method = "structured_source_url_and_citation"
        confidence = "C3"
        year = source.get("proposed_year")
        source_year = int(year) if isinstance(year, (int, float)) else None
    elif collection_label == "AIM Workshop Problem Lists":
        workshop_tag = next((t.split(":", 1)[1] for t in source.get("tags", []) if isinstance(t, str) and t.startswith("aim-workshop:")), None)
        workshop = background_field(background, "Workshop")
        if not workshop_tag:
            raise ValueError(f"AIM record lacks workshop tag: {record['problem_id']}")
        natural_id = f"aim-workshop:{workshop_tag}"
        natural_label = workshop or workshop_tag
        natural_type = "aim_workshop_problem_list"
        method = "structured_tag_plus_background"
        confidence = "C3"
    elif collection_label == "AMR Open Problem Lists":
        source_list = background_field(background, "Source list")
        if source_list:
            natural_id = f"amr-source-list:{slugify(source_list)}"
            natural_label = source_list
            natural_type = "amr_source_list"
            method = "parsed_frozen_background"
            confidence = "C2"
            year_match = re.search(r"\b((?:18|19|20)\d{2})\b", source_list)
            source_year = int(year_match.group(1)) if year_match else None
        else:
            natural_id = f"collection:{collection_slug}"
            natural_label = collection_label
            natural_type = "collection_fallback"
            method = "collection_fallback"
            confidence = "C1"
    elif collection_label == "Unassigned":
        natural_id = None
        natural_label = "Unknown / unassigned"
        natural_type = "unknown"
        method = "unassigned"
        confidence = "C0"
    else:
        natural_id = f"collection:{collection_slug}"
        natural_label = collection_label
        natural_type = "named_collection"
        method = "frozen_collection"
        confidence = "C2"

    return {
        "problem_id": record["problem_id"],
        "problem_number": record.get("problem_number"),
        "cohort_code": code,
        "cohort_label": label,
        "source_collection_id": classification.get("id"),
        "source_collection_slug": collection_slug,
        "source_collection_label": collection_label,
        "source_document_id": natural_id,
        "source_document_label": natural_label,
        "source_document_type": natural_type,
        "source_document_url": raw_url,
        "source_citation": citation,
        "source_year": source_year,
        "source_assignment_method": method,
        "source_assignment_confidence": confidence,
        "literature_checked_at": source.get("literature_checked_at"),
        "supporting_evidence_links": source.get("literature_sources") or [],
    }


def as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        return float(value)
    return None


def values(rows: list[dict[str, Any]], dim: str) -> np.ndarray:
    path = DIMENSIONS[dim]["path"]
    result = [as_float(get(row, path)) for row in rows]
    return np.asarray([x for x in result if x is not None], dtype=float)


def descriptive(array: np.ndarray) -> dict[str, Any]:
    if array.size == 0:
        return {key: None for key in ("n", "mean", "sd", "min", "p05", "p25", "median", "p75", "p95", "max")}
    q = np.quantile(array, [0.05, 0.25, 0.5, 0.75, 0.95])
    return {
        "n": int(array.size), "mean": float(np.mean(array)), "sd": float(np.std(array, ddof=1)) if array.size > 1 else 0.0,
        "min": float(np.min(array)), "p05": float(q[0]), "p25": float(q[1]), "median": float(q[2]),
        "p75": float(q[3]), "p95": float(q[4]), "max": float(np.max(array)),
    }


def hedges_g(a: np.ndarray, b: np.ndarray) -> float | None:
    if a.size < 2 or b.size < 2:
        return None
    pooled_var = ((a.size - 1) * np.var(a, ddof=1) + (b.size - 1) * np.var(b, ddof=1)) / (a.size + b.size - 2)
    if pooled_var <= 0:
        return 0.0
    d = (float(np.mean(b)) - float(np.mean(a))) / math.sqrt(float(pooled_var))
    correction = 1.0 - 3.0 / (4.0 * (a.size + b.size) - 9.0)
    return d * correction


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> float | None:
    if a.size == 0 or b.size == 0:
        return None
    ordered = np.sort(a)
    less = np.searchsorted(ordered, b, side="left")
    greater = a.size - np.searchsorted(ordered, b, side="right")
    return float((np.sum(less) - np.sum(greater)) / (a.size * b.size))


def wasserstein_1(a: np.ndarray, b: np.ndarray) -> float | None:
    if a.size == 0 or b.size == 0:
        return None
    a = np.sort(a); b = np.sort(b)
    grid = np.sort(np.unique(np.concatenate([a, b])))
    if grid.size < 2:
        return 0.0
    fa = np.searchsorted(a, grid[:-1], side="right") / a.size
    fb = np.searchsorted(b, grid[:-1], side="right") / b.size
    return float(np.sum(np.abs(fa - fb) * np.diff(grid)))


def normal_two_sided_p(delta: float, se: float) -> float:
    if se <= 0:
        return 1.0 if delta == 0 else 0.0
    return float(math.erfc(abs(delta / se) / math.sqrt(2.0)))


def bh_adjust(p_values: list[float]) -> list[float]:
    n = len(p_values)
    order = sorted(range(n), key=lambda i: p_values[i])
    adjusted = [1.0] * n
    running = 1.0
    for rank in range(n, 0, -1):
        idx = order[rank - 1]
        running = min(running, p_values[idx] * n / rank)
        adjusted[idx] = min(1.0, running)
    return adjusted


def source_unit_means(rows: list[dict[str, Any]], assignments: dict[int, dict[str, Any]], dim: str) -> np.ndarray:
    grouped: dict[str, list[float]] = defaultdict(list)
    path = DIMENSIONS[dim]["path"]
    for row in rows:
        unit = assignments[row["problem_id"]]["source_document_id"]
        value = as_float(get(row, path))
        if unit is not None and value is not None:
            grouped[unit].append(value)
    return np.asarray([statistics.fmean(xs) for xs in grouped.values()], dtype=float)


def bootstrap_source_macro_delta(a: np.ndarray, b: np.ndarray, reps: int, rng: np.random.Generator) -> dict[str, float | None]:
    if a.size < 2 or b.size < 2:
        return {"ci90_low": None, "ci90_high": None, "ci95_low": None, "ci95_high": None}
    draws = np.empty(reps, dtype=float)
    batch = 200
    cursor = 0
    while cursor < reps:
        take = min(batch, reps - cursor)
        a_idx = rng.integers(0, a.size, size=(take, a.size))
        b_idx = rng.integers(0, b.size, size=(take, b.size))
        draws[cursor:cursor + take] = np.mean(b[b_idx], axis=1) - np.mean(a[a_idx], axis=1)
        cursor += take
    q = np.quantile(draws, [0.025, 0.05, 0.95, 0.975])
    return {"ci90_low": float(q[1]), "ci90_high": float(q[2]), "ci95_low": float(q[0]), "ci95_high": float(q[3])}


def fixed_group_eta2(rows: list[dict[str, Any]], assignments: dict[int, dict[str, Any]], dim: str) -> dict[str, Any]:
    path = DIMENSIONS[dim]["path"]
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        unit = assignments[row["problem_id"]]["source_document_id"]
        value = as_float(get(row, path))
        if unit is not None and value is not None:
            grouped[unit].append(value)
    flat = [x for xs in grouped.values() for x in xs]
    if len(flat) < 2 or len(grouped) < 2:
        return {"n": len(flat), "groups": len(grouped), "eta2": None, "within_sd": None, "total_sd": None}
    mean = statistics.fmean(flat)
    total_ss = sum((x - mean) ** 2 for x in flat)
    within_ss = sum(sum((x - statistics.fmean(xs)) ** 2 for x in xs) for xs in grouped.values())
    return {
        "n": len(flat), "groups": len(grouped), "eta2": 1.0 - within_ss / total_ss if total_ss else 0.0,
        "within_sd": math.sqrt(within_ss / (len(flat) - len(grouped))) if len(flat) > len(grouped) else None,
        "total_sd": statistics.stdev(flat),
    }


def practical_direction(dim: str, delta: float) -> str:
    direction = DIMENSIONS[dim]["direction"]
    if abs(delta) < 1e-12:
        return "unchanged"
    higher = delta > 0
    if direction == "burden":
        return "newer higher / more burdensome" if higher else "newer lower / less burdensome"
    if direction == "favorable":
        return "newer higher / more favorable" if higher else "newer lower / less favorable"
    return "newer higher (context signal)" if higher else "newer lower (context signal)"


def magnitude(g: float | None) -> str:
    if g is None:
        return "not_available"
    value = abs(g)
    if value < 0.10: return "negligible"
    if value < 0.20: return "very_small"
    if value < 0.50: return "small"
    if value < 0.80: return "moderate"
    return "large"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError(f"No rows for {path}")
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def write_reproducible_gzip_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as zipped:
            with io.TextIOWrapper(zipped, encoding="utf-8", newline="\n") as text:
                json.dump(payload, text, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                text.write("\n")


def main() -> None:
    args = parse_args()
    payload = load_json(args.input)
    base = load_json(args.base_v15)
    records: list[dict[str, Any]] = payload["records"]
    base_records: list[dict[str, Any]] = base["records"]
    if len(records) != 15458 or len(base_records) != 8785:
        raise ValueError("Unexpected release sizes")
    ids = [r["problem_id"] for r in records]
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ValueError("v1.6 IDs are not strictly ascending and unique")
    if records[:len(base_records)] != base_records:
        raise ValueError("Append-only failure: published v1.5 records changed")

    by_cohort: dict[str, list[dict[str, Any]]] = defaultdict(list)
    assignments: dict[int, dict[str, Any]] = {}
    for record in records:
        code, _ = cohort(record)
        by_cohort[code].append(record)
        assignments[record["problem_id"]] = source_assignment(record)
    observed_counts = {code: len(rows) for code, rows in by_cohort.items()}
    if observed_counts != EXPECTED_COUNTS:
        raise ValueError(f"Unexpected cohort counts: {observed_counts}")

    source_sizes = Counter(a["source_document_id"] for a in assignments.values() if a["source_document_id"] is not None)
    for assignment in assignments.values():
        assignment["source_document_problem_count"] = source_sizes.get(assignment["source_document_id"])

    sidecar = {
        "document": "OPDP v1.6 source-provenance hierarchy",
        "schema_version": "1.0.0",
        "author": AUTHOR,
        "release_date": RELEASE_DATE,
        "record_count": len(records),
        "join_key": "problem_id",
        "upstream": {"revision": args.upstream_revision, "problems_sha256": args.upstream_sha256},
        "opdp_input": {
            "path": args.input.name[:-3] if args.input.name.endswith(".gz") else args.input.name,
            "sha256": uncompressed_json_sha256(args.input),
        },
        "compatibility": {
            "base": args.base_v15.name[:-3] if args.base_v15.name.endswith(".gz") else args.base_v15.name,
            "base_record_count": len(base_records),
            "prior_records_changed": 0,
        },
        "hierarchy": ["release_cohort", "source_collection", "source_document"],
        "source_document_rules": {
            "OWR": "Exact DOI from source_record.source_url, labelled by source_record.source_citation.",
            "AIM": "aim-workshop:<slug> source tag, labelled by the frozen Workshop background field.",
            "AMR": "Exact frozen Source list background field; source URL retained separately.",
            "other_named_collections": "Frozen collection slug.",
            "unassigned": "Explicit null source_document_id; no source is fabricated.",
        },
        "counts": {
            "cohorts": observed_counts,
            "source_collections": len({a["source_collection_label"] for a in assignments.values()}),
            "natural_source_documents": len(source_sizes),
            "assignment_confidence": dict(Counter(a["source_assignment_confidence"] for a in assignments.values())),
        },
        "records": [assignments[problem_id] for problem_id in ids],
    }

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    sidecar_path = output / "OPDP_v1.6_Source_Provenance.json.gz"
    write_reproducible_gzip_json(sidecar_path, sidecar)

    cohort_summary_rows: list[dict[str, Any]] = []
    for code in ("V0", "V1", "V2"):
        for dim in DIMENSIONS:
            stats = descriptive(values(by_cohort[code], dim))
            cohort_summary_rows.append({
                "cohort_code": code, "cohort_label": COHORTS[[k for k, v in COHORTS.items() if v[0] == code][0]][1],
                "dimension": dim, "dimension_label": DIMENSIONS[dim]["label"], "family": DIMENSIONS[dim]["family"],
                "direction_semantics": DIMENSIONS[dim]["direction"], **stats,
            })
    write_csv(output / "OPDP_v1.6_Cohort_Dimension_Summary.csv", cohort_summary_rows)

    contrasts = [("V0", "V1"), ("V0", "V2"), ("V1", "V2")]
    comparison_rows: list[dict[str, Any]] = []
    rng = np.random.default_rng(20260826)
    for older, newer in contrasts:
        for dim in DIMENSIONS:
            a = values(by_cohort[older], dim); b = values(by_cohort[newer], dim)
            sa = descriptive(a); sb = descriptive(b)
            delta = float(np.mean(b) - np.mean(a))
            se = math.sqrt(float(np.var(a, ddof=1) / a.size + np.var(b, ddof=1) / b.size)) if a.size > 1 and b.size > 1 else 0.0
            g = hedges_g(a, b)
            macro_a = source_unit_means(by_cohort[older], assignments, dim)
            macro_b = source_unit_means(by_cohort[newer], assignments, dim)
            macro_delta = float(np.mean(macro_b) - np.mean(macro_a)) if macro_a.size and macro_b.size else None
            macro_se = math.sqrt(float(np.var(macro_a, ddof=1) / macro_a.size + np.var(macro_b, ddof=1) / macro_b.size)) if macro_a.size > 1 and macro_b.size > 1 else 0.0
            boot = bootstrap_source_macro_delta(macro_a, macro_b, args.bootstrap_replicates, rng)
            pooled_sd = math.sqrt(((a.size - 1) * float(np.var(a, ddof=1)) + (b.size - 1) * float(np.var(b, ddof=1))) / (a.size + b.size - 2)) if a.size > 1 and b.size > 1 else 0.0
            equivalence_band = 0.20 * pooled_sd
            if boot["ci90_low"] is None:
                equivalence = "not_available"
            elif boot["ci90_low"] > -equivalence_band and boot["ci90_high"] < equivalence_band:
                equivalence = "practically_equivalent_source_macro"
            elif boot["ci90_low"] > equivalence_band or boot["ci90_high"] < -equivalence_band:
                equivalence = "different_source_macro"
            else:
                equivalence = "inconclusive_source_macro"
            comparison_rows.append({
                "older_cohort": older, "newer_cohort": newer, "contrast": f"{newer}-{older}",
                "dimension": dim, "dimension_label": DIMENSIONS[dim]["label"], "family": DIMENSIONS[dim]["family"],
                "direction_semantics": DIMENSIONS[dim]["direction"],
                "older_n": int(a.size), "newer_n": int(b.size), "older_mean": sa["mean"], "newer_mean": sb["mean"],
                "older_sd": sa["sd"], "newer_sd": sb["sd"], "older_median": sa["median"], "newer_median": sb["median"],
                "mean_delta": delta, "hedges_g": g, "effect_magnitude": magnitude(g), "cliffs_delta": cliffs_delta(a, b),
                "wasserstein_1": wasserstein_1(a, b), "sd_ratio_newer_to_older": sb["sd"] / sa["sd"] if sa["sd"] else None,
                "row_welch_normal_approx_p": normal_two_sided_p(delta, se),
                "older_source_units": int(macro_a.size), "newer_source_units": int(macro_b.size),
                "older_source_macro_mean": float(np.mean(macro_a)) if macro_a.size else None,
                "newer_source_macro_mean": float(np.mean(macro_b)) if macro_b.size else None,
                "source_macro_delta": macro_delta,
                "source_macro_welch_normal_approx_p": normal_two_sided_p(macro_delta or 0.0, macro_se) if macro_delta is not None else None,
                **boot, "equivalence_band_raw": equivalence_band, "equivalence_status": equivalence,
                "practical_direction": practical_direction(dim, delta),
            })

    row_q = bh_adjust([float(r["row_welch_normal_approx_p"]) for r in comparison_rows])
    macro_q = bh_adjust([
        float(r["source_macro_welch_normal_approx_p"])
        if r["source_macro_welch_normal_approx_p"] is not None else 1.0
        for r in comparison_rows
    ])
    for row, q1, q2 in zip(comparison_rows, row_q, macro_q):
        row["row_bh_q_60_tests"] = q1; row["source_macro_bh_q_60_tests"] = q2
    write_csv(output / "OPDP_v1.6_Cohort_Comparisons.csv", comparison_rows)

    clustering_rows: list[dict[str, Any]] = []
    for code in ("V0", "V1", "V2"):
        for dim in DIMENSIONS:
            clustering_rows.append({"cohort_code": code, "dimension": dim, "dimension_label": DIMENSIONS[dim]["label"], **fixed_group_eta2(by_cohort[code], assignments, dim)})
    write_csv(output / "OPDP_v1.6_Source_Clustering.csv", clustering_rows)

    # Reference centroids for source-profile distances.
    ref_stats: dict[str, dict[str, tuple[float, float]]] = {}
    for code in ("V0", "V1"):
        ref_stats[code] = {}
        for dim in PROFILE_DISTANCE_DIMS:
            array = values(by_cohort[code], dim)
            ref_stats[code][dim] = (float(np.mean(array)), float(np.std(array, ddof=1)))

    records_by_source: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        assignment = assignments[record["problem_id"]]
        source_id = assignment["source_document_id"]
        if source_id is not None:
            records_by_source[(assignment["cohort_code"], source_id)].append(record)

    source_rows: list[dict[str, Any]] = []
    for (code, source_id), source_records in sorted(records_by_source.items()):
        assignment = assignments[source_records[0]["problem_id"]]
        categories = Counter(get(r, ("classification", "category", "label")) or "Unknown" for r in source_records)
        statuses = Counter(r.get("catalog_status") or "Unknown" for r in source_records)
        gates = Counter(get(r, ("assessment_gate", "value")) or "Unknown" for r in source_records)
        flagged = sum(bool(r.get("flags")) for r in source_records)
        c2plus = sum((get(r, ("intrinsic_difficulty", "confidence", "level")) or 0) >= 2 for r in source_records)
        row: dict[str, Any] = {
            "cohort_code": code, "cohort_label": assignment["cohort_label"],
            "source_collection_label": assignment["source_collection_label"],
            "source_document_id": source_id, "source_document_label": assignment["source_document_label"],
            "source_document_type": assignment["source_document_type"], "source_document_url": assignment["source_document_url"],
            "source_citation": assignment["source_citation"], "source_year": assignment["source_year"],
            "source_assignment_confidence": assignment["source_assignment_confidence"], "n": len(source_records),
            "status_open_n": statuses.get("open", 0), "status_partially_solved_n": statuses.get("partially_solved", 0), "status_solved_n": statuses.get("solved", 0),
            "source_claimed_open_gate_n": gates.get("source_claimed_open", 0),
            "distinct_categories": len(categories), "leading_category": categories.most_common(1)[0][0],
            "flagged_rate": flagged / len(source_records), "difficulty_C2plus_rate": c2plus / len(source_records),
            "reliability_label": "descriptive_only_n_lt_5" if len(source_records) < 5 else "sparse_n_5_to_9" if len(source_records) < 10 else "descriptive_centroid_n_ge_10",
        }
        source_means: dict[str, float] = {}
        for dim in DIMENSIONS:
            array = values(source_records, dim)
            row[f"mean_{dim}"] = float(np.mean(array)) if array.size else None
            row[f"sd_{dim}"] = float(np.std(array, ddof=1)) if array.size > 1 else None
            source_means[dim] = row[f"mean_{dim}"]
        for ref in ("V0", "V1"):
            squares = []
            for dim in PROFILE_DISTANCE_DIMS:
                value = source_means.get(dim)
                mean, sd = ref_stats[ref][dim]
                if value is not None and sd > 0:
                    squares.append(((value - mean) / sd) ** 2)
            row[f"distance_to_{ref}_centroid"] = math.sqrt(sum(squares)) if squares else None
            row[f"distance_dimensions_{ref}"] = len(squares)
        source_rows.append(row)
    write_csv(output / "OPDP_v1.6_Source_Summary.csv", source_rows)

    composition_rows = []
    for code in ("V0", "V1", "V2"):
        cohort_assignments = [assignments[r["problem_id"]] for r in by_cohort[code]]
        identified = [a for a in cohort_assignments if a["source_document_id"] is not None]
        counts = Counter(a["source_document_id"] for a in identified)
        shares = np.asarray(list(counts.values()), dtype=float) / max(1, sum(counts.values()))
        hhi = float(np.sum(shares ** 2)) if shares.size else None
        entropy = float(-np.sum(shares * np.log(shares))) if shares.size else None
        composition_rows.append({
            "cohort_code": code, "records": len(cohort_assignments), "identified_source_records": len(identified),
            "source_assignment_coverage": len(identified) / len(cohort_assignments),
            "source_collections": len({a["source_collection_label"] for a in cohort_assignments}),
            "natural_source_documents": len(counts), "source_hhi": hhi,
            "effective_sources_inverse_hhi": 1.0 / hhi if hhi else None,
            "effective_sources_entropy": math.exp(entropy) if entropy is not None else None,
            "minimum_source_size": min(counts.values()) if counts else None, "median_source_size": statistics.median(counts.values()) if counts else None,
            "maximum_source_size": max(counts.values()) if counts else None,
        })
    write_csv(output / "OPDP_v1.6_Source_Composition.csv", composition_rows)

    validation = {
        "status": "pass",
        "input_records": len(records), "base_records_checked": len(base_records), "base_records_changed": 0,
        "cohort_counts": observed_counts, "unique_problem_ids": len(set(ids)),
        "source_assignment_records": len(assignments), "natural_source_documents": len(source_sizes),
        "owr_records": sum(a["cohort_code"] == "V2" for a in assignments.values()),
        "owr_distinct_reports": len({a["source_document_id"] for a in assignments.values() if a["cohort_code"] == "V2"}),
        "owr_high_confidence_assignments": sum(a["cohort_code"] == "V2" and a["source_assignment_confidence"] == "C3" for a in assignments.values()),
        "output_files": {},
    }
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name != "OPDP_v1.6_Analysis_Validation.json":
            validation["output_files"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    validation_path = output / "OPDP_v1.6_Analysis_Validation.json"
    validation_path.write_bytes((json.dumps(validation, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    print(json.dumps(validation, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
