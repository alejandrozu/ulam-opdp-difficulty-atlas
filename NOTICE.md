# Attribution and provenance notice

## Source corpus

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

The 2026-08-04 ChatGPT 5.6 Sol Ultra companion derives a model-specific 0–1000 full-solution difficulty estimate from those OPDP inputs. Its scores and rationales were also prepared by **Alejandro Zarzuelo Urdiales with ChatGPT 5.6 Sol Ultra**. No problem was empirically attempted; the scores are ordinal editorial estimates under the protocol stated in the rationale file, not probabilities or claims of solvability.

## Rights

Public visibility of this repository does not, by itself, grant additional rights over original analysis or third-party source material. Reusers remain responsible for complying with the UnsolvedMath dataset license, preserving attribution to both Ulam and the identified natural source, and respecting any per-record rights notices or underlying-source restrictions.

