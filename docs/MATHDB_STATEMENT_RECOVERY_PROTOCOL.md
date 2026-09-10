# MathDB statement-recovery protocol (prototype)

## Purpose and scope

This document specifies a conservative bridge between an incomplete MathDB
catalogue entry and a recoverable source statement.  It is intentionally a
*recovery* procedure, not a semantic search procedure: an apparent topical
match must not become a new OPDP statement or score simply because it sounds
plausible.

The accompanying `scripts/prototype_mathdb_statement_recovery.mjs` is an
offline, fixture-tested proof of the data contract.  It neither calls MathDB
or arXiv nor changes the v1.7 corpus.  It takes one frozen MathDB summary and
one already-acquired source text and emits ranked statement candidates,
immutable locators, recovery labels, and scoring gates.

It is deliberately **not** a production recovery-ledger writer.  Its output
sets `output_kind = candidate_recovery_sidecar_not_production_ledger_event`
and `production_ledger_event = false`.  A later, separately validated ledger
adapter must bind the selected evidence to a canonical task and create any
append-only event; this prototype does not do so.

## Programmatic interface

The command is deterministic and emits exactly one JSON sidecar to stdout
unless `--output` is supplied.  It has no network dependency and returns a
nonzero exit code for malformed input.  Its stable inputs are a JSON MathDB
problem object or `opdp.mathdb.problem-summary.v1` envelope, an acquired
plain-text or TeX file, and the source artifact's canonical URL:

```text
node scripts/prototype_mathdb_statement_recovery.mjs \
  --record SUMMARY.json --source SOURCE.tex --source-url CANONICAL_URL \
  --source-format tex --redact-quotes --output SIDECAR.json
```

For a Node caller, the module also exports `recover` and
`redactQuotedSourceText`; command-line use is preferred for batch workers
because it makes the artifact paths and source URL explicit.  Neither API
fetches a source or writes an OPDP record.

## Why recovery is necessary

The v1.7 frozen public-list capture has 87,105 records.  A local streaming
audit of `mathdb_problem_summaries.jsonl` found a nonempty title and excerpt
on every record, but the excerpt is capped at 182 characters (median 180).
It therefore cannot generally identify a full statement by itself.  The same
audit found `first_stated_year_source_url` for 55,149 entries and
`documented_by_source_url` for 13,640; these are useful source *leads*, not
proof that a particular passage is the intended problem.

In particular, title similarity alone is insufficient.  MathDB titles can be
editorial summaries, a source can contain several related conjectures, and
an excerpt may begin or end in the middle of a definition.  The procedure
preserves the original list item and records every inference separately.

## Required provenance chain

Each recovery result must retain the following pieces of evidence.

1. **Frozen catalogue identity.**  MathDB number, UUID when present, title,
   exact public-list excerpt, source-envelope schema, and a SHA-256 hash of
   the frozen summary object.
2. **Source binding.**  The source URL asserted by MathDB, if any, and the
   exact field that asserted it (`documented_by_source_url`,
   `first_stated_year_source_url`, or another named field).  A discovered URL
   that was not asserted by MathDB is explicitly `not_declared_in_summary`.
3. **Retrieved artifact.**  Canonical URL, retrieval timestamp, content type,
   artifact SHA-256, and—where applicable—arXiv identifier, version, and
   source-file path.  An arXiv record must come from the approved bulk
   snapshot and retain the bulk-snapshot manifest/hash; do not infer arXiv
   provenance from topical text.
4. **Passage locator.**  At minimum, a UTF-8 byte range with end-exclusive
   semantics, one-based source line range, source-artifact digest, passage
   quote digest, and extraction method.  For PDF-only recovery, add page and
   bounding-box/character-offset information.  A page number alone is not
   reproducible enough.
5. **Transformation ledger.**  The verbatim source quote is retained beside
   any whitespace or TeX-display normalization.  Contextual assembly lists
   every contributing passage and never silently replaces the quote.

This makes it possible to re-run extraction against the exact artifact and
to distinguish a source quotation from an editor's reconstruction.

## Local evidence versus publishable ledgers

An acquired arXiv or publisher artifact can be suitable for local text
analysis without giving OPDP a right to redistribute the paper's prose or
source.  The prototype's default sidecar contains source quotes for local
curation only.  Before public release, check the individual work's license
and use the redacted mode unless publication of the quoted material is
clearly permitted.

`--redact-quotes` produces a locator-only sidecar: it removes the verbatim
source quote, normalized display text, heading text, adjacent context, and
any proposed reconstructed display statement.  It retains the artifact hash,
quote hash, source URL, line/byte locator, extraction method, matching
metrics, recovery label, and scoring/status gates.  An authorized reviewer
can therefore reproduce the evidence without the public atlas distributing
the underlying text.

## Candidate extraction and matching

The prototype extracts likely statement passages from TeX environments
(`conjecture`, `problem`, `question`, `openproblem`, and `theorem`) and from
plain-text paragraphs containing an open-problem cue.  It keeps the nearest
section heading as supporting context.  This is deliberately a candidate
generator, not a parser of mathematical meaning.

For each candidate it reports:

- title-token recall and title-token Jaccard similarity;
- whether the non-ellipsis prefix of the MathDB excerpt occurs literally in
  the source quote;
- whether it occurs after strictly presentational TeX/plain-text
  normalization;
- excerpt token-LCS coverage and a composite ranking score; and
- cue and context-dependence flags.

The ranking is only for review order.  A low score cannot establish
non-identity, and a high score cannot establish equivalence of mathematical
claims.  Math notation is deliberately not rewritten into new semantics by
the automated pass.

## Recovery labels

| Label | Minimum evidence | What is stored | OPDP scoring consequence |
| --- | --- | --- | --- |
| `exact_source` | A MathDB-declared source URL binds the acquired artifact; a self-contained source passage has the literal non-truncated excerpt fragment and a compatible title/cue. | Verbatim passage and immutable locator. | May proceed to human/open-status verification. |
| `faithful_normalization` | Same declared binding and high match, but the correspondence requires only explicitly logged presentation changes such as TeX markup, whitespace, typographic punctuation, or notation rendering. | Verbatim passage plus normalized display text and transformation ledger. | May proceed to human/open-status verification. |
| `contextual_reconstruction` | A strongly matching passage is context-dependent, or multiple cited passages are needed to make the variables, quantifiers, or referenced conditions intelligible. | Each source quote/locator and an explicitly labelled proposed assembly. | Not eligible for automatic OPDP recalculation; requires curator approval. |
| `unresolved` | No sufficiently specific, source-bound candidate. | Ranking evidence and failure reasons, never an invented statement. | No recalculation; retain v1.7 excerpt-only record. |

The labels describe evidence quality, not mathematical difficulty or truth.
`exact_source` is still not a claim that the problem is currently open.

### Automated thresholds in the prototype

The prototype uses conservative, deliberately visible guards rather than a
hidden model score:

- A source must match a URL explicitly present in the MathDB summary before
  it can receive `exact_source` or `faithful_normalization`.
- The candidate must look like a statement passage and have title-token
  recall of at least 0.30 plus either normalized excerpt containment or at
  least 0.86 excerpt token-LCS coverage.
- `exact_source` additionally requires literal (whitespace-collapsed)
  containment of a non-ellipsis excerpt fragment of at least six tokens.
- `faithful_normalization` requires the same substantive match after the
  logged presentation-only normalizer and no unresolved contextual reference.
- A reference such as “condition (4.2)”, “defined above”, or “the following
  notation” blocks the first two labels and yields at most
  `contextual_reconstruction`.

These are triage defaults, not statistical confidence estimates.  A future
production pipeline should tune them on curator-labelled positives and
negatives, retain threshold/version metadata, and sample every automated
acceptance for review.

## Current-open verification gate

Statement recovery and open-status verification are separate operations.  A
record can receive a strong statement label while being solved, refuted, or
obsolete.  Before any OPDP dimensions are recalculated, a reviewer must
record:

1. the source's date/version and its stated status;
2. a current, independent status check appropriate to the field (for example,
   a cited resolution, erratum, or authoritative problem-list update);
3. the evidence URLs, access dates, and a reasoned `verified_open`,
   `verified_closed`, `status_uncertain`, or `not_checked` outcome; and
4. whether the recovered formulation is the same claim as the catalogue
   item, rather than merely a related special case or strengthening.

MathDB's native `open` status is useful evidence but is not by itself a
current-literature verification.  The prototype always writes
`current_open_verification.state = "not_checked"` and makes no scoring
change.

## Recalculation gate

Only a recovery labelled `exact_source` or `faithful_normalization`, followed
by a documented `verified_open` outcome and curator sign-off, may enter a
separate OPDP rescoring queue.  The queue must preserve both the v1.7
excerpt-derived record and the recovery sidecar, record the rubric version,
and state exactly which dimensions were recomputed.  No legacy record should
be overwritten in place.

`contextual_reconstruction` and `unresolved` remain C0/excerpt-only.  This
prevents an attractive reconstructed statement from silently receiving a
more confident difficulty profile.

## Using approved arXiv bulk data

At scale, source acquisition should first build a source manifest from
explicit identifiers/URLs, then retrieve only through arXiv's approved bulk
mechanisms and retain their release metadata.  Process each artifact once,
extract all candidate passages, and link multiple MathDB records to the same
artifact digest.  Do not use repeated per-record API calls, rotate identity
or network controls, or treat a search-result hit as source provenance.

The expected production flow is:

1. freeze MathDB catalogue records and build a deduplicated, evidence-ranked
   source manifest;
2. acquire the authorized source corpus with checksums and snapshot metadata;
3. run this candidate generator source-by-source;
4. have a curator adjudicate `contextual_reconstruction` and borderline
   matches; and
5. independently audit current status before a separately versioned OPDP
   recalculation.

## Reproducible fixture check

From the repository root, run:

```powershell
node scripts/prototype_mathdb_statement_recovery.mjs --verify-fixtures
```

The fixtures exercise all four recovery labels using synthetic source text;
they intentionally perform no network I/O and do not change `data/`.

For an acquired text artifact, use an explicit output path outside the frozen
release data, for example:

```powershell
node scripts/prototype_mathdb_statement_recovery.mjs `
  --record path\\to\\mathdb-summary-envelope.json `
  --source path\\to\\source.tex `
  --source-url "https://example.org/source.tex" `
  --source-format tex `
  --redact-quotes `
  --output path\\to\\recovery-sidecar.json
```

The output is a sidecar prototype and must be reviewed before use in an OPDP
release.  Omit `--redact-quotes` only for a local evidence file that will not
be published without a source-specific rights determination.
