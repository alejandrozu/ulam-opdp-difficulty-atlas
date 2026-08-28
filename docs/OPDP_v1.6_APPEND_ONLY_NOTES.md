# Ulam OPDP append-only expansion — v1.6

This release expands the Open Problem Difficulty Profile (OPDP) from 8,785 to 15,458 records. It appends 6,673 problems from Ulam's Oberwolfach Reports expansion while preserving every previously published OPDP record exactly.

## Compatibility contract

- The first 8,785 records are deep-equal to the complete v1.5 OPDP export. The check covers every nested object, source row, score, rationale, flag, and implementation field; zero legacy records changed.
- The top-level and per-record schema version remains `1.0.0`.
- The assessment rule remains `OPDP-1.0-rulepass-2026-07-31`.
- Integer `problem_id` remains the canonical primary and join key. `problem_number`, title, and derived Ulam URL are not guaranteed unique.
- Exactly 6,673 records are appended, with IDs `30000001` through `30006673` and `implementation.dataset_version = "1.6.0"`.
- Upstream edits to the 8,785 pre-existing Ulam rows are intentionally not imported. This is an append-only assessment release, not a legacy status refresh.
- The OPDP JSON record shape is unchanged. Source-document classification is supplied as a joinable sidecar, so existing consumers need no schema migration.

## Frozen source snapshots

- Published OPDP v1.5 compatibility export: 8,785 records; uncompressed JSON SHA-256 `E59D8F3AD7408548839956D432DE1E08FB5D8E8179CCA2E5E6807972449999E6`.
- Latest upstream consulted: UnsolvedMath v1.6.0 at Hugging Face revision `b9437975f3c873f635a13c48f8b022f5ba80898a`; 15,458 rows; upstream `problems.json` SHA-256 `0A11E1B86385B6095F001803E2BB9D176B3498DA0932D7AF3700E1132EF153AC`.
- Append-only composite source used for scoring: 15,458 rows; SHA-256 `52F4D1C618ACC02256045F0B44EDEA2E059B2C90FF40F51F1F628B16C3E9FC0E`.
- Complete uncompressed OPDP v1.6 JSON: 262,396,647 bytes; SHA-256 `C4B838A85B1FA60E6C1FA3C78A9DE6EA79FD0F72F331985CBDB787C310735C97`.

These hashes distinguish three objects that should not be conflated: current upstream, the frozen append-only scoring input, and the enriched OPDP export.

## What was appended

All 6,673 additions belong to Ulam's `Oberwolfach Reports Open Problems` collection. The collection label alone is too coarse for dependence-aware analysis, so the release also identifies the exact source report for every new record:

- 6,673 of 6,673 additions have an exact Oberwolfach Report DOI and source citation.
- The additions span 1,106 distinct report DOIs from 2004 through 2026.
- Report-level problem counts range from 1 to 33, with median 5.
- Source assignment confidence is C3 for all 6,673 new rows because it is based on structured `source_url` plus `source_citation`, not topic inference.
- The upstream status mix is 4,493 `open` and 2,180 `partially_solved`; none of the additions is catalogued as solved.
- Every addition carries structured literature-assessment fields. These are retained as source evidence, not treated as an independent OPDP literature audit.
- The upstream L3 difficulty attached to every addition is a collection default. OPDP retains it as weak source provenance only; it is never copied into the OPDP result.

The joinable file `data/OPDP_v1.6_Source_Provenance.json.gz` contains one row per `problem_id` with the hierarchy `release_cohort -> source_collection -> source_document`. It also covers the two earlier cohorts using the best recoverable frozen source unit.

## New-record assessment summary

The 6,673 additions were classified under the unchanged OPDP rule:

- Difficulty tiers: 38 T2 Research Sprint; 5,881 T3 Serious Project; 754 T4 Frontier Challenge; no T1 or T5 additions.
- Assessment gates: 6,540 source-claimed open; 66 status unclear; 67 ill-posed; no solved gates.
- AI fit: 866 AI-favored; 4,915 AI-neutral/mixed; 892 AI-hostile.
- Mean intrinsic difficulty D: 5.831/10; mean AI difficulty: 5.730/10.
- Mean 100-hour tractability T among 6,606 scored records: 5.316/10. The remaining 67 T values are intentionally null for ill-posed gates, not zero.
- Intrinsic and AI confidence: 6,590 C2 and 83 C1 records. C2 means rich machine-readable context, not expert certification.

The complete 15,458-record atlas contains 135 T2, 13,803 T3, 1,514 T4, and 6 T5 records. Catalog status is 9,840 open, 5,105 partially solved, and 513 solved.

## Source-document registry

The sidecar applies the following deterministic hierarchy:

| Cohort | Records | Natural source unit | Identified records | Distinct units | Confidence |
|---|---:|---|---:|---:|---|
| V0: original v1.2 compatibility cohort | 5,426 | Exact AMR source-list field where recoverable; otherwise frozen named collection | 5,040 | 99 | C2; 386 explicit unassigned rows remain null |
| V1: AIM v1.5 additions | 3,359 | `aim-workshop:<slug>` tag plus frozen workshop background | 3,359 | 162 | C3 |
| V2: Oberwolfach v1.6 additions | 6,673 | Exact report DOI plus citation | 6,673 | 1,106 | C3 |

Within V0, the 99 identified units comprise 89 AMR source lists and 10 other named collections. No source is fabricated for the 386 unassigned records. `literature_sources` and other evidence links are not promoted to origin sources.

## Data-quality signals

The new rows preserve source text rather than silently rewriting it. Review flags therefore remain visible:

- 2,503 truncated/ellipsized display titles;
- 621 compound-scope and 83 programmatic-scope formulations;
- 133 title-collision flags and 71 records in exact duplicate-statement groups;
- 72 context-dependent statements and 34 figure/table residues;
- 66 possible-stale-or-resolved status signals; and
- one truncated statement.

These flags are curation prompts, not exclusions and not proof of an upstream error. Consumers should render `catalog_status`, `assessment_gate`, confidence, and flags with every individual score.

## Validation

- Strict input and output record count: 15,458.
- Unique, strictly ascending integer IDs: 15,458.
- Legacy deep-equality: 8,785 checked, zero changed.
- Cohort partition: 5,426 + 3,359 + 6,673 = 15,458.
- Source sidecar join: 15,458 assignments for 15,458 OPDP records.
- OWR provenance: 6,673 rows, 1,106 distinct report DOIs, 6,673 high-confidence assignments.
- Statistical output generation: pass; deterministic seed `20260826`; 4,000 source-bootstrap replicates per dimension and contrast.
- Workbook package: eight sheets, 15,458 aligned unique IDs, zero nonzero D/AI/T formula deltas, zero cached formula errors, 627 intentional null T values, and 39 valid T0 values. Visual QA was performed on every sheet; see `data/OPDP_v1.6_Workbook_Validation.json`.

The scores remain a reproducible, provisional editorial first pass. They are not expert rankings, and no per-problem AI solution episodes or complete independent current-literature audit were performed.
