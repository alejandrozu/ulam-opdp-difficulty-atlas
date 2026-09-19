# OPDP v1.8 ProofAtlas append-only notes

## Scope

v1.8 appends **256** curated ProofAtlas discovery records to the immutable
102,563-record v1.7 export, for **102,819** OPDP records. It is a narrow,
source-linked intake release: the records are candidates determined to be
separately scoped relative to the frozen v1.7 atlas after a documented identity
review. It is not an independent confirmation that each item remains open.

| Discovery surface | Inspected | Appended | Stable ID rule |
| --- | ---: | ---: | --- |
| ProofAtlas Top 500 v16 | 500 | 208 | 50,000,000 + releaseRank |
| ProofAtlas Collaboration directory | 268 cards | 48 | 50,000,000 + 1,000,000 + frozenCardOrdinal |
| **Total** | **768 source entries** | **256** | distinct reversible namespaces |

The Top 500 frozen source was
[top500-v16-science-v1.json](https://www.proofatlas.ai/data/open-problems/top500-v16-science-v1.json),
retrieved 2026-09-19 (SHA-256
0f9c5a99e82951111a30cc0008e89bbddb37c1d06ec6266d499e366cb13286d6).
The collaboration source was the public
[/collaboration/](https://www.proofatlas.ai/collaboration/) directory,
retrieved the same day (SHA-256
9066cafad3630924e7172640331489b87597d6c5df3c26e095e2d92d33c044a7).

## What was compared

The review did not treat a string non-match as novelty. Each candidate was
compared against the complete frozen v1.7 payload using title, statement scope,
and the relevant source locator. Decisions distinguish the same problem,
a differently scoped variant or subproblem, related-but-not-identical targets,
and unresolved cases. Only a separate target with a source-published open
status was eligible for this append; unresolved and source-closed entries were
not appended.

For the Top 500, the initial fixed-presentation crosswalk had 5 exact matches,
195 strong matches, 54 ambiguous matches, and 246 no-strong-match abstentions.
The subsequent human scope review retained 13 separately scoped targets from
the ambiguous set, 72 from the lower-ranked no-strong-match review, 60 from the
higher-ranked no-strong-match review, and 63 from an audit of the exact/strong
candidate pool. These contributions total 208.

For the collaboration directory, 125 of 268 workspaces were directly linked by
the Top 500 data and 143 were not. The initial crosswalk classified 142 as the
same v1.7 problem, 20 as variants/subproblems, 96 as related-but-not-identical,
4 as no-strong-match candidates, and 6 as unresolved. The complete review of
the nontrivial set produced 48 separately scoped, source-published-open targets
for the append. A route relation was treated as provenance evidence, not proof
that two formulations were mathematically identical.

The machine-readable audit ledgers are deliberately split by review tranche;
see the data/PROOFATLAS_* crosswalk, review, and inventory files and the
public-safe fixtures/proofatlas_* review inputs. They identify records,
locators, hashes, decisions, and original curator normalizations without
reproducing ProofAtlas target or collaboration-card prose.

## Statement and status boundary

No site-wide reuse licence or terms page was established for redistributing the
ProofAtlas target/card text. The release therefore does **not** mirror it.
Each appended source_text.statement is a one-line, curator-authored
normalization that is source-linked, has an explicit no-copy provenance
attestation, and passed an in-memory guard against exact, contained, or
materially long contiguous reuse of any of the 1,268 frozen Top-500
target/question or collaboration-card source strings. The frozen source
artifacts remain local evidence; the public release retains their URLs and
hashes instead of their prose.

catalog_status preserves the status published by the source, but every new
record remains source_claimed_open or status_unclear, never verified_open.
The append makes no literature-wide current-status claim. A future recovery
pass should independently establish statement fidelity, a durable locator, and
current open status before replacing the initial assessment.

## Assessment boundary

The v1.8 records use the existing OPDP schema and all ten rationale slots, but
their numerical dimensions are deliberately quarantined as **C0** provisional
cue-based intake estimates. They are appropriate for discovery, filtering, and
future calibration queues; they are not expert-certified difficulty ratings or
per-problem agent-run results. The builder applies the same C0 rule to
intrinsic difficulty, AI-relative difficulty, human-attention prior,
tractability, verification burden, formalization, prerequisites, and tool
leverage. Every appended record is flagged:

- proofatlas_curated_input
- proofatlas_provisional_c0_assessment
- source_statement_not_independently_verified

## Compatibility and validation

The v1.7 payload is inspected and copied in two streaming passes. The builder
rejects the release unless the complete legacy record sequence and the input
header retain their canonical semantic digests during construction. It then
regenerates v1.8 release metadata while retaining the base identity and audit
information. It also checks unique numeric IDs, case-insensitive display IDs,
reversible namespace mapping, lossless curated envelopes, C0 confidence,
absence of verified_open, schema/field order, recomputed summary counters, and
gzip/strict-JSON transport.

The authoritative artifacts are:

| Artifact | Role |
| --- | --- |
| data/Ulam_MathDB_ProofAtlas_OPDP_Assessments_v1.8.json.gz | v1.8 website-import payload |
| data/OPDP_v1.8_ProofAtlas_Append_Validation.json | append-only build validation report |
| data/PROOFATLAS_CURATED_APPEND_v1.jsonl | frozen public-safe curator input (256 records) |
| data/PROOFATLAS_CURATED_APPEND_MANIFEST_v1.json | hash/count/snapshot manifest for that input |
| scripts/assemble_proofatlas_curated_append_v1.py | source-safe curator-input assembly gate |
| scripts/build_proofatlas_append_v1_8.py | streaming v1.7-to-v1.8 builder |

The v1.7 historical prefix is not reclassified, renumbered, or rewritten. The
append occupies a separate ProofAtlas namespace so consumers can adopt v1.8
without a schema migration or ambiguity about existing keys.

## Interpretation

This is intentionally a conservative intake. A problem can be absent from this
append because it was already present, the source formulation was merely a
variant, it was related but not separately scoped, the source status was not
eligible, or the evidence was unresolved. Conversely, inclusion means only
that the review accepted a distinct source-published target under the stated
provenance and C0 limitations. It does not establish priority, truth, novelty
in mathematics, or current openness.
