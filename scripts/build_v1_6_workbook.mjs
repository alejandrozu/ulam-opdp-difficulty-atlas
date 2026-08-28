import fs from "node:fs/promises";
import path from "node:path";
import zlib from "node:zlib";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const [jsonPath, analysisDir, provenancePath, outputPath, previewDir] = process.argv.slice(2);
if (!jsonPath || !analysisDir || !provenancePath || !outputPath || !previewDir) {
  throw new Error("Usage: node build_compact_workbook_v1_6.mjs INPUT_JSON ANALYSIS_DIR SOURCE_PROVENANCE_JSON_GZ OUTPUT_XLSX PREVIEW_DIR");
}
const jsonBytes = await fs.readFile(jsonPath);
const payload = JSON.parse((jsonPath.endsWith(".gz") ? zlib.gunzipSync(jsonBytes) : jsonBytes).toString("utf8"));
const records = payload.records;
if (records.length !== 15458) throw new Error(`Expected 15458 records, found ${records.length}`);

const parseCsv = text => {
  const rows = [];
  let row = [], cell = "", quoted = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (ch === '"') quoted = false;
      else cell += ch;
    } else if (ch === '"') quoted = true;
    else if (ch === ',') { row.push(cell); cell = ""; }
    else if (ch === '\n') { row.push(cell.replace(/\r$/, "")); rows.push(row); row = []; cell = ""; }
    else cell += ch;
  }
  if (cell.length || row.length) { row.push(cell.replace(/\r$/, "")); rows.push(row); }
  const headers = (rows.shift() || []).map((h, i) => i === 0 ? h.replace(/^\uFEFF/, "") : h);
  return rows.filter(r => r.some(v => v !== "")).map(r => Object.fromEntries(headers.map((h, i) => [h, r[i] ?? ""])));
};
const readCsv = async filename => parseCsv(await fs.readFile(path.join(analysisDir, filename), "utf8"));
const cohortSummary = await readCsv("OPDP_v1.6_Cohort_Dimension_Summary.csv");
const cohortComparisons = await readCsv("OPDP_v1.6_Cohort_Comparisons.csv");
const sourceSummaryRows = await readCsv("OPDP_v1.6_Source_Summary.csv");
const sourceComposition = await readCsv("OPDP_v1.6_Source_Composition.csv");
const sourceClustering = await readCsv("OPDP_v1.6_Source_Clustering.csv");
const provenance = JSON.parse(zlib.gunzipSync(await fs.readFile(provenancePath)).toString("utf8"));
if (provenance.record_count !== records.length || provenance.records.length !== records.length) throw new Error("Source provenance count mismatch");
const provenanceById = new Map(provenance.records.map(r => [r.problem_id, r]));

const wb = Workbook.create();
const dashboard = wb.worksheets.add("Dashboard");
const cohortAnalysis = wb.worksheets.add("Cohort Analysis");
const sourceSummary = wb.worksheets.add("Source Summary");
const atlas = wb.worksheets.add("Atlas");
const inputs = wb.worksheets.add("Scoring Inputs");
const sources = wb.worksheets.add("Source Index");
const qa = wb.worksheets.add("QA Flags");
const rubric = wb.worksheets.add("Rubric");

const navy = "#123047", teal = "#1F6F78", red = "#A33A3A", pale = "#F4F7F9", white = "#FFFFFF", ink = "#1F2933", line = "#D7DEE4";
const title = (sheet, heading, subtitle, cols) => {
  sheet.getRangeByIndexes(0, 0, 1, cols).merge();
  sheet.getRangeByIndexes(0, 0, 1, cols).values = [[heading]];
  sheet.getRangeByIndexes(0, 0, 1, cols).format = { fill: navy, font: { bold: true, color: white, size: 16 }, rowHeight: 30 };
  sheet.getRangeByIndexes(1, 0, 1, cols).merge();
  sheet.getRangeByIndexes(1, 0, 1, cols).values = [[subtitle]];
  sheet.getRangeByIndexes(1, 0, 1, cols).format = { fill: pale, font: { color: ink, size: 10 }, rowHeight: 24 };
  sheet.showGridLines = false;
};
const header = (range, fill = teal) => { range.format = { fill, font: { bold: true, color: white }, wrapText: true, rowHeight: 34, verticalAlignment: "center" }; };
const body = (range, wrap = false) => { range.format = { font: { color: ink, size: 9 }, wrapText: wrap, verticalAlignment: "top", borders: { bottom: { style: "thin", color: line } } }; };
const cleanCell = value => {
  if (typeof value !== "string") return value;
  const cleaned = value.replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\uFFFE\uFFFF]/g, " ");
  return cleaned.length <= 32767 ? cleaned : `${cleaned.slice(0, 32740)} [truncated in workbook]`;
};
const numberOrBlank = value => value === "" || value === null || value === undefined ? null : Number(value);
const writeChunks = (sheet, startRow, startCol, rows, chunk = 400) => {
  if (!rows.length) return;
  for (let i = 0; i < rows.length; i += chunk) {
    const part = rows.slice(i, i + chunk).map(row => row.map(cleanCell));
    sheet.getRangeByIndexes(startRow + i, startCol, part.length, part[0].length).values = part;
  }
};
const tagText = r => r.tags.join("; ");
const flagText = r => r.flags.join("; ");

// Rubric constants and compatibility notes.
title(rubric, "OPDP 1.0 Rubric and Compatibility Contract", "Unchanged rule version; v1.6 appends 6,673 OWR records while freezing all 8,785 prior OPDP records", 8);
const rubricRows = [
  ["Parameter", "Value", "Meaning", null, "Tier", "Low", "High", "Label"],
  ["Dataset version", "1.6.0", "Ulam upstream version used for additions", null, "T1", 0, 2.5, "Accessible/Audit"],
  ["Rule version", payload.assessment_release.rule_version, "Unchanged OPDP scoring rule", null, "T2", 2.5, 4.5, "Research Sprint"],
  ["Compatibility base", payload.assessment_release.compatibility.base_export_id, "All 8,785 v1.5 records preserved exactly", null, "T3", 4.5, 6.5, "Serious Project"],
  ["Added records", payload.assessment_release.compatibility.added_record_count, "New Oberwolfach Reports records", null, "T4", 6.5, 8.5, "Frontier Challenge"],
  ["Total records", records.length, "Complete expanded OPDP", null, "T5", 8.5, 10, "Grand Challenge"],
  ["D formula", "2.5×(.30CG+.20RG+.20TD+.20KB+.10SS)", "Rounded to one decimal and clamped 0–10", null, null, null, null, null],
  ["AI formula", "clamp(D+0.5×sum predictors)", "Predictors use −1 favorable, 0 mixed, +1 hostile", null, null, null, null, null],
  ["T formula", "round(clamp(9−.8AI+.2L−.15mean(V)+bonuses/penalty))", "Partial-progress tractability, not full-solution probability", null, null, null, null, null],
  ["Source snapshot SHA-256", payload.dataset_snapshot.sha256, "Append-only composite source snapshot", null, null, null, null, null],
  ["Upstream revision", payload.dataset_snapshot.upstream_revision, "Frozen Hugging Face revision", null, null, null, null, null],
];
writeChunks(rubric, 3, 0, rubricRows);
header(rubric.getRange("A4:H4"), navy);
body(rubric.getRange("A5:H14"), true);
rubric.getRange("A4:A14").format.columnWidth = 24;
rubric.getRange("B4:B14").format.columnWidth = 70;
rubric.getRange("C4:C14").format.columnWidth = 52;
rubric.getRange("D4:D14").format.columnWidth = 4;
rubric.getRange("E4:H14").format.columnWidth = 18;
rubric.freezePanes.freezeRows(4);

// Compact formula audit. Detailed predictors and every rationale remain in the canonical JSON.
const inputHeaders = [
  "Dataset ID", "Catalog Status", "Assessment Gate", "CG", "RG", "TD", "KB", "SS", "Human H", "Tool L", "V True", "V False",
  "Stored D", "Recalc D", "D Delta", "AI Relative", "Stored AI D", "Recalc AI D", "AI D Delta", "Stored T", "Recalc T", "T Delta"
];
title(inputs, "Compact Formula Audit", "Published inputs and independent D / AI / T recalculation; full predictor trace is in the canonical JSON", inputHeaders.length);
inputs.getRangeByIndexes(3, 0, 1, inputHeaders.length).values = [inputHeaders];
header(inputs.getRangeByIndexes(3, 0, 1, inputHeaders.length));
const inputRows = records.map(r => [
  r.problem_id, r.catalog_status, r.assessment_gate.value,
  r.intrinsic_difficulty.factors.conceptual_gap.score, r.intrinsic_difficulty.factors.route_gap.score, r.intrinsic_difficulty.factors.technical_depth.score,
  r.intrinsic_difficulty.factors.known_barrier.score, r.intrinsic_difficulty.factors.search_scale.score,
  r.human_attention.effort.score, r.tool_leverage.score, r.verification.if_true_score, r.verification.if_false_score,
  r.intrinsic_difficulty.score, null, null, r.ai_assessment.relative_adjustment, r.ai_assessment.difficulty_score, null, null,
  r.tractability.score, null, null
]);
writeChunks(inputs, 4, 0, inputRows, 350);
const last = records.length + 4;
const formulaCols = {
  13: row => `=MIN(IF(B${row}="solved",8.5,10),ROUND(2.5*(0.30*D${row}+0.20*E${row}+0.20*F${row}+0.20*G${row}+0.10*H${row}),1))`,
  14: row => `=N${row}-M${row}`,
  17: row => `=MIN(10,MAX(0,ROUND(M${row}+P${row},1)))`,
  18: row => `=R${row}-Q${row}`,
  20: row => `=IF(OR(C${row}="solved",C${row}="ill_posed"),"",ROUND(MAX(0,MIN(10,9-0.8*R${row}+0.2*J${row}-0.15*AVERAGE(K${row}:L${row})+0.6*IF(I${row}<=4,1,0)+0.5*IF(B${row}="partially_solved",1,0)-1.0*IF(OR(C${row}="status_unclear",C${row}="ill_posed",C${row}="solved"),1,0))),0))`,
  21: row => `=IF(AND(T${row}="",U${row}=""),0,U${row}-T${row})`,
};
for (const [zeroCol, maker] of Object.entries(formulaCols)) {
  inputs.getRangeByIndexes(4, Number(zeroCol), records.length, 1).formulas = records.map((_, i) => [maker(i + 5)]);
}
body(inputs.getRangeByIndexes(4, 0, records.length, inputHeaders.length));
inputs.getRangeByIndexes(4, 0, records.length, inputHeaders.length).format.rowHeight = 20;
for (let c = 0; c < inputHeaders.length; c++) inputs.getRangeByIndexes(3, c, records.length + 1, 1).format.columnWidth = c < 3 ? [12,16,18][c] : 13;
inputs.getRange(`M5:V${last}`).format.numberFormat = "0.0";
for (const col of ["O", "S", "V"]) inputs.getRange(`${col}5:${col}${last}`).conditionalFormats.add("cellIs", { operator: "notEqual", formula: 0, format: { fill: "#FBEAEA", font: { color: red, bold: true } } });
inputs.freezePanes.freezeRows(4);
inputs.freezePanes.freezeColumns(1);

// Compact atlas for filtering and database review.
const atlasHeaders = ["Dataset ID", "Problem #", "Title", "Catalog Status", "Assessment Gate", "Category", "Source Collection", "Scope", "Answer Type", "Intrinsic D", "Difficulty Label", "AI Difficulty", "AI Fit", "Human H", "Exposure X", "Tractability T", "V True", "V False", "Formalization F", "Prerequisite P", "Breadth B", "Tool Leverage", "Route", "Tags", "Review Priority", "Flags", "Overall Rationale"];
title(atlas, "Ulam / UnsolvedMath OPDP Atlas", `Append-only v1.6 expansion | ${records.length.toLocaleString("en-US")} records | all 8,785 prior records unchanged`, atlasHeaders.length);
atlas.getRangeByIndexes(3, 0, 1, atlasHeaders.length).values = [atlasHeaders];
header(atlas.getRangeByIndexes(3, 0, 1, atlasHeaders.length), navy);
const atlasRows = records.map(r => [r.problem_id, r.problem_number, r.title, r.catalog_status, r.assessment_gate.value, r.classification.category.label, r.classification.source_collection.label, r.classification.scope, r.classification.answer_type, r.intrinsic_difficulty.score, r.difficulty_label, r.ai_assessment.difficulty_score, r.ai_assessment.fit_label, r.human_attention.effort.score, r.human_attention.exposure.score, r.tractability.score, r.verification.if_true_score, r.verification.if_false_score, r.formalization.score, r.prerequisites.preparation_score, r.prerequisites.breadth_score, r.tool_leverage.score, r.recommended_approach.route_label, tagText(r), r.review.priority_code, flagText(r), r.explanation]);
writeChunks(atlas, 4, 0, atlasRows, 400);
body(atlas.getRangeByIndexes(4, 0, records.length, atlasHeaders.length));
atlas.getRangeByIndexes(4, 0, records.length, atlasHeaders.length).format.rowHeight = 22;
for (let c = 0; c < atlasHeaders.length; c++) atlas.getRangeByIndexes(3, c, records.length + 1, 1).format.columnWidth = [12,15,34,14,18,18,30,13,16,11,24,12,18,10,10,12,10,10,13,13,10,12,38,30,15,34,70][c];
atlas.getRange(`J5:J${last}`).conditionalFormats.add("colorScale", { colors: ["#DCE9F5", "#FFE5A3", "#E6A1A1"], thresholds: ["min", "50%", "max"] });
atlas.freezePanes.freezeRows(4);
atlas.freezePanes.freezeColumns(3);

// Source statement and natural-source index without repeating long background fields.
const sourceHeaders = ["Dataset ID", "Problem #", "Cohort", "Title", "Catalog Status", "Category", "Source Collection", "Natural Source ID", "Natural Source", "Source Type", "Source Year", "Assignment Confidence", "Ulam URL", "Natural Source URL", "OPDP Source URL"];
title(sources, "Source Index", "Cohort → collection → natural source document, joined by problem_id; statements and full source rows remain in the JSON", sourceHeaders.length);
sources.getRangeByIndexes(3, 0, 1, sourceHeaders.length).values = [sourceHeaders];
header(sources.getRangeByIndexes(3, 0, 1, sourceHeaders.length), navy);
const sourceRows = records.map(r => {
  const p = provenanceById.get(r.problem_id);
  if (!p) throw new Error(`Missing source provenance for ${r.problem_id}`);
  return [r.problem_id, r.problem_number, p.cohort_code, r.title, r.catalog_status, r.classification.category.label, r.classification.source_collection.label, p.source_document_id, p.source_document_label, p.source_document_type, p.source_year, p.source_assignment_confidence, r.provenance.ulam_url, p.source_document_url, r.provenance.source_url_used];
});
writeChunks(sources, 4, 0, sourceRows, 250);
body(sources.getRangeByIndexes(4, 0, records.length, sourceHeaders.length), true);
sources.getRangeByIndexes(4, 0, records.length, sourceHeaders.length).format.rowHeight = 54;
[12,15,10,34,14,18,30,36,58,24,12,16,42,48,42].forEach((width, c) => sources.getRangeByIndexes(3, c, records.length + 1, 1).format.columnWidth = width);
sources.freezePanes.freezeRows(4);
sources.freezePanes.freezeColumns(4);

// QA queue.
const flagged = records.filter(r => r.flags.length || !["verified_open", "source_claimed_open"].includes(r.assessment_gate.value));
const qaHeaders = ["Dataset ID", "Problem #", "Title", "Catalog Status", "Assessment Gate", "Review Priority", "Flags", "Recommended Curation Action", "Source URL"];
title(qa, "Curation and QA Queue", `${flagged.length.toLocaleString("en-US")} records require status, scope, identity, or source review`, qaHeaders.length);
qa.getRangeByIndexes(3, 0, 1, qaHeaders.length).values = [qaHeaders];
header(qa.getRangeByIndexes(3, 0, 1, qaHeaders.length), red);
writeChunks(qa, 4, 0, flagged.map(r => [r.problem_id, r.problem_number, r.title, r.catalog_status, r.assessment_gate.value, r.review.priority_code, flagText(r), r.review.recommended_curation_action, r.provenance.source_url_used]), 300);
body(qa.getRangeByIndexes(4, 0, flagged.length, qaHeaders.length), true);
qa.getRangeByIndexes(4, 0, flagged.length, qaHeaders.length).format.rowHeight = 38;
[12,15,34,14,18,15,48,44,44].forEach((width, c) => qa.getRangeByIndexes(3, c, flagged.length + 1, 1).format.columnWidth = width);
qa.freezePanes.freezeRows(4);
qa.freezePanes.freezeColumns(3);

// Three-cohort summaries, pairwise contrasts, and source-clustering diagnostics.
title(cohortAnalysis, "Cohort and Source-Aware Comparison", "V0 original 5,426 | V1 AIM 3,359 | V2 Oberwolfach 6,673; p/q values are descriptive, not causal", 20);
const cohortHeaders = ["Cohort", "Cohort label", "Dimension", "Meaning", "Family", "Direction", "n", "Mean", "SD", "P05", "P25", "Median", "P75", "P95"];
cohortAnalysis.getRangeByIndexes(3, 0, 1, cohortHeaders.length).values = [cohortHeaders];
header(cohortAnalysis.getRangeByIndexes(3, 0, 1, cohortHeaders.length), navy);
const cohortRows = cohortSummary.map(r => [r.cohort_code, r.cohort_label, r.dimension, r.dimension_label, r.family, r.direction_semantics, numberOrBlank(r.n), numberOrBlank(r.mean), numberOrBlank(r.sd), numberOrBlank(r.p05), numberOrBlank(r.p25), numberOrBlank(r.median), numberOrBlank(r.p75), numberOrBlank(r.p95)]);
writeChunks(cohortAnalysis, 4, 0, cohortRows, 250);
body(cohortAnalysis.getRangeByIndexes(4, 0, cohortRows.length, cohortHeaders.length));
const contrastStart = 5 + cohortRows.length + 2;
cohortAnalysis.getRangeByIndexes(contrastStart - 1, 0, 1, 20).merge();
cohortAnalysis.getRangeByIndexes(contrastStart - 1, 0, 1, 20).values = [["Pairwise contrasts: positive delta means the newer cohort is numerically higher; read direction_semantics before interpreting."]];
cohortAnalysis.getRangeByIndexes(contrastStart - 1, 0, 1, 20).format = { fill: pale, font: { bold: true, color: ink }, rowHeight: 24 };
const contrastHeaders = ["Contrast", "Dimension", "Meaning", "Direction", "Older n", "Newer n", "Older mean", "Newer mean", "Mean delta", "Hedges g", "Magnitude", "Cliff delta", "W1", "Row p", "Row BH q", "Source units old", "Source units new", "Source-macro delta", "Source-macro BH q", "Equivalence result"];
cohortAnalysis.getRangeByIndexes(contrastStart, 0, 1, contrastHeaders.length).values = [contrastHeaders];
header(cohortAnalysis.getRangeByIndexes(contrastStart, 0, 1, contrastHeaders.length), teal);
const contrastRows = cohortComparisons.map(r => [r.contrast, r.dimension, r.dimension_label, r.direction_semantics, numberOrBlank(r.older_n), numberOrBlank(r.newer_n), numberOrBlank(r.older_mean), numberOrBlank(r.newer_mean), numberOrBlank(r.mean_delta), numberOrBlank(r.hedges_g), r.effect_magnitude, numberOrBlank(r.cliffs_delta), numberOrBlank(r.wasserstein_1), numberOrBlank(r.row_welch_normal_approx_p), numberOrBlank(r.row_bh_q_60_tests), numberOrBlank(r.older_source_units), numberOrBlank(r.newer_source_units), numberOrBlank(r.source_macro_delta), numberOrBlank(r.source_macro_bh_q_60_tests), r.equivalence_status]);
writeChunks(cohortAnalysis, contrastStart + 1, 0, contrastRows, 250);
body(cohortAnalysis.getRangeByIndexes(contrastStart + 1, 0, contrastRows.length, contrastHeaders.length));
const clusteringStart = contrastStart + contrastRows.length + 3;
cohortAnalysis.getRangeByIndexes(clusteringStart - 1, 0, 1, 8).merge();
cohortAnalysis.getRangeByIndexes(clusteringStart - 1, 0, 1, 8).values = [["Fixed-group source clustering: η² is descriptive source-associated variance, not a causal source effect."]];
cohortAnalysis.getRangeByIndexes(clusteringStart - 1, 0, 1, 8).format = { fill: pale, font: { bold: true, color: ink }, rowHeight: 24 };
const clusteringHeaders = ["Cohort", "Dimension", "Meaning", "n", "Source units", "Source η²", "Within-source SD", "Total SD"];
cohortAnalysis.getRangeByIndexes(clusteringStart, 0, 1, clusteringHeaders.length).values = [clusteringHeaders];
header(cohortAnalysis.getRangeByIndexes(clusteringStart, 0, 1, clusteringHeaders.length), teal);
const clusteringRows = sourceClustering.map(r => [r.cohort_code, r.dimension, r.dimension_label, numberOrBlank(r.n), numberOrBlank(r.groups), numberOrBlank(r.eta2), numberOrBlank(r.within_sd), numberOrBlank(r.total_sd)]);
writeChunks(cohortAnalysis, clusteringStart + 1, 0, clusteringRows, 250);
body(cohortAnalysis.getRangeByIndexes(clusteringStart + 1, 0, clusteringRows.length, clusteringHeaders.length));
for (let c = 0; c < 20; c++) cohortAnalysis.getRangeByIndexes(3, c, contrastStart + contrastRows.length - 2, 1).format.columnWidth = c === 1 ? 34 : c === 2 ? 12 : c === 3 || c === 19 ? 28 : 13;
cohortAnalysis.getRangeByIndexes(4, 7, cohortRows.length, 7).format.numberFormat = "0.000";
cohortAnalysis.getRangeByIndexes(contrastStart + 1, 6, contrastRows.length, 13).format.numberFormat = "0.000";
cohortAnalysis.getRangeByIndexes(clusteringStart + 1, 5, clusteringRows.length, 3).format.numberFormat = "0.000";
cohortAnalysis.freezePanes.freezeRows(4);
cohortAnalysis.freezePanes.freezeColumns(2);

// Complete natural-source summary (99 original units, 162 AIM workshops, 1,106 OWR reports).
const sourceSummaryHeaders = ["Cohort", "Collection", "Natural Source ID", "Natural Source", "Type", "Year", "n", "Open", "Partial", "Solved", "Leading Category", "Categories", "D Mean", "D SD", "AI D Mean", "T Mean", "F Mean", "P Mean", "B Mean", "L Mean", "Flagged Rate", "Reliability", "Distance to V0", "Distance to V1", "Source URL"];
title(sourceSummary, "Natural-Source Profiles", `${sourceSummaryRows.length.toLocaleString("en-US")} identified source documents; small units are descriptive only`, sourceSummaryHeaders.length);
sourceSummary.getRangeByIndexes(3, 0, 1, sourceSummaryHeaders.length).values = [sourceSummaryHeaders];
header(sourceSummary.getRangeByIndexes(3, 0, 1, sourceSummaryHeaders.length), navy);
const sourceProfileRows = sourceSummaryRows.map(r => [r.cohort_code, r.source_collection_label, r.source_document_id, r.source_document_label, r.source_document_type, numberOrBlank(r.source_year), numberOrBlank(r.n), numberOrBlank(r.status_open_n), numberOrBlank(r.status_partially_solved_n), numberOrBlank(r.status_solved_n), r.leading_category, numberOrBlank(r.distinct_categories), numberOrBlank(r.mean_D), numberOrBlank(r.sd_D), numberOrBlank(r.mean_AI_D), numberOrBlank(r.mean_T), numberOrBlank(r.mean_F), numberOrBlank(r.mean_P), numberOrBlank(r.mean_B), numberOrBlank(r.mean_L), numberOrBlank(r.flagged_rate), r.reliability_label, numberOrBlank(r.distance_to_V0_centroid), numberOrBlank(r.distance_to_V1_centroid), r.source_document_url]);
writeChunks(sourceSummary, 4, 0, sourceProfileRows, 250);
body(sourceSummary.getRangeByIndexes(4, 0, sourceProfileRows.length, sourceSummaryHeaders.length));
sourceSummary.getRangeByIndexes(4, 0, sourceProfileRows.length, sourceSummaryHeaders.length).format.rowHeight = 28;
[10,30,38,58,24,10,9,9,9,9,22,10,11,11,12,11,11,11,11,11,12,28,14,14,50].forEach((width, c) => sourceSummary.getRangeByIndexes(3, c, sourceProfileRows.length + 1, 1).format.columnWidth = width);
sourceSummary.getRangeByIndexes(4, 12, sourceProfileRows.length, 12).format.numberFormat = "0.000";
sourceSummary.freezePanes.freezeRows(4);
sourceSummary.freezePanes.freezeColumns(4);

// Dashboard with formula-driven headline counts.
title(dashboard, "UnsolvedMath OPDP | Decision View", "Append-only v1.6: 5,426 original + 3,359 AIM + 6,673 Oberwolfach; all prior records frozen", 10);
dashboard.getRange("A4:B12").values = [["Metric", "Value"], ["Total records", null], ["Original V0", 5426], ["AIM V1", 3359], ["Oberwolfach V2", 6673], ["Natural source documents", provenance.counts.natural_source_documents], ["Catalog open", null], ["P0 immediate review", null], ["Grand challenges", null]];
header(dashboard.getRange("A4:B4"), navy);
dashboard.getRange("B5").formulas = [[`=COUNTA('Atlas'!$A$5:$A$${last})`]];
dashboard.getRange("B10").formulas = [[`=COUNTIF('Atlas'!$D$5:$D$${last},"open")`]];
dashboard.getRange("B11").formulas = [[`=COUNTIF('Atlas'!$Y$5:$Y$${last},"P0")`]];
dashboard.getRange("B12").formulas = [[`=COUNTIF('Atlas'!$K$5:$K$${last},"T5 Grand Challenge")`]];
body(dashboard.getRange("A5:B12"));
dashboard.getRange("A4:A12").format.columnWidth = 28;
dashboard.getRange("B4:B12").format.columnWidth = 18;
dashboard.getRange("B5:B12").format.numberFormat = "#,##0";
dashboard.getRange("D4:G10").values = [
  ["Compatibility contract", null, null, null],
  ["Legacy records changed", 0, "Required", "0"],
  ["Schema version", payload.schema_version, "Base", "1.0.0"],
  ["Record schema", payload.schema.record_schema_version, "Base", "1.0.0"],
  ["Rule version", payload.assessment_release.rule_version, "Policy", "unchanged"],
  ["Canonical join key", "problem_id", "New ID range", "30000001–30006673"],
  ["OWR reports", provenance.counts?.cohorts?.V2 ? 1106 : null, "Assignment", "exact DOI/citation"],
];
header(dashboard.getRange("D4:G4"), teal);
body(dashboard.getRange("D5:G10"));
dashboard.getRange("D4:D10").format.columnWidth = 25;
dashboard.getRange("E4:E10").format.columnWidth = 34;
dashboard.getRange("F4:G10").format.columnWidth = 18;
dashboard.getRange("I4:J8").values = [["Source structure", "Natural units"], ...sourceComposition.map(r => [r.cohort_code, numberOrBlank(r.natural_source_documents)]), ["Total identified", provenance.counts.natural_source_documents]];
header(dashboard.getRange("I4:J4"), teal);
body(dashboard.getRange("I5:J8"));
dashboard.getRange("I4:I8").format.columnWidth = 22;
dashboard.getRange("J4:J8").format.columnWidth = 18;
dashboard.getRange("J5:J8").format.numberFormat = "#,##0";
dashboard.showGridLines = false;

await fs.mkdir(path.dirname(outputPath), { recursive: true });
const xlsx = await SpreadsheetFile.exportXlsx(wb);
await xlsx.save(outputPath);

await fs.mkdir(previewDir, { recursive: true });
for (const [sheetName, range, filename] of [
  ["Dashboard", "A1:J12", "dashboard.png"], ["Cohort Analysis", "A1:T14", "cohort-analysis.png"], ["Source Summary", "A1:Y12", "source-summary.png"],
  ["Atlas", "A1:K12", "atlas.png"], ["Scoring Inputs", "A1:V12", "scoring-inputs.png"],
  ["Source Index", "A1:O9", "source-index.png"], ["QA Flags", "A1:I9", "qa.png"], ["Rubric", "A1:H14", "rubric.png"],
]) {
  const preview = await wb.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(path.join(previewDir, filename), new Uint8Array(await preview.arrayBuffer()));
}

const dashboardCheck = await wb.inspect({ kind: "table", sheetId: "Dashboard", range: "A1:J12", include: "values,formulas", tableMaxRows: 12, tableMaxCols: 10, maxChars: 7000 });
console.log(dashboardCheck.ndjson);
const formulaErrors = await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 100 }, maxChars: 3000, summary: "final formula error scan" });
console.log(formulaErrors.ndjson);

console.log(JSON.stringify({ outputPath, records: records.length, flagged: flagged.length }));
