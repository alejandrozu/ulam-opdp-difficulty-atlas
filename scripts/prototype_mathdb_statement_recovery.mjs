#!/usr/bin/env node
/**
 * Offline, non-destructive prototype for recovering a source statement from
 * one frozen MathDB summary object and one previously acquired source text.
 *
 * It intentionally makes no network calls and never reads or writes an OPDP
 * release payload.  See docs/MATHDB_STATEMENT_RECOVERY_PROTOCOL.md.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const THIS_FILE = fileURLToPath(import.meta.url);
const FIXTURE_DIR = path.resolve(SCRIPT_DIR, "..", "fixtures", "mathdb_statement_recovery");
const RECOVERY_SCHEMA = "opdp.mathdb.statement-recovery.prototype.v1";
const PROTOTYPE_VERSION = "0.1.0";

const STATEMENT_ENVIRONMENTS = new Set([
  "conjecture", "problem", "question", "openproblem", "open-question", "theorem",
]);
const STATEMENT_CUE = /\b(?:conjecture|open\s+problem|open\s+question|question|problem|is\s+there|does\s+(?:every|there)|whether|prove\s+or\s+disprove)\b/i;
const CONTEXT_REFERENCE = /\b(?:condition|equation|definition|lemma|theorem|proposition)\s*(?:\(?\d+(?:\.\d+)*\)?|above|below|preceding|following)\b|\b(?:defined|introduced|proved|stated)\s+(?:above|below|earlier)\b|\b(?:this|that|the following)\s+(?:condition|notation|definition|assumption)\b/i;
const TITLE_STOPWORDS = new Set([
  "a", "an", "and", "are", "for", "from", "in", "is", "of", "on", "or", "the", "to", "with",
  "problem", "question", "conjecture", "open", "synthetic", "mathdb", "erdos", "erdős",
]);

function usage() {
  return `Usage:
  node scripts/prototype_mathdb_statement_recovery.mjs --verify-fixtures
  node scripts/prototype_mathdb_statement_recovery.mjs --fixture <exact_source|faithful_normalization|contextual_reconstruction|unresolved> [--redact-quotes]
  node scripts/prototype_mathdb_statement_recovery.mjs --record <summary.json> --source <source.tex|source.txt> --source-url <canonical-url> [--source-format auto|tex|text] [--redact-quotes] [--output <sidecar.json>]

This prototype performs no network I/O and never modifies data/ artifacts.`;
}

function fail(message) {
  throw new Error(message);
}

function parseArgs(argv) {
  const out = { sourceFormat: "auto" };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--help" || arg === "-h") out.help = true;
    else if (arg === "--verify-fixtures") out.verifyFixtures = true;
    else if (arg === "--redact-quotes") out.redactQuotes = true;
    else if (["--record", "--source", "--source-url", "--source-format", "--output", "--fixture"].includes(arg)) {
      const key = {
        "--record": "record", "--source": "source", "--source-url": "sourceUrl",
        "--source-format": "sourceFormat", "--output": "output", "--fixture": "fixture",
      }[arg];
      const value = argv[i + 1];
      if (!value || value.startsWith("--")) fail(`${arg} requires a value`);
      out[key] = value;
      i += 1;
    } else {
      fail(`Unknown argument ${JSON.stringify(arg)}\n\n${usage()}`);
    }
  }
  return out;
}

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function canonicalJson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
}

function readJsonWithRaw(filename) {
  const raw = fs.readFileSync(filename, "utf8");
  return { raw, value: JSON.parse(raw) };
}

function canonicalUrl(value) {
  if (!value || typeof value !== "string") return null;
  const trimmed = value.trim();
  try {
    const parsed = new URL(trimmed);
    parsed.hash = "";
    const result = parsed.toString();
    return result.endsWith("/") && parsed.pathname === "/" ? result.slice(0, -1) : result;
  } catch {
    return trimmed.replace(/#.*$/, "").replace(/\/$/, "");
  }
}

function collapseWhitespace(value) {
  return String(value ?? "").replace(/\s+/gu, " ").trim();
}

/** Display-only TeX normalization.  It does not expand definitions or make
 * any mathematical inference. */
function texDisplay(value) {
  let output = String(value ?? "").replace(/%[^\r\n]*/gu, " ");
  output = output.replace(/\\(?:begin|end)\s*\{[^}]+\}/giu, " ");
  output = output.replace(/\\(?:label|ref|cite)\s*\{[^}]*\}/giu, " ");
  for (let pass = 0; pass < 4; pass += 1) {
    output = output.replace(/\\(?:textbf|textit|textrm|emph|mathrm|mathbf|mathit|operatorname|mathbb|mathcal|underline)\s*\{([^{}]*)\}/giu, "$1");
  }
  output = output
    .replace(/\\pmod\s*\{([^{}]*)\}/giu, " modulo $1 ")
    .replace(/\\(?:equiv|cong)/giu, " congruent to ")
    .replace(/\\leq?|\\le/giu, " less than or equal to ")
    .replace(/\\geq?|\\ge/giu, " greater than or equal to ")
    .replace(/\\neq/giu, " not equal to ")
    .replace(/\\in/giu, " in ")
    .replace(/\\to/giu, " to ")
    .replace(/\\ldots|\\dots/giu, " … ")
    .replace(/\\[,!;:]/gu, " ")
    .replace(/\\[\[\]]/gu, " ")
    .replace(/[${}]/gu, " ")
    // Preserve an unfamiliar control-sequence name rather than silently
    // deleting mathematical content (for example, \delta becomes "delta").
    .replace(/\\([a-zA-Z]+)\*?/gu, " $1 ");
  return collapseWhitespace(output);
}

function normalizeForMatch(value) {
  return texDisplay(value)
    .normalize("NFKD")
    .replace(/\p{M}/gu, "")
    .toLocaleLowerCase("en-US")
    .replace(/[‐‑‒–—]/gu, "-")
    .replace(/[^a-z0-9]+/gu, " ")
    .trim()
    .replace(/\s+/gu, " ");
}

function tokens(value, { title = false } = {}) {
  const words = normalizeForMatch(value).split(" ").filter(Boolean);
  return title ? words.filter(word => !TITLE_STOPWORDS.has(word) && !/^\d+$/.test(word)) : words;
}

function unique(items) {
  return [...new Set(items)];
}

function tokenRecall(needle, haystack) {
  const n = unique(needle);
  const h = new Set(haystack);
  if (!n.length) return 0;
  return n.filter(word => h.has(word)).length / n.length;
}

function tokenJaccard(left, right) {
  const a = new Set(left), b = new Set(right);
  if (!a.size && !b.size) return 1;
  const intersection = [...a].filter(word => b.has(word)).length;
  return intersection / (a.size + b.size - intersection);
}

/** Ratio of excerpt tokens that occur in a longest common subsequence. */
function lcsCoverage(needle, haystack) {
  const a = needle.slice(0, 300), b = haystack.slice(0, 1600);
  if (!a.length || !b.length) return 0;
  let previous = new Uint16Array(b.length + 1);
  for (const word of a) {
    const current = new Uint16Array(b.length + 1);
    for (let j = 1; j <= b.length; j += 1) {
      current[j] = word === b[j - 1] ? previous[j - 1] + 1 : Math.max(previous[j], current[j - 1]);
    }
    previous = current;
  }
  return previous[b.length] / a.length;
}

function rounded(value) {
  return Number(value.toFixed(4));
}

function excerptFragment(excerpt) {
  const raw = String(excerpt ?? "");
  const match = /(?:…|\.\.\.|\\(?:ldots|dots))/iu.exec(raw);
  return {
    excerpt_has_ellipsis: Boolean(match),
    non_ellipsis_prefix: (match ? raw.slice(0, match.index) : raw).trim(),
  };
}

function makeLineMap(source) {
  const lines = [];
  let cursor = 0;
  let number = 1;
  while (cursor < source.length) {
    let contentEnd = cursor;
    while (contentEnd < source.length && source[contentEnd] !== "\r" && source[contentEnd] !== "\n") contentEnd += 1;
    let next = contentEnd;
    if (source[next] === "\r" && source[next + 1] === "\n") next += 2;
    else if (source[next] === "\r" || source[next] === "\n") next += 1;
    lines.push({ number, text: source.slice(cursor, contentEnd), startChar: cursor, endChar: contentEnd });
    cursor = next;
    number += 1;
  }
  if (!lines.length) lines.push({ number: 1, text: "", startChar: 0, endChar: 0 });
  return lines;
}

function byteOffset(source, characterOffset) {
  return Buffer.byteLength(source.slice(0, characterOffset), "utf8");
}

function locatorForRange({ source, lines, sourceUrl, artifactSha256, startIndex, endIndex, extractionMethod }) {
  const start = lines[startIndex];
  const end = lines[endIndex];
  const quote = source.slice(start.startChar, end.endChar);
  return {
    locator_scheme: "utf8_byte_range_plus_one_based_lines.v1",
    source_url: sourceUrl,
    artifact_sha256: artifactSha256,
    extraction_method: extractionMethod,
    line_start: start.number,
    line_end: end.number,
    byte_start: byteOffset(source, start.startChar),
    byte_end_exclusive: byteOffset(source, end.endChar),
    quote_sha256: sha256(quote),
  };
}

function sectionHeadingBefore(lines, index, source, sourceUrl, artifactSha256) {
  for (let i = index - 1; i >= 0 && i >= index - 160; i -= 1) {
    const tex = /\\(?:sub)*section\*?\s*\{([^}]*)\}/iu.exec(lines[i].text);
    const markdown = /^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$/u.exec(lines[i].text);
    if (tex || markdown) {
      const text = tex ? tex[1] : markdown[1];
      return {
        text: texDisplay(text),
        locator: locatorForRange({
          source, lines, sourceUrl, artifactSha256, startIndex: i, endIndex: i,
          extractionMethod: tex ? "nearest_tex_section_heading" : "nearest_markdown_heading",
        }),
      };
    }
  }
  return null;
}

function precedingTextBlock(lines, index, source, sourceUrl, artifactSha256) {
  let endIndex = index - 1;
  while (endIndex >= 0 && !lines[endIndex].text.trim()) endIndex -= 1;
  if (endIndex < 0) return null;
  let startIndex = endIndex;
  while (startIndex > 0 && lines[startIndex - 1].text.trim()) startIndex -= 1;
  const raw = source.slice(lines[startIndex].startChar, lines[endIndex].endChar);
  if (!raw.trim()) return null;
  return {
    source_quote: raw,
    display_text: texDisplay(raw),
    source_locator: locatorForRange({
      source, lines, sourceUrl, artifactSha256, startIndex, endIndex,
      extractionMethod: "preceding_nonblank_text_block",
    }),
  };
}

function candidateFromRange({ source, lines, sourceUrl, artifactSha256, startIndex, endIndex, kind }) {
  const sourceQuote = source.slice(lines[startIndex].startChar, lines[endIndex].endChar);
  const displayText = texDisplay(sourceQuote);
  const heading = sectionHeadingBefore(lines, startIndex, source, sourceUrl, artifactSha256);
  const contextReference = CONTEXT_REFERENCE.test(displayText);
  return {
    kind,
    source_quote: sourceQuote,
    display_text: displayText,
    statement_cue_detected: STATEMENT_CUE.test(displayText) || kind.startsWith("tex_environment:"),
    context_reference_detected: contextReference,
    heading,
    preceding_context: contextReference ? precedingTextBlock(lines, startIndex, source, sourceUrl, artifactSha256) : null,
    source_locator: locatorForRange({
      source, lines, sourceUrl, artifactSha256, startIndex, endIndex, extractionMethod: kind,
    }),
  };
}

function extractCandidates({ source, sourceUrl, artifactSha256, format }) {
  const lines = makeLineMap(source);
  const candidates = [];
  const occupied = new Set();
  if (format === "tex") {
    for (let i = 0; i < lines.length; i += 1) {
      const begin = /\\begin\s*\{([^}]+)\}/iu.exec(lines[i].text);
      if (!begin) continue;
      const environment = begin[1].toLocaleLowerCase("en-US").trim();
      if (!STATEMENT_ENVIRONMENTS.has(environment)) continue;
      const endPattern = new RegExp(`\\\\end\\s*\\{${environment.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&")}\\}`, "iu");
      let endIndex = i;
      while (endIndex < lines.length && !endPattern.test(lines[endIndex].text)) endIndex += 1;
      if (endIndex >= lines.length) continue;
      candidates.push(candidateFromRange({
        source, lines, sourceUrl, artifactSha256, startIndex: i, endIndex,
        kind: `tex_environment:${environment}`,
      }));
      for (let j = i; j <= endIndex; j += 1) occupied.add(j);
      i = endIndex;
    }
  }

  for (let index = 0; index < lines.length;) {
    while (index < lines.length && (!lines[index].text.trim() || occupied.has(index))) index += 1;
    if (index >= lines.length) break;
    const startIndex = index;
    let endIndex = index;
    while (endIndex + 1 < lines.length && lines[endIndex + 1].text.trim() && !occupied.has(endIndex + 1)) endIndex += 1;
    const raw = source.slice(lines[startIndex].startChar, lines[endIndex].endChar);
    const display = texDisplay(raw);
    if (STATEMENT_CUE.test(display)) {
      candidates.push(candidateFromRange({
        source, lines, sourceUrl, artifactSha256, startIndex, endIndex, kind: format === "tex" ? "tex_text_block" : "plain_text_block",
      }));
    }
    index = endIndex + 1;
  }
  return candidates;
}

function getDeclaredSourceUrls(problem) {
  const fields = [
    "documented_by_source_url", "first_stated_year_source_url", "source_url", "reference_url",
  ];
  return fields
    .filter(field => typeof problem?.[field] === "string" && problem[field].trim())
    .map(field => ({ field, url: problem[field], canonical_url: canonicalUrl(problem[field]) }));
}

function sourceBinding(problem, sourceUrl) {
  const declared = getDeclaredSourceUrls(problem);
  const canonicalSourceUrl = canonicalUrl(sourceUrl);
  const matched = declared.filter(item => item.canonical_url === canonicalSourceUrl);
  return {
    supplied_source_url: sourceUrl,
    canonical_source_url: canonicalSourceUrl,
    declared_by_mathdb: matched.length > 0,
    binding_status: matched.length ? "declared_by_mathdb" : declared.length ? "mismatch_to_declared_summary_url" : "not_declared_in_summary",
    matching_declarations: matched,
    all_summary_source_declarations: declared,
  };
}

function scoreCandidate(candidate, problem) {
  const titleWords = tokens(problem.title, { title: true });
  const matchingText = `${candidate.heading?.text ?? ""} ${candidate.display_text}`;
  const candidateTitleWords = tokens(matchingText, { title: true });
  const excerpt = excerptFragment(problem.excerpt);
  const excerptWords = tokens(problem.excerpt);
  const normalizedPrefix = normalizeForMatch(excerpt.non_ellipsis_prefix);
  const prefixWords = tokens(excerpt.non_ellipsis_prefix);
  const rawPrefix = collapseWhitespace(excerpt.non_ellipsis_prefix);
  const rawPrefixContained = prefixWords.length >= 6 && collapseWhitespace(candidate.source_quote).includes(rawPrefix);
  const normalizedPrefixContained = prefixWords.length >= 6 && Boolean(normalizedPrefix) && normalizeForMatch(candidate.display_text).includes(normalizedPrefix);
  const excerptCoverage = lcsCoverage(excerptWords, tokens(candidate.display_text));
  const titleRecall = tokenRecall(titleWords, candidateTitleWords);
  const titleJaccard = tokenJaccard(titleWords, candidateTitleWords);
  const rank = (0.35 * titleRecall) + (0.55 * Math.max(excerptCoverage, normalizedPrefixContained ? 1 : 0)) + (0.10 * Number(candidate.statement_cue_detected));
  return {
    title_token_recall: rounded(titleRecall),
    title_token_jaccard: rounded(titleJaccard),
    excerpt_token_lcs_coverage: rounded(excerptCoverage),
    excerpt_has_ellipsis: excerpt.excerpt_has_ellipsis,
    excerpt_fragment_token_count: prefixWords.length,
    literal_non_ellipsis_excerpt_fragment_contained: rawPrefixContained,
    normalized_non_ellipsis_excerpt_fragment_contained: normalizedPrefixContained,
    composite_rank: rounded(rank),
  };
}

function publicCandidate(candidate, index, problem) {
  const match = scoreCandidate(candidate, problem);
  return {
    candidate_id: `candidate-${String(index + 1).padStart(4, "0")}`,
    passage_kind: candidate.kind,
    source_quote: candidate.source_quote,
    display_text: candidate.display_text,
    source_locator: candidate.source_locator,
    nearest_heading: candidate.heading,
    statement_cue_detected: candidate.statement_cue_detected,
    context_reference_detected: candidate.context_reference_detected,
    preceding_context: candidate.preceding_context,
    match: match,
  };
}

function classify(candidates, binding) {
  const best = candidates[0] ?? null;
  if (!best) {
    return { label: "unresolved", best: null, reasons: ["No theorem, conjecture, problem, question, or open-question passage was extracted from the supplied artifact."] };
  }
  const match = best.match;
  const specificMatch = match.title_token_recall >= 0.30 && (
    match.normalized_non_ellipsis_excerpt_fragment_contained || match.excerpt_token_lcs_coverage >= 0.86
  );
  if (!binding.declared_by_mathdb) {
    return {
      label: "unresolved", best,
      reasons: ["The supplied artifact is not bound by a source URL explicitly present in the frozen MathDB summary.", `Best candidate composite rank: ${match.composite_rank}.`],
    };
  }
  if (!specificMatch) {
    return {
      label: "unresolved", best,
      reasons: ["The best passage does not meet the conservative title-plus-excerpt specificity threshold.", `Best candidate composite rank: ${match.composite_rank}.`],
    };
  }
  if (best.context_reference_detected) {
    return {
      label: "contextual_reconstruction", best,
      reasons: ["The matched passage refers to external context; a self-contained formulation would require an explicitly labelled multi-passage reconstruction."],
    };
  }
  if (match.literal_non_ellipsis_excerpt_fragment_contained) {
    return {
      label: "exact_source", best,
      reasons: ["The declared source contains the literal non-ellipsis MathDB excerpt fragment in a self-contained statement passage."],
    };
  }
  if (match.normalized_non_ellipsis_excerpt_fragment_contained || match.excerpt_token_lcs_coverage >= 0.86) {
    return {
      label: "faithful_normalization", best,
      reasons: ["The declared source has a high-specificity self-contained passage, but correspondence depends on logged presentation-only TeX/plain-text normalization."],
    };
  }
  return { label: "unresolved", best, reasons: ["No recovery label could be justified by the available evidence."] };
}

function buildContextualProposal(best) {
  if (!best?.preceding_context) {
    return {
      state: "context_missing",
      note: "The statement references context, but no immediately preceding text block was captured. A curator must locate the referenced material.",
      constituent_locators: [best?.source_locator].filter(Boolean),
    };
  }
  return {
    state: "proposed_for_human_review_only",
    note: "This is an explicit assembly of adjacent source passages, not a verbatim source statement and not eligible for automatic scoring.",
    proposed_display_text: `[Context] ${best.preceding_context.display_text}\n\n[Statement] ${best.display_text}`,
    constituent_locators: [best.preceding_context.source_locator, best.source_locator],
  };
}

function resolveSourceFormat(requested, sourceFile) {
  if (["tex", "text"].includes(requested)) return requested;
  if (requested !== "auto") fail(`Unsupported --source-format ${JSON.stringify(requested)}; use auto, tex, or text.`);
  return /\.(?:tex|ltx|latex)$/iu.test(sourceFile) ? "tex" : "text";
}

/** Produce a locator-only sidecar suitable for a public ledger.  The matching
 * happened locally; only hashes, offsets, and non-source-derived metrics are
 * emitted.  MathDB's own public title/excerpt remain in the input identity. */
function redactQuotedSourceText(result) {
  const redacted = structuredClone(result);
  for (const candidate of redacted.candidate_extraction.candidates) {
    delete candidate.source_quote;
    delete candidate.display_text;
    candidate.source_text_redacted = true;
    if (candidate.nearest_heading) {
      delete candidate.nearest_heading.text;
      candidate.nearest_heading.source_text_redacted = true;
    }
    if (candidate.preceding_context) {
      delete candidate.preceding_context.source_quote;
      delete candidate.preceding_context.display_text;
      candidate.preceding_context.source_text_redacted = true;
    }
  }
  if (redacted.recovery.contextual_reconstruction) {
    delete redacted.recovery.contextual_reconstruction.proposed_display_text;
    redacted.recovery.contextual_reconstruction.source_text_redacted = true;
  }
  redacted.quote_handling = {
    mode: "redacted_public_locator_only",
    source_quotes_emitted: false,
    note: "Source-derived quotation and reconstructed display text were removed. Artifact and quote hashes plus exact locators remain for an authorized verifier.",
  };
  return redacted;
}

function recover({ recordRaw, recordValue, recordPath, source, sourcePath, sourceUrl, sourceFormat, redactQuotes = false }) {
  const envelope = recordValue?.problem && typeof recordValue.problem === "object" ? recordValue : { problem: recordValue };
  const problem = envelope.problem;
  if (!problem || typeof problem !== "object") fail("The record must be a MathDB problem object or an envelope containing problem.");
  for (const field of ["number", "title", "excerpt"]) {
    if (problem[field] === undefined || problem[field] === null || String(problem[field]).trim() === "") fail(`MathDB summary is missing required ${field}.`);
  }
  const resolvedFormat = resolveSourceFormat(sourceFormat, sourcePath);
  const sourceArtifactSha256 = sha256(source);
  const binding = sourceBinding(problem, sourceUrl);
  const candidates = extractCandidates({ source, sourceUrl, artifactSha256: sourceArtifactSha256, format: resolvedFormat })
    .map((candidate, index) => publicCandidate(candidate, index, problem))
    .sort((left, right) => right.match.composite_rank - left.match.composite_rank || left.source_locator.byte_start - right.source_locator.byte_start);
  const classified = classify(candidates, binding);
  const contextualProposal = classified.label === "contextual_reconstruction" ? buildContextualProposal(classified.best) : null;
  const recoveryIsReliable = ["exact_source", "faithful_normalization"].includes(classified.label);
  const result = {
    schema: RECOVERY_SCHEMA,
    prototype_version: PROTOTYPE_VERSION,
    output_kind: "candidate_recovery_sidecar_not_production_ledger_event",
    production_ledger_event: false,
    non_destructive: true,
    input: {
      mathdb_summary: {
        schema: envelope.schema ?? null,
        number: problem.number,
        uuid: problem.id ?? null,
        title: problem.title,
        excerpt: problem.excerpt,
        native_status: problem.status ?? null,
        frozen_summary_sha256: sha256(recordRaw),
        canonicalized_summary_sha256: sha256(canonicalJson(recordValue)),
        local_basename: path.basename(recordPath),
        snapshot_inventory_url: envelope.snapshot?.inventory_url ?? null,
      },
      source_artifact: {
        canonical_url: sourceUrl,
        local_basename: path.basename(sourcePath),
        source_format: resolvedFormat,
        artifact_sha256: sourceArtifactSha256,
        byte_length: Buffer.byteLength(source, "utf8"),
      },
    },
    source_binding: binding,
    candidate_extraction: {
      method_version: "heuristic-tex-or-text-candidate-extractor.v1",
      candidate_count: candidates.length,
      candidates,
    },
    recovery: {
      label: classified.label,
      label_reasons: classified.reasons,
      selected_candidate_id: classified.best?.candidate_id ?? null,
      contextual_reconstruction: contextualProposal,
      transformations: classified.label === "faithful_normalization"
        ? ["display-only TeX/plain-text normalization; verbatim source quote retained"]
        : classified.label === "contextual_reconstruction"
          ? ["explicit multi-passage assembly proposed for human review; no source quote is silently replaced"]
          : [],
    },
    current_open_verification: {
      state: "not_checked",
      native_mathdb_status: problem.status ?? null,
      note: "A recovered statement is not evidence that the problem remains open. Independent status verification is required.",
    },
    opdp_recalculation_gate: {
      statement_recovery_is_sufficiently_reliable: recoveryIsReliable,
      current_open_status_is_verified: false,
      curator_signoff_present: false,
      eligible_for_automatic_recalculation: false,
      next_required_step: recoveryIsReliable
        ? "Independently verify present open status and obtain curator sign-off before scheduling a separate OPDP recalculation."
        : "Retain the excerpt-only C0 assessment; resolve statement evidence before considering recalculation.",
    },
    quote_handling: {
      mode: "local_evidence_with_verbatim_source_quotes",
      source_quotes_emitted: true,
      note: "This mode is for local evidence review. Check the underlying source license before publishing or redistributing source-derived text.",
    },
  };
  return redactQuotes ? redactQuotedSourceText(result) : result;
}

function fixtureSourceUrl(recordValue) {
  const problem = recordValue.problem ?? recordValue;
  return problem.documented_by_source_url ?? problem.first_stated_year_source_url;
}

function runFixture(name, redactQuotes = false) {
  const recordPath = path.join(FIXTURE_DIR, `${name}.record.json`);
  const texPath = path.join(FIXTURE_DIR, `${name}.tex`);
  const textPath = path.join(FIXTURE_DIR, `${name}.txt`);
  const sourcePath = fs.existsSync(texPath) ? texPath : textPath;
  if (!fs.existsSync(recordPath) || !fs.existsSync(sourcePath)) fail(`Unknown or incomplete fixture ${JSON.stringify(name)}.`);
  const record = readJsonWithRaw(recordPath);
  return recover({
    recordRaw: record.raw,
    recordValue: record.value,
    recordPath,
    source: fs.readFileSync(sourcePath, "utf8"),
    sourcePath,
    sourceUrl: fixtureSourceUrl(record.value),
    sourceFormat: "auto",
    redactQuotes,
  });
}

function verifyFixtures() {
  const expected = JSON.parse(fs.readFileSync(path.join(FIXTURE_DIR, "expected_labels.json"), "utf8"));
  const results = [];
  let failures = 0;
  for (const [name, expectedLabel] of Object.entries(expected)) {
    const result = runFixture(name);
    const redacted = runFixture(name, true);
    const actual = result.recovery.label;
    const locator = result.candidate_extraction.candidates[0]?.source_locator;
    const hasLocator = Boolean(locator?.artifact_sha256 && Number.isInteger(locator.line_start) && Number.isInteger(locator.byte_start));
    const scoreGateIsClosed = result.current_open_verification.state === "not_checked"
      && result.opdp_recalculation_gate.eligible_for_automatic_recalculation === false;
    const contextualAssemblyIsExplicit = actual !== "contextual_reconstruction"
      || Boolean(result.recovery.contextual_reconstruction?.constituent_locators?.length >= 1);
    const redactedTextIsAbsent = !containsSourceTextFields(redacted);
    const passed = actual === expectedLabel && hasLocator && result.source_binding.declared_by_mathdb
      && scoreGateIsClosed && contextualAssemblyIsExplicit && redactedTextIsAbsent;
    if (!passed) failures += 1;
    results.push({ fixture: name, expected_label: expectedLabel, actual_label: actual, has_locator: hasLocator, score_gate_is_closed: scoreGateIsClosed, redacted_text_is_absent: redactedTextIsAbsent, passed });
  }
  const report = { status: failures ? "fail" : "pass", fixtures: results.length, failures, results };
  console.log(JSON.stringify(report, null, 2));
  if (failures) process.exitCode = 1;
}

function containsSourceTextFields(value) {
  const forbidden = new Set(["source_quote", "display_text", "proposed_display_text"]);
  if (Array.isArray(value)) return value.some(containsSourceTextFields);
  if (!value || typeof value !== "object") return false;
  return Object.entries(value).some(([key, child]) => forbidden.has(key) || containsSourceTextFields(child));
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(usage());
    return;
  }
  if (args.verifyFixtures) {
    if (args.record || args.source || args.fixture) fail(`--verify-fixtures cannot be combined with record/source/fixture input.\n\n${usage()}`);
    verifyFixtures();
    return;
  }
  if (args.fixture) {
    if (args.record || args.source || args.output) fail(`--fixture cannot be combined with record/source/output input.\n\n${usage()}`);
    console.log(JSON.stringify(runFixture(args.fixture, args.redactQuotes), null, 2));
    return;
  }
  if (!args.record || !args.source || !args.sourceUrl) fail(`${usage()}\n\n--record, --source, and --source-url are required.`);
  const record = readJsonWithRaw(args.record);
  const result = recover({
    recordRaw: record.raw,
    recordValue: record.value,
    recordPath: args.record,
    source: fs.readFileSync(args.source, "utf8"),
    sourcePath: args.source,
    sourceUrl: args.sourceUrl,
    sourceFormat: args.sourceFormat,
    redactQuotes: args.redactQuotes,
  });
  const rendered = `${JSON.stringify(result, null, 2)}\n`;
  if (args.output) fs.writeFileSync(args.output, rendered, "utf8");
  else process.stdout.write(rendered);
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(THIS_FILE)) {
  try {
    main();
  } catch (error) {
    console.error(`Statement-recovery prototype failed: ${error.message}`);
    process.exitCode = 1;
  }
}

export { recover, redactQuotedSourceText };
