# OPDP v1.5 distribution comparison

This note compares the 5,426 preserved UnsolvedMath v1.2 OPDP records with the 3,359 AIM Workshop Problem Lists records appended in v1.5. It is intended to help researchers choose problems and interpret the expanded atlas.

## Main conclusion

The AIM additions are not meaningfully harder overall. Their mean intrinsic difficulty is only 0.060 points higher on the 0–10 scale, but their profile is different: they are more technically specialized, broader, somewhat harder to formalize and verify, and more concentrated in the middle of the scale. Their higher tractability score mainly reflects partial-progress statuses and assessment-gate mechanics rather than a greater probability of complete solution.

## Method

- `Previous` is the non-overlapping 5,426-record compatibility cohort; `AIM additions` is the 3,359-record appended cohort.
- Delta is AIM additions minus previous.
- `g` is Hedges' standardized mean difference: about 0.2 is small, 0.5 moderate, and 0.8 large.
- `q` is the Benjamini–Hochberg-adjusted two-sided Mann–Whitney p-value across 27 prespecified numeric comparisons.
- Higher values mean harder or more burdensome except for tractability T and tool leverage L, where higher is favorable. Human effort H and exposure X describe attention, not difficulty.

These are deterministic scores for two fixed, clustered corpora. The p-values are descriptive compatibility signals, not evidence that source membership causes difficulty.

## Main OPDP axes

| Dimension | Previous | AIM additions | Delta | g | Adjusted q | Practical reading |
|---|---:|---:|---:|---:|---:|---|
| Intrinsic difficulty D | 5.715 | 5.775 | +0.060 | +0.107 | 1.47e-17 | Essentially unchanged |
| AI-relative adjustment | -0.169 | -0.137 | +0.032 | +0.041 | 1.02e-6 | Negligibly less AI-favorable |
| AI difficulty | 5.544 | 5.638 | +0.094 | +0.077 | 7.42e-14 | Negligibly harder for AI |
| Human effort H | 1.860 | 1.844 | -0.016 | -0.013 | 3.71e-112 | Same mean; the distributions cross |
| Exposure X | 1.488 | 3.001 | +1.513 | +2.586 | <1e-300 | Much higher encoded exposure |
| Tractability T, scored records | 4.693 | 5.697 | +1.004 | +0.745 | 4.13e-209 | More favorable for checked partial progress |
| Verification if true | 5.483 | 5.667 | +0.184 | +0.152 | 2.78e-18 | Slightly harder to verify |
| Verification if false | 5.299 | 5.519 | +0.220 | +0.269 | 2.19e-39 | Slightly harder to verify |
| Formalization F | 5.542 | 5.869 | +0.327 | +0.210 | 1.87e-18 | Somewhat more burdensome |
| Prerequisite preparation P | 6.565 | 7.082 | +0.517 | +0.515 | 4.77e-100 | Moderately more specialized |
| Breadth B, 0–5 | 1.875 | 2.431 | +0.556 | +0.556 | 1.01e-200 | Moderately more cross-field |
| Tool leverage L | 4.865 | 4.804 | -0.061 | -0.055 | 1.43e-5 | Negligibly less tool-friendly |
| Ambiguity | 2.180 | 1.906 | -0.274 | -0.250 | 1.87e-155 | Somewhat clearer statements |
| Literature load | 2.783 | 2.064 | -0.719 | -0.857 | <1e-300 | Lower machine-recognized context load |
| Collection-barrier prior | 5.676 | 6.147 | +0.471 | +0.539 | <1e-300 | Higher source-collection prior |

The full 8,785-record catalog moves less because the additions are 38.2% of it. Relative to the previous catalog, the combined mean changes by +0.023 for D, +0.036 for AI difficulty, +0.198 for P, +0.125 for F, +0.212 for B, and +0.344 for scored T.

## Intrinsic-difficulty decomposition

| Factor | Previous | AIM additions | Delta | g | Adjusted q |
|---|---:|---:|---:|---:|---:|
| Conceptual gap CG | 2.459 | 2.408 | -0.052 | -0.181 | 3.58e-25 |
| Route gap RG | 2.404 | 2.174 | -0.230 | -0.813 | <1e-300 |
| Technical depth TD | 2.663 | 2.870 | +0.206 | +0.421 | 1.70e-87 |
| Known barrier KB | 1.242 | 1.461 | +0.219 | +0.529 | 2.85e-257 |
| Search scale SS | 2.844 | 2.866 | +0.022 | +0.065 | 0.157 |

Under the D formula, the easier conceptual and route gaps contribute about -0.154 D points, while greater technical depth and known barriers contribute about +0.213. Search scale adds about +0.006. These opposing movements explain the small net D change.

## Distribution shape

The additions are more tightly concentrated around T3 Serious Project:

| Tier | Previous | AIM additions |
|---|---:|---:|
| T2 Research Sprint | 1.53% | 0.42% |
| T3 Serious Project | 86.82% | 95.59% |
| T4 Frontier Challenge | 11.54% | 3.99% |
| T5 Grand Challenge | 0.11% | 0% |

The D standard deviation falls from 0.619 to 0.450. Thus the small mean increase does not mean that v1.5 added many harder frontier problems: both the lower and upper tails became smaller.

AI classifications also move toward the center: AI-neutral/mixed rises from 57.52% to 73.38%, while AI-favored falls from 26.30% to 15.33% and AI-hostile from 16.18% to 11.28%.

## Why the cohorts differ

1. **Source and status are confounded.** Every addition comes from one AIM corpus. The previous cohort is 95.02% catalog-open; the AIM additions are 79.31% partially solved and 15.00% solved.
2. **Tractability is status-sensitive.** Removal of the legacy cohort's frequent `status_unclear` penalty contributes +0.606 T points, and the partial-status bonus contributes +0.442. These explain almost all of the +1.004 T difference.
3. **Category and text mix affect P, B, F, TD, and V.** The additions contain much more algebraic geometry, computer science, logic, and PDE. Their formulaic workshop backgrounds also trigger more advanced-field and cross-domain patterns.
4. **Lower literature load is an extraction result.** The reference detector recognizes few citations in the AIM format. It should not be read as proof that the real literature is smaller.
5. **Exposure is largely a rule effect.** Almost every AIM record receives X3 because the collection defaults to X2 and the word `Workshop` supplies a rounding increment.
6. **Human effort has a different shape, not a different mean.** A typical addition is H2 rather than H1, but the previous corpus contains the long H3–H9 tail of older and highly cited problems.

## Guidance for problem selection

- Use D to choose the overall research scale, then inspect its factors. A low RG with high P or TD means a plausible route exists but substantial technical preparation is still required.
- Use T only for scoped partial-progress projects; it is not a probability of complete solution.
- Use V and F to estimate checking and formalization costs before committing to a benchmark or proof-assistant project.
- Treat H, X, literature load, and collection-barrier prior as evidence-quality and context signals rather than direct measures of mathematical hardness.
- Inspect `catalog_status`, `assessment_gate`, confidence, flags, and the per-axis rationales before selecting any individual problem.

Restricting both cohorts to `source_claimed_open` records makes the intrinsic-D difference effectively disappear (-0.014), while higher prerequisite, breadth, formalization, and verification burdens remain. The defensible interpretation is therefore profile change, not a general increase in hardness.
