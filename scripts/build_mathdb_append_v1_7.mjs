#!/usr/bin/env node
/**
 * Append a frozen MathDB catalog snapshot to the canonical OPDP v1.6 exchange
 * file without changing a byte of meaning in any existing record.
 *
 * The input is JSON Lines: one acquirer envelope {schema,snapshot,problem} per
 * line (a bare API object is also accepted for tests). Both full-detail
 * envelopes and complete-catalog public-list summary envelopes are supported.
 * The inner API object is retained losslessly under source_record. When only
 * MathDB's public-list excerpt is available it is used transparently as an
 * incomplete scoring input, receives C0 confidence and explicit disclosure in
 * every rationale, and is never represented as a complete problem statement.
 * MathDB's
 * number is projected to MATHDB-{number}; numeric OPDP id is the stable,
 * reversible 40,000,000 + MathDB-number mapping. Records must enter in strict
 * MathDB-number order, and the builder validates that order. The OPDP-1.0 rule pass is the
 * same cue-based editorial rule used by v1.6; MathDB importance, votes, views,
 * model attempts, solutions, and human-solves are deliberately not score
 * inputs.
 *
 * This builder is intentionally JSON-only.  A roughly 100k-row exchange file
 * is written record-by-record through gzip rather than assembled as one giant
 * string. Run Node with an expanded heap because the input objects and OPDP
 * base v1.6 records are resident while duplicate maps and summaries are built;
 * appended source and OPDP records are streamed through a compressed cache.
 *
 * Example:
 *   node --max-old-space-size=16384 scripts/build_mathdb_append_v1_7.mjs `
 *     --base data/Ulam_UnsolvedMath_OPDP_Assessments_v1.6.json.gz `
 *     --mathdb mathdb_problem_summaries.jsonl `
 *     --manifest mathdb_catalog_manifest.json `
 *     --output data/Ulam_MathDB_OPDP_Assessments_v1.7.json.gz `
 *     --validation data/OPDP_v1.7_MathDB_Append_Validation.json
 */

import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import zlib from "node:zlib";
import crypto from "node:crypto";
import readline from "node:readline";
import { once } from "node:events";

const RELEASE_VERSION = "1.7.0";
const RELEASE_DATE = "2026-09-08";
const RULE_VERSION = "OPDP-1.0-rulepass-2026-07-31";
const AI_PROTOCOL_ID = "frontier-generalist-hs1-2026-07-31";
const MATHDB_ID_OFFSET = 40_000_000;
const MATHDB_SET_ID = 400_000;
const EXPECTED_BASE_VERSION = "1.6.0";
const EXPECTED_BASE_COUNT = 15_458;
const RECORD_SCHEMA_VERSION = "1.0.0";
const MATHDB_DETAIL_SCHEMA = "opdp.mathdb.problem-detail.v1";
const MATHDB_SUMMARY_SCHEMA = "opdp.mathdb.problem-summary.v1";
const MATHDB_CATALOG_MANIFEST_SCHEMA = "opdp.mathdb.catalog-snapshot-manifest.v1";
const MATHDB_SUMMARY_TEXT_MODE = "mathdb_public_list_excerpt";
const MATHDB_SUMMARY_COMPLETENESS = "source_provided_excerpt_not_guaranteed_complete";
const SOURCE_EXCERPT_FLAG = "source_excerpt_only";

const HOUR_BANDS = [
  [0, 0, 10, "<10"], [1, 10, 30, "10-30"], [2, 30, 100, "30-100"],
  [3, 100, 300, "100-300"], [4, 300, 1000, "300-1,000"],
  [5, 1000, 3000, "1,000-3,000"], [6, 3000, 10000, "3,000-10,000"],
  [7, 10000, 30000, "10,000-30,000"], [8, 30000, 100000, "30,000-100,000"],
  [9, 100000, 300000, "100,000-300,000"], [10, 300000, null, ">300,000"],
].map(([score, minimum, maximum, label]) => ({ score, code: `H${score}`, minimum, maximum, label }));

const TRACTABILITY_BANDS = [
  [0, 0, .01, "<1%"], [1, .01, .03, "1-3%"], [2, .03, .07, "3-7%"],
  [3, .07, .15, "7-15%"], [4, .15, .25, "15-25%"], [5, .25, .40, "25-40%"],
  [6, .40, .55, "40-55%"], [7, .55, .70, "55-70%"], [8, .70, .85, "70-85%"],
  [9, .85, .95, "85-95%"], [10, .95, 1, ">95%"],
].map(([score, low, high, label]) => ({ score, code: `T${score}`, low, high, label, high_inclusive: score === 10 }));

const TIERS = [
  { code: "T1", label: "Accessible/Audit", difficulty_label: "T1 Accessible/Audit", low: 0, high: 2.5 },
  { code: "T2", label: "Research Sprint", difficulty_label: "T2 Research Sprint", low: 2.5, high: 4.5 },
  { code: "T3", label: "Serious Project", difficulty_label: "T3 Serious Project", low: 4.5, high: 6.5 },
  { code: "T4", label: "Frontier Challenge", difficulty_label: "T4 Frontier Challenge", low: 6.5, high: 8.5 },
  { code: "T5", label: "Grand Challenge", difficulty_label: "T5 Grand Challenge", low: 8.5, high: 10.000001 },
];

const CATEGORY_INFO = {
  "Number Theory": [1, "number_theory", "number-theory", 5.7, 4.0],
  "Combinatorics": [2, "combinatorics", "combinatorics", 5.2, 3.7],
  "Graph Theory": [3, "graph_theory", "graph-theory", 5.0, 3.5],
  "Algebra": [4, "algebra", "algebra", 6.2, 4.8],
  "Algebraic Geometry": [5, "algebraic_geometry", "algebraic-geometry", 8.0, 7.2],
  "Geometry": [6, "geometry", "geometry", 5.9, 5.8],
  "Topology": [7, "topology", "topology", 7.1, 6.8],
  "Analysis": [8, "analysis", "analysis", 6.7, 6.7],
  "Partial Differential Equations": [9, "partial_differential_equations", "partial-differential-equations", 7.8, 7.8],
  "Set Theory": [10, "set_theory", "set-theory", 7.2, 5.8],
  "Dynamical Systems": [11, "dynamical_systems", "dynamical-systems", 6.9, 7.0],
  "Mathematical Physics": [16, "physics", "physics", 7.8, 8.0],
  "Group Theory": [17, "group_theory", "group-theory", 6.3, 4.8],
  "Logic": [18, "logic", "logic", 6.7, 4.8],
  "Computer Science": [15, "computer_science", "computer-science", 5.9, 4.2],
  "Probability": [19, "probability", "probability", 6.0, 6.0],
  "Miscellaneous": [20, "miscellaneous", "miscellaneous", 5.0, 5.5],
};

const CATEGORY_PATTERNS = [
  ["Algebraic Geometry", /algebraic geometry|scheme|variet|motivic|\betale\b|stack\b|algebraic cycle/i],
  ["Partial Differential Equations", /partial differential|differential equation|\bpde\b|navier.?stokes|euler equation|sobolev|elliptic equation|parabolic equation|hyperbolic equation/i],
  ["Number Theory", /number theory|diophant|prime\b|arithmetic geometry|l-function|zeta function|modular form/i],
  ["Graph Theory", /graph theory|hypergraph|digraph|vertex|edge.colou?r|network/i],
  ["Combinatorics", /combinator|ramsey|extremal|matroid|design theory|additive combinatorics/i],
  ["Group Theory", /group theory|finite group|group action|geometric group|representation theory/i],
  ["Topology", /topolog|homotop|homolog|cohomolog|knot theory|low.dimension/i],
  ["Dynamical Systems", /dynamical|ergodic|billiard|periodic orbit|chaos|hamiltonian/i],
  ["Probability", /probab|stochastic|random matrix|percolation|random walk/i],
  ["Set Theory", /set theory|forcing\b|cardinal|continuum hypothesis|descriptive set/i],
  ["Logic", /mathematical logic|model theory|proof theory|computability|recursion theory/i],
  ["Computer Science", /computer science|algorithm|complexity|machine learning|cryptograph|\bP\s*(?:=|vs)\s*NP\b/i],
  ["Mathematical Physics", /mathematical physics|quantum|gauge theor|yang.?mills|statistical mechanics|string theory|relativity/i],
  ["Analysis", /analysis|operator algebra|functional analysis|harmonic analysis|measure theory|banach|hilbert space/i],
  ["Geometry", /geometry|manifold|symplectic|contact structure|curvature|geodesic/i],
  ["Algebra", /algebra|ring theory|commutative ring|galois|module|polynomial ideal/i],
];

const advancedPatterns = [
  /algebraic cycle|scheme|motivic|étale|\betale\b|derived categor|stack\b|cohomolog|homotop|spectral sequence/i,
  /floer|gauge theor|seiberg|symplectic|contact (structure|manifold|topology)|teichm/i,
  /automorphic|langlands|l-function|elliptic curve|abelian variet|galois representation/i,
  /c\*[- ]?algebra|von neumann|operator algebra|banach|hilbert space/i,
  /navier.?stokes|yang.?mills|nonlinear pde|partial differential|regularity|sobolev/i,
  /infinite[- ]dimensional|measurable cardinal|forcing\b|large cardinal|model theory/i,
];
const continuousTacitPattern = /manifold|knot|embedding|isotop|symplectic|contact|curvature|geodesic|billiard|fluid|pde|dynamical|quantum|gauge|hamiltonian|measure space/i;
const computePattern = /algorithm|compute|enumerat|finite graph|finite group|matrix|polynomial|integer solution|ramsey number|coloring|tiling|packing|configuration|code\b|sat\b|linear programming|optimization|exact value/i;
const formalPattern = /for every|for all|there exists|does there exist|is it true|prove or disprove|conjecture|if .* then|such that|supremum|infimum/i;
const programmaticPattern = /develop (?:a |the |new )?.*(?:theory|framework|mathematics)|understand the (?:general|full|structure)|model and predict|build (?:a |the )?(?:theory|framework)|investigate (?:all|the general)|comprehensive (?:classification|theory)|research program|what can be said|to what extent/i;
const resistancePattern = /long[- ]?standing|notorious|famous|major unsolved|fundamental problem|remains open|open for (?:more than|over)|no known (?:method|approach)|unattackable|despite (?:extensive|many)|only special cases|best known|partial result/i;
const resolutionPattern = /\b(?:this (?:problem|conjecture|inequality) is not true|counterexample (?:was|is|has been)|has been (?:solved|settled|disproved)|was (?:solved|settled|disproved)|is now known|affirmative solution|negative solution|independent of zfc)\b/i;
const famousPattern = /p versus np|riemann hypothesis|yang.?mills|navier.?stokes|birch and swinnerton|hodge conjecture|collatz|twin prime|goldbach|abc conjecture|hadwiger conjecture|inverse galois|continuum hypothesis|smooth 4.*poincar|jacobian conjecture|odd perfect|erd.s.*distinct subset/i;
const independencePattern = /continuum hypothesis|whitehead problem/i;
const objectPatterns = [
  [/l-function|riemann zeta|automorphic/i, "L-functions"], [/elliptic curve|abelian variet/i, "elliptic curves and abelian varieties"],
  [/algebraic cycle|projective variet|scheme\b/i, "algebraic varieties and cycles"], [/manifold|cobord|diffeomorph/i, "manifolds"],
  [/knot|link invariant|jones polynomial/i, "knots and link invariants"], [/finite group|group action|automorphism of .*group|subgroup/i, "groups and group actions"],
  [/hypergraph/i, "hypergraphs"], [/graph|tree\b|vertex|edge coloring/i, "graphs"],
  [/prime|divisor|diophant|integer solution|perfect number/i, "primes and Diophantine structure"], [/partial differential|navier|euler equation|regularity/i, "nonlinear PDE"],
  [/dynamical|hamiltonian|periodic orbit|billiard/i, "dynamical systems"], [/operator algebra|c\*[- ]?algebra|von neumann/i, "operator algebras"],
  [/probability|random|stochastic/i, "probabilistic structure"], [/set theory|cardinal|forcing/i, "set-theoretic foundations"],
  [/algorithm|complexity|polynomial time|computab/i, "algorithms and complexity"], [/matroid/i, "matroids"],
  [/matrix|determinant|eigenvalue|spectral/i, "matrices and spectral data"], [/polynomial (?:equation|system|ideal|ring)|algebraic equation/i, "polynomial systems"],
  [/ramsey/i, "Ramsey-type structure"], [/homotop|homolog|cohomolog/i, "homotopy and (co)homology"],
  [/symplectic|contact/i, "symplectic/contact geometry"],
];

const TAG_CODES = {
  "AI-assisted progress candidate": "ai_assisted_progress_candidate", "Frontier benchmark": "frontier_benchmark",
  "Neglected gem": "neglected_gem", "Formalization target": "formalization_target",
  "Grand challenge": "grand_challenge", "Needs curation": "needs_curation",
};
const AI_FIT_CODES = { "AI-favored": "ai_favored", "AI-neutral/mixed": "ai_neutral_mixed", "AI-hostile": "ai_hostile" };

function fail(message) { throw new Error(message); }
function clamp(x, low, high) { return Math.max(low, Math.min(high, x)); }
function round1(x) { return Math.round((x + Number.EPSILON) * 10) / 10; }
function roundHalf(x) { return Math.round(x * 2) / 2; }
function normalizeKey(value) { return String(value ?? "").toLowerCase().normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, " ").trim(); }
function clean(value, maxLength = 32_700) { const text = String(value ?? "").replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, " ").trim(); return text.length <= maxLength ? text : text.slice(0, maxLength - 24) + " [truncated in workbook]"; }
function countMatches(text, re) { const rx = new RegExp(re.source, re.flags.includes("g") ? re.flags : re.flags + "g"); return (text.match(rx) || []).length; }
function routeCode(label) { return normalizeKey(label).replace(/ /g, "_"); }
function tierFor(score) { return TIERS.find(t => score < t.high) ?? TIERS.at(-1); }
function confidence(level, status = "assigned") { return status === "assigned" ? { level, code: `C${level}`, status } : { level: null, code: null, status }; }
function width(level) { return level >= 3 ? .8 : level === 2 ? 1.2 : level === 1 ? 1.8 : 2.5; }
function sha256Buffer(buffer) { return crypto.createHash("sha256").update(buffer).digest("hex").toUpperCase(); }
async function sha256File(file) { const h = crypto.createHash("sha256"); for await (const chunk of fs.createReadStream(file)) h.update(chunk); return h.digest("hex").toUpperCase(); }
async function writeJsonAtomic(file, value) {
  const absolute = path.resolve(file), temporary = path.join(path.dirname(absolute), `.${path.basename(absolute)}.${process.pid}.${crypto.randomUUID()}.tmp`);
  await fsp.mkdir(path.dirname(absolute), { recursive: true });
  try {
    await fsp.writeFile(temporary, JSON.stringify(value, null, 2) + "\n", "utf8");
    await fsp.rename(temporary, absolute);
  } finally {
    await fsp.unlink(temporary).catch(() => {});
  }
}
function stableRecordDigest(records) { const h = crypto.createHash("sha256"); for (const record of records) h.update(JSON.stringify(record)); return h.digest("hex").toUpperCase(); }
function asArray(value) { return Array.isArray(value) ? value : value == null ? [] : [value]; }
function scalarLabel(value) {
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (!value || typeof value !== "object") return "";
  for (const key of ["display_name", "name", "username", "title", "label", "slug", "id"]) {
    const candidate = value[key];
    if ((typeof candidate === "string" || typeof candidate === "number") && String(candidate).trim()) return String(candidate);
  }
  return "";
}
function firstString(object, keys) { for (const key of keys) if (typeof object?.[key] === "string" && object[key].trim()) return object[key]; return ""; }
function firstInteger(object, keys) {
  for (const key of keys) {
    const value = object?.[key];
    if (value == null || typeof value === "boolean" || (typeof value === "string" && !value.trim())) continue;
    const n = Number(value);
    if (Number.isInteger(n)) return n;
  }
  return null;
}
function unwrapEntry(entry, lineNumber = null) {
  const isEnvelope = [MATHDB_DETAIL_SCHEMA, MATHDB_SUMMARY_SCHEMA].includes(entry?.schema) || (entry?.snapshot && entry?.problem && typeof entry.problem === "object" && !Array.isArray(entry.problem));
  if (isEnvelope) {
    if (!entry.problem || Array.isArray(entry.problem) || typeof entry.problem !== "object") fail(`${lineNumber ? `line ${lineNumber}: ` : ""}MathDB envelope.problem must be an object`);
    if (![MATHDB_DETAIL_SCHEMA, MATHDB_SUMMARY_SCHEMA].includes(entry.schema)) fail(`${lineNumber ? `line ${lineNumber}: ` : ""}unsupported MathDB envelope schema ${JSON.stringify(entry.schema)}`);
    return { raw: entry.problem, snapshot: entry.snapshot && typeof entry.snapshot === "object" && !Array.isArray(entry.snapshot) ? entry.snapshot : null, envelopeSchema: entry.schema ?? null };
  }
  return { raw: entry, snapshot: null, envelopeSchema: null };
}

function parseArgs(argv) {
  const args = {};
  for (let i = 2; i < argv.length; i++) {
    const item = argv[i];
    if (!item.startsWith("--")) fail(`Unexpected positional argument: ${item}`);
    const key = item.slice(2);
    if (["help", "allow-base-mismatch"].includes(key)) args[key] = true;
    else if (i + 1 >= argv.length || argv[i + 1].startsWith("--")) fail(`Missing value for --${key}`);
    else args[key] = argv[++i];
  }
  return args;
}

function usage() {
  return `Usage: node --max-old-space-size=16384 scripts/build_mathdb_append_v1_7.mjs\n\
  --base <v1.6.json.gz> --mathdb <catalog.jsonl[.gz]> --output <v1.7.json.gz>\n\
  --validation <validation.json> [--manifest <manifest.json>] [--date YYYY-MM-DD]\n\
  [--id-offset 40000000] [--limit N] [--allow-base-mismatch]`;
}

async function loadJsonMaybeGzip(file) {
  const bytes = await fsp.readFile(file);
  const data = file.toLowerCase().endsWith(".gz") ? zlib.gunzipSync(bytes) : bytes;
  return JSON.parse(data.toString("utf8"));
}

async function* readJsonLines(file, limit = Infinity) {
  const source = fs.createReadStream(file);
  const stream = file.toLowerCase().endsWith(".gz") ? source.pipe(zlib.createGunzip()) : source;
  const lines = readline.createInterface({ input: stream, crlfDelay: Infinity });
  let lineNumber = 0, emitted = 0;
  for await (const line of lines) {
    lineNumber++;
    if (!line.trim()) continue;
    let value;
    try { value = JSON.parse(line); } catch (error) { fail(`${file}:${lineNumber}: invalid JSON: ${error.message}`); }
    if (!value || Array.isArray(value) || typeof value !== "object") fail(`${file}:${lineNumber}: each line must be one JSON object`);
    yield { value, lineNumber };
    emitted++;
    if (emitted >= limit) break;
  }
}

function inferCategory(raw) {
  const labels = [...asArray(raw.tags), ...asArray(raw.collections)].map(scalarLabel).filter(Boolean);
  const haystack = `${labels.join(" ")} ${raw.title ?? ""} ${raw.statement ?? raw.excerpt ?? ""}`.replace(/[-_]+/g, " ");
  for (const [label, pattern] of CATEGORY_PATTERNS) if (pattern.test(haystack)) return label;
  return "Miscellaneous";
}

function mapCatalogStatus(raw) {
  const status = normalizeKey(raw.status);
  if (/^(solved|resolved|closed|refuted|disproved|false)$/.test(status)) return "solved";
  if (/partial|claimed solved|claim solved|solution claimed|progress/.test(status)) return "partially_solved";
  return "open";
}

function nativeStatusNeedsReview(raw) {
  const status = normalizeKey(raw.status);
  return !["open", "unsolved", "claimed progress", "partial progress", "partially solved"].includes(status);
}

function nativeContext(raw) {
  const chunks = [];
  const references = firstString(raw, ["references", "reference", "bibliography"]);
  if (references) chunks.push(references);
  if (raw.progress && typeof raw.progress === "string") chunks.push(`Progress: ${raw.progress}`);
  else if (raw.progress && typeof raw.progress === "object") {
    const progressText = [raw.progress.summary, raw.progress.tldr, raw.progress.verification_note, raw.progress.verification_outcome].filter(value => typeof value === "string" && value.trim()).join("\n\n");
    if (progressText) chunks.push(`Progress:\n\n${progressText}`);
  }
  const sourceName = firstString(raw, ["source_name"]);
  const sourceUrl = firstString(raw, ["source_url"]);
  if (sourceName) chunks.push(`Source: ${sourceName}`);
  if (sourceUrl) chunks.push(`Source URL: ${sourceUrl}`);
  const yearUrl = firstString(raw, ["first_stated_year_source_url", "documented_by_source_url"]);
  if (yearUrl) chunks.push(`Year evidence: ${yearUrl}`);
  const nativeStatus = firstString(raw, ["status"]);
  if (nativeStatus && nativeStatusNeedsReview(raw)) chunks.push(`Status evidence: NEEDS_REVIEW (${nativeStatus})`);
  return chunks.join("\n\n");
}

function adaptMathDb(raw, idOffset, snapshot = null, envelopeSchema = null) {
  const number = firstInteger(raw, ["number", "problem_number", "post_number"]);
  if (number === null || number < 0) fail("MathDB record has no nonnegative integer number");
  const title = firstString(raw, ["title", "name"]);
  const fullStatement = firstString(raw, ["statement", "problem", "text", "content", "question"]);
  const excerpt = firstString(raw, ["excerpt"]);
  const statement = fullStatement || excerpt;
  if (!statement) fail(`MathDB problem ${number} has no statement`);
  const excerptOnly = !fullStatement && Boolean(excerpt);
  if (envelopeSchema === MATHDB_SUMMARY_SCHEMA && !excerptOnly) fail(`MathDB summary ${number} must provide its assessed text in problem.excerpt, not a full-statement field`);
  if (excerptOnly && envelopeSchema === MATHDB_DETAIL_SCHEMA) fail(`MathDB detail ${number} lacks its required full statement`);
  if (envelopeSchema === MATHDB_SUMMARY_SCHEMA && snapshot?.text_basis !== MATHDB_SUMMARY_TEXT_MODE) fail(`MathDB summary ${number} lacks snapshot.text_basis=${MATHDB_SUMMARY_TEXT_MODE}`);
  if (envelopeSchema === MATHDB_SUMMARY_SCHEMA && snapshot?.statement_completeness !== MATHDB_SUMMARY_COMPLETENESS) fail(`MathDB summary ${number} lacks snapshot.statement_completeness=${MATHDB_SUMMARY_COMPLETENESS}`);
  if (envelopeSchema === MATHDB_SUMMARY_SCHEMA && snapshot?.inventory_number !== number) fail(`MathDB summary ${number} has mismatched or missing snapshot.inventory_number`);
  const category = inferCategory(raw);
  const [categoryId, categoryName, categorySlug] = CATEGORY_INFO[category];
  const status = mapCatalogStatus(raw);
  const firstYear = firstInteger(raw, ["first_stated_year", "documented_by_year"]);
  const background = nativeContext(raw);
  const sourceUrl = firstString(raw, ["source_url", "first_stated_year_source_url", "documented_by_source_url"]);
  return {
    id: idOffset + number,
    problem_number: `MATHDB-${number}`,
    title: title || `MathDB Problem ${number}`,
    statement,
    background,
    status,
    proposed_by: scalarLabel(raw.author) || null,
    proposed_year: firstYear,
    category_id: categoryId,
    set_id: MATHDB_SET_ID,
    source_url: sourceUrl || snapshot?.inventory_url || `https://mathdb.com/p/${number}`,
    view_count: raw.view_count == null ? null : Number.isFinite(Number(raw.view_count)) ? Number(raw.view_count) : null,
    favorite_count: raw.vote_count == null ? null : Number.isFinite(Number(raw.vote_count)) ? Number(raw.vote_count) : null,
    created_at: raw.created_at ?? null,
    updated_at: raw.edited_at ?? raw.moderator_edited_at ?? null,
    published: true,
    category: { id: categoryId, name: categoryName, display_name: category, slug: categorySlug },
    difficulty: null,
    set: { id: MATHDB_SET_ID, name: "mathdb", display_name: "MathDB", slug: "mathdb" },
    _mathdb_number: number,
    _native_status: raw.status ?? null,
    _native_post_type: raw.post_type ?? null,
    _status_needs_review: nativeStatusNeedsReview(raw),
    _excerpt_only: excerptOnly,
    _statement_completeness: excerptOnly ? (snapshot?.statement_completeness ?? MATHDB_SUMMARY_COMPLETENESS) : "full_source_statement",
    _snapshot: snapshot,
    _envelope_schema: envelopeSchema,
    _missing_context: !background,
    _raw: raw,
  };
}

function detectAnswerType(text) {
  if (programmaticPattern.test(text)) return "programmatic";
  if (/exact value|determine (?:the )?(?:exact|maximum|minimum)|what is (?:the )?(?:largest|smallest|minimum|maximum|exact)|find all (?:integer|rational|real)? ?solutions/i.test(text)) return "exact_value";
  if (/upper bound|lower bound|best (?:possible )?constant|asymptotic|growth rate|order of magnitude|largest|smallest|maximal|minimal|supremum|infimum/i.test(text)) return "bound";
  if (/algorithm|polynomial[- ]time|computational complexity|decidable|computable|efficient procedure/i.test(text)) return "algorithm";
  if (/classify|characterize|determine all|which (?:groups|spaces|graphs|manifolds|integers|varieties)/i.test(text)) return "classification";
  if (/\bconstruct(?:ion)?\b|\bexhibit\b|find an example|give an example/i.test(text)) return "construction";
  if (/does there exist|is there (?:a|an|any)|are there infinitely many|existence of|prove .* exists/i.test(text)) return "existence";
  if (/experiment|measurement|physical observation|numerical evidence/i.test(text)) return "experimental";
  return "proof_disproof";
}
function detectScope(statement, type) {
  if (type === "programmatic") return "programmatic";
  if ((countMatches(statement, /\?/g) >= 2 && !/more formally|equivalently|in other words/i.test(statement)) || /\(a\)[\s\S]{0,300}\(b\)|\bparts? \(a\).+\(b\)/i.test(statement)) return "compound";
  if (/for all|for every|for each|as \$?n|in every dimension|for arbitrary|classify all|determine all/i.test(statement)) return "family";
  return "atomic";
}
function countReferences(text) {
  const labels = countMatches(text, /\[[A-Za-z]{1,8}\d{2,4}[a-z]?\]/g);
  const dois = countMatches(text, /doi:|doi\.org\//gi);
  const arxiv = countMatches(text, /arxiv:/gi);
  const bullets = countMatches(text, /^\s*-\s*\[[^\]]+\]/gm);
  return Math.min(99, Math.max(labels, bullets) + dois + arxiv);
}
function extractObjects(text, fallback) { const found = []; for (const [re, label] of objectPatterns) if (re.test(text) && !found.includes(label) && found.push(label) >= 2) break; return found.length ? found.join("; ") : fallback.toLowerCase(); }
function estimateYear(p, background, assessmentYear) {
  if (Number.isInteger(p.proposed_year) && p.proposed_year >= 1800 && p.proposed_year <= assessmentYear) return { year: p.proposed_year, basis: "explicit proposed_year" };
  const patterns = [/(?:posed|proposed|dated|dates it to|first mentioned|introduced|posted)\D{0,35}((?:18|19|20)\d{2})/i, /Source list:[^\n\r]*\(((?:18|19|20)\d{2})\)/i, /Posted:\s*[^\n\r]*?((?:19|20)\d{2})/i];
  for (const pattern of patterns) { const match = background.match(pattern); if (match) return { year: Number(match[1]), basis: "inferred from source/background wording" }; }
  return { year: null, basis: "unknown" };
}

function assessProblem(p, duplicateNumbers, duplicateTitles, duplicateStatements, assessmentYear) {
  const title = clean(p.title, 2_000), statement = clean(p.statement), background = clean(p.background);
  const excerptOnly = p._excerpt_only === true;
  const combined = `${title}\n${statement}\n${background}`;
  const category = p.category.display_name;
  const source = "MathDB";
  const legacyLevel = 0;
  const answerType = detectAnswerType(`${title}\n${statement}`);
  const scope = detectScope(statement, answerType);
  const refCount = countReferences(background);
  const yearInfo = estimateYear(p, background, assessmentYear);
  const age = yearInfo.year ? Math.max(0, assessmentYear - yearInfo.year) : null;
  const sourcePrior = 5.8;
  const needsStatusReview = p._status_needs_review;
  const needsRightsReview = false;
  const malformed = /\}\s*,\s*\{|"difficulty"\s*:\s*"L[1-5]"/i.test(`${p.title ?? ""}\n${p.statement ?? ""}\n${p.background ?? ""}`);
  const invalidControl = /[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(`${p.title ?? ""}\n${p.statement ?? ""}\n${p.background ?? ""}`);
  const truncatedTitle = /\.\.\.$|…$/u.test(String(p.title).trim());
  const truncatedStatement = /\.\.\.$|…$/u.test(String(p.statement).trim());
  const sourceLabelPlaceholder = /\[source label:[^\]]+\]/i.test(statement);
  const contextDependent = /as (?:defined|described|stated|shown|above|below)|previous (?:section|theorem|definition)|following (?:figure|table|diagram)/i.test(statement);
  const figureTableResidue = /\b(?:figure|table)\s+\d+|\[image\]|see (?:the )?(?:figure|table)/i.test(statement);
  const suspectedResolution = resolutionPattern.test(background) || independencePattern.test(title) || p.status === "solved";
  const missingBackground = p._missing_context;
  const duplicateNumber = (duplicateNumbers.get(normalizeKey(p.problem_number)) ?? []).length > 1;
  const duplicateTitle = (duplicateTitles.get(normalizeKey(title)) ?? []).length > 1;
  const duplicateStatement = normalizeKey(statement).length > 20 && (duplicateStatements.get(normalizeKey(statement)) ?? []).length > 1;

  let ambiguity = 1.5 + (scope === "compound" ? 2 : 0) + (scope === "programmatic" ? 5 : 0);
  if (/what can be said|under what conditions|to what extent|appropriate notion|develop|investigate/i.test(statement)) ambiguity += 1.5;
  if (statement.length < 80) ambiguity += .7;
  if (formalPattern.test(statement)) ambiguity -= .7;
  if (needsStatusReview || malformed) ambiguity += .8;
  ambiguity = round1(clamp(ambiguity, 0, 10));

  let assessmentGate = "source_claimed_open";
  if (p.status === "solved") assessmentGate = "solved";
  else if (suspectedResolution || needsStatusReview) assessmentGate = "status_unclear";
  else if (scope === "programmatic" && ambiguity >= 8) assessmentGate = "ill_posed";
  const statusConfidence = excerptOnly ? 0 : assessmentGate === "solved" ? 2 : 1;

  const breadthPatterns = [/number theory|prime|diophant|integer/i, /combinator|graph|ramsey|matroid/i, /algebra|group|ring|galois|representation/i, /geometry|manifold|topolog|knot|homotop/i, /analysis|pde|measure|operator|functional/i, /probab|random|stochastic/i, /algorithm|complexity|comput/i, /physics|quantum|statistical mechanics/i, /logic|set theory|cardinal|model theory/i];
  const breadthHits = breadthPatterns.filter(re => re.test(combined)).length;
  const breadth = clamp(breadthHits <= 1 ? 1 : breadthHits, 1, 5);
  let prereq = CATEGORY_INFO[category][3] + advancedPatterns.filter(re => re.test(combined)).length * .55 + (breadth - 1) * .35;
  if (/elementary|undergraduate|high school|simple to state/i.test(background) && !advancedPatterns.some(re => re.test(combined))) prereq -= .6;
  prereq = round1(clamp(prereq, 0, 10));
  let formalization = CATEGORY_INFO[category][4] + (scope === "programmatic" ? 2.3 : 0) + (scope === "compound" ? .7 : 0) + (ambiguity >= 6 ? 1 : 0);
  if (computePattern.test(statement) && formalPattern.test(statement)) formalization -= .8;
  if (/finite (?:graph|group|set)|integer|polynomial|matrix/i.test(statement)) formalization -= .5;
  if (continuousTacitPattern.test(statement)) formalization += .5;
  formalization = round1(clamp(formalization, 0, 10));
  let toolLeverage = 4.5 + (answerType === "exact_value" ? 2 : 0) + (["algorithm", "construction"].includes(answerType) ? 1.5 : 0) + (computePattern.test(statement) ? 1.5 : 0) + (/finite|bounded|smallest|largest|matrix|polynomial|integer/i.test(statement) ? .8 : 0) - (scope === "programmatic" ? 3 : 0) - (continuousTacitPattern.test(statement) ? 1.3 : 0) - (/asymptotic|all dimensions|infinite[- ]dimensional|for arbitrary/i.test(statement) ? .7 : 0);
  toolLeverage = round1(clamp(toolLeverage, 0, 10));

  let vTrue, vFalse;
  if (answerType === "existence") { vTrue = 1.4 + .24 * formalization + .10 * prereq - .08 * toolLeverage; vFalse = 2.8 + .38 * formalization + .16 * prereq; }
  else if (answerType === "construction") { vTrue = 1.6 + .22 * formalization + .10 * prereq; vFalse = 3.3 + .34 * formalization + .14 * prereq; }
  else if (answerType === "exact_value") vTrue = vFalse = 2 + .22 * formalization + .12 * prereq - .08 * toolLeverage;
  else if (answerType === "bound") vTrue = vFalse = 2.8 + .28 * formalization + .14 * prereq;
  else if (answerType === "algorithm") { vTrue = 2.8 + .26 * formalization + .16 * prereq; vFalse = 3.2 + .22 * formalization + .16 * prereq; }
  else if (answerType === "classification") vTrue = vFalse = 3.5 + .30 * formalization + .16 * prereq;
  else if (answerType === "programmatic") vTrue = vFalse = 9;
  else if (answerType === "experimental") vTrue = vFalse = 7.5;
  else { vTrue = 3 + .32 * formalization + .18 * prereq; vFalse = 2.6 + .28 * formalization + .15 * prereq; }
  vTrue = round1(clamp(vTrue, 0, 10)); vFalse = round1(clamp(vFalse, 0, 10));

  const baseFactor = { exact_value: 1.5, bound: 2, algorithm: 2.1, construction: 1.9, existence: 2.3, classification: 2.6, proof_disproof: 2.4, experimental: 2.7, programmatic: 3.4 }[answerType] ?? 2.4;
  let conceptualGap = baseFactor + (scope === "compound" ? .25 : scope === "family" ? .15 : 0) + (resistancePattern.test(background) ? .45 : 0) + (famousPattern.test(title) ? .55 : 0) - (answerType === "exact_value" && toolLeverage >= 7 ? .35 : 0) - (p.status === "partially_solved" ? .15 : 0);
  conceptualGap = roundHalf(clamp(conceptualGap, 0, 4));
  let routeGap = ({ exact_value: 1.7, bound: 2.2, algorithm: 2.2, construction: 2, existence: 2.3, classification: 2.6, proof_disproof: 2.4, experimental: 2.8, programmatic: 3.5 }[answerType] ?? 2.4);
  if (/no known (?:method|approach)|open even|only special cases|best known/i.test(background)) routeGap += .65;
  if (/reduces to|equivalent to|conditional on|it suffices|known method/i.test(background)) routeGap -= .5;
  if (p.status === "partially_solved") routeGap -= .25;
  if (scope === "compound") routeGap += .25;
  routeGap = roundHalf(clamp(routeGap, 0, 4));
  let technicalDepth = prereq * .4 + (refCount >= 10 ? .35 : refCount >= 4 ? .15 : 0) + (scope === "compound" ? .25 : 0) - (statement.length < 120 && prereq < 6 ? .2 : 0);
  technicalDepth = roundHalf(clamp(technicalDepth, 0, 4));
  let knownBarrier = 1 + clamp((sourcePrior - 5) * .15, -.4, .75) + (age >= 100 ? 1 : age >= 50 ? .7 : age >= 20 ? .4 : 0) + (resistancePattern.test(background) ? .8 : 0) + (famousPattern.test(title) ? .8 : 0) + (p.status === "partially_solved" ? .3 : 0) + (refCount >= 15 ? .5 : refCount >= 5 ? .25 : 0) - (age !== null && age <= 2 && refCount <= 1 ? .5 : 0) - (needsStatusReview && refCount === 0 ? .15 : 0);
  knownBarrier = roundHalf(clamp(knownBarrier, 0, 4));
  let searchScale = ({ exact_value: 1.6, bound: 2.4, algorithm: 2.5, construction: 2.3, existence: 2.6, classification: 3, proof_disproof: 2.8, experimental: 3.3, programmatic: 3.8 }[answerType] ?? 2.8) + (scope === "family" ? .25 : 0) + (scope === "compound" ? .35 : 0) + (5 - toolLeverage) * .08;
  searchScale = roundHalf(clamp(searchScale, 0, 4));
  let d = round1(2.5 * (.30 * conceptualGap + .20 * routeGap + .20 * technicalDepth + .20 * knownBarrier + .10 * searchScale));
  if (p.status === "solved") d = Math.min(d, 8.5);

  let difficultyConfidence = 2;
  if (missingBackground) difficultyConfidence = 0;
  else if (needsStatusReview || malformed || background.length < 180) difficultyConfidence = 1;
  if (scope === "programmatic") difficultyConfidence = Math.min(difficultyConfidence, 1);
  if (excerptOnly) difficultyConfidence = 0;
  const dWidth = width(difficultyConfidence) + (excerptOnly ? .5 : 0) + (scope === "compound" ? .4 : 0);
  const dLow = round1(clamp(d - dWidth, 0, 10)), dHigh = round1(clamp(d + dWidth, 0, 10));
  let literatureLoad = 2 + Math.min(4, refCount / 4) + (background.length > 2500 ? 1 : 0) + (famousPattern.test(title) ? 4 : 0);
  literatureLoad = round1(clamp(literatureLoad, 0, 10));
  const meanVerificationExact = (vTrue + vFalse) / 2, meanVerification = round1(meanVerificationExact);
  const aiFormalFit = formalization <= 4 && ambiguity <= 3 ? -1 : (formalization >= 7 || ambiguity >= 7 ? 1 : 0);
  const aiFeedbackFit = meanVerification <= 3 ? -1 : (meanVerification >= 6 ? 1 : 0);
  const aiToolFit = toolLeverage >= 7 ? -1 : (toolLeverage <= 3 ? 1 : 0);
  const aiContextFit = literatureLoad <= 3 ? -1 : (literatureLoad >= 7 ? 1 : 0);
  const aiHorizonFit = d <= 4 ? -1 : (d >= 7 || scope === "programmatic" ? 1 : 0);
  const aiTacitFit = famousPattern.test(title) || d >= 8.5 ? 1 : (continuousTacitPattern.test(combined) || scope === "programmatic" ? 1 : (computePattern.test(statement) && prereq <= 6 ? -1 : 0));
  const aiRelative = round1(.5 * (aiFormalFit + aiFeedbackFit + aiToolFit + aiContextFit + aiHorizonFit + aiTacitFit));
  const aiDifficulty = round1(clamp(d + aiRelative, 0, 10)), aiConfidence = excerptOnly ? 0 : Math.min(2, difficultyConfidence), aiWidth = width(aiConfidence) + .5 + (excerptOnly ? .5 : 0);
  const aiLow = round1(clamp(aiDifficulty - aiWidth, 0, 10)), aiHigh = round1(clamp(aiDifficulty + aiWidth, 0, 10));
  const aiFitLabel = aiRelative <= -1 ? "AI-favored" : aiRelative >= 1 ? "AI-hostile" : "AI-neutral/mixed";
  let exposure = famousPattern.test(title) ? 5 : 2;
  if (refCount >= 15) exposure += 1; else if (refCount >= 5) exposure += .5;
  if (/workshop|survey|prize|widely known/i.test(background)) exposure += .5;
  exposure = clamp(Math.round(exposure), 0, 5);
  let humanEffort = refCount === 0 ? 1 : refCount <= 2 ? 2 : refCount <= 5 ? 3 : refCount <= 10 ? 4 : refCount <= 20 ? 5 : 6;
  humanEffort += age >= 100 ? 2 : age >= 50 ? 1.5 : age >= 20 ? 1 : 0;
  if (resistancePattern.test(background)) humanEffort += .5;
  if (p.status === "partially_solved") humanEffort += .5;
  if (famousPattern.test(title) || /\$1,?000,?000|million dollar prize/i.test(background)) humanEffort = Math.max(humanEffort, 9);
  humanEffort = clamp(Math.round(humanEffort), 0, 10);
  const humanConfidence = excerptOnly || missingBackground || (!yearInfo.year && refCount === 0) ? 0 : (yearInfo.basis === "explicit proposed_year" && refCount >= 5 ? 2 : 1);
  let tractability = Math.round(clamp(9 - .8 * aiDifficulty + .20 * toolLeverage - .15 * meanVerificationExact + (humanEffort <= 4 ? .6 : 0) + (p.status === "partially_solved" ? .5 : 0) - (["status_unclear", "ill_posed", "solved"].includes(assessmentGate) ? 1 : 0), 0, 10));
  if (["solved", "ill_posed"].includes(assessmentGate)) tractability = null;
  let verificationConfidence = ambiguity >= 7 ? 1 : 2, formalizationConfidence = ambiguity >= 7 ? 1 : 2, prerequisiteConfidence = advancedPatterns.some(re => re.test(combined)) || breadth >= 2 ? 2 : 1;
  if (missingBackground) { verificationConfidence = Math.min(1, verificationConfidence); formalizationConfidence = Math.min(1, formalizationConfidence); prerequisiteConfidence = Math.min(1, prerequisiteConfidence); }
  if (excerptOnly) { verificationConfidence = 0; formalizationConfidence = 0; prerequisiteConfidence = 0; }

  const qualityFlags = [];
  if (excerptOnly) qualityFlags.push(SOURCE_EXCERPT_FLAG);
  if (missingBackground) qualityFlags.push("missing_background");
  if (needsStatusReview) qualityFlags.push("status_NEEDS_REVIEW");
  if (suspectedResolution) qualityFlags.push("possible_stale_or_resolved");
  if (malformed) qualityFlags.push("malformed_import_fragment");
  if (invalidControl) qualityFlags.push("invalid_control_character");
  if (truncatedTitle) qualityFlags.push("truncated_title");
  if (truncatedStatement) qualityFlags.push("truncated_statement");
  if (sourceLabelPlaceholder) qualityFlags.push("source_label_placeholder");
  if (contextDependent) qualityFlags.push("context_dependent_statement");
  if (figureTableResidue) qualityFlags.push("figure_or_table_residue");
  if (duplicateNumber) qualityFlags.push("duplicate_problem_number");
  if (duplicateTitle) qualityFlags.push("title_collision");
  if (duplicateStatement) qualityFlags.push("duplicate_statement");
  if (scope === "compound") qualityFlags.push("compound_scope");
  if (scope === "programmatic") qualityFlags.push("programmatic_scope");
  if (p.status === "solved") qualityFlags.push("catalog_solved_record");
  if (yearInfo.basis !== "explicit proposed_year" && yearInfo.year) qualityFlags.push("proposal_year_semantics_uncertain");

  let suggestedRoute = "Expert-guided proof search";
  if (["solved", "status_unclear"].includes(assessmentGate)) suggestedRoute = "Status and literature audit";
  else if (assessmentGate === "ill_posed" || scope === "programmatic") suggestedRoute = "Scope decomposition and success-criterion rewrite";
  else if (answerType === "exact_value" && toolLeverage >= 6) suggestedRoute = "Exact computation plus proof certificate";
  else if (answerType === "bound" && toolLeverage >= 6) suggestedRoute = "Computational extremal search plus bound proof";
  else if (answerType === "algorithm") suggestedRoute = "Algorithm design plus complexity verification";
  else if (answerType === "construction") suggestedRoute = "Search/optimization plus witness verification";
  else if (answerType === "existence" && vTrue <= 3.5) suggestedRoute = "Witness search plus independent checker";
  else if (formalization <= 3.5 && meanVerification <= 3.5) suggestedRoute = "Formalization-first theorem proving";
  else if (literatureLoad >= 7) suggestedRoute = "Literature map plus bottleneck extraction";
  else if (toolLeverage >= 6) suggestedRoute = "Finite experiments then lemma mining";
  const tags = [], openForOpportunity = ["verified_open", "source_claimed_open"].includes(assessmentGate);
  const criticalCuration = needsStatusReview || needsRightsReview || malformed || missingBackground || duplicateStatement || scope === "programmatic" || excerptOnly;
  const eligible = openForOpportunity && difficultyConfidence >= 2 && !criticalCuration;
  if (eligible && d >= 2.5 && d < 6 && aiRelative <= -.5 && tractability >= 7 && meanVerification <= 4) tags.push("AI-assisted progress candidate");
  if (eligible && d >= 6 && d <= 8.4 && meanVerification <= 5.5 && formalization <= 6.5) tags.push("Frontier benchmark");
  if (eligible && exposure <= 2 && humanEffort <= 4 && d >= 3 && d <= 7 && tractability >= 5) tags.push("Neglected gem");
  if (eligible && formalization <= 3.5 && meanVerification <= 4) tags.push("Formalization target");
  if (eligible && d >= 8.5) tags.push("Grand challenge");
  if (["status_unclear", "ill_posed"].includes(assessmentGate) || scope === "programmatic" || malformed || missingBackground || duplicateStatement || excerptOnly) tags.push("Needs curation");
  let reviewPriority = "P3 routine audit";
  if (["status_unclear", "ill_posed"].includes(assessmentGate) || malformed || duplicateStatement || (missingBackground && !excerptOnly)) reviewPriority = "P0 immediate curation";
  else if (excerptOnly) reviewPriority = "P2 calibration review";
  else if (d >= 8 || exposure >= 4) reviewPriority = "P1 expert review";
  else if (difficultyConfidence <= 1 || scope === "compound") reviewPriority = "P2 calibration review";

  const objects = extractObjects(combined, category), hBand = HOUR_BANDS[humanEffort], pBand = tractability === null ? null : TRACTABILITY_BANDS[tractability];
  const completenessCaveat = excerptOnly ? " Input limitation: MathDB supplied only its public-list excerpt, not the complete problem statement; this is a provisional C0 cue-based estimate that must be recalibrated from the full statement." : "";
  const barrierPhrase = knownBarrier >= 3.5 ? "documented longevity or canonical resistance raises the barrier prior" : knownBarrier <= 1.5 ? "the record shows little documented resistance" : "the available record shows a nontrivial but incompletely documented barrier";
  const routePhrase = routeGap >= 3 ? "no narrow route is visible in the supplied context" : routeGap <= 1.5 ? "the target appears to reduce to a bounded local step" : "partial structure exists but the decisive step remains open";
  const difficultyRationale = `D${d.toFixed(1)} [${dLow.toFixed(1)}-${dHigh.toFixed(1)}]/C${difficultyConfidence} - This ${scope} ${answerType.replaceAll("_", " ")} target concerns ${objects}; ${barrierPhrase}, and ${routePhrase}.${completenessCaveat}`;
  const factorRationale = `CG${conceptualGap.toFixed(1)}/RG${routeGap.toFixed(1)}/TD${technicalDepth.toFixed(1)}/KB${knownBarrier.toFixed(1)}/SS${searchScale.toFixed(1)} - Statement type and scope set CG/SS; route cues and partial status set RG; prerequisites set TD; documented age, resistance, and citation evidence set KB. Collection metadata affects KB only.${completenessCaveat}`;
  const aiAdvantages = [], aiObstacles = [];
  if (aiFormalFit < 0) aiAdvantages.push("a crisp symbolic target"); else if (aiFormalFit > 0) aiObstacles.push("high formalization ambiguity");
  if (aiFeedbackFit < 0) aiAdvantages.push("cheap exact feedback"); else if (aiFeedbackFit > 0) aiObstacles.push("expensive claim verification");
  if (aiToolFit < 0) aiAdvantages.push("strong code/CAS/search leverage"); else if (aiToolFit > 0) aiObstacles.push("weak machine feedback");
  if (aiContextFit > 0) aiObstacles.push("a large or fragmented literature load"); if (aiHorizonFit > 0) aiObstacles.push("long-horizon global reasoning"); if (aiTacitFit > 0) aiObstacles.push("tacit geometric or cross-domain insight");
  const aiRationale = `AI${aiRelative >= 0 ? "+" : ""}${aiRelative.toFixed(1)} -> ${aiDifficulty.toFixed(1)} [${aiLow.toFixed(1)}-${aiHigh.toFixed(1)}]/C${aiConfidence} - ${aiAdvantages.length ? aiAdvantages.slice(0, 2).join(" and ") : "no decisive machine-side advantage"} helps, while ${aiObstacles.length ? aiObstacles.slice(0, 2).join(" and ") : "no dominant AI-specific obstacle"} limits current frontier agents; this is a protocol-dated estimate, not a run result.${completenessCaveat}`;
  const toolRationale = `L${toolLeverage.toFixed(1)}/10 - ${computePattern.test(statement) ? "explicit computational structure provides exact feedback" : "no explicit exact-feedback loop is named"}; ${continuousTacitPattern.test(statement) ? "continuous/tacit structure reduces leverage" : "no strong statement-level limiter was detected"}. This is a cue-based estimate.${completenessCaveat}`;
  const humanRationale = `H${humanEffort}/X${exposure}/C${humanConfidence} - ${famousPattern.test(title) ? "Iconic status supplies a conservative H9 floor; " : ""}the record has ${refCount} explicit citation signals and an age basis of ${yearInfo.year ? `${yearInfo.year} (${yearInfo.basis})` : "no defensible proposal year"}; ${hBand.label} specialist-hours is an order-of-magnitude prior because unpublished attempts are unobserved.${completenessCaveat}`;
  const tractabilityRationale = tractability === null ? `T N/A - not scored because the target is not an actionable open item; status or scope must be repaired before a progress forecast is meaningful.${completenessCaveat}` : `T${tractability} (${pBand.label}) - With tool leverage ${toolLeverage.toFixed(1)}/10 and outcome-average verification ${meanVerification.toFixed(1)}/10, material checked progress in 100 combined expert+AI hours is ${tractability >= 7 ? "plausible" : tractability >= 4 ? "possible but uncertain" : "unlikely"}; full resolution is not being forecast.${excerptOnly ? " This tractability value is retained as a low-confidence heuristic for coverage, not as a calibrated forecast." : ""}${completenessCaveat}`;
  let verificationMechanism = "A positive proof and a negative answer have materially different checking costs.";
  if (answerType === "existence") verificationMechanism = "A positive witness can be checked locally, whereas nonexistence requires a universal argument.";
  else if (answerType === "construction") verificationMechanism = "A proposed object offers a concrete witness; impossibility requires broader proof.";
  else if (answerType === "exact_value") verificationMechanism = "An exact answer needs matching lower and upper certificates, even when finite computation guides the search.";
  else if (answerType === "bound") verificationMechanism = "A claimed bound needs a uniform argument and often a matching construction or obstruction.";
  else if (answerType === "algorithm") verificationMechanism = "Correctness and complexity must both be audited; a negative result usually needs a reduction or lower bound.";
  else if (answerType === "classification") verificationMechanism = "Completeness of the classification is the main checking burden, not validating individual examples.";
  else if (answerType === "programmatic") verificationMechanism = "The current success criterion is too broad for a single decisive checker.";
  const verificationRationale = `V${vTrue.toFixed(1)}/${vFalse.toFixed(1)}/C${verificationConfidence} - ${verificationMechanism}${completenessCaveat}`;
  const formalizationMechanism = formalization >= 7 ? "major library gaps or continuous/geometric infrastructure are likely" : formalization <= 3.5 ? "the objects and quantifiers map comparatively directly to mature discrete libraries" : "bespoke definitions and supporting lemmas are needed, but the target is still expressible";
  const formalizationRationale = `F${formalization.toFixed(1)}/C${formalizationConfidence} - For ${objects}, ${formalizationMechanism}; this scores the statement and prerequisites, not formalizing an unknown proof.${completenessCaveat}`;
  const prepLevel = prereq >= 8 ? "active frontier expertise" : prereq >= 6.5 ? "PhD-level specialist preparation" : prereq >= 4.5 ? "graduate or advanced-specialist preparation" : "advanced undergraduate foundations";
  const prerequisiteRationale = `P${prereq.toFixed(1)}/B${breadth}/C${prerequisiteConfidence} - Contributing requires ${prepLevel}${breadth >= 3 ? ` across ${breadth} major areas` : breadth === 2 ? " across two interacting areas" : " within one main subfield"} in ${category}; surface readability is not treated as readiness to work at the frontier.${completenessCaveat}`;
  const statusReasons = [];
  if (needsStatusReview) statusReasons.push(`MathDB status is ${p._native_status ?? "not open"}`); if (suspectedResolution) statusReasons.push("the supplied context contains a resolution or independence signal"); if (malformed) statusReasons.push("the imported context contains a serialization fragment"); if (duplicateStatement) statusReasons.push("an exact-statement duplicate candidate exists"); if (duplicateNumber || duplicateTitle) statusReasons.push("a number/title collision needs identity review but is not itself proof of duplication"); if (excerptOnly) statusReasons.push("only the public-list excerpt was available for this release"); if (!statusReasons.length) statusReasons.push("MathDB presents the item as open, without an independent current-literature audit");
  const statusRationale = `${p.status} -> ${assessmentGate}/C${statusConfidence} - ${statusReasons.join("; ")}. Status should be rechecked before publishing a benchmark result.${completenessCaveat}`;
  const tier = tierFor(d), overallRationale = `${tier.difficulty_label}: D${d.toFixed(1)} and AI ${aiDifficulty.toFixed(1)} (${aiFitLabel.toLowerCase()}); ${suggestedRoute.toLowerCase()} is the most credible first mode. ${qualityFlags.length ? `Review flags: ${qualityFlags.slice(0, 3).join(", ")}.` : "No major corpus-quality flag was detected."}${completenessCaveat}`;
  return { p, title, statement, background, excerptOnly, category, source, legacyLevel, answerType, scope, refCount, yearInfo, age, sourcePrior, ambiguity, assessmentGate, statusConfidence, breadth, prereq, prerequisiteConfidence, formalization, formalizationConfidence, toolLeverage, vTrue, vFalse, verificationConfidence, conceptualGap, routeGap, technicalDepth, knownBarrier, searchScale, d, dLow, dHigh, difficultyConfidence, tier, literatureLoad, aiFormalFit, aiFeedbackFit, aiToolFit, aiContextFit, aiHorizonFit, aiTacitFit, aiRelative, aiDifficulty, aiLow, aiHigh, aiConfidence, aiFitLabel, humanEffort, humanConfidence, exposure, tractability, suggestedRoute, tags, reviewPriority, qualityFlags, objects, difficultyRationale, factorRationale, aiRationale, toolRationale, humanRationale, tractabilityRationale, verificationRationale, formalizationRationale, prerequisiteRationale, statusRationale, overallRationale, scoreBasis: excerptOnly ? "rule-based provisional excerpt-only assessment; MathDB importance/popularity/attempt outcomes excluded" : "rule-based; MathDB importance/popularity/attempt outcomes excluded", sourceUrl: p.source_url };
}

function findControls(value, pointer = "") {
  const found = [];
  if (typeof value === "string") for (let i = 0; i < value.length; i++) { const n = value.charCodeAt(i); if (n <= 8 || n === 11 || n === 12 || (n >= 14 && n <= 31) || n === 127) found.push({ path: pointer, index: i, code_point: `U+${n.toString(16).toUpperCase().padStart(4, "0")}` }); }
  else if (Array.isArray(value)) value.forEach((v, i) => found.push(...findControls(v, `${pointer}/${i}`)));
  else if (value && typeof value === "object") for (const [k, v] of Object.entries(value)) found.push(...findControls(v, `${pointer}/${k.replaceAll("~", "~0").replaceAll("/", "~1")}`));
  return found;
}
function peerIds(map, key, id) { return (map.get(normalizeKey(key)) ?? []).filter(peer => peer !== id); }
function curationAction(a) {
  if (a.qualityFlags.includes("status_NEEDS_REVIEW") || a.qualityFlags.includes("possible_stale_or_resolved")) return "Verify current open status against the canonical source";
  if (a.qualityFlags.includes("malformed_import_fragment")) return "Repair imported context and re-run scoring";
  if (a.qualityFlags.some(flag => flag.startsWith("duplicate_"))) return "Resolve duplicate identity / canonical record mapping";
  if (a.qualityFlags.includes("title_collision")) return "Review identity; never merge records from a shared title alone";
  if (a.qualityFlags.includes(SOURCE_EXCERPT_FLAG)) return "Retrieve the complete MathDB statement and recalibrate every excerpt-derived estimate";
  if (["compound", "programmatic"].includes(a.scope)) return "Split into atomic targets or define success criteria";
  return "Expert review and confirm score anchors";
}

function buildRecord(a, maps, snapshotSha, assessmentDate) {
  const p = a.p, raw = p._raw, tier = tierFor(a.d), hBand = HOUR_BANDS[a.humanEffort], tBand = a.tractability === null ? null : TRACTABILITY_BANDS[a.tractability];
  const controls = findControls(raw), priority = a.reviewPriority.match(/^(P\d)\s+(.+)$/), category = CATEGORY_INFO[a.category];
  const numberPeers = peerIds(maps.numbers, p.problem_number, p.id), titlePeers = peerIds(maps.titles, p.title, p.id), statementPeers = peerIds(maps.statements, p.statement, p.id);
  const result = {
    problem_id: p.id, difficulty_label: tier.difficulty_label, explanation: a.overallRationale,
    problem_number: p.problem_number, title: a.title, catalog_status: p.status,
    assessment_gate: { value: a.assessmentGate, confidence: confidence(a.statusConfidence) },
    classification: {
      category: { id: category[0], name: category[1], slug: category[2], label: a.category },
      source_collection: { id: MATHDB_SET_ID, name: "mathdb", slug: "mathdb", label: "MathDB", nested_object_present: true },
      scope: a.scope, answer_type: a.answerType,
      mathematical_objects: a.objects.split(";").map(v => v.trim()).filter(Boolean), mathematical_objects_summary: a.objects,
      ambiguity_score: a.ambiguity,
      legacy_difficulty: { id: null, level: null, label: null, description: null, role_in_opdp: "absent_not_used" },
    },
    intrinsic_difficulty: {
      score: a.d, range: { low: a.dLow, high: a.dHigh }, confidence: confidence(a.difficultyConfidence), tier_code: tier.code, tier_label: tier.label,
      factors: {
        conceptual_gap: { code: "CG", score: a.conceptualGap, weight: .30 }, route_gap: { code: "RG", score: a.routeGap, weight: .20 },
        technical_depth: { code: "TD", score: a.technicalDepth, weight: .20 }, known_barrier: { code: "KB", score: a.knownBarrier, weight: .20 },
        search_scale: { code: "SS", score: a.searchScale, weight: .10 },
      },
    },
    ai_assessment: {
      relative_adjustment: a.aiRelative, fit: AI_FIT_CODES[a.aiFitLabel], fit_label: a.aiFitLabel, difficulty_score: a.aiDifficulty,
      range: { low: a.aiLow, high: a.aiHigh }, confidence: confidence(a.aiConfidence), protocol_id: AI_PROTOCOL_ID,
      predictors: { formal_fit: a.aiFormalFit, verification_feedback: a.aiFeedbackFit, tool_fit: a.aiToolFit, context_load: a.aiContextFit, reasoning_horizon: a.aiHorizonFit, tacit_experimental_insight: a.aiTacitFit },
      empirical_problem_episode_run: false,
    },
    human_attention: {
      effort: { score: a.humanEffort, code: hBand.code, estimated_specialist_hours: { minimum: hBand.minimum, maximum: hBand.maximum, minimum_inclusive: true, maximum_inclusive: hBand.maximum === null ? null : false, maximum_kind: hBand.maximum === null ? "unbounded" : "finite", display_label: hBand.label }, confidence: confidence(a.humanConfidence), estimate_kind: "order_of_magnitude_prior_not_observed_labor" },
      exposure: { score: a.exposure, code: `X${a.exposure}`, confidence: confidence(null, "not_separately_assigned") },
    },
    tractability: {
      status: a.tractability === null ? "not_applicable" : "scored", score: a.tractability, code: tBand?.code ?? null,
      progress_probability: tBand ? { low: tBand.low, high: tBand.high, low_inclusive: true, high_inclusive: tBand.high_inclusive, display_label: tBand.label } : null,
      target_combined_hours: 100, minimum_outcome_level: 3, target: "novel_independently_checked_partial_progress_not_full_resolution",
      confidence: a.tractability === null ? confidence(null, "not_applicable") : a.excerptOnly ? confidence(0) : confidence(null, "not_separately_assigned"),
    },
    verification: { meaning: "independent_checking_burden_not_scientific_value", if_true_score: a.vTrue, if_false_score: a.vFalse, confidence: confidence(a.verificationConfidence) },
    formalization: { score: a.formalization, confidence: confidence(a.formalizationConfidence), meaning: "burden_to_encode_statement_and_prerequisites_not_unknown_proof" },
    prerequisites: { preparation_score: a.prereq, breadth_score: a.breadth, confidence: confidence(a.prerequisiteConfidence) },
    tool_leverage: { score: a.toolLeverage, direction: "higher_is_more_favorable", confidence: confidence(null, "not_separately_assigned") },
    recommended_approach: { route_code: routeCode(a.suggestedRoute), route_label: a.suggestedRoute },
    evidence: { codes: [`source:MathDB`, `text:${a.excerptOnly ? "public_list_excerpt" : "full_statement"}`, `statement:${a.answerType}/${a.scope}`, `refs:${a.refCount}`, `year:${a.yearInfo.year ?? "unknown"}`, "legacy:absent", a.assessmentGate], reference_signal_count: a.refCount, estimated_proposal_year: a.yearInfo.year, proposal_year_basis: a.yearInfo.basis === "explicit_proposed_year" ? "explicit_proposed_year" : a.yearInfo.year ? "source_or_background_inference" : "unknown", proposal_year_basis_detail: a.yearInfo.basis, estimated_age_years_at_assessment: a.age, literature_load_score: a.literatureLoad, collection_barrier_prior: a.sourcePrior, score_basis: a.scoreBasis, status_evidence: p._native_status, rights_note: null },
    rationales: { intrinsic_difficulty: a.difficultyRationale, intrinsic_factors: a.factorRationale, ai_assessment: a.aiRationale, tool_leverage: a.toolRationale, human_attention: a.humanRationale, tractability: a.tractabilityRationale, verification: a.verificationRationale, formalization: a.formalizationRationale, prerequisites: a.prerequisiteRationale, status_and_data: a.statusRationale },
    tags: a.tags.map(tag => TAG_CODES[tag]),
    review: { priority_code: priority[1], priority_label: priority[2], recommended_curation_action: curationAction(a), identity_collisions: { problem_number_peer_ids: numberPeers, normalized_title_peer_ids: titlePeers, normalized_statement_peer_ids: statementPeers }, manual_override: { applied: false, reviewer_id: null, reviewed_at: null, reason: null, changes: [] } },
    flags: [...a.qualityFlags],
    source_text: { title: raw.title ?? p.title, statement: a.excerptOnly ? raw.excerpt : (raw.statement ?? p.statement), background: nativeContext(raw), text_mode: a.excerptOnly ? MATHDB_SUMMARY_TEXT_MODE : "mathdb_native_field_projection", assessed_title_differs_from_source: clean(raw.title ?? p.title, 2_000) !== a.title, transport_qa: { forbidden_control_character_count: controls.length, occurrences: controls, json_serialization: "escaped_by_json_encoder", display_policy: "sanitize_before_rendering_when_count_is_nonzero" } },
    provenance: {
      ulam_url: null, canonical_source_url: p.source_url, source_url_used: p.source_url, source_url_kind: raw.source_url ? "mathdb_attributed_source" : "mathdb_record_fallback",
      source_url_verification_status: "not_independently_verified", source_url_qa_flags: [], problem_number_is_unique: numberPeers.length === 0, ulam_url_is_unique: null,
      source_collection_id: MATHDB_SET_ID, source_collection_label: "MathDB", category_id: category[0], category_label: a.category,
      proposed_by: p.proposed_by, proposed_year: p.proposed_year, source_created_at: p.created_at, source_updated_at: p.updated_at, source_published: p.published,
      engagement: { view_count: p.view_count, favorite_count: p.favorite_count, excluded_from_scoring: true }, declared_dataset_license: "CC-BY-4.0",
      dataset_terms_url: "https://mathdb.com/terms", mathdb_url: p._snapshot?.inventory_url || `https://mathdb.com/p/${p._mathdb_number}`,
      mathdb_id: raw.id ?? null, mathdb_number: p._mathdb_number, mathdb_post_type: p._native_post_type, mathdb_native_status: p._native_status,
      mathdb_snapshot: p._snapshot, mathdb_text_basis: a.excerptOnly ? MATHDB_SUMMARY_TEXT_MODE : "mathdb_full_detail_statement", statement_completeness: p._statement_completeness,
      category_assignment_method: `deterministic OPDP keyword projection from MathDB tags, collections, title, and ${a.excerptOnly ? "public-list excerpt" : "statement"}`,
    },
    source_record: raw,
    implementation: { record_schema_version: RECORD_SCHEMA_VERSION, dataset_version: RELEASE_VERSION, dataset_sha256: snapshotSha, rule_version: RULE_VERSION, assessment_date: assessmentDate, ai_protocol_id: AI_PROTOCOL_ID, calculation_state: "fresh", input_mode: a.excerptOnly ? "copied_mathdb_public_list_excerpt_plus_stored_editorial_inputs" : "copied_mathdb_source_plus_stored_editorial_inputs", derived_fields_regenerated: true, expert_certified: false, workbook_formula_canonicalized_fields: [], source_envelope_schema: p._envelope_schema, source_snapshot: p._snapshot },
  };
  return result;
}

function addToMap(map, key, id) { const norm = normalizeKey(key); if (!norm) return; if (!map.has(norm)) map.set(norm, []); map.get(norm).push(id); }
function makeMaps(baseRecords) {
  const maps = { numbers: new Map(), titles: new Map(), statements: new Map() };
  for (const r of baseRecords) { addToMap(maps.numbers, r.problem_number, r.problem_id); addToMap(maps.titles, r.title, r.problem_id); addToMap(maps.statements, r.source_text?.statement, r.problem_id); }
  return maps;
}
function count(values, seed = []) { const result = Object.fromEntries(seed.map(key => [key, 0])); for (const value of values) result[value] = (result[value] ?? 0) + 1; return result; }
function increment(object, key, amount = 1) { object[key] = (object[key] ?? 0) + amount; }
function sortedObject(object) { return Object.fromEntries(Object.entries(object).sort(([a], [b]) => a.localeCompare(b))); }

class SummaryAccumulator {
  constructor() {
    this.total = 0;
    this.catalog = count([], ["open", "partially_solved", "solved"]);
    this.gates = count([], ["verified_open", "source_claimed_open", "status_unclear", "solved", "ill_posed"]);
    this.tiers = count([], ["T1", "T2", "T3", "T4", "T5"]);
    this.fits = count([], ["ai_favored", "ai_neutral_mixed", "ai_hostile"]);
    this.confidence = count([], ["C0", "C1", "C2", "C3"]);
    this.scopes = count([], ["atomic", "family", "compound", "programmatic"]);
    this.answers = count([], ["proof_disproof", "bound", "existence", "algorithm", "classification", "construction", "exact_value", "programmatic", "experimental"]);
    this.hours = count([], Array.from({ length: 11 }, (_, i) => `H${i}`));
    this.tractability = count([], Array.from({ length: 11 }, (_, i) => `T${i}`));
    this.tractability.not_applicable = 0;
    this.priorities = count([], ["P0", "P1", "P2", "P3"]);
    this.tags = count([], Object.values(TAG_CODES)); this.flags = {}; this.categories = {}; this.sources = {}; this.routes = {};
    this.flagged = 0; this.curation = 0; this.tractabilityCount = 0; this.sumD = 0; this.sumAi = 0; this.sumT = 0;
    this.ids = new Set(); this.numbers = new Set(); this.duplicateNumbers = new Set(); this.duplicateNumberRecords = 0;
    this.fallbackUrls = 0; this.malformedUrls = 0; this.controls = 0; this.controlRecords = 0;
    this.missingSet = 0; this.missingNestedSet = 0; this.zeroTractability = 0;
  }
  update(record) {
    this.total++; this.ids.add(record.problem_id); this.numbers.add(record.problem_number);
    increment(this.catalog, record.catalog_status); increment(this.gates, record.assessment_gate.value);
    increment(this.tiers, record.intrinsic_difficulty.tier_code); increment(this.fits, record.ai_assessment.fit);
    increment(this.confidence, record.intrinsic_difficulty.confidence.code); increment(this.scopes, record.classification.scope);
    increment(this.answers, record.classification.answer_type); increment(this.hours, record.human_attention.effort.code);
    if (record.tractability.code) { increment(this.tractability, record.tractability.code); this.tractabilityCount++; this.sumT += record.tractability.score; }
    else increment(this.tractability, "not_applicable");
    increment(this.priorities, record.review.priority_code); increment(this.categories, record.classification.category.label);
    increment(this.sources, record.classification.source_collection.label); increment(this.routes, record.recommended_approach.route_code);
    record.tags.forEach(tag => increment(this.tags, tag)); record.flags.forEach(flag => increment(this.flags, flag));
    this.flagged += Number(record.flags.length > 0); this.curation += Number(record.tags.includes("needs_curation"));
    this.sumD += record.intrinsic_difficulty.score; this.sumAi += record.ai_assessment.difficulty_score;
    const peers = record.review.identity_collisions.problem_number_peer_ids;
    if (peers.length) { this.duplicateNumberRecords++; this.duplicateNumbers.add(record.problem_number); }
    this.fallbackUrls += Number(/fallback/.test(record.provenance.source_url_kind));
    this.malformedUrls += Number(record.provenance.source_url_qa_flags.includes("duplicated_scheme"));
    const controlCount = record.source_text.transport_qa.forbidden_control_character_count;
    this.controls += controlCount; this.controlRecords += Number(controlCount > 0);
    this.missingSet += Number(record.classification.source_collection.id == null);
    this.missingNestedSet += Number(record.classification.source_collection.id != null && !record.classification.source_collection.nested_object_present);
    this.zeroTractability += Number(record.tractability.score === 0);
  }
  finish() {
    return {
      records_total: this.total, catalog_status_counts: this.catalog, assessment_gate_counts: this.gates,
      difficulty_tier_counts: this.tiers, ai_fit_counts: this.fits, difficulty_confidence_counts: this.confidence,
      scope_counts: this.scopes, answer_type_counts: this.answers, human_effort_band_counts: this.hours,
      tractability_band_counts: this.tractability, review_priority_counts: this.priorities, tag_counts: this.tags,
      flag_counts: this.flags, category_counts: sortedObject(this.categories), source_collection_counts: sortedObject(this.sources),
      records_with_flags: this.flagged, records_needing_curation: this.curation, tractability_scored_records: this.tractabilityCount,
      means: { intrinsic_difficulty: round1(this.sumD / this.total), ai_difficulty: round1(this.sumAi / this.total), tractability: this.tractabilityCount ? round1(this.sumT / this.tractabilityCount) : null },
      corpus_audit: {
        unique_problem_ids: this.ids.size, unique_problem_numbers: this.numbers.size,
        duplicate_problem_number_groups: this.duplicateNumbers.size, records_in_duplicate_problem_number_groups: this.duplicateNumberRecords,
        surplus_problem_number_records: this.duplicateNumberRecords - this.duplicateNumbers.size,
        source_url_fallback_records: this.fallbackUrls, malformed_duplicated_scheme_source_urls: this.malformedUrls,
        forbidden_control_character_occurrences: this.controls, records_with_forbidden_control_characters: this.controlRecords,
        missing_set_id_records: this.missingSet, set_id_present_but_nested_set_missing_records: this.missingNestedSet,
        intentional_null_tractability_records: this.total - this.tractabilityCount, valid_zero_tractability_records: this.zeroTractability,
      },
      route_counts: this.routes,
      summary_semantics: "Cached convenience values recomputable from records; means use the stated denominator and are rounded to one decimal.",
    };
  }
}

function refreshRegistries(payload, summary) {
  for (const [key, item] of Object.entries(payload.rubric.tag_registry ?? {})) item.record_count = summary.tag_counts[key] ?? 0;
  for (const [key, item] of Object.entries(payload.rubric.route_registry ?? {})) item.record_count = summary.route_counts?.[key] ?? 0;
  for (const [key, item] of Object.entries(payload.rubric.flag_registry ?? {})) item.record_count = summary.flag_counts[key] ?? 0;
}

function buildPayloadHeader(base, additionStats, summary, inputSha, inputPath, manifest, assessmentDate) {
  const payload = { ...base };
  delete payload.records;
  const detailReleases = manifest?.consistency?.detail_release_headers;
  const catalogReleaseCounts = manifest?.catalog?.api_release_header_counts;
  const catalogReleases = catalogReleaseCounts && typeof catalogReleaseCounts === "object" ? Object.keys(catalogReleaseCounts).filter(value => value !== "<missing>") : [];
  const upstreamRevision = catalogReleases.length === 1 ? catalogReleases[0] : Array.isArray(detailReleases) && detailReleases.length === 1 ? detailReleases[0] : null;
  payload.export_id = `opdp-v${RELEASE_VERSION}__${RULE_VERSION}`;
  payload.generated_at = `${assessmentDate}T00:00:00Z`;
  payload.dataset_snapshot = {
    name: "OPDP combined UnsolvedMath and MathDB public-catalog snapshot", version: RELEASE_VERSION,
    source_file: path.basename(inputPath), record_count: summary.records_total, sha256: inputSha,
    dataset_url: "https://mathdb.com", source_file_url: null, website_url: "https://mathdb.com", declared_license: "CC-BY-4.0", terms_url: "https://mathdb.com/terms", sha256_scope: "exact_frozen_mathdb_jsonl_input",
    text_preservation: `All 15,458 v1.6 OPDP records are preserved exactly. Each appended source_record is the complete frozen MathDB API object available in its JSONL line; ${additionStats.summaryCount.toLocaleString("en-US")} records contain the public-list excerpt rather than a complete problem statement.`,
    upstream_revision: upstreamRevision, upstream_problems_sha256: inputSha,
    upstream_inventory_sha256: manifest?.inventory?.sha256 ?? null, upstream_inventory_count: manifest?.inventory?.count ?? null,
    upstream_snapshot_consistency: manifest?.consistency ?? null,
    upstream_source_file_url: manifest?.source_url ?? null, compatibility_mode: "append_only",
    source_segments: [
      { role: "compatibility_base", dataset_version: EXPECTED_BASE_VERSION, record_count: base.records.length, sha256: base.dataset_snapshot.sha256, policy: "Every complete legacy OPDP record is preserved exactly." },
      { role: "mathdb_additions", dataset_version: RELEASE_VERSION, record_count: additionStats.count, id_minimum: additionStats.idMinimum, id_maximum: additionStats.idMaximum, input_sha256: inputSha, full_statement_record_count: additionStats.count - additionStats.summaryCount, public_list_excerpt_record_count: additionStats.summaryCount, text_basis: additionStats.summaryCount === additionStats.count ? MATHDB_SUMMARY_TEXT_MODE : "mixed_mathdb_detail_and_public_list_excerpt", policy: "One losslessly retained MathDB API source object per JSONL line; public-list excerpts receive explicit C0/provisional treatment under the unchanged OPDP rule version." },
    ],
  };
  payload.assessment_release = {
    ...base.assessment_release, assessment_date: assessmentDate, records_assessed: summary.records_total, records_total: summary.records_total,
    generator_name: "opdp-mathdb-append-builder", generator_version: "1.1.0",
    limitations: [...base.assessment_release.limitations, "MathDB source statuses are preserved and mapped conservatively; claimed solutions are not treated as verified resolutions.", "The single OPDP category for each MathDB record is a deterministic keyword projection from available native tags/collections, title, and assessed text, not a source-native taxonomy.", ...(additionStats.summaryCount ? [`For ${additionStats.summaryCount.toLocaleString("en-US")} MathDB additions, the public list supplied only an excerpt (at most the source's list-view length), not the complete problem statement. Their dimension values are provisional cue-based coverage estimates: assigned confidences are capped at C0, D/AI intervals are widened, tractability remains a specifically labeled low-confidence heuristic, and every record is flagged source_excerpt_only for recalibration.`] : [])],
    supporting_artifacts: ["Ulam_OPDP_Methodology_v1.0.docx"], workbook_reconciliation: { status: "not_applicable_json_only_expansion", rows_checked: base.records.length, record_generation: "v1.6 records copied exactly; MathDB additions generated directly under the canonical OPDP rule engine." },
    compatibility: { mode: "append_only", base_export_id: base.export_id, base_record_count: base.records.length, added_record_count: additionStats.count, legacy_record_policy: "Every complete v1.6 record is retained without alteration.", schema_policy: "Top-level and per-record schema versions remain unchanged; consumers may ingest the expansion without a schema migration." },
  };
  payload.integration_guide = {
    ...base.integration_guide,
    join_and_route_warning: `Use numeric problem_id. Existing v1.6 ids are unchanged; each MathDB id is 40,000,000 + its native number (${additionStats.idMinimum ?? "N/A"}-${additionStats.idMaximum ?? "N/A"}) and displays as MATHDB-{number}.`,
    deployment: "This complete exchange file is intentionally large. Split or index it at build time; do not make browsers download the full source and audit payload for list views.",
    raw_text_warning: "MathDB source_record is the lossless API object received for that record. source_text.statement is either the native full statement or, when text_mode is mathdb_public_list_excerpt, only the public-list excerpt and must not be presented as complete.",
  };
  payload.schema = { ...base.schema, record_contract: { ...base.schema.record_contract, problem_id: "Stable numeric OPDP id; canonical join and route key. v1.6 ids are frozen; each MathDB id equals 40,000,000 plus its native number.", source_record: "Complete original frozen source row/object, copied losslessly." } };
  payload.rubric = { ...base.rubric, flag_registry: { ...base.rubric.flag_registry, [SOURCE_EXCERPT_FLAG]: { description: "MathDB supplied only a public-list excerpt, not a guaranteed-complete problem statement; all excerpt-derived estimates are provisional and require recalibration.", record_count: 0 } } };
  payload.protocols = { ...base.protocols, [AI_PROTOCOL_ID]: { ...base.protocols[AI_PROTOCOL_ID], assessment_date: assessmentDate } };
  payload.summary = summary;
  refreshRegistries(payload, payload.summary);
  return payload;
}

function validateRecord(record, raw, base, errors, idOffset) {
  const keys = base.schema.record_field_order;
  const rationaleKeys = ["intrinsic_difficulty", "intrinsic_factors", "ai_assessment", "tool_leverage", "human_attention", "tractability", "verification", "formalization", "prerequisites", "status_and_data"];
    if (JSON.stringify(Object.keys(record)) !== JSON.stringify(keys)) errors.push(`record ${record.problem_id}: field order/schema mismatch`);
    if (rationaleKeys.some(key => !record.rationales[key])) errors.push(`record ${record.problem_id}: missing rationale`);
    if (JSON.stringify(record.source_record) !== JSON.stringify(raw)) errors.push(`record ${record.problem_id}: source_record is not lossless`);
    const nativeNumber = firstInteger(raw, ["number", "problem_number", "post_number"]);
    if (record.problem_id !== idOffset + nativeNumber || record.problem_number !== `MATHDB-${nativeNumber}`) errors.push(`record ${record.problem_id}: stable MathDB id projection mismatch`);
    const f = record.intrinsic_difficulty.factors;
    const expectedD = round1(2.5 * (.30 * f.conceptual_gap.score + .20 * f.route_gap.score + .20 * f.technical_depth.score + .20 * f.known_barrier.score + .10 * f.search_scale.score));
    if (record.intrinsic_difficulty.score !== Math.min(expectedD, record.catalog_status === "solved" ? 8.5 : 10)) errors.push(`record ${record.problem_id}: D formula mismatch`);
    const preds = Object.values(record.ai_assessment.predictors), expectedAdj = round1(.5 * preds.reduce((a, b) => a + b, 0));
    if (record.ai_assessment.relative_adjustment !== expectedAdj || record.ai_assessment.difficulty_score !== round1(clamp(record.intrinsic_difficulty.score + expectedAdj, 0, 10))) errors.push(`record ${record.problem_id}: AI formula mismatch`);
    if ([record.provenance.engagement.view_count, record.provenance.engagement.favorite_count].some(v => v != null) && !record.provenance.engagement.excluded_from_scoring) errors.push(`record ${record.problem_id}: MathDB engagement was not excluded from scoring`);
    if (record.implementation.source_envelope_schema === MATHDB_SUMMARY_SCHEMA) {
      if (record.source_text.statement !== raw.excerpt || record.source_text.text_mode !== MATHDB_SUMMARY_TEXT_MODE) errors.push(`record ${record.problem_id}: summary excerpt/text_mode mismatch`);
      if (!record.flags.includes(SOURCE_EXCERPT_FLAG)) errors.push(`record ${record.problem_id}: summary record lacks ${SOURCE_EXCERPT_FLAG}`);
      if (record.assessment_gate.confidence.code !== "C0" || record.intrinsic_difficulty.confidence.code !== "C0" || record.ai_assessment.confidence.code !== "C0" || record.human_attention.effort.confidence.code !== "C0" || record.verification.confidence.code !== "C0" || record.formalization.confidence.code !== "C0" || record.prerequisites.confidence.code !== "C0" || (record.tractability.score !== null && record.tractability.confidence.code !== "C0")) errors.push(`record ${record.problem_id}: summary-derived assigned confidences are not all C0`);
      if (record.tractability.status === "scored" && record.tractability.confidence.code !== "C0") errors.push(`record ${record.problem_id}: excerpt-derived tractability is not labeled C0`);
      if (rationaleKeys.some(key => !record.rationales[key].includes("public-list excerpt"))) errors.push(`record ${record.problem_id}: excerpt limitation is absent from one or more rationales`);
    }
}

function buildValidation(base, additionStats, summary, payload, inputSha, legacyDigestBefore, legacyDigestAfter, errors) {
  const warnings = [];
  if (legacyDigestBefore !== legacyDigestAfter) errors.push("Legacy v1.6 record digest changed");
  if (summary.records_total !== base.records.length + additionStats.count) errors.push("Combined record count does not equal base plus MathDB count");
  if (summary.corpus_audit.unique_problem_ids !== summary.records_total) errors.push("problem_id is not unique");
  if (payload.summary.records_total !== summary.records_total) errors.push("summary.records_total mismatch");
  if (payload.dataset_snapshot.sha256 !== inputSha) errors.push("dataset snapshot hash mismatch");
  if ((summary.flag_counts[SOURCE_EXCERPT_FLAG] ?? 0) !== additionStats.summaryCount) errors.push("source_excerpt_only count does not equal summary-envelope count");
  if (!additionStats.count) warnings.push("No MathDB records were appended");
  return {
    validation_schema_version: "1.0.0", release_version: RELEASE_VERSION, rule_version: RULE_VERSION,
    status: errors.length ? "fail" : "pass", errors, warnings,
    inputs: { base_export_id: base.export_id, base_count: base.records.length, mathdb_count: additionStats.count, mathdb_summary_excerpt_count: additionStats.summaryCount, mathdb_sha256: inputSha },
    outputs: { records_total: summary.records_total, additions: additionStats.count, first_mathdb_id: additionStats.idMinimum, last_mathdb_id: additionStats.idMaximum },
    checks: { legacy_deep_digest_unchanged: legacyDigestBefore === legacyDigestAfter, unique_problem_ids: summary.corpus_audit.unique_problem_ids === summary.records_total, complete_ten_rationales: !errors.some(e => e.includes("missing rationale") || e.includes("absent from one or more rationales")), lossless_mathdb_source_records: !errors.some(e => e.includes("source_record")), formulas_recomputed: !errors.some(e => e.includes("formula")), engagement_excluded: !errors.some(e => e.includes("engagement")), stable_reversible_mathdb_ids: !errors.some(e => e.includes("id projection")), excerpt_limitations_and_c0_caps: !errors.some(e => e.includes("summary") || e.includes("excerpt")), streamed_mathdb_record_cache: true },
  };
}

async function writeGzipPayload(file, header, baseRecords, newRecordCache) {
  await fsp.mkdir(path.dirname(path.resolve(file)), { recursive: true });
  const gzip = zlib.createGzip({ level: 9 });
  const target = fs.createWriteStream(file);
  gzip.pipe(target);
  const entries = Object.entries(header);
  gzip.write("{");
  entries.forEach(([key, value], i) => gzip.write(`${i ? "," : ""}${JSON.stringify(key)}:${JSON.stringify(value)}`));
  gzip.write(`${entries.length ? "," : ""}"records":[`);
  let written = 0;
  for (const record of baseRecords) {
    if (!gzip.write(`${written++ ? "," : ""}${JSON.stringify(record)}`)) await once(gzip, "drain");
  }
  for await (const { value: line } of readRawLines(newRecordCache)) {
    if (!gzip.write(`${written++ ? "," : ""}${line}`)) await once(gzip, "drain");
  }
  gzip.end("]}");
  await once(target, "close");
}

async function* readRawLines(file) {
  const source = fs.createReadStream(file), stream = file.toLowerCase().endsWith(".gz") ? source.pipe(zlib.createGunzip()) : source;
  const lines = readline.createInterface({ input: stream, crlfDelay: Infinity });
  let lineNumber = 0;
  for await (const line of lines) { lineNumber++; if (line.trim()) yield { value: line, lineNumber }; }
}

async function main() {
  const args = parseArgs(process.argv);
  if (args.help) { console.log(usage()); return; }
  for (const required of ["base", "mathdb", "output", "validation"]) if (!args[required]) fail(`${usage()}\n\nMissing --${required}`);
  const assessmentDate = args.date ?? RELEASE_DATE, assessmentYear = Number(assessmentDate.slice(0, 4));
  const idOffset = Number(args["id-offset"] ?? MATHDB_ID_OFFSET), limit = args.limit ? Number(args.limit) : Infinity;
  const parsedAssessmentDate = new Date(`${assessmentDate}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(assessmentDate) || Number.isNaN(parsedAssessmentDate.valueOf()) || parsedAssessmentDate.toISOString().slice(0, 10) !== assessmentDate) fail("--date must be a valid YYYY-MM-DD date");
  if (!Number.isInteger(idOffset) || idOffset < 1) fail("--id-offset must be a positive integer");
  if (!(Number.isInteger(limit) && limit > 0) && limit !== Infinity) fail("--limit must be a positive integer");
  const base = await loadJsonMaybeGzip(args.base);
  if (!args["allow-base-mismatch"] && (base.dataset_snapshot?.version !== EXPECTED_BASE_VERSION || base.records?.length !== EXPECTED_BASE_COUNT)) fail(`Expected canonical v${EXPECTED_BASE_VERSION} with ${EXPECTED_BASE_COUNT} records; use --allow-base-mismatch only for fixture tests`);
  if (!Array.isArray(base.records)) fail("Base export has no records array");
  const legacyDigestBefore = stableRecordDigest(base.records);
  const inputSha = await sha256File(args.mathdb), manifest = args.manifest ? await loadJsonMaybeGzip(args.manifest) : null;
  if (!args.limit && !manifest) fail("--manifest is required for a complete release build; omit it only with --limit smoke tests");
  const maps = makeMaps(base.records), baseIds = new Set(base.records.map(r => r.problem_id));
  let inputCount = 0, summaryInputCount = 0, summaryEnvelopeCount = 0, previousNumber = -Infinity, idMinimum = null, idMaximum = null;

  // Pass 1 retains only normalized identity keys. The full MathDB objects are
  // deliberately not accumulated: at current scale that would approach the
  // V8 string/heap limits once transformed to OPDP records.
  for await (const { value: entry, lineNumber } of readJsonLines(args.mathdb, limit)) {
    const { raw, envelopeSchema } = unwrapEntry(entry, lineNumber);
    const number = firstInteger(raw, ["number", "problem_number", "post_number"]);
    if (number === null || number < 0) fail(`${args.mathdb}:${lineNumber}: missing nonnegative integer number`);
    if (number <= previousNumber) fail(`${args.mathdb}:${lineNumber}: MathDB JSONL must be strictly sorted by number (saw ${number} after ${previousNumber}); the acquisition snapshot must be frozen in sitemap-number order`);
    previousNumber = number;
    const id = idOffset + number;
    if (!Number.isSafeInteger(id)) fail(`${args.mathdb}:${lineNumber}: projected OPDP id is not a safe integer`);
    if (baseIds.has(id)) fail(`${args.mathdb}:${lineNumber}: projected OPDP id ${id} collides with v1.6`);
    const title = firstString(raw, ["title", "name"]) || `MathDB Problem ${number}`;
    const statement = firstString(raw, ["statement", "problem", "text", "content", "question", "excerpt"]);
    if (!statement) fail(`${args.mathdb}:${lineNumber}: MathDB problem ${number} has no statement or public-list excerpt`);
    addToMap(maps.numbers, `MATHDB-${number}`, id); addToMap(maps.titles, title, id); addToMap(maps.statements, statement, id);
    summaryInputCount += Number(envelopeSchema === MATHDB_SUMMARY_SCHEMA || (!firstString(raw, ["statement", "problem", "text", "content", "question"]) && Boolean(firstString(raw, ["excerpt"]))));
    summaryEnvelopeCount += Number(envelopeSchema === MATHDB_SUMMARY_SCHEMA);
    inputCount++; idMinimum ??= id; idMaximum = id;
  }
  if (manifest) {
    const section = manifest.catalog && typeof manifest.catalog === "object" ? manifest.catalog : manifest.details;
    const manifestSha = String(section?.sha256 ?? "").toUpperCase();
    if (!args.limit && !manifestSha) fail("Complete release manifest must declare the exact catalog/details SHA-256");
    if (manifestSha && manifestSha !== inputSha) fail(`Manifest snapshot SHA-256 ${manifestSha} does not match --mathdb ${inputSha}`);
    if (!args.limit) {
      if (manifest.status !== "complete") fail(`Complete release requires manifest.status=complete, found ${JSON.stringify(manifest.status)}`);
      if (manifest.catalog && manifest.schema !== MATHDB_CATALOG_MANIFEST_SCHEMA) fail(`Catalog release requires manifest.schema=${MATHDB_CATALOG_MANIFEST_SCHEMA}`);
      const sectionComplete = manifest.catalog ? section?.status === "complete" : section?.completed_count === section?.selected_count && section?.post_run_validation?.status === "passed";
      if (!section || !sectionComplete) fail("Complete release requires a completed and post-validated manifest catalog/details section");
      const completedCount = Number(section.completed_count), selectedCount = Number(section.selected_count ?? completedCount), inventoryCount = Number(manifest.inventory?.count);
      if (![completedCount, selectedCount, inventoryCount].every(Number.isInteger)) fail("Complete manifest must declare integer completed, selected, and inventory counts");
      if (completedCount !== inputCount || selectedCount !== inputCount || inventoryCount !== inputCount) fail(`Complete manifest counts must all equal JSONL count ${inputCount}; completed=${completedCount}, selected=${selectedCount}, inventory=${inventoryCount}`);
      if (manifest.catalog && section.text_basis !== MATHDB_SUMMARY_TEXT_MODE) fail(`Catalog manifest requires catalog.text_basis=${MATHDB_SUMMARY_TEXT_MODE}`);
      if (manifest.catalog && section.output_path !== path.basename(args.mathdb)) fail(`Catalog manifest output_path ${JSON.stringify(section.output_path)} does not match --mathdb basename ${JSON.stringify(path.basename(args.mathdb))}`);
      if (manifest.catalog && summaryEnvelopeCount !== inputCount) fail(`Catalog manifest requires ${inputCount} explicit ${MATHDB_SUMMARY_SCHEMA} envelopes; found ${summaryEnvelopeCount}`);
      if (manifest.details && manifest.details.scope && manifest.details.scope !== "full") fail(`Complete detail release requires details.scope=full, found ${JSON.stringify(manifest.details.scope)}`);
    } else if (!["complete", "complete_sample"].includes(manifest.status)) fail(`Smoke test manifest status is ${manifest.status}; only completed snapshots may be scored`);
  }

  const outputAbs = path.resolve(args.output), cacheFile = `${outputAbs}.mathdb-records.${process.pid}.tmp.jsonl.gz`;
  const outputTemp = path.join(path.dirname(outputAbs), `.${path.basename(outputAbs)}.${process.pid}.${crypto.randomUUID()}.tmp`);
  const validationAbs = path.resolve(args.validation), protectedInputs = [path.resolve(args.base), path.resolve(args.mathdb), ...(args.manifest ? [path.resolve(args.manifest)] : [])];
  if (protectedInputs.includes(outputAbs)) fail("--output must not overwrite the base, MathDB snapshot, or manifest input");
  if (validationAbs === outputAbs || protectedInputs.includes(validationAbs)) fail("--validation must be distinct from every input and from --output");
  if (!path.resolve(cacheFile).startsWith(path.dirname(outputAbs) + path.sep)) fail("Internal cache path escaped the output directory");
  if (!path.resolve(outputTemp).startsWith(path.dirname(outputAbs) + path.sep)) fail("Internal output temp path escaped the output directory");
  await fsp.mkdir(path.dirname(outputAbs), { recursive: true });
  const cacheGzip = zlib.createGzip({ level: 6 }), cacheTarget = fs.createWriteStream(cacheFile);
  cacheGzip.pipe(cacheTarget);
  const accumulator = new SummaryAccumulator();
  base.records.forEach(record => accumulator.update(record));
  const errors = [];
  let builtCount = 0, builtSummaryCount = 0, cacheClosed = false;
  try {
    // Pass 2 scores one source object at a time and writes one compact OPDP
    // record per gzip member line. Only summary state and duplicate maps stay
    // resident; no MathDB source-record or OPDP-record array is materialized.
    for await (const { value: entry, lineNumber } of readJsonLines(args.mathdb, limit)) {
      const { raw, snapshot, envelopeSchema } = unwrapEntry(entry, lineNumber);
      const p = adaptMathDb(raw, idOffset, snapshot, envelopeSchema);
      const record = buildRecord(assessProblem(p, maps.numbers, maps.titles, maps.statements, assessmentYear), maps, inputSha, assessmentDate);
      validateRecord(record, raw, base, errors, idOffset);
      accumulator.update(record);
      if (!cacheGzip.write(JSON.stringify(record) + "\n")) await once(cacheGzip, "drain");
      builtSummaryCount += Number(p._excerpt_only);
      builtCount++;
      if (errors.length >= 100) break;
    }
    cacheGzip.end();
    await once(cacheTarget, "close");
    cacheClosed = true;
    if (builtCount !== inputCount) errors.push(`First-pass MathDB count ${inputCount} differs from scored count ${builtCount}`);
    if (builtSummaryCount !== summaryInputCount) errors.push(`First-pass summary count ${summaryInputCount} differs from scored count ${builtSummaryCount}`);
    const inputShaAfter = await sha256File(args.mathdb);
    if (inputShaAfter !== inputSha) errors.push(`MathDB input changed between the initial hash and completed scoring pass (${inputSha} -> ${inputShaAfter})`);
    const legacyDigestAfter = stableRecordDigest(base.records);
    const additionStats = { count: builtCount, summaryCount: builtSummaryCount, idMinimum, idMaximum };
    const summary = accumulator.finish();
    const header = buildPayloadHeader(base, additionStats, summary, inputSha, args.mathdb, manifest, assessmentDate);
    const validation = buildValidation(base, additionStats, summary, header, inputSha, legacyDigestBefore, legacyDigestAfter, errors);
    await fsp.mkdir(path.dirname(path.resolve(args.validation)), { recursive: true });
    if (validation.status !== "pass") { await writeJsonAtomic(args.validation, validation); fail(`Validation failed with ${validation.errors.length} error(s); see ${args.validation}`); }
    await writeGzipPayload(outputTemp, header, base.records, cacheFile);
    validation.outputs.output_path = path.relative(process.cwd(), outputAbs).replaceAll(path.sep, "/");
    validation.outputs.output_sha256 = await sha256File(outputTemp);
    validation.outputs.output_bytes = (await fsp.stat(outputTemp)).size;
    await fsp.rename(outputTemp, outputAbs);
    await writeJsonAtomic(args.validation, validation);
    console.log(JSON.stringify({ status: "pass", output: path.resolve(args.output), validation: path.resolve(args.validation), base_records: base.records.length, mathdb_records: builtCount, records_total: summary.records_total, mathdb_input_sha256: inputSha, output_sha256: validation.outputs.output_sha256 }, null, 2));
  } finally {
    // The exact, validated cache target was created solely for this run. It is
    // removed only after all streams have closed; the canonical output remains.
    if (!cacheClosed) {
      const closed = cacheTarget.closed ? Promise.resolve() : once(cacheTarget, "close").catch(() => {});
      cacheGzip.destroy();
      cacheTarget.destroy();
      await closed;
    }
    await fsp.unlink(cacheFile).catch(() => {});
    await fsp.unlink(outputTemp).catch(() => {});
  }
}

main().catch(error => { console.error(error.stack || error.message); process.exitCode = 1; });
