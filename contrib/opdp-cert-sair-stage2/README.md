# OPDP-Cert for SAIR Equational Theories Stage 2

OPDP-Cert turns a small, challenge-specific difficulty profile into a
**certificate-safe attempt order**. It can choose which existing proof or
countermodel route to try and how much budget to reserve. It never decides
whether an implication is true or false, and it never bypasses EULER's existing
certificate checks.

This is a public contribution by Alejandro Zarzuelo Urdiales, developed with
OpenAI Codex assistance on 2026-08-30. The code in this directory is licensed
under the scoped [MIT License](LICENSE); that license does not relicense the
atlas, its data, or other repository content.

## What is ready now

The deadline-safe artifact is the compact
[`euler_priority_block.py`](euler_priority_block.py) and its exact
[`euler-5524bd9-baseline-priority.patch`](patches/euler-5524bd9-baseline-priority.patch)
for EULER commit `5524bd9949873d64ea9e121f0683b7158e3d6145`.

With `OPDP_CERT_POLICY = "baseline"`, the patch:

- reproduces EULER's existing six Marathon priority buckets and stable input
  order;
- binds equation IDs to normalized equation text before consulting EULER's
  oracle, preventing a borrowed or mismatched ID from affecting priority;
- invokes the unchanged legacy `_difficulty` function on any adapter exception,
  preserving legacy handling if the input itself is invalid;
- changes ordering only—never solving, verdict emission, Lean code, or
  certificate validation; and
- adds about 2.3 KB to the single-file solver and uses only its existing
  standard-library imports and functions.

This is compatibility and statement-fidelity evidence, **not a score-improvement
claim**. The optional `structural_v0` policy adds deterministic within-bucket
ordering by operation count, variable count, variable occurrences, and term
depth. It materially changes released-set order and must remain A/B-only until
held-out evaluation shows a benefit with no new rejected artifacts.

## Released-set validation

The validator compared the compact adapter with EULER's original `_difficulty`
at the pinned commits below:

| Set | Rows | Baseline bucket matches | Input mutations | `structural_v0` position changes |
|---|---:|---:|---:|---:|
| Normal | 1,000 | 1,000 | 0 | 993 |
| Hard 1 | 69 | 69 | 0 | 65 |
| Hard 2 | 200 | 200 | 0 | 196 |
| Hard 3 | 400 | 400 | 0 | 391 |
| **Total** | **1,669** | **1,669** | **0** | **1,645** |

The synthetic ID/text mismatch probe returned `unknown`. See the machine-readable
[`euler-priority-parity.json`](validation/euler-priority-parity.json) and detached
[`euler-patch-smoke.json`](validation/euler-patch-smoke.json). The applied solver
compiled under Python 3.11.15, reproduced the full baseline sort, and occupied
445,307 LF UTF-8 bytes, leaving 54,693 bytes below the conservative cap. These
are public-fixture compatibility results, not an estimate of private-set lift.

## Contents

| Path | Purpose | Production status |
|---|---|---|
| [`patches/euler-5524bd9-baseline-priority.patch`](patches/euler-5524bd9-baseline-priority.patch) | Exact transplant for the pinned EULER commit | Ready with `baseline` |
| [`euler_priority_block.py`](euler_priority_block.py) | 1,972-byte source block used by the patch | Ready with `baseline` |
| [`validate_euler_priority.py`](validate_euler_priority.py) | Replay against all four released manifests | Validation only |
| [`opdp_cert.py`](opdp_cert.py) | Feature, priority, route-plan, and episode reference API | Research scaffold |
| [`episode.schema.json`](episode.schema.json) | Offline route-attempt record contract | Research scaffold |
| [`portfolio_select.py`](portfolio_select.py) | Deterministic joint-coverage selector under byte/time/token budgets | Offline research only |
| [`examples/portfolio.json`](examples/portfolio.json) | Minimal selector input | Example |
| [`INTEGRATION_EULER.md`](INTEGRATION_EULER.md) | Exact integration and A/B procedure | Operational guide |
| [`PROVENANCE.md`](PROVENANCE.md) | Source commits, authorship, claims, and reuse boundary | Evidence |
| [`SUBMISSION_NOTE_TEMPLATE.md`](SUBMISSION_NOTE_TEMPLATE.md) | Attribution/disclosure wording to adapt | Template, not legal advice |

Only the compact patch belongs inside Christopher's competition `solver.py`.
Do not copy the README, tests, schema, selector, or full reference module into
the submitted single file.

## Local verification

From the atlas repository root, with Python 3.11 or later:

```bash
python -m unittest discover -s contrib/opdp-cert-sair-stage2/tests -v

python contrib/opdp-cert-sair-stage2/validate_euler_priority.py \
  --euler-repo /path/to/euler-sair-stage2 \
  --official-repo /path/to/equational-theories-lean-stage2
```

The validator requires these exact repository states:

- EULER: `5524bd9949873d64ea9e121f0683b7158e3d6145`
- official SAIR repository: `817a4653bf762584931d49c6714c9fcfab7df66a`

For the ten-minute transplant and the current submission gate, follow
[`INTEGRATION_EULER.md`](INTEGRATION_EULER.md).

## The longer research loop

The larger API is deliberately separated from the immediate patch:

1. `profile_implication` records cheap, reproducible structural burdens and
   existing cache/transitivity signals.
2. `priority(..., "baseline")` preserves the legacy bucket; the opt-in
   `structural_v0` policy refines only within that bucket.
3. `build_schedule` produces route names and bounded caps, never a verdict or
   certificate. Its caps are starting priors, not measured optima.
4. A caller executes existing EULER engines and retains every `_T`, `_F`, `_IF`,
   finite-table, byte-limit, and Lean replay gate.
5. `Episode` records development outcomes outside competition output. Calibration
   must use accepted certificates as successes and group splits by hypothesis or
   equation family to limit leakage.
6. `portfolio_select.py` chooses measured, complementary routes under additive
   source-byte, time, and token budgets using observed joint coverage. It assumes
   neither independence nor private-set transfer.

The useful next step is route-level episode collection and grouped A/B testing,
not a larger unmeasured router transplant.

## Non-negotiable safety boundary

- A route timeout, search failure, or empty result is not evidence for the
  opposite truth direction.
- Known-true plans contain no false-certificate routes; known-false plans contain
  no true-proof routes; unknown plans retain both directions.
- Oracle direction is usable only after ID/text binding.
- Every route deadline must be bounded by the outer problem deadline.
- Marathon may emit only artifacts that its existing offline checks permit.
- Development episodes must never be written to the official JSONL output or to
  protocol stdout.
- The current solver's judge gates and artifact size limits remain authoritative.

## Scope and disclosure

Publishing this contribution and citing its commit establishes provenance and a
reuse license. It does not itself decide SAIR team, sponsor, contributor-network,
or external-support obligations. Christopher should disclose the contribution,
its author, and any AI assistance as required, and should confirm any team-status
question with the organizers before submission.

The official repository states an August 31, 2026 23:59 AoE deadline. At the
current pinned rules this corresponds to September 1, 2026 04:59:59 PDT. Recheck
the [official overview](https://github.com/SAIRcompetition/equational-theories-lean-stage2/blob/817a4653bf762584931d49c6714c9fcfab7df66a/rules/overview.md#L65-L70)
and repository HEAD immediately before upload.
