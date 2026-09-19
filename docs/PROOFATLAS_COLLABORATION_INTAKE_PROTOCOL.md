# ProofAtlas Collaboration-directory intake protocol

This is a discovery and identity-audit protocol, not an OPDP append. It covers
the public ProofAtlas Collaboration directory separately from the Top 500
ranking so that a workspace route is never silently treated as a new
mathematical problem.

## Current public boundary

On 2026-09-19, the public directory exposed 268 workspace cards. The Top 500
download exposed 500 ranking records and explicitly linked 125 distinct
`/collaboration/{slug}/` routes. Those 125 routes are direct Top-500 evidence.
The remaining 143 routes are not referenced by the Top-500 JSON, but that is
not evidence that their targets are absent from either the Top 500 or OPDP:
titles, scope, and statement formulation can differ.

The non-linked route-status breakdown is 122 `open`, 10
`partially_resolved`, 7 `open_with_unverified_claim`, 3 `solved`, and 1
`resolved_up_to_finite_check`. Closed/finite-check routes are never candidates
for an ordinary open-problem append without an explicit historical-record
decision.

## Public-safe evidence ledger

For every directory card, retain in a public sidecar only:

- the canonical workspace URL and slug;
- the source-declared status and area;
- a normalized title and statement **hash**, not the statement text;
- hashes of cited source URLs or an independently collected locator manifest;
- the Top-500 route relation: `explicit_top500_route`,
  `route_not_in_top500_json`, or `not_determined`; and
- the date, page byte hash, parser version, and extraction policy.

Keep any fetched full workspace HTML and exact mathematical statements only in
an ignored local evidence store. Public artifacts must not republish
ProofAtlas statement prose, research-route text, or cited-source excerpts.

## Conservative identity stages

1. First assign the 125 exact route matches from the Top-500
   `researchWorkspaces.route` field. This is a provenance relation, not an
   assertion that the ranked and workspace formulations have identical scope.
2. For the 143 remaining routes, compare title and private/local statement
   tokens against frozen OPDP v1.7 using the existing fixed-presentation
   normalizer. Do not translate notation, simplify formulas, expand macros, or
   infer equivalence.
3. Emit only `exact_identity_candidate`, `strong_identity_candidate`,
   `ambiguous`, or `no_strong_match`. A non-match is an abstention, never proof
   of novelty or absence from OPDP.
4. Require a human scope decision for every ambiguous candidate and every
   prospective append. A workspace can be a refinement, subproblem, or
   differently scoped version of a Top-500 target.
5. Before any append, create a curated envelope accepted by the v1.8 builder.
   It must use a curator-authored independent normalization with a problem-level
   source locator, unless documented redistribution clearance is explicitly
   declared. Source status remains unverified until a separate current-status
   review.

## Release gate

No Collaboration-directory page is appended merely because it is not one of
the 125 explicit routes. A release candidate requires all of: an approved
identity decision, an independently authored or rights-cleared statement,
explicit statement-provenance mode, a source locator, and the normal v1.8
frozen-input manifest. Difficulty dimensions remain C0 until provenance and
current-status recovery support a later recalibration.
