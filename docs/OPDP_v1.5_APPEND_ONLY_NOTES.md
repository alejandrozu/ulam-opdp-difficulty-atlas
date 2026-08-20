# Ulam OPDP append-only expansion — v1.5

This release expands the Open Problem Difficulty Profile from 5,426 to 8,785 records while preserving backward compatibility.

## Compatibility contract

- All 5,426 records from the v1.2 OPDP export are preserved exactly, including their complete nested objects, source rows, scores, rationales, flags, and implementation metadata.
- The per-record schema version remains `1.0.0`.
- The record field order and OPDP rule version remain unchanged.
- Numeric `problem_id` remains the canonical join key.
- Exactly 3,359 new records are appended, using IDs `20000001` through `20003359` from the AIM Workshop Problem Lists added by Ulam.
- Changes that Ulam v1.5 made to pre-existing source rows are intentionally not imported into legacy OPDP records. This is an append-only expansion, not a status-refresh release.

## Source snapshots

- Compatibility base: UnsolvedMath v1.2.0, 5,426 records, SHA-256 `954E1151491871B831B6FC3084957E790075ED9AD739FE715328863EBE710515`.
- Latest upstream consulted: UnsolvedMath v1.5.0 at Hugging Face revision `c423bd6c88433fe614b0f8b206201f580e0a7355`, 8,785 records; `problems.json` SHA-256 `8AE5B01B910BB6123E6DE5813204026A01BE16DD9BC1883CD51C9C0E1A4B62DA`.
- Append-only composite source: 8,785 records, SHA-256 `E706B196A94A3E820FEC035DC839051013C5AF87474875A67886BB7979CC29FA`.

## New-record assessment summary

The 3,359 appended AIM records were assessed under the unchanged `OPDP-1.0-rulepass-2026-07-31` rule:

- Difficulty tiers: 14 T2 Research Sprint; 3,211 T3 Serious Project; 134 T4 Frontier Challenge.
- Catalog status: 191 open; 2,664 partially solved; 504 solved.
- Assessment gates: 2,804 source-claimed open; 504 solved; 38 ill-posed; 13 status unclear.
- AI fit: 515 AI-favored; 2,465 neutral/mixed; 379 AI-hostile.
- Mean intrinsic difficulty: 5.8/10; mean AI difficulty: 5.6/10.

The source's research classifications and statuses are machine-generated and require independent expert verification. OPDP scores remain a provisional rule-based editorial assessment rather than expert certification.

## Validation

- Independent export validation: zero errors and zero warnings.
- Legacy deep-equality check: 5,426 checked, zero changed.
- New-record completeness: 3,359 checked, zero missing required fields or rationale dimensions.
- Workbook formula audit: zero D, AI-relative, AI-difficulty, or tractability deltas across 8,785 rows; zero formula errors.
- Workbook visual QA: all seven sheets rendered and reviewed.
