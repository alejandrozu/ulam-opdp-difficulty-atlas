# ProofAtlas Top 500: second review of low-rank abstentions

This review covers the 119 ProofAtlas Top 500 entries at release ranks 3--249
that the initial conservative crosswalk labeled `no_strong_match`. It is a
scope and identity review, not a corpus append, a current-openness audit, or a
reassessment of any OPDP difficulty dimension.

The resulting content-minimized sidecar is
[`../data/PROOFATLAS_TOP500_NO_STRONG_REVIEW_RANKS_003_249_v1.jsonl`](../data/PROOFATLAS_TOP500_NO_STRONG_REVIEW_RANKS_003_249_v1.jsonl).
Each line supplies the ProofAtlas identifier and hashes, one review label,
one-line non-quoted evidence, retained candidate metadata where relevant, and
an explicit status-verification limitation. It does not contain a ProofAtlas
target or an OPDP statement/excerpt.

## Method

The review used the frozen ProofAtlas release and a private full-v1.7 local
workbench. Candidate discovery combined the original crosswalk candidate with
title phrase, title all-term, title OR-term, target-term, and rare-target-term
searches across all 102,563 OPDP records. The source target and potential
candidate statement were reviewed privately; only identifiers, hashes,
retrieval-method labels, and text-completeness metadata are released.

`same_problem_reviewed` requires a reviewed identity in a complete frozen
OPDP record. `variant_or_subproblem` records the scoped relation and includes
`distinct_problem_worthy_of_append`: `true` only for a materially distinct,
self-contained target; `false` for a special case already covered by the
existing record. If only an excerpt-derived MathDB record could plausibly
match, the result is `unresolved_due_to_incomplete_existing_text` rather than
an identity claim. A target receives `appendable_no_strong_match` only after
the broader search found no scope-compatible identity and its private source
target was judged self-contained.

## Result

| Review decision | Count |
| --- | ---: |
| `same_problem_reviewed` | 21 |
| `variant_or_subproblem` | 23 |
| `unresolved_due_to_incomplete_existing_text` | 20 |
| `appendable_no_strong_match` | 55 |

There are 72 guarded append candidates: the 55 no-strong-match targets plus
17 materially distinct variants. They are not yet OPDP records. Their local,
curator-authored normalizations are deliberately kept in an ignored draft for
source, rights, and present-status review before any v1.8 input is frozen.

Every source-published status remains unverified by this review. In
particular, a ProofAtlas release label is preserved only as a source claim;
the sidecar never states that a target is currently open.
