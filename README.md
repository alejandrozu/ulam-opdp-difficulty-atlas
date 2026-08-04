# Ulam OPDP Difficulty Atlas

An auditable Open Problem Difficulty Profile (OPDP) for **5,426 problems** in the UnsolvedMath v1.2.0 corpus.

**Author:** Alejandro Zarzuelo Urdiales with ChatGPT 5.6 Sol  
**Assessment date:** 2026-07-31  
**Repository release:** 2026-08-02

The atlas replaces a single, ambiguous notion of “difficulty” with a granular profile covering intrinsic mathematical difficulty, AI-relative difficulty, human attention, tractability, verification burden, formalization burden, prerequisite depth, breadth, and tool leverage. Every problem includes concise public justifications so that assessments can be audited and revised.

## Files

| File | Purpose |
|---|---|
| [`Ulam_UnsolvedMath_OPDP_Assessments_v1.2.json.gz`](data/Ulam_UnsolvedMath_OPDP_Assessments_v1.2.json.gz) | Recommended website-import payload. Decompresses to the complete 5,426-record JSON. |
| [`Ulam_UnsolvedMath_OPDP_Assessments_v1.2.json`](https://github.com/alejandrozu/ulam-opdp-difficulty-atlas/releases/download/v1.2.0-opdp1.0/Ulam_UnsolvedMath_OPDP_Assessments_v1.2.json) | Uncompressed 85 MB JSON, attached to the GitHub release because it exceeds GitHub's browser source-upload limit. |
| [`Ulam_UnsolvedMath_Difficulty_Atlas_v1.2.xlsx`](data/Ulam_UnsolvedMath_Difficulty_Atlas_v1.2.xlsx) | Filterable workbook with the atlas, scoring inputs, formulas, rationales, source index, QA queue, and rubric. |
| [`Ulam_OPDP_Integrated_Report_v1.0.pdf`](docs/Ulam_OPDP_Integrated_Report_v1.0.pdf) | Integrated, easy-to-read report combining the analysis and methodology. |
| [`Ulam_OPDP_Methodology_v1.0.docx`](docs/Ulam_OPDP_Methodology_v1.0.docx) | Editable methodology document. |
| [`Ulam_UnsolvedMath_ChatGPT_5.6_Sol_Ultra_Difficulty_v1.0.json`](data/Ulam_UnsolvedMath_ChatGPT_5.6_Sol_Ultra_Difficulty_v1.0.json) | Barebones 5,426-record import file containing only each problem's ID, display number, name, statement, and 0–1000 ChatGPT difficulty. |
| [`Ulam_UnsolvedMath_ChatGPT_5.6_Sol_Ultra_Rationales_v1.0.json`](data/Ulam_UnsolvedMath_ChatGPT_5.6_Sol_Ultra_Rationales_v1.0.json) | Companion methodology, protocol, limitations, per-problem explanation, source inputs, and reproducible calculation trace. |
| [`CHECKSUMS.sha256`](CHECKSUMS.sha256) | SHA-256 checksums for the release deliverables and companion JSON files. |

All five original deliverables are also attached to the [tagged GitHub release](https://github.com/alejandrozu/ulam-opdp-difficulty-atlas/releases/tag/v1.2.0-opdp1.0).

## JSON integration

The JSON is a self-describing exchange file. Its top level contains authorship, dataset and assessment versions, integration guidance, the AI protocol, the complete machine-readable rubric, schema semantics, corpus summary, and the `records` array.

Every record begins with the three fields requested for website integration:

```json
{
  "problem_id": 1,
  "difficulty_label": "T5 Grand Challenge",
  "explanation": "T5 Grand Challenge: D9.6 and AI 10.0 ..."
}
```

The remainder of each record contains:

- the source statement, background, taxonomy, proposer, dates, and complete original source row;
- intrinsic difficulty score, interval, confidence, tier, and five factor inputs;
- AI-relative adjustment, dated protocol, six predictors, score, interval, confidence, and fit label;
- estimated specialist-hours and exposure;
- tractability and its probability band;
- verification, formalization, prerequisites, breadth, and tool leverage;
- all ten axis/status rationales, the overall explanation, evidence codes, recommended route, tags, review priority, provenance, and QA flags.

### Import rules that matter

1. Use numeric `problem_id` as the canonical database key. `problem_number` is a display identifier and is not unique: 35 duplicate-number groups cover 86 records.
2. Keep the two T namespaces separate: intrinsic difficulty tiers are T1–T5, while tractability bands are T0–T10.
3. Preserve `null` versus zero. Eighteen solved/ill-posed records intentionally have `tractability.score = null`; 37 records have the valid score T0.
4. Keep `catalog_status` and `assessment_gate` visible. A score does not certify that a problem is currently open.
5. The legacy L1–L5 field is source provenance and a weak prior, not the OPDP difficulty result.
6. Treat recovered source URLs as unverified candidates and honor the explicit Ulam-fallback marker.
7. Sanitize source text before browser rendering when `source_text.transport_qa` reports control characters.

## ChatGPT 5.6 Sol Ultra companion

The companion adds one model-specific full-solution difficulty integer to every source record. Join either file to the main atlas only by numeric `problem_id`.

The first JSON is intentionally minimal and contains no scoring formula, intermediate dimensions, rationale, or methodology. Its record shape is:

```json
{
  "problem_id": 1,
  "problem_number": "MPP-001",
  "name": "P versus NP Problem",
  "statement": "Does P = NP? ...",
  "chatgpt_difficulty_0_1000": 936
}
```

The second JSON defines **ChatGPT Full-Solution Difficulty 1000 (CFSD-1000) v1.0** and explains every assigned score. It combines all core OPDP dimensions under a fixed ChatGPT 5.6 Sol Ultra research protocol, records the component calculation, and preserves status and data-quality caveats.

The scale is an ordinal editorial estimate of how difficult a complete, novel, independently verified resolution would be for that protocol. It is not a success probability, an empirical benchmark, a replacement for the multidimensional OPDP, or evidence that any problem was attempted. One-point differences are not epistemically meaningful.

## Validation

The release passed two independent validation passes with zero errors and zero warnings. Checks included:

- strict UTF-8 and JSON parsing with duplicate-key detection;
- 5,426 unique, ascending numeric IDs and complete frozen-source equality;
- all ten per-problem rationale fields;
- score, interval, tier, AI, tractability, human-hour, and probability-band recomputation;
- enum and registry membership;
- summary and corpus-audit recomputation;
- explicit testing of the 18 null and 37 valid-zero tractability cases.

The ChatGPT 5.6 Sol Ultra companion was separately regenerated and independently checked across all 5,426 records. Its checks included exact ID/name/statement projection from the canonical assessment export, an intentionally restricted first-file schema, cross-file score equality, integer/range and UTF-8 checks, full formula and calculation-trace recomputation, and summary/band-count recomputation.

Frozen source snapshot SHA-256:

```text
954E1151491871B831B6FC3084957E790075ED9AD739FE715328863EBE710515
```

## Interpretation and limitations

This is a reproducible **provisional editorial first pass**, not expert certification.

- Open/solved status was not independently checked against the current literature for every record.
- AI difficulty is protocol-dated and is not based on empirical per-problem agent runs.
- Human specialist-hours are order-of-magnitude priors; unpublished work is unobserved.
- Tractability forecasts independently checked partial progress in 100 combined expert-plus-AI hours, not full resolution.
- Opportunity tags are high-precision filters; absence of a tag is not a negative judgment.

Expert corrections should modify stored inputs, regenerate every derived field and rationale, and preserve an attributable override history.

## Provenance and reuse

The frozen source corpus is UnsolvedMath v1.2.0, published by Ulam AI and declared CC BY 4.0. See [`NOTICE.md`](NOTICE.md) for attribution, source links, and reuse cautions.

Public repository visibility does not itself grant additional rights over original analysis beyond the rights held by the respective contributors and source licensors.
