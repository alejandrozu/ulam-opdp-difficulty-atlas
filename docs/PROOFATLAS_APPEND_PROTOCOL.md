# ProofAtlas append protocol (v1.8)

`scripts/build_proofatlas_append_v1_8.py` is an offline, append-only intake
builder. It is deliberately separate from any website acquisition process. It
does not crawl ProofAtlas, bypass access controls, infer a license, or convert a
source-declared status into a verified-open claim.

The builder accepts only a locally frozen JSONL snapshot whose every line uses
`opdp.proofatlas.curated-problem.v1`. Each envelope must bind to the same
snapshot object and include:

- `snapshot.curation_status: "approved_for_opdp_append"` and
  `snapshot.source_name: "ProofAtlas"`;
- a stable positive `problem.namespace_number`, strictly increasing in the
  file;
- a stable `problem.source_id`, title, curator-declared full statement, and
  source attribution; and
- an HTTP(S) snapshot source URL and an HTTP(S) per-problem source URL.

Both URLs must parse with an HTTP(S) scheme and nonempty host; whitespace and
control characters are rejected rather than carried as a purported locator.

## Statement provenance and reuse gate

The builder retains every curated statement in the public release. A missing
license, public URL, or permissive `robots.txt` is **not** permission to copy
source prose. Every frozen snapshot must therefore carry exactly one explicit
`statement_provenance` mode:

- `curator_authored_independent_normalization` — requires a nonempty curator
  attestation that the supplied statement is independently authored and does
  not copy ProofAtlas prose. This is the default path for ProofAtlas discovery
  records when no redistribution clearance is available.
- `source_text_with_declared_rights_clearance` — requires a
  `rights_clearance` object with `status: "declared_cleared"`, plus nonempty
  `basis` and `reference` fields. It is for an actual open license, written
  permission, public-domain basis, or comparable documented authorization.

The builder records the selected mode and any declared clearance in the output,
but does not independently adjudicate legal rights. It rejects every other
mode, including an absent mode or `declared_license: null` without an
independent-normalization attestation.

For the separate Collaboration-directory discovery surface, use the
[collaboration intake protocol](PROOFATLAS_COLLABORATION_INTAKE_PROTOCOL.md)
before presenting a non-linked workspace as a prospective append.

## Crosswalk-audit drafts

`fixtures/proofatlas_top500_match_identity_append_drafts_v1.jsonl` and the
corresponding no-strong-match draft use
`opdp.proofatlas-top500-append-draft.v1`. They are public-safe discovery
artifacts, not builder input: each contains a source ID, rank, formal source
URL, source-published status, compact scope-review label, and an independently
authored one-sentence normalization. Their required
`release_gate: "draft_only_requires_curator_confirmation_before_append"` means
they cannot be passed directly to the v1.8 builder.

To turn a draft into a curated builder record, a curator must independently
confirm the separate scope and source status, assign a stable namespace number,
select an authorized statement-provenance mode, and place the final statement
in an approved `opdp.proofatlas.curated-problem.v1` snapshot with its manifest.
`scripts/assemble_proofatlas_curated_append_v1.py` implements that conversion
for the v1.8 release: it cross-checks frozen metadata and rejects material
reuse against every frozen Top-500 target/question and Collaboration-card
statement held in memory. This conversion must not copy upstream prose unless
the declared-rights path above is satisfied.

For a complete release, the builder also requires a completed
`opdp.proofatlas.curated-snapshot-manifest.v1` whose filename, byte hash,
record count, and snapshot ID agree exactly with the frozen JSONL. `--limit`
is reserved for synthetic fixtures and smoke tests: it requires
`--allow-base-mismatch`, may not use a manifest, and may not target the
canonical v1.7 base. It is the only mode that may omit a manifest.

## Compatibility and identifiers

The v1.7 base is read and copied in a two-pass streaming operation. Canonical
semantic digests of every base record **and the complete top-level metadata**
are checked before the output is committed, so an input change during the build
aborts the output. Existing records are not renumbered, rescored, or
normalized.

ProofAtlas records use a distinct, reversible namespace:

| Field | Rule |
| --- | --- |
| `problem_id` | `50,000,000 + namespace_number` |
| `problem_number` | `PROOFATLAS-{source_id}` |
| `source_record` | The complete frozen curator envelope, retained losslessly |

Source IDs are unique case-insensitively, and generated display identifiers are
checked case-insensitively against the base export before append.

The output uses the existing OPDP record field order and nested dimension
shape. It therefore does not require a consumer-side schema migration.

## Assessment boundary

Until a source-recovery and current-status review occurs, every assigned
ProofAtlas dimension has confidence `C0`; all ten rationales explicitly say so;
and every record has these flags:

- `proofatlas_curated_input`
- `proofatlas_provisional_c0_assessment`
- `source_statement_not_independently_verified`

The builder never emits `verified_open` for a ProofAtlas record. It may retain
the source claim as `source_claimed_open`, or route missing/ambiguous/partial
status to `status_unclear`. This makes the initial append useful for identity
and curation while avoiding a false assertion about a problem's current
mathematical status.

## Example

```powershell
python scripts/build_proofatlas_append_v1_8.py `
  --base data/Ulam_MathDB_OPDP_Assessments_v1.7.json.gz `
  --proofatlas path\to\frozen_proofatlas_curated.jsonl `
  --manifest path\to\frozen_proofatlas_manifest.json `
  --output data/Ulam_MathDB_ProofAtlas_OPDP_Assessments_v1.8.json.gz `
  --validation data/OPDP_v1.8_ProofAtlas_Append_Validation.json
```

## Released instance

The v1.8 release uses a 256-record curator input and manifest under `data/`,
then produces the append-only payload
`Ulam_MathDB_ProofAtlas_OPDP_Assessments_v1.8.json.gz`. The raw ProofAtlas
snapshots remain local evidence rather than repository content; public audit
artifacts retain source URLs, hashes, review decisions, and independent
normalizations. See [the v1.8 append notes](OPDP_v1.8_PROOFATLAS_APPEND_NOTES.md)
for release counts, source hashes, and limitations.
