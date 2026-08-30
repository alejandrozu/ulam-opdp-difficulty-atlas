# EULER integration guide

This guide separates the safe, immediate transplant from the experimental
research layer. It targets EULER commit
`5524bd9949873d64ea9e121f0683b7158e3d6145` and official SAIR commit
`817a4653bf762584931d49c6714c9fcfab7df66a`.

## A. Immediate baseline transplant

Start from a clean EULER checkout at the pinned commit:

```bash
git status --short
git rev-parse HEAD
git apply --check /path/to/euler-5524bd9-baseline-priority.patch
git apply /path/to/euler-5524bd9-baseline-priority.patch
```

Keep this line unchanged:

```python
OPDP_CERT_POLICY = "baseline"
```

The patch performs three contained edits in
`SUBMIT-NOW-2026-08-29/EULER/solver.py`:

1. It adds the 1,972-byte priority block immediately before `_difficulty`.
2. It retains `_difficulty` and adds an exception-safe `_opdp_sort_key` wrapper.
3. It changes only Marathon's `sorted(...)` key and appends the original input
   index for explicit deterministic ties.

No proof engine, countermodel engine, output path, certificate generator, oracle
table, or judge boundary is changed.

### What `baseline` does

For a valid problem it normalizes both equations, binds the supplied IDs to that
text, consults the oracle only after binding, and recomputes the same six buckets:

1. known false;
2. singleton collapse;
3. hypothesis LHS-only variable;
4. one or two free-side variables;
5. known true;
6. unknown.

It returns only `(bucket,)`. Appending the original manifest index reproduces the
old stable ordering. If feature extraction fails, `_opdp_sort_key` calls the
unchanged legacy `_difficulty`.

### Required checks after applying

From the atlas checkout:

```bash
python -m unittest discover -s contrib/opdp-cert-sair-stage2/tests -v

python contrib/opdp-cert-sair-stage2/validate_euler_priority.py \
  --euler-repo /path/to/euler-sair-stage2 \
  --official-repo /path/to/equational-theories-lean-stage2
```

From EULER:

```bash
python -m py_compile SUBMIT-NOW-2026-08-29/EULER/solver.py
git diff --check
```

Count the exact LF-normalized UTF-8 upload, not the platform-dependent working
tree size. Stay below the conservative 500,000-byte cap:

```bash
python -c 'from pathlib import Path; p=Path("SUBMIT-NOW-2026-08-29/EULER/solver.py"); b=p.read_bytes().replace(b"\r\n", b"\n"); print(len(b))'
```

On Windows PowerShell, normalize only CRLF pairs and count the working file:

```powershell
$path = "SUBMIT-NOW-2026-08-29/EULER/solver.py"
$raw = [IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $path))
$text = [Text.Encoding]::UTF8.GetString($raw).Replace("`r`n", "`n")
[Text.Encoding]::UTF8.GetByteCount($text)
```

After staging, `git cat-file -s :SUBMIT-NOW-2026-08-29/EULER/solver.py` reports
the canonical index blob size directly.

## B. Structural priority candidate

Do not enable this in the baseline candidate. Make a second otherwise identical
copy and change only:

```python
OPDP_CERT_POLICY = "structural_v0"
```

`structural_v0` retains the same first/coarse bucket but sorts within it using
cheap syntax. It changed 993 of 1,000 positions in the released Normal manifest,
so it is a real scheduling intervention despite its small code size.

Use grouped held-out A/B evaluation:

1. Freeze the EULER and OPDP-Cert commits, manifests, environment, budgets, and
   random seeds.
2. Group by hypothesis/equation family; never split closely related pairs across
   calibration and evaluation.
3. Run byte-identical candidates except for the policy string.
4. Compare accepted count first, then wall time and rows never attempted.
5. Inspect every status. Require zero new `incorrect`, `malformed`, or
   `incomplete_proof` artifacts.
6. Enable `structural_v0` only if the grouped evaluation improves accepted count
   or preserves it with a clear operational benefit.

Released-set reordering alone is not evidence of improvement.

## C. Building route allocation on top

The compact patch intentionally stops at problem ordering. The next integration
point is inside `marathon()`, after EULER normalizes equations, binds IDs to text,
and computes the authoritative `known` value. At the pinned commit this is the
block beginning around original lines 4599–4608.

Map OPDP-Cert route names to existing EULER hooks:

| Route | Existing EULER function |
|---|---|
| `lemma_chain_quick` | `_mt_prove(..., rounds=2)` |
| `matching_chain` | `_ce_chain_proof(..., time_cap=cap_s)` |
| `mini_twee` | `_mt_prove(...)` |
| `mini_twee_deep` | `_mt_prove(..., rounds=12)` |
| `structural_true` | `find_proof(...)` |
| `transitivity` | `_transitivity_prove(..., deadline=route_deadline)` |
| `bfs` | `proof_engine_v5(...)` |
| `specialized_simp` | `specialized_simp_v5(...)` |
| `simp_constancy` | `simp_constancy_v5(...)` |
| `rw_chain` | `rw_chain_v5(...)` |
| `hybrid_calc` | `hybrid_calc_v5(...)` |
| `invertibility` | `invertibility_sweep(...)` |
| `tactic_sweep` | `tactic_sweep(...)` |
| `finite_ce` | `find_counterexample(..., deadline=route_deadline)` |
| `infinite_parity` | `parity_walk_infinite_countermodel(...)`, Solo only |
| `csp_false` | `csp_false_counterexample(..., deadline=route_deadline)` |
| `offline_true_quick` / `offline_true_tail` | `_offline_true_body(...)`, Marathon only |
| `llm_true` / `llm_false` / `llm_both` | `llm_fallback_v5(...)`, Solo only |

For every attempt, compute:

```python
route_deadline = min(outer_deadline, time.time() + cap_seconds)
```

Retain EULER's current emission gates. In particular:

- Solo TRUE, finite FALSE, and infinite FALSE artifacts still pass `_T`, `_F`,
  and `_IF` respectively.
- Marathon finite tables are still exhaustively checked against both equations
  before Lean generation and remain below 20,000 UTF-8 bytes.
- A failed route returns control to the scheduler; it never supplies a verdict.
- Infinite FALSE is not emitted in Marathon without an offline Lean gate.
- Router exceptions fall back to legacy behavior or abstention.

The current unknown Marathon path can let `find_counterexample` consume almost
all of a problem slice before CSP and the TRUE tail. Per-route caps may reduce
that starvation, but this is the first change that needs measured episode data;
do not infer a useful cap from the released ordering results.

For Solo, preserve the text-bound authoritative oracle value in a separate
variable before EULER's unknown-direction predictor mutates its local `direction`.
The router must receive only that authoritative value as `known`.

## D. Episode-driven calibration

Use [`opdp_cert.py`](opdp_cert.py) and [`episode.schema.json`](episode.schema.json)
outside the submitted process to record one row per route attempt:

- problem and family/group IDs;
- profile and policy versions;
- route and cap;
- elapsed time and timeout;
- accepted status after the real certificate gate;
- token reservation and certificate byte count; and
- normalized failure reason and exact toolchain commit.

Never log a timeout as a false or true label. Fit priorities and caps on complete
grouped histories, then test a frozen candidate on unseen groups. The
[`portfolio_select.py`](portfolio_select.py) helper can select complementary
measured routes under additive budgets, but its greedy result is a development
aid, not an optimality or private-score guarantee.

## E. Before-submit gate

- Record both source commit hashes and the final solver SHA-256.
- Keep exactly one regular submission file named `solver.py`.
- Verify LF-normalized UTF-8 size below 500,000 bytes.
- Run Python compilation and the current official Solo and Marathon harnesses.
- Replay representative certificates under current Lean 4.33.1/Mathlib 4.33.1,
  not only EULER's older local evidence.
- Preserve the 100,000-byte Lean artifact and 20,000-byte FALSE limits.
- Confirm the runner uses only Python standard library and production-available
  resources.
- Flush Marathon output before budget expiry; do not rely on grace-period writes.
- Recheck official HEAD and rules immediately before upload.
- Cite and disclose this public contribution using
  [`SUBMISSION_NOTE_TEMPLATE.md`](SUBMISSION_NOTE_TEMPLATE.md), and resolve any
  team/sponsor/external-support question with SAIR.

The current official evaluation rules are pinned here:
[evaluation.md](https://github.com/SAIRcompetition/equational-theories-lean-stage2/blob/817a4653bf762584931d49c6714c9fcfab7df66a/rules/evaluation.md).
