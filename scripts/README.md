# v1.6 analysis and workbook builders

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

The canonical assessment scores were produced under the unchanged `OPDP-1.0-rulepass-2026-07-31` rule. The scripts in this directory rebuild the new source-aware analysis and workbook layers; they do not refresh or rewrite frozen legacy records.
