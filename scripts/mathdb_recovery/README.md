# MathDB statement-recovery ledger

This directory contains the **separate** recovery workflow for turning the
MathDB v1.7 public-list excerpts into citable, self-contained mathematical
statements.  It is intentionally not part of the v1.7 classifier or release
builder: no command here rewrites `Ulam_MathDB_OPDP_Assessments_v1.7.json.gz`,
and no recovered text is scored automatically.

The workflow is designed for a 87,105-item corpus and uses immutable shards
plus an append-only event log:

```text
runs/<run-name>/
  run.json                         immutable source and policy binding
  canonical_tasks/
    task-000000001-000001000.jsonl immutable task shards
    canonical_tasks_manifest.json  hashes and coverage for those shards
  recovery_events.jsonl            append-only, one validated event per line
  recovery_validation.json         derived report; safe to regenerate
```

`runs/` is ignored by Git.  It may contain local source assets and review
state; do not add downloaded paper corpora or unreviewed reconstructed
statements to a release by accident.

## 1. Create the canonical task collection

Start from the frozen, complete MathDB catalog snapshot, not from the
excerpt-derived OPDP payload.  The source JSONL has one
`opdp.mathdb.problem-summary.v1` envelope per MathDB item and preserves the
exact public-list object used by v1.7.

```powershell
$py = 'C:\Users\Propietario\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'

& $py scripts/mathdb_recovery/bootstrap_mathdb_recovery.py `
  --snapshot ..\opdp-v1_7-mathdb-build\mathdb-snapshot-2026-09-08\mathdb_problem_summaries.jsonl `
  --source-manifest ..\opdp-v1_7-mathdb-build\mathdb-snapshot-2026-09-08\mathdb_catalog_manifest.json `
  --run-dir scripts\mathdb_recovery\runs\mathdb-2026-09-08 `
  --resume
```

The bootstrapper streams the snapshot, verifies its hash and catalog count
when a source manifest is supplied, and writes deterministic 1,000-record
shards atomically.  On a restart it byte-checks completed shards and recreates
only the missing/incomplete shard.  A differing source hash, task payload, or
shard contents is an error rather than an implicit mutation.

Each task contains only the canonical MathDB identifiers, title/excerpt,
source-object hash, frozen snapshot metadata, source leads, and a proposed
acquisition route.  The source object itself remains in the immutable catalog
snapshot.  `task_key` is `mathdb:<native-number>`; `task_content_sha256` binds
later events to exactly that task content.

The bootstrapper has `--max-records N` solely for smoke tests.  Such a run is
marked `sample` and must never be used as a complete recovery corpus.

## 2. Deduplicate arXiv acquisition targets (no network)

Before retrieving any paper, derive one source manifest from the canonical
task shards.  It normalizes modern/legacy arXiv IDs and version suffixes,
retains every declared MathDB source URL/field on the target rows, and writes
one source row per identifier plus a task-to-source join table.  URL aliases
such as an ar5iv rendering or an arXiv DOI are provenance leads only; the
subsequent acquisition should use the canonical `arxiv.org/abs/<id>` form via
an approved bulk channel.

```powershell
& $py scripts/mathdb_recovery/build_arxiv_source_manifest.py `
  --run-dir scripts\mathdb_recovery\runs\mathdb-2026-09-08 `
  --resume
```

This produces `arxiv_sources.jsonl`, `arxiv_targets.jsonl`, and an immutable
`arxiv_source_manifest.json`.  The target file deliberately contains no paper
text; it binds each acquisition target to a `task_key` and task hash.

## 3. Acquire arXiv metadata first (no paper text)

The source manifest records 42,371 normalized arXiv identifiers in its final
revision.  Before any source archive is considered, acquire a small,
auditable metadata layer with `acquire_arxiv_metadata.py`.  This worker uses
only the official HTTPS legacy API endpoint
`https://export.arxiv.org/api/query?id_list=...&max_results=...`; it never requests `/pdf/`,
`/src/`, `/e-print/`, HTML, or any other full-text endpoint.

The default is a **dry run with no network access**.  It writes an immutable
request plan with at most 50 IDs per request, an explicit `max_results` equal
to each batch's size (the API otherwise defaults to ten results), exact request URLs, and the
frozen-manifest hashes.  Run the two-ID smoke plan first:

```powershell
& $py scripts/mathdb_recovery/acquire_arxiv_metadata.py `
  --run-dir scripts\mathdb_recovery\runs\mathdb-2026-09-08 `
  --source-manifest scripts\mathdb_recovery\runs\mathdb-2026-09-08\arxiv_source_manifest-final\arxiv_source_manifest.json `
  --output-dir scripts\mathdb_recovery\runs\mathdb-2026-09-08\arxiv_metadata_api-smoke-2 `
  --max-ids 2

& $py scripts/mathdb_recovery/acquire_arxiv_metadata.py `
  --run-dir scripts\mathdb_recovery\runs\mathdb-2026-09-08 `
  --source-manifest scripts\mathdb_recovery\runs\mathdb-2026-09-08\arxiv_source_manifest-final\arxiv_source_manifest.json `
  --output-dir scripts\mathdb_recovery\runs\mathdb-2026-09-08\arxiv_metadata_api-smoke-2 `
  --max-ids 2 --resume --allow-network --max-retries 0
```

When an approved full metadata pass is desired, it requires both explicit
flags below.  The worker is single-threaded, uses `Connection: close`, accepts
no redirects, retains a single active request, and enforces at least three
seconds between request starts.  Thus the 848 batches have a rate-limit lower
bound of roughly 42 minutes; do not use a parallel wrapper around this command.

```powershell
& $py scripts/mathdb_recovery/acquire_arxiv_metadata.py `
  --run-dir scripts\mathdb_recovery\runs\mathdb-2026-09-08 `
  --source-manifest scripts\mathdb_recovery\runs\mathdb-2026-09-08\arxiv_source_manifest-final\arxiv_source_manifest.json `
  --output-dir scripts\mathdb_recovery\runs\mathdb-2026-09-08\arxiv_metadata_api `
  --resume --allow-network --allow-full-run
```

`--resume` verifies every existing response envelope rather than overwriting
it.  Successful batches are immutable `responses/*.json` envelopes containing
the exact base64-encoded Atom XML bytes, their SHA-256 hashes, request URL,
parsed title/abstract/categories/authors, returned version, and any
license/withdrawal fields the API exposes.  `error_ledger.jsonl` is append
only.  `derived_metadata_records.jsonl` is deliberately replaceable: it is a
convenience index regenerated from the immutable response envelopes.

The legacy API often omits a license and does not offer a complete version or
withdrawal history.  The worker represents those absences explicitly rather
than inferring a license or a current open/closed status.  A word such as
“withdrawn” is only recorded as a non-decisive text marker.

This metadata layer is an input to—not a substitute for—the later authorized
S3 source-extraction phase.  That stage must map canonical IDs and returned
versions to `src/arXiv_src_manifest.xml`, obtain a human-approved
Requester-Pays budget, verify the source-archive checksum and rights, stream
one archive at a time, and create exact passage locators.  Metadata title or
abstract agreement alone may not create a `source_matched`,
`recovery_outcome`, openness, or OPDP-rescore event.

## 4. Read-only validate the metadata acquisition

`validate_arxiv_metadata_acquisition.py` independently audits the immutable
plan and every completed response envelope.  It checks the frozen-manifest
binding; the exact official request URL, including `max_results`; batch and ID
coverage; response-body and parsed-metadata hashes; exact Atom-to-parsed-data
reconstruction; metadata-only content declarations; and append-only
error-ledger bindings.  It does **not** write a report, repair an output, or
touch v1.7.  Its only output is JSON on stdout.

Run it after the acquisition writer exits (or against a copied/quiescent
snapshot), because the append-only error ledger can otherwise be observed
mid-append:

```powershell
& $py scripts/mathdb_recovery/validate_arxiv_metadata_acquisition.py `
  --run-dir scripts\mathdb_recovery\runs\mathdb-2026-09-08 `
  --source-manifest scripts\mathdb_recovery\runs\mathdb-2026-09-08\arxiv_source_manifest-final\arxiv_source_manifest.json `
  --metadata-dir scripts\mathdb_recovery\runs\mathdb-2026-09-08\arxiv_metadata_api-v2
```

The status can be `valid_incomplete`: this means the current immutable
evidence is internally sound but not all planned batches have a response yet.
Only `valid_complete` means every planned metadata batch has an audited
envelope.  A nonzero process exit indicates an integrity finding, not merely
an incomplete resumable pass.

## 5. Emit a content-free public acquisition summary (only after completion)

`summarize_arxiv_metadata_acquisition.py` is a separate, read-only public
exporter.  It does **not** copy the replaceable metadata index or summary. It
re-reads only immutable request-plan/response evidence, uses the independent
validator, and emits fixed aggregate counts and SHA-256 bindings.  It never
emits an arXiv ID, request URL, response path, title, abstract, author name,
Atom/XML byte, error message, source passage, or mathematical statement.

Before publishing, it requires a stable quiescent snapshot: the acquisition
lock must be absent both before and after the audit, and a digest covering the
recovery run, source manifest, metadata run, request plan, error ledger, and
all response envelopes must remain unchanged.  A validator warning (including
an unterminated ledger line), integrity error, live/stale lock, or changing
snapshot blocks publication.  `valid_incomplete` can be inspected only with
the explicit stdout-preview flag; it cannot write an artifact.

After the full worker has exited with a clean `valid_complete` audit, produce
the versioned public evidence artifact outside the ignored recovery run:

```powershell
& $py scripts/mathdb_recovery/summarize_arxiv_metadata_acquisition.py `
  --run-dir scripts\mathdb_recovery\runs\mathdb-2026-09-08 `
  --source-manifest scripts\mathdb_recovery\runs\mathdb-2026-09-08\arxiv_source_manifest-final\arxiv_source_manifest.json `
  --metadata-dir scripts\mathdb_recovery\runs\mathdb-2026-09-08\arxiv_metadata_api-v2 `
  --output data\ARXIV_METADATA_ACQUISITION_EVIDENCE_v1.json
```

The command refuses to overwrite a different file and refuses `--output`
unless its own stable-snapshot gate reaches `verified_complete`.  It is an
acquisition-coverage report only: it neither reconstructs problem statements,
checks whether a result remains open, nor recalculates an OPDP field.

## 6. Acquire source material through an approved route

Register acquisition evidence as an append-only event before a matching or
reconstruction event.  For arXiv, use a documented approved bulk channel
(for example, its S3/Kaggle bulk corpus or OAI-PMH metadata), record the asset
hash/version/license information, and keep raw full text outside the release
repository unless its rights permit redistribution.  The task's suggested
route is only a routing hint, not proof of provenance.

The recovery protocol and schemas are in:

- [`../../docs/MATHDB_STATEMENT_RECOVERY_PROTOCOL.md`](../../docs/MATHDB_STATEMENT_RECOVERY_PROTOCOL.md)
- [`schema/recovery-task.schema.json`](schema/recovery-task.schema.json)
- [`schema/recovery-event.schema.json`](schema/recovery-event.schema.json)

### Rights boundary

`recovery_events.jsonl` separates `local_evidence` from
`publishable_statement`.  Local evidence carries an exact URL, source/quote
digests, and reproducible byte/line locator; it must not carry a
`source_quote` or full source text.  `publishable_statement` is a concise,
attributed mathematical restatement suitable for a later curated release, not
an assertion that upstream prose may be redistributed.  Keep authorized TeX,
PDF, and any verbatim quotations in local, access-controlled asset storage and
record only their hashes/locators in the ledger.

## 7. Append a recovery event

Create one JSON object conforming to `schema/recovery-event.schema.json`, then
append it through the guarded writer.  The writer checks the task key and
task hash against the canonical shards, assigns an event UUID if omitted, and
uses an exclusive advisory lock for a single local writer.

```powershell
& $py scripts/mathdb_recovery/append_recovery_event.py `
  --run-dir scripts\mathdb_recovery\runs\mathdb-2026-09-08 `
  --event C:\path\to\event.json
```

The ledger never overwrites an earlier conclusion.  Corrections and
supersessions are new events referring to `supersedes_event_id`; consumers use
the latest valid event of each type only after validation.

## 8. Validate before using any recovery for scoring

```powershell
& $py scripts/mathdb_recovery/validate_recovery_run.py `
  --run-dir scripts\mathdb_recovery\runs\mathdb-2026-09-08 `
  --report scripts\mathdb_recovery\runs\mathdb-2026-09-08\recovery_validation.json
```

Validation streams every canonical shard and every ledger line.  It uses a
temporary SQLite index for task/event joins, so it does not materialize the
87,105-task corpus in memory.  It rejects a rescore recommendation unless the
latest recovery outcome is `exact_source` or `faithful_normalization`, its
statement is declared self-contained, it has an exact source locator, and its
openness status is `verified_open` with the required independent evidence.

Passing that gate means *eligible for a later, separately versioned OPDP
recalculation*.  It does not itself modify a record, certify a proof, or claim
that literature search can prove a negative globally.
