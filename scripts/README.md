# Release builders and validators

## MathDB statement recovery (separate, append-only workstream)

`mathdb_recovery/` is deliberately separate from the v1.7 builder. It binds
all recovery work to the frozen 87,105-record public catalog, preserves an
append-only outcome ledger, and never rewrites the excerpt-derived v1.7
payload. It supplies:

- `bootstrap_mathdb_recovery.py` — deterministic canonical task shards;
- `build_arxiv_source_manifest.py` — read-only normalization/deduplication of
  declared arXiv source leads;
- `acquire_arxiv_metadata.py` — dry-run-by-default, resumable metadata-only
  acquisition from the official arXiv API (never a full-text fetch);
- `validate_arxiv_metadata_acquisition.py` — read-only integrity audit of
  immutable metadata request plans and response envelopes;
- `capture_collection_source_evidence.py` and
  `build_collection_source_inventory.py` — hash-only collection evidence and
  frozen-Ulam overlap pointers;
- `append_recovery_event.py` and `validate_recovery_run.py` — guarded ledger
  writes and rescore-gate validation; and
- `prototype_mathdb_statement_recovery.mjs` — fixture-tested local candidate
  extraction and label assignment for an already-authorized source artifact.

See [`../docs/MATHDB_STATEMENT_RECOVERY_PROTOCOL.md`](../docs/MATHDB_STATEMENT_RECOVERY_PROTOCOL.md),
[`../docs/MATHDB_COLLECTION_RECOVERY_INVENTORY.md`](../docs/MATHDB_COLLECTION_RECOVERY_INVENTORY.md),
and [`../docs/ARXIV_BULK_RECOVERY_DECISION_NOTE.md`](../docs/ARXIV_BULK_RECOVERY_DECISION_NOTE.md).
The arXiv full-source stage requires an authorized bulk-data route; it is not
implicitly triggered by any release builder.  The separate metadata worker
only builds immutable, rate-limited API evidence to inform a later S3 mapping;
it does not alter v1.7 or recover a mathematical statement on its own.

## v1.7 MathDB append-only expansion (current)

The v1.7 pipeline freezes the exact 87,105-item MathDB inventory, captures the corresponding public catalog list items, and appends them to the 15,458-record v1.6 OPDP base. The complete release contains 102,563 records. It is JSON-only: no 102,563-row workbook is generated.

First freeze the sitemap inventory. `--limit 1` deliberately stops the richer per-problem acquisition after one permitted diagnostic item; the inventory itself is complete. Reusing the same output directory resumes the same frozen inventory, while a later snapshot must use a new directory.

```powershell
python scripts/acquire_mathdb_snapshot.py `
  --output-dir ..\opdp-v1_7-mathdb-build\mathdb-snapshot-2026-09-08 `
  --limit 1
```

Then acquire the complete public-catalog snapshot against that frozen inventory:

```powershell
python scripts/acquire_mathdb_catalog_snapshot.py `
  --snapshot-dir ..\opdp-v1_7-mathdb-build\mathdb-snapshot-2026-09-08
```

This produces `mathdb_problem_summaries.jsonl`, with one `{schema,snapshot,problem}` envelope per item under schema `opdp.mathdb.problem-summary.v1`, and `mathdb_catalog_manifest.json`. The exact raw list item is `problem`; the only assessed mathematical text is `problem.excerpt`. The excerpt is explicitly incomplete and is not represented as a full problem statement.

After the catalog manifest reports `status: complete` and its embedded validation checks pass, build the append-only OPDP export. The expanded heap is for the 262 MB uncompressed v1.6 base. MathDB records and the final JSON are streamed rather than accumulated as one JavaScript array or string.

```powershell
node --max-old-space-size=16384 scripts/build_mathdb_append_v1_7.mjs `
  --base data/Ulam_UnsolvedMath_OPDP_Assessments_v1.6.json.gz `
  --mathdb ..\opdp-v1_7-mathdb-build\mathdb-snapshot-2026-09-08\mathdb_problem_summaries.jsonl `
  --manifest ..\opdp-v1_7-mathdb-build\mathdb-snapshot-2026-09-08\mathdb_catalog_manifest.json `
  --output data/Ulam_MathDB_OPDP_Assessments_v1.7.json.gz `
  --validation data/OPDP_v1.7_MathDB_Append_Validation.json
```

MathDB additions use the reversible mapping `problem_id = 40000000 + MathDB number` and `problem_number = "MATHDB-{number}"`. The input JSONL remains in strictly increasing sitemap-number order. The builder preserves every complete v1.6 record exactly, embeds the exact inner public-list item under `source_record`, and applies the unchanged `OPDP-1.0-rulepass-2026-07-31` rule. Every excerpt-derived record is flagged `source_excerpt_only`; all assigned confidence fields are capped at C0; ranges are widened; and all ten public rationales disclose the incomplete-text limitation.

MathDB's richer `/api/posts/{number}` surface enforces a limit of 500 unique problems per day, and [MathDB's Terms](https://mathdb.com/terms) direct bulk-data users to contact its administrators. This pipeline does not evade that control: it does not rotate identities, credentials, or network addresses, and it does not claim to have downloaded 87,105 full statements. It uses the supported public catalog surface and preserves that limitation in every appended assessment.

Run the independent streaming validator against the acquisition manifest. The
manifest supplies the frozen catalog and sitemap-inventory paths, hashes, and
counts; the validator independently derives the reversible ID mapping.

```powershell
python scripts/validate_v1_7_mathdb_expansion.py `
  --input data/Ulam_MathDB_OPDP_Assessments_v1.7.json.gz `
  --base data/Ulam_UnsolvedMath_OPDP_Assessments_v1.6.json.gz `
  --manifest ..\opdp-v1_7-mathdb-build\mathdb-snapshot-2026-09-08\mathdb_catalog_manifest.json `
  --snapshot-schema summary `
  --expected-records 102563 `
  --base-records 15458 `
  --report data/OPDP_v1.7_Independent_Validation.json
```

See `docs/OPDP_v1.7_APPEND_ONLY_NOTES.md` for the compatibility and validation contract and the completed release hashes.

## Historical v1.6 analysis and workbook builders

`build_v1_6_source_analysis.py` reconstructs the release-cohort/source hierarchy and regenerates every v1.6 analysis CSV plus the provenance sidecar. It accepts compressed or uncompressed OPDP exports and asserts deep equality of all 8,785 v1.5 compatibility records.

```powershell
python scripts/build_v1_6_source_analysis.py `
  --input data/Ulam_UnsolvedMath_OPDP_Assessments_v1.6.json.gz `
  --base-v15 data/Ulam_UnsolvedMath_OPDP_Assessments_v1.5.json.gz `
  --output-dir analysis-v1.6
```

The analysis builder requires Python 3.11+ and NumPy. Its source bootstrap is deterministic (`seed = 20260826`; 4,000 replicates by default).

`build_v1_6_workbook.mjs` reconstructs the eight-sheet Excel decision workbook from the canonical payload and generated analysis files. It requires Node.js and `@oai/artifact-tool`. The expanded heap avoids Node's default memory ceiling for 15,458 rows.

```powershell
node --max-old-space-size=8192 scripts/build_v1_6_workbook.mjs `
  data/Ulam_UnsolvedMath_OPDP_Assessments_v1.6.json.gz `
  analysis-v1.6 `
  analysis-v1.6/OPDP_v1.6_Source_Provenance.json.gz `
  Ulam_UnsolvedMath_Difficulty_Atlas_v1.6.xlsx `
  workbook-previews-v1.6
```

The canonical assessment scores were produced under the unchanged `OPDP-1.0-rulepass-2026-07-31` rule. These historical scripts rebuild the v1.6 source-aware analysis and workbook layers; they do not refresh or rewrite frozen legacy records.
