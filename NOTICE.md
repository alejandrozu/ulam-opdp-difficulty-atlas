# Attribution and provenance notice

## Source corpus

This project analyzes the **UnsolvedMath v1.2.0** corpus published by Ulam AI.

- Dataset page: https://huggingface.co/datasets/ulamai/UnsolvedMath
- Project website: https://www.unsolvedmath.com
- Append-only OPDP record count: 8,785
- Compatibility-base `problems.json` SHA-256: `954E1151491871B831B6FC3084957E790075ED9AD739FE715328863EBE710515`
- Upstream UnsolvedMath v1.5.0 revision: `c423bd6c88433fe614b0f8b206201f580e0a7355`
- Upstream v1.5.0 `problems.json` SHA-256: `8AE5B01B910BB6123E6DE5813204026A01BE16DD9BC1883CD51C9C0E1A4B62DA`
- Append-only composite source SHA-256: `E706B196A94A3E820FEC035DC839051013C5AF87474875A67886BB7979CC29FA`
- Declared dataset license: Creative Commons Attribution 4.0 International (CC BY 4.0)

The JSON deliberately preserves the complete frozen source row for each problem under `source_record`. Individual records may contain additional source, status, or rights warnings. Consumers should retain attribution, inspect `evidence.rights_note`, `evidence.status_evidence`, and `flags`, and verify canonical sources before republishing source text or presenting a problem as currently open.

## Assessment work

The OPDP framework application, generated rationales, workbook, methodology, report, and JSON export were prepared by **Alejandro Zarzuelo Urdiales with ChatGPT 5.6 Sol**.

The assessment is a provisional, rule-based editorial first pass dated 2026-07-31. It is not expert certification, an empirical benchmark run, or a complete current-literature status audit.

The 2026-08-18 expansion preserves all 5,426 legacy OPDP records exactly and appends OPDP classifications for 3,359 AIM Workshop Problem Lists records from UnsolvedMath v1.5.0. Ulam's AIM research classifications and statuses are machine-generated and require independent expert verification.

The 2026-08-04 ChatGPT 5.6 Sol Ultra companion derives a model-specific 0–1000 full-solution difficulty estimate from those OPDP inputs. Its scores and rationales were also prepared by **Alejandro Zarzuelo Urdiales with ChatGPT 5.6 Sol Ultra**. No problem was empirically attempted; the scores are ordinal editorial estimates under the protocol stated in the rationale file, not probabilities or claims of solvability.

## Rights

Public visibility of this repository does not, by itself, grant additional rights over original analysis or third-party source material. Reusers remain responsible for complying with the UnsolvedMath dataset license, preserving attribution, and respecting any per-record rights notices or underlying-source restrictions.

