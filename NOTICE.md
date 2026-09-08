# Attribution and provenance notice

## Source corpora

The current append-only OPDP release contains 102,563 records: the complete 15,458-record UnsolvedMath v1.6 compatibility base plus 87,105 exact MathDB public-catalog items frozen on 2026-09-08.

### MathDB public-catalog expansion

- Website: https://mathdb.com
- Terms: https://mathdb.com/terms
- Frozen public inventory: 87,105 unique problem numbers across five problem sitemaps
- Public catalog file: `mathdb_problem_summaries.jsonl`
- Envelope schema: `opdp.mathdb.problem-summary.v1`
- Combined OPDP v1.7 record count: 102,563
- Declared database-content license: Creative Commons Attribution 4.0 International (CC BY 4.0)

For each appended record, `source_record` is deep-equal to the exact public-list `problem` item captured by the catalog acquirer. That list item supplies `excerpt`, not a statement guaranteed to be complete. OPDP therefore stores the excerpt with `source_text.text_mode = "mathdb_public_list_excerpt"`, marks `source_excerpt_only`, assigns C0 to every excerpt-derived confidence field, widens its uncertainty ranges, and repeats the limitation in all ten rationales. Neither this notice nor the release represents those excerpts as 87,105 complete mathematical statements.

MathDB's richer per-problem endpoint enforces a limit of 500 unique problems per day, and its Terms direct bulk-data users to contact the administrators. This project did not evade the limit by rotating accounts, credentials, network addresses, or hosts. It froze the exact sitemap inventory, retrieved only a permitted diagnostic detail sample, and used the supported public catalog list surface for complete coverage. The catalog manifest retains page hashes, retrieval times, release headers, and exact sitemap joins; final artifact hashes are recorded in `CHECKSUMS.sha256` and the v1.7 append-only notes.

MathDB attribution does not supersede terms attached to separately cited underlying material. Reusers should retain MathDB attribution, the available per-item source fields, the excerpt limitation, and any third-party citation or rights notice.

### UnsolvedMath v1.6 compatibility corpus

This project analyzes three frozen, append-only cohorts from the **UnsolvedMath** corpus published by Ulam AI: 5,426 original compatibility-base records, 3,359 AIM Workshop Problem Lists additions, and 6,673 Oberwolfach Reports Open Problems additions.

- Dataset page: https://huggingface.co/datasets/ulamai/UnsolvedMath
- Project website: https://www.unsolvedmath.com
- Append-only OPDP record count: 15,458
- Frozen prior-release records: 8,785, retained exactly from OPDP v1.5
- New UnsolvedMath v1.6.0 records: 6,673
- Distinct natural-source Oberwolfach reports recovered for the new cohort: 1,106
- Compatibility-base `problems.json` SHA-256: `954E1151491871B831B6FC3084957E790075ED9AD739FE715328863EBE710515`
- Upstream UnsolvedMath v1.5.0 revision: `c423bd6c88433fe614b0f8b206201f580e0a7355`
- Upstream v1.5.0 `problems.json` SHA-256: `8AE5B01B910BB6123E6DE5813204026A01BE16DD9BC1883CD51C9C0E1A4B62DA`
- Upstream UnsolvedMath v1.6.0 revision: `b9437975f3c873f635a13c48f8b022f5ba80898a`
- Upstream v1.6.0 `problems.json` SHA-256: `0A11E1B86385B6095F001803E2BB9D176B3498DA0932D7AF3700E1132EF153AC`
- Append-only v1.6 composite source SHA-256: `52F4D1C618ACC02256045F0B44EDEA2E059B2C90FF40F51F1F628B16C3E9FC0E`
- Declared dataset license: Creative Commons Attribution 4.0 International (CC BY 4.0)

The JSON deliberately preserves the complete frozen source row for each problem under `source_record`. The source-provenance sidecar separately records the release cohort, Ulam collection, and finest recoverable natural source document. For the newest cohort this is ordinarily the exact Oberwolfach report DOI and citation; for earlier records it may be an AIM workshop slug, a parsed source-list reference, the frozen collection, or an explicit unknown/fallback. Evidence and literature URLs are not automatically treated as origin sources.

Individual records may contain additional source, status, or rights warnings. Consumers should retain attribution, inspect `evidence.rights_note`, `evidence.status_evidence`, `flags`, and the provenance sidecar, and verify canonical sources before republishing source text or presenting a problem as currently open. The UnsolvedMath dataset license does not supersede any attribution or reuse terms attached to an underlying source document; Oberwolfach reports and other source materials should be cited individually when their content is reused.

## Assessment work

The OPDP framework application, generated rationales, workbook, methodology, report, and JSON export were prepared by **Alejandro Zarzuelo Urdiales with ChatGPT 5.6 Sol**.

The assessment is a provisional, rule-based editorial first pass dated 2026-07-31. It is not expert certification, an empirical benchmark run, or a complete current-literature status audit.

The 2026-08-18 expansion preserves all 5,426 legacy OPDP records exactly and appends OPDP classifications for 3,359 AIM Workshop Problem Lists records from UnsolvedMath v1.5.0. Ulam's AIM research classifications and statuses are machine-generated and require independent expert verification.

The 2026-08-26 expansion preserves all 8,785 OPDP v1.5 records exactly and appends OPDP classifications for 6,673 records from UnsolvedMath v1.6.0's Oberwolfach Reports Open Problems collection. Those records resolve to 1,106 distinct report sources. Their Ulam L3 labels are collection defaults rather than independent expert rankings for each problem; all OPDP classifications remain provisional and require expert review.

The 2026-09-08 v1.7 expansion preserves all 15,458 OPDP v1.6 records exactly and appends provisional classifications for 87,105 MathDB public-list items. Each classification uses only the source's incomplete `excerpt`; it is flagged `source_excerpt_only`, capped at C0 confidence, and requires recalibration before use as a statement-complete difficulty assessment. No full-detail corpus, independent status audit, source-provenance study, or cross-dataset comparison is asserted for this expansion.

The 2026-08-04 ChatGPT 5.6 Sol Ultra companion derives a model-specific 0–1000 full-solution difficulty estimate from those OPDP inputs. Its scores and rationales were also prepared by **Alejandro Zarzuelo Urdiales with ChatGPT 5.6 Sol Ultra**. No problem was empirically attempted; the scores are ordinal editorial estimates under the protocol stated in the rationale file, not probabilities or claims of solvability.

## Rights

Public visibility of this repository does not, by itself, grant additional rights over original analysis or third-party source material. Reusers remain responsible for complying with the UnsolvedMath and MathDB dataset terms, preserving attribution to the applicable dataset and available cited source, retaining the MathDB excerpt limitation, and respecting any per-record rights notices or underlying-source restrictions.

