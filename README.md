# OPDP Difficulty Atlas

An auditable Open Problem Difficulty Profile (OPDP) for **102,563 records**: 15,458 statement-bearing records preserved from three frozen UnsolvedMath cohorts, plus 87,105 exact MathDB public-catalog items appended in v1.7. MathDB supplies only a public-list excerpt for this complete-catalog surface, so every new assessment is explicitly excerpt-based, flagged for recalibration, and capped at C0 confidence.

**Author:** Alejandro Zarzuelo Urdiales with ChatGPT 5.6 Sol  

**Original assessment date:** 2026-07-31

**Latest append-only expansion date:** 2026-09-08

The atlas replaces a single, ambiguous notion of “difficulty” with a granular profile covering intrinsic mathematical difficulty, AI-relative difficulty, human attention, tractability, verification burden, formalization burden, prerequisite depth, breadth, and tool leverage. Every problem includes concise public justifications so that assessments can be audited and revised.

## Files

| File | Purpose |
|---|---|
| [`Ulam_MathDB_OPDP_Assessments_v1.7.json.gz`](https://media.githubusercontent.com/media/alejandrozu/ulam-opdp-difficulty-atlas/main/data/Ulam_MathDB_OPDP_Assessments_v1.7.json.gz) | **Recommended website-import payload.** Direct Git LFS download of all 102,563 append-only OPDP records. The 87,105 MathDB additions retain exact public-list items and incomplete `excerpt` text, not full statements. |
| [`OPDP_v1.7_MathDB_Append_Validation.json`](data/OPDP_v1.7_MathDB_Append_Validation.json) | Builder validation of append-only equality, IDs, source-item preservation, formulas, C0 excerpt controls, rationales, and summary counts. |
| [`OPDP_v1.7_Independent_Validation.json`](data/OPDP_v1.7_Independent_Validation.json) | Independent streaming validation against the frozen sitemap inventory and MathDB catalog manifest. |
| [`OPDP_v1.7_APPEND_ONLY_NOTES.md`](docs/OPDP_v1.7_APPEND_ONLY_NOTES.md) | v1.7 acquisition, text-coverage, compatibility, attribution, and validation contract. |
| [`Ulam_UnsolvedMath_OPDP_Assessments_v1.6.json.gz`](https://media.githubusercontent.com/media/alejandrozu/ulam-opdp-difficulty-atlas/main/data/Ulam_UnsolvedMath_OPDP_Assessments_v1.6.json.gz) | Historical 15,458-record statement-bearing compatibility payload. |
| [`Ulam_UnsolvedMath_Difficulty_Atlas_v1.6.xlsx`](https://media.githubusercontent.com/media/alejandrozu/ulam-opdp-difficulty-atlas/main/data/Ulam_UnsolvedMath_Difficulty_Atlas_v1.6.xlsx) | Current workbook for the historical 15,458-record v1.6 scope, with the profile table, rationales, dashboard, cohort analysis, source summaries, formula audit, QA queue, and rubric. v1.7 is JSON-only. |
| [`OPDP_v1.6_Source_Provenance.json.gz`](data/OPDP_v1.6_Source_Provenance.json.gz) | Machine-readable source sidecar joining every problem to its cohort, Ulam collection, and finest recoverable natural source document. |
| [`OPDP_v1.6_Cohort_Dimension_Summary.csv`](data/OPDP_v1.6_Cohort_Dimension_Summary.csv) | Per-cohort descriptive statistics for the 20 analyzed OPDP dimensions. |
| [`OPDP_v1.6_Cohort_Comparisons.csv`](data/OPDP_v1.6_Cohort_Comparisons.csv) | Pairwise cohort contrasts with effect sizes, uncertainty intervals, descriptive p-values, and multiplicity-adjusted q-values. |
| [`OPDP_v1.6_Source_Clustering.csv`](data/OPDP_v1.6_Source_Clustering.csv) | Source-associated variance summaries for calibration and heterogeneity analysis. |
| [`OPDP_v1.6_Source_Summary.csv`](data/OPDP_v1.6_Source_Summary.csv) | Natural-source-level counts and dimension summaries, including individual Oberwolfach report DOIs where recoverable. |
| [`OPDP_v1.6_Source_Composition.csv`](data/OPDP_v1.6_Source_Composition.csv) | Cohort source concentration, effective source counts, and related composition diagnostics. |
| [`OPDP_v1.6_Analysis_Validation.json`](data/OPDP_v1.6_Analysis_Validation.json) | Machine-readable integrity checks for cohort membership, append-only equality, provenance coverage, and generated analysis tables. |
| [`OPDP_v1.6_Main_Validation.json`](data/OPDP_v1.6_Main_Validation.json) | Independent strict-JSON, schema, source equality, summary, registry, D/AI/T, interval, tier, and null-semantics validation report. |
| [`OPDP_v1.6_Workbook_Validation.json`](data/OPDP_v1.6_Workbook_Validation.json) | Workbook package, row-alignment, cached-formula, null/zero, and formula-delta validation evidence. |
| [`OPDP_v1.6_APPEND_ONLY_NOTES.md`](docs/OPDP_v1.6_APPEND_ONLY_NOTES.md) | v1.6 compatibility contract, frozen snapshots, provenance rules, and validation evidence. |
| [`OPDP_v1.6_SOURCE_AND_COHORT_COMPARISON.md`](docs/OPDP_v1.6_SOURCE_AND_COHORT_COMPARISON.md) | Rigorous comparison of the original, AIM, and Oberwolfach cohorts, both problem-weighted and source-aware. |
| [`scripts/`](scripts/) | Reproducible acquisition, append-only scoring, streaming validation, historical source-analysis, and workbook builders with usage instructions. |
| [`Ulam_UnsolvedMath_OPDP_Assessments_v1.5.json.gz`](data/Ulam_UnsolvedMath_OPDP_Assessments_v1.5.json.gz) | Historical 8,785-record append-only website-import payload. |
| [`Ulam_UnsolvedMath_Difficulty_Atlas_v1.5.xlsx`](data/Ulam_UnsolvedMath_Difficulty_Atlas_v1.5.xlsx) | Expanded workbook with dashboard, atlas, formula audit, all rationales, statements, QA queue, and rubric. |
| [`OPDP_v1.5_APPEND_ONLY_NOTES.md`](docs/OPDP_v1.5_APPEND_ONLY_NOTES.md) | Compatibility contract, source snapshots, new-record summary, and validation evidence. |
| [`OPDP_v1.5_DISTRIBUTION_COMPARISON.md`](docs/OPDP_v1.5_DISTRIBUTION_COMPARISON.md) | Statistical comparison of the preserved cohort and AIM additions, with interpretation guidance for problem selection. |
| [`OPDP_v1.5_Visual_Brief.pdf`](docs/OPDP_v1.5_Visual_Brief.pdf) | Five-page, image-first summary of how the 3,359 AIM additions differ from the previous 5,426 problems. |
| [`OPDP_v1.5_X_Figures.zip`](docs/OPDP_v1.5_X_Figures.zip) | Ten standalone 1600×1200 PNG figures ready for social posts and presentations. |
| [`Ulam_UnsolvedMath_OPDP_Assessments_v1.2.json.gz`](data/Ulam_UnsolvedMath_OPDP_Assessments_v1.2.json.gz) | Historical 5,426-record compatibility-base payload. |
| [`Ulam_UnsolvedMath_OPDP_Assessments_v1.2.json`](https://github.com/alejandrozu/ulam-opdp-difficulty-atlas/releases/download/v1.2.0-opdp1.0/Ulam_UnsolvedMath_OPDP_Assessments_v1.2.json) | Uncompressed 85 MB JSON, attached to the GitHub release because it exceeds GitHub's browser source-upload limit. |
| [`Ulam_UnsolvedMath_Difficulty_Atlas_v1.2.xlsx`](data/Ulam_UnsolvedMath_Difficulty_Atlas_v1.2.xlsx) | Filterable workbook with the atlas, scoring inputs, formulas, rationales, source index, QA queue, and rubric. |
| [`Ulam_OPDP_Integrated_Report_v1.0.pdf`](docs/Ulam_OPDP_Integrated_Report_v1.0.pdf) | Integrated, easy-to-read report combining the analysis and methodology. |
| [`Ulam_OPDP_Methodology_v1.0.docx`](docs/Ulam_OPDP_Methodology_v1.0.docx) | Editable methodology document. |
| [`Ulam_UnsolvedMath_ChatGPT_5.6_Sol_Ultra_Difficulty_v1.0.json`](data/Ulam_UnsolvedMath_ChatGPT_5.6_Sol_Ultra_Difficulty_v1.0.json) | Barebones 5,426-record import file containing only each problem's ID, display number, name, statement, and 0–1000 ChatGPT difficulty. |
| [`Ulam_UnsolvedMath_ChatGPT_5.6_Sol_Ultra_Rationales_v1.0.json`](data/Ulam_UnsolvedMath_ChatGPT_5.6_Sol_Ultra_Rationales_v1.0.json) | Companion methodology, protocol, limitations, per-problem explanation, source inputs, and reproducible calculation trace. |
| [`CHECKSUMS.sha256`](CHECKSUMS.sha256) | SHA-256 checksums for the release deliverables and companion JSON files. |

All five original deliverables are also attached to the [tagged v1.2 GitHub release](https://github.com/alejandrozu/ulam-opdp-difficulty-atlas/releases/tag/v1.2.0-opdp1.0). The v1.2, v1.5, and v1.6 files remain available as immutable historical compatibility artifacts.

The v1.7 JSON and the large v1.6 artifacts are stored with Git LFS. The links above target GitHub's media endpoint and return the actual binary files; `raw.githubusercontent.com` returns only the small LFS pointer. A normal clone with Git LFS installed materializes them automatically.

## v1.7 MathDB append-only compatibility

The current expansion is deliberately non-destructive:

- all 15,458 complete v1.6 records remain deep-equal, including source rows, scores, rationales, flags, and implementation metadata;
- exactly 87,105 MathDB public-catalog items are appended, producing 102,563 total records;
- each addition uses `problem_id = 40000000 + MathDB number` and display identifier `MATHDB-{number}`;
- schema version `1.0.0`, record field order, rule `OPDP-1.0-rulepass-2026-07-31`, and numeric primary-key semantics are unchanged; and
- v1.7 is JSON-only and does not claim a 102,563-row workbook.

The MathDB acquisition schema is `opdp.mathdb.problem-summary.v1`. Each envelope's exact public-list `problem` object is preserved under `source_record`, but the assessed mathematical text is only `problem.excerpt`. MathDB does not guarantee that excerpt to be a full statement. Consequently every addition carries `source_excerpt_only` and `needs_curation`; all assigned confidence fields are C0; intervals are widened; and all ten rationales disclose that the scores require recalibration from a complete statement.

MathDB's richer detail endpoint limits clients to 500 unique problems per day, while [its Terms](https://mathdb.com/terms) direct bulk-data users to contact the administrators. This project did not evade that limit. It froze the complete sitemap inventory, used the supported public catalog surface, and makes no claim to have downloaded 87,105 full statements. See the [v1.7 append-only notes](docs/OPDP_v1.7_APPEND_ONLY_NOTES.md) for the exact acquisition and validation contract.

## v1.6 append-only compatibility

The expansion is deliberately non-destructive:

- all 8,785 complete v1.5 OPDP records are retained exactly, including the frozen 5,426-record v1.2 base and 3,359 AIM additions;
- 6,673 Oberwolfach Reports Open Problems records are appended at IDs `30000001`–`30006673`;
- schema version, record schema, field order, rule version, and `problem_id` join semantics are unchanged;
- later Ulam edits to the 8,785 previously published source rows are not imported in this release.

Consumers can replace the decompressed v1.5 payload with v1.6 without a schema migration. Existing records remain stable; only new primary keys appear. The three release cohorts are therefore exactly 5,426 original records, 3,359 AIM additions, and 6,673 Oberwolfach additions.

### Source hierarchy

The provenance sidecar distinguishes three levels that should not be conflated:

1. **Release cohort** — original 5,426, AIM 3,359, or Oberwolfach 6,673.
2. **Ulam collection** — the collection/set under which Ulam distributes the record.
3. **Natural source document** — the finest recoverable origin: an exact Oberwolfach report DOI for the new cohort, an AIM workshop slug for AIM records, a parsed source-list reference for AMR records, or an explicitly marked fallback when no finer source can be established.

The 6,673 newest records come from **1,106 distinct Oberwolfach reports**, not from 6,673 independent sources. Source-aware analyses therefore report both problem-weighted results and source-unit summaries; repeated problems from one report must not be treated as independent evidence about the wider universe of open problems.

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

- the available source text, taxonomy, proposer/author, dates, and complete original source row or list item; for MathDB v1.7, `source_text.statement` is only the source's public-list `excerpt`;
- intrinsic difficulty score, interval, confidence, tier, and five factor inputs;
- AI-relative adjustment, dated protocol, six predictors, score, interval, confidence, and fit label;
- estimated specialist-hours and exposure;
- tractability and its probability band;
- verification, formalization, prerequisites, breadth, and tool leverage;
- all ten axis/status rationales, the overall explanation, evidence codes, recommended route, tags, review priority, provenance, and QA flags.

### Import rules that matter

1. Use numeric `problem_id` as the canonical database key. `problem_number` is a display identifier; the historical v1.6 scope contains 35 duplicate-number groups covering 86 records, while MathDB additions use the namespaced `MATHDB-{number}` form.
2. Keep the two T namespaces separate: intrinsic difficulty tiers are T1–T5, while tractability bands are T0–T10.
3. Preserve `null` versus zero. In the v1.6 export, 627 records intentionally have `tractability.score = null`; 39 records have the valid score T0.
4. Keep `catalog_status` and `assessment_gate` visible. A score does not certify that a problem is currently open.
5. The legacy L1–L5 field is source provenance and a weak prior, not the OPDP difficulty result.
6. Treat recovered source URLs as unverified candidates and honor the explicit Ulam-fallback marker.
7. Sanitize source text before browser rendering when `source_text.transport_qa` reports control characters.
8. When `source_text.text_mode = "mathdb_public_list_excerpt"`, never label the excerpt as the complete statement. Display `source_excerpt_only`, C0 confidence, and the record's recalibration warning.

## ChatGPT 5.6 Sol Ultra companion

The v1.0 companion adds one model-specific full-solution difficulty integer to each of the 5,426 records in its frozen v1.2 scope. Join either file to the main atlas only by numeric `problem_id`.

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

The current v1.7 release is built under the unchanged `OPDP-1.0-rulepass-2026-07-31` rule and independently stream-validated against the frozen acquisition artifacts. Its release gates include:

- exactly 87,105 MathDB catalog envelopes and 102,563 total OPDP records;
- exact agreement between frozen sitemap numbers and public-catalog numbers;
- deep equality of the complete 15,458-record v1.6 prefix;
- stable reversible MathDB IDs, unique UUIDs and numbers, and strict append order;
- exact equality of each `source_record` to its source envelope's public-list `problem` item;
- equality of assessed text to `problem.excerpt`, explicit incomplete-statement metadata, `source_excerpt_only`, `needs_curation`, and C0 confidence for all assigned excerpt-derived axes;
- all ten rationales carrying the excerpt limitation and recalibration requirement;
- complete formula, tier, interval, null, registry, and summary recomputation; and
- valid streaming gzip/JSON transport and artifact hashes matching the completed catalog manifest and `CHECKSUMS.sha256`.

The retained v1.6 artifacts were separately validated with:

- strict UTF-8 and JSON parsing with duplicate-key detection;
- 15,458 unique, ascending numeric IDs and complete append-only frozen-record equality for all 8,785 previously published records;
- all ten per-problem rationale fields;
- score, interval, tier, AI, tractability, human-hour, and probability-band recomputation;
- enum and registry membership;
- summary and corpus-audit recomputation;
- formula and registry recomputation, including 627 intentional null and 39 valid-zero tractability cases;
- deep equality of all 8,785 v1.5 records, with zero changes;
- complete fields and all ten rationales for each of the 6,673 appended records;
- one-to-one source-sidecar alignment and recovery of 1,106 distinct Oberwolfach report sources;
- workbook formula, content, and visual QA across every sheet.

The ChatGPT 5.6 Sol Ultra companion was separately regenerated and independently checked across all 5,426 records. Its checks included exact ID/name/statement projection from the canonical assessment export, an intentionally restricted first-file schema, cross-file score equality, integer/range and UTF-8 checks, full formula and calculation-trace recomputation, and summary/band-count recomputation.

Append-only composite source snapshot SHA-256:

```text
52F4D1C618ACC02256045F0B44EDEA2E059B2C90FF40F51F1F628B16C3E9FC0E
```

## Interpretation and limitations

This is a reproducible **provisional editorial first pass**, not expert certification.

- Every MathDB v1.7 assessment is based on a public-list excerpt that is not guaranteed complete. Its assigned confidence fields are C0, and its values are coverage placeholders requiring recalibration from the full statement.
- Open/solved status was not independently checked against the current literature for every record.
- AI difficulty is protocol-dated and is not based on empirical per-problem agent runs.
- Human specialist-hours are order-of-magnitude priors; unpublished work is unobserved.
- Tractability forecasts independently checked partial progress in 100 combined expert-plus-AI hours, not full resolution.
- Opportunity tags are high-precision filters; absence of a tag is not a negative judgment.
- Cohort contrasts are observational and source-confounded. Differences may reflect source selection, report conventions, mathematical-field composition, time period, or extraction practice rather than an intrinsic change in the worldwide population of open problems.
- Row-level p-values are descriptive because problems within a source document are dependent. Source-balanced summaries, effect sizes, uncertainty intervals, and multiplicity-adjusted q-values should be read together.

Expert corrections should modify stored inputs, regenerate every derived field and rationale, and preserve an attributable override history.

## Provenance and reuse

The compatibility base is UnsolvedMath v1.2.0; AIM additions were frozen from UnsolvedMath v1.5.0 at Hugging Face revision `c423bd6c88433fe614b0f8b206201f580e0a7355`; Oberwolfach additions were frozen from UnsolvedMath v1.6.0 at revision `b9437975f3c873f635a13c48f8b022f5ba80898a`. Ulam AI declares the dataset CC BY 4.0. Underlying source documents may carry their own attribution or reuse terms. See [`NOTICE.md`](NOTICE.md) for source links, hashes, and reuse cautions.

The v1.7 append uses the exact public-list items corresponding to MathDB's 87,105-entry sitemap inventory frozen on 2026-09-08. MathDB identifies contributed database content under CC BY 4.0 in [its Terms](https://mathdb.com/terms). The raw list item and available source fields remain attached to every appended record; the Terms and any separately cited underlying sources still govern reuse. No complete-catalog detail-endpoint acquisition is claimed.

Public repository visibility does not itself grant additional rights over original analysis beyond the rights held by the respective contributors and source licensors.
