# Provenance and claim register

## Authorship and scope

- Contributor: Alejandro Zarzuelo Urdiales.
- Development date: 2026-08-30.
- Assistance: OpenAI Codex helped inspect public code and rules, design and
  implement the adapter and research scaffold, and run tests and review.
- License: the MIT license in this directory applies only to
  `contrib/opdp-cert-sair-stage2/`. It does not relicense the OPDP atlas, data,
  reports, or other repository content.
- Privacy: no private email, attachment, credential, or nonpublic conversation is
  reproduced here. Public technical claims below are traceable to public code,
  public rules, or released-fixture measurements.

## Pinned public inputs

| Input | Commit | Use |
|---|---|---|
| [`primaryhosting/euler-sair-stage2`](https://github.com/primaryhosting/euler-sair-stage2/tree/5524bd9949873d64ea9e121f0683b7158e3d6145) | `5524bd9949873d64ea9e121f0683b7158e3d6145` | Target solver, bucket definition, route hooks, and safety gates |
| [`SAIRcompetition/equational-theories-lean-stage2`](https://github.com/SAIRcompetition/equational-theories-lean-stage2/tree/817a4653bf762584931d49c6714c9fcfab7df66a) | `817a4653bf762584931d49c6714c9fcfab7df66a` | Released manifests, runner contract, limits, and Lean toolchain |
| [`alejandrozu/ulam-opdp-difficulty-atlas`](https://github.com/alejandrozu/ulam-opdp-difficulty-atlas) | base `6eee8ab4f5dd5d246f03e1f6e74b19acf73c5f84` | Host repository and OPDP methodology context |

The compact patch is intentionally commit-specific. Revalidate it after either
upstream changes.

## Evidence-backed claims

| Claim | Evidence | Interpretation |
|---|---|---|
| Baseline bucket parity is 1,669/1,669 | [`validation/euler-priority-parity.json`](validation/euler-priority-parity.json) and reproducible validator | Compatibility only |
| Input mutations are zero on released fixtures | Same validator | Adapter reads but does not mutate manifests |
| A mismatched ID/text pair is unbound | Unit and validator probe | Prevents borrowed oracle priority |
| Compact source block is exactly 1,972 LF UTF-8 bytes | Unit test reads raw bytes | Source-block size, excluding wrapper |
| Applied solver is 445,307 LF UTF-8 bytes and compiles on Python 3.11.15 | [`validation/euler-patch-smoke.json`](validation/euler-patch-smoke.json) | 54,693 bytes of conservative cap headroom at the pinned commits |
| `structural_v0` preserves every released row's coarse bucket | Same validator | Direction/bucket invariant only |
| `structural_v0` changes 1,645 released-set positions | Same validator | It is a material experiment, not proof of benefit |
| Router module is Python 3.11 standard-library only and below 20 KB | Source inspection and unit test | Development portability; only compact block is meant for submission |
| Schedules respect supplied aggregate time/token caps | Unit property checks | Planner invariant, not executor timing evidence |

There is no accepted-score, private-set, runtime-improvement, scientific-progress,
or optimality claim in this release.

## Measurement protocol

`validate_euler_priority.py` loads the pinned EULER solver without invoking its
entry point, executes the exact compact block, and compares the old and new first
priority components on `normal.jsonl`, `hard1.jsonl`, `hard2.jsonl`, and
`hard3.jsonl` from the pinned official repository. It also checks structural
bucket preservation, input equality before/after calls, and a synthetic ID/text
mismatch.

The recorded wall time is descriptive and machine-dependent. It is not used as a
performance claim.

## Competition compliance boundary

This public, permissively licensed contribution makes its source and provenance
available for citation. It does not adjudicate SAIR's team-member, sponsor,
external-support, Contributor Network, or AI-disclosure rules. The participant
remains responsible for registration and disclosure, for verifying the current
rules, and for ensuring that a final submission can be legally released under
the competition terms.

Relevant pinned official sources:

- [overview and deadline](https://github.com/SAIRcompetition/equational-theories-lean-stage2/blob/817a4653bf762584931d49c6714c9fcfab7df66a/rules/overview.md)
- [evaluation and artifact limits](https://github.com/SAIRcompetition/equational-theories-lean-stage2/blob/817a4653bf762584931d49c6714c9fcfab7df66a/rules/evaluation.md)
- [Marathon protocol](https://github.com/SAIRcompetition/equational-theories-lean-stage2/blob/817a4653bf762584931d49c6714c9fcfab7df66a/docs/marathon_mode.md)
- [Lean toolchain](https://github.com/SAIRcompetition/equational-theories-lean-stage2/blob/817a4653bf762584931d49c6714c9fcfab7df66a/lean-toolchain)
