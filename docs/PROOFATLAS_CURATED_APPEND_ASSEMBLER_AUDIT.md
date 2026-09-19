# ProofAtlas curated-append assembler audit

This audit covers the offline assembler only. It does not alter the OPDP
payload, append any records, verify current problem status, or change a review
decision.

## Findings and narrow fixes

- Collaboration assembly previously lost the workspace slug while building the
  frozen lookup record, even though the entry constructor requires it. The
  lookup now retains the slug, so mixed Top-500/Collaboration assembly can
  complete with the intended stable namespace mapping.
- The `opdp.proofatlas-curator-normalization-draft.v1` path now requires a
  draft-supplied curator attestation. The sole backwards-compatible exception
  is the earlier low-rank review contract, whose explicit
  `source_text_not_copied: true` and curator-authored draft status are retained
  verbatim as its provenance declaration.
- The assembler keeps every frozen Top-500 target/reader wording and every
  frozen Collaboration card wording only in memory for one global copy guard
  (1,268 source strings). It rejects an exact, contained, or materially long
  contiguous reuse in any alleged independent normalization, including reuse
  of a different ProofAtlas record. Those upstream strings are never
  serialized into the curated JSONL or its manifest.

## Offline coverage

`scripts/tests/test_assemble_proofatlas_curated_append_v1.py` uses fabricated
500-record and 268-card snapshots. It covers:

1. stable, disjoint namespaces for a mixed Top-500/Collaboration selection;
2. rejection when draft URL/status metadata disagrees with its frozen source;
3. rejection of duplicate Top-500 and Collaboration selections;
4. rejection of copied frozen target/card wording and confirmation that it is
   absent from successful output; and
5. rejection of cross-record and cross-surface source copying; and
6. rejection of a Top-500 normalization draft without its own attestation.

The focused assembler tests and the existing guarded v1.8 append-builder tests
pass together via:

```text
python -m unittest scripts.tests.test_assemble_proofatlas_curated_append_v1 scripts.tests.test_build_proofatlas_append_v1_8 -v
```

## Safe workflow when the copy guard rejects a draft

1. Keep the frozen source snapshot and original draft unchanged as evidence.
2. Treat the rejection as a prompt for an independent rewrite; do not evade it
   by punctuation edits, case changes, or deleting a few stopwords.
3. Re-express the mathematical target from first principles with a distinct
   sentence structure, while preserving scope, quantifiers, hypotheses, and
   requested conclusion.
4. Preserve the frozen source identifier, URL, rank/ordinal, and
   source-published status exactly; these are provenance fields, not text to
   rewrite.
5. Put only the replacement normalization in a separate ignored local draft,
   rerun the assembler against the same frozen snapshots, and inspect the
   resulting JSONL for source prose before any later release decision.
6. Keep the normal release gates in force: curator review, rights/provenance,
   and independent current-status review remain separate from this text guard.

For the current audited set, source-safe local replacement drafts exist only
in the ignored evidence store. Public review drafts that previously contained a
guard hit are deterministically rewritten by
`scripts/mathdb_recovery/remediate_proofatlas_public_draft_copy_guard.py`;
its default mode verifies the reviewed replacements and `--apply` performs the
mechanical rewrite. All such replacements are assembler-accepted, not an
independent current-open verification.
