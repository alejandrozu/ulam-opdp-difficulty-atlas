# OPDP v1.6 source and cohort comparison

This note compares the three non-overlapping vintages in the 15,458-problem atlas and explains how the answer changes when problems, rather than source documents, receive equal weight.

## Executive conclusion

The 6,673 Oberwolfach additions are not materially harder overall than the 3,359 AIM additions. Their row-weighted intrinsic difficulty D is 0.056 points higher on a 0–10 scale (Hedges' g = 0.110), but the difference falls to +0.007 when every source document receives equal weight. The source-balanced 90% bootstrap interval is [-0.040, +0.052], entirely inside the prespecified practical-equivalence band of +/-0.102. Their AI difficulty is likewise source-balanced equivalent to AIM (+0.081; q = 0.334).

The profile nevertheless changes in ways that matter for research selection. Relative to AIM, the Oberwolfach problems have larger conceptual and route gaps, but lower encoded technical depth, prerequisite preparation, breadth, formalization burden, and ambiguity. Their 100-hour tractability is lower by 0.381 row-weighted points and 0.424 source-balanced points, mostly because fewer are marked partially solved and because the human-effort prior removes a favorable T bonus more often. They also contain much richer machine-recognized literature context. None of these statements implies that membership in an Oberwolfach report *causes* a score.

Relative to the original 5,426 problems, Oberwolfach is only slightly higher in row-weighted D (+0.116; g = 0.202), and the source-balanced result remains inconclusive (+0.119; q = 0.061). The defensible summary is therefore **different profile and evidence regime, not a general escalation in mathematical hardness**.

## Cohorts and source hierarchy

| Code | Release vintage | Records | Top-level collections | Natural source documents | Source assignment coverage |
|---|---|---:|---:|---:|---:|
| V0 | Original v1.2 compatibility cohort | 5,426 | 12 | 99 | 5,040/5,426 = 92.89% |
| V1 | AIM v1.5 additions | 3,359 | 1 | 162 workshops | 100% |
| V2 | Oberwolfach v1.6 additions | 6,673 | 1 | 1,106 report DOIs | 100% |

“Source” has two defensible meanings here. At the collection layer V1 and V2 each come from one collection, whereas V0 spans 12. At the natural-document layer V2 is the most diverse: 1,106 Oberwolfach reports spanning 2004–2026. The report size is 1–33 problems (median 5). These layers must not be mixed.

The natural source rule is deterministic:

1. V2 uses the exact DOI in `source_record.source_url`, labelled by `source_record.source_citation`.
2. V1 uses the structured `aim-workshop:<slug>` tag and frozen workshop background.
3. V0 uses the exact frozen AMR `Source list:` field where recoverable (89 lists); ten other named collections remain collection-level units.
4. The 386 unassigned V0 records retain a null source-document ID. No source is fabricated.
5. Literature evidence links are not treated as origin sources.

The source units are not perfectly exchangeable: an entire legacy collection can be coarser than one report, and an AIM workshop can differ from an Oberwolfach report. Source-balanced results answer a robustness question—whether conclusions survive equal weighting of the best recoverable document unit—not a metaphysical question about the one true sampling unit.

## Dimension directions

- Higher D, CG, RG, TD, KB, SS, AI adjustment, AI difficulty, V, F, P, B, or ambiguity means more burden under the OPDP rubric.
- Higher T or L is favorable: T means greater probability of a checked partial result within 100 combined expert-plus-AI hours; L means more leverage from computation, search, formal tools, or exact checkers.
- H, X, literature load, and collection prior are context signals, not direct hardness axes. H is an order-of-magnitude human-effort prior; X is visibility; literature load is machine-recognized context; collection prior is an input prior only.
- T nulls are not zeros. T uses 5,408 V0, 2,817 V1, and 6,606 V2 records after solved and ill-posed gates are excluded.

## Descriptive levels

Each cell reports mean (sample SD).

| Dimension | V0 mean (SD) | V1 mean (SD) | V2 mean (SD) |
|---|---:|---:|---:|
| D | 5.715 (0.619) | 5.775 (0.450) | 5.831 (0.538) |
| CG | 2.459 (0.298) | 2.408 (0.263) | 2.486 (0.298) |
| RG | 2.404 (0.271) | 2.174 (0.302) | 2.313 (0.293) |
| TD | 2.663 (0.492) | 2.870 (0.485) | 2.724 (0.494) |
| KB | 1.242 (0.475) | 1.461 (0.291) | 1.458 (0.464) |
| SS | 2.844 (0.346) | 2.866 (0.320) | 2.859 (0.318) |
| AI adjustment | -0.169 (0.851) | -0.137 (0.678) | -0.100 (0.719) |
| AI difficulty | 5.544 (1.345) | 5.638 (1.021) | 5.730 (1.101) |
| H | 1.860 (1.509) | 1.844 (0.509) | 3.605 (0.762) |
| X | 1.488 (0.744) | 3.001 (0.052) | 3.004 (0.088) |
| T | 4.693 (1.492); n=5,408 | 5.697 (1.018); n=2,817 | 5.316 (1.108); n=6,606 |
| V true | 5.483 (1.206) | 5.667 (1.215) | 5.567 (1.130) |
| V false | 5.299 (0.828) | 5.519 (0.796) | 5.333 (0.753) |
| F | 5.542 (1.626) | 5.869 (1.436) | 5.599 (1.360) |
| P | 6.565 (0.973) | 7.082 (1.056) | 6.530 (1.092) |
| B | 1.875 (1.078) | 2.431 (0.858) | 1.848 (0.938) |
| L | 4.865 (1.126) | 4.804 (1.120) | 4.815 (1.120) |
| Ambiguity | 2.180 (1.060) | 1.906 (1.161) | 1.527 (1.010) |
| Literature load | 2.783 (1.046) | 2.064 (0.275) | 3.061 (0.322) |
| Collection prior | 5.676 (0.964) | 6.147 (0.701) | 6.000 (0.000) |

## Problem-weighted comparisons

The compact entries are `newer minus older mean / Hedges' g / BH q`. The q-value adjusts the 60 prespecified dimension-by-contrast tests. Very small q-values with negligible g are expected in a deterministic corpus of this size; magnitude and source robustness matter more than the threshold q < 0.05.

| Dim | V1-V0 delta / g / q | V2-V0 delta / g / q | V2-V1 delta / g / q |
|---|---:|---:|---:|
| D | +0.060 / +0.107 / 2.5e-07 | +0.116 / +0.202 / 3e-27 | +0.056 / +0.110 / 5.8e-08 |
| CG | -0.052 / -0.181 / 4e-17 | +0.027 / +0.090 / 1.3e-06 | +0.078 / +0.273 / 6.3e-41 |
| RG | -0.230 / -0.813 / 1.5e-284 | -0.091 / -0.322 / 2.7e-70 | +0.139 / +0.470 / 1.7e-106 |
| TD | +0.206 / +0.421 / 3e-82 | +0.061 / +0.124 / 1.9e-11 | -0.145 / -0.296 / 1.4e-44 |
| KB | +0.219 / +0.529 / 1.2e-157 | +0.216 / +0.460 / 2.3e-138 | -0.003 / -0.008 / 0.646 |
| SS | +0.022 / +0.065 / 0.004 | +0.015 / +0.045 / 0.017 | -0.007 / -0.021 / 0.334 |
| AI adjustment | +0.032 / +0.041 / 0.058 | +0.069 / +0.088 / 3.4e-06 | +0.037 / +0.052 / 0.016 |
| AI difficulty | +0.094 / +0.077 / 0.00027 | +0.186 / +0.153 / 3.9e-16 | +0.092 / +0.086 / 4.8e-05 |
| H | -0.016 / -0.013 / 0.484 | +1.745 / +1.506 / <1e-300 | +1.761 / +2.561 / <1e-300 |
| X | +1.513 / +2.586 / <1e-300 | +1.516 / +3.018 / <1e-300 | +0.002 / +0.031 / 0.093 |
| T | +1.004 / +0.745 / 2.4e-282 | +0.623 / +0.481 / 8.7e-143 | -0.381 / -0.352 / 1.7e-58 |
| V true | +0.184 / +0.152 / 7.1e-12 | +0.084 / +0.072 / 0.00012 | -0.100 / -0.086 / 9.2e-05 |
| V false | +0.220 / +0.269 / 7.4e-35 | +0.034 / +0.043 / 0.023 | -0.186 / -0.242 / 5.8e-29 |
| F | +0.327 / +0.210 / 1.2e-22 | +0.057 / +0.038 / 0.045 | -0.270 / -0.195 / 2.7e-19 |
| P | +0.517 / +0.515 / 1.8e-116 | -0.036 / -0.034 / 0.065 | -0.553 / -0.512 / 1.1e-131 |
| B | +0.556 / +0.556 / 2.4e-156 | -0.027 / -0.027 / 0.162 | -0.582 / -0.638 / 2.4e-211 |
| L | -0.061 / -0.055 / 0.016 | -0.050 / -0.045 / 0.017 | +0.011 / +0.010 / 0.646 |
| Ambiguity | -0.274 / -0.250 / 1.8e-28 | -0.653 / -0.632 / 7e-259 | -0.379 / -0.356 / 7.8e-58 |
| Literature load | -0.719 / -0.857 / <1e-300 | +0.278 / +0.376 / 4.7e-79 | +0.997 / +3.251 / <1e-300 |
| Collection prior | +0.471 / +0.539 / 2.9e-153 | +0.324 / +0.501 / 1.9e-134 | -0.147 / -0.363 / 8.6e-34 |

## Source-balanced robustness

For each contrast and dimension, every source document is first reduced to its mean; those source means then receive equal weight. Entries are `source-macro delta / BH q / equivalence call`. “Equivalent” requires the 90% source-bootstrap interval to lie entirely within +/-0.2 pooled row SD; “different” requires it to lie entirely outside that band; all overlap cases are “inconclusive.” Inconclusive is not evidence of equality or difference.

| Dim | V1-V0 source delta / q / call | V2-V0 source delta / q / call | V2-V1 source delta / q / call |
|---|---:|---:|---:|
| D | +0.111 / 0.107 / inconclusive | +0.119 / 0.061 / inconclusive | +0.007 / 0.830 / equivalent |
| CG | -0.057 / 0.047 / inconclusive | +0.027 / 0.293 / inconclusive | +0.084 / 9.6e-11 / different |
| RG | -0.231 / 7.6e-22 / different | -0.107 / 1.3e-06 / different | +0.124 / 2.4e-21 / different |
| TD | +0.316 / 3.9e-09 / different | +0.071 / 0.112 / inconclusive | -0.245 / 3.3e-10 / different |
| KB | +0.216 / 8.1e-05 / different | +0.217 / 4.8e-05 / different | +0.002 / 0.912 / equivalent |
| SS | +0.029 / 0.267 / inconclusive | +0.037 / 0.103 / inconclusive | +0.007 / 0.690 / equivalent |
| AI adjustment | +0.049 / 0.646 / inconclusive | +0.123 / 0.139 / inconclusive | +0.074 / 0.201 / inconclusive |
| AI difficulty | +0.173 / 0.236 / inconclusive | +0.253 / 0.045 / inconclusive | +0.081 / 0.334 / equivalent |
| H | +0.252 / 0.061 / inconclusive | +2.065 / 2e-67 / different | +1.814 / <1e-300 / different |
| X | +1.689 / 1.4e-105 / different | +1.691 / 1.1e-105 / different | +0.002 / 0.166 / equivalent |
| T | +1.160 / 1.3e-18 / different | +0.737 / 8.3e-11 / different | -0.424 / 6.6e-08 / different |
| V true | +0.130 / 0.209 / inconclusive | +0.110 / 0.208 / inconclusive | -0.020 / 0.785 / equivalent |
| V false | +0.184 / 0.040 / inconclusive | -0.015 / 0.830 / equivalent | -0.198 / 0.00039 / inconclusive |
| F | +0.130 / 0.558 / inconclusive | -0.037 / 0.830 / equivalent | -0.167 / 0.201 / inconclusive |
| P | +0.680 / 1.4e-08 / different | -0.038 / 0.717 / equivalent | -0.718 / 1.2e-15 / different |
| B | +0.961 / 2.2e-24 / different | +0.143 / 0.075 / inconclusive | -0.818 / 2.9e-34 / different |
| L | +0.067 / 0.560 / equivalent | -0.073 / 0.412 / equivalent | -0.140 / 0.052 / inconclusive |
| Ambiguity | -0.258 / 0.003 / inconclusive | -0.775 / 4.2e-42 / different | -0.517 / 1.1e-14 / different |
| Literature load | -0.530 / 4.1e-11 / different | +0.389 / 4.7e-07 / different | +0.919 / 2.3e-298 / different |
| Collection prior | +0.360 / 2.3e-05 / different | +0.260 / 0.00094 / different | -0.100 / 0.006 / inconclusive |

The source analysis changes the most important headline: row weighting calls V2 slightly harder than V1 because large AIM workshops pull the V1 row mean down, whereas equal source weighting gives D means of 5.826 for V1 and 5.833 for V2. That +0.007 source-level difference is practically equivalent.

## Why D and AI difficulty barely move

The V2-V1 factor movements nearly cancel under the published D formula. Using the unrounded cohort deltas, larger conceptual gap contributes about +0.059 D points and larger route gap +0.070, while lower technical depth contributes -0.073; KB and SS together contribute about -0.003. The resulting +0.052 is close to the observed rounded-score delta of +0.056.

At source level the cancellation is stronger: CG contributes about +0.063, RG +0.062, TD -0.123, and KB plus SS about +0.003, yielding approximately +0.005. AI difficulty is D plus the protocol-specific AI adjustment, so the small D shift plus a negligible +0.037 row adjustment explains the +0.092 AI-difficulty change. There is no empirical per-problem AI episode behind this comparison.

Against V0, V2's +0.116 D difference is driven primarily by the higher known-barrier factor (+0.108 D contribution), partly offset by a lower route gap (-0.046). This is still a small standardized change and source-level inconclusive.

## Why tractability moves

T is deliberately status- and execution-sensitive; it is not a probability of complete solution. Among rows with non-null T, the V2-V1 change in mean formula components is approximately:

- -0.056 from slightly higher AI difficulty;
- +0.003 from tool leverage;
- +0.023 from lower verification burden;
- -0.064 because the H <= 4 bonus applies less often;
- -0.303 because the partial-progress bonus applies much less often; and
- -0.005 from the status-gate penalty.

Those pre-rounding terms sum to about -0.402, close to the observed -0.381 after per-record rounding and clamping. V1 is 79.31% partially solved and 15.00% solved; V2 is 32.67% partially solved and has no solved rows. Thus AIM's higher T mainly says its scored subset carries more documented partial progress, not that AIM contains easier full resolutions.

V2 is nevertheless +0.623 T above V0. Most of that contrast comes from V0's frequent `status_unclear` penalty: the mean gate contribution changes from -0.610 in scored V0 to -0.010 in V2. This is a data/status effect, not a mathematical-hardness result.

## Why context and execution dimensions move

### Human effort and literature load

Every V2 row has an explicit proposal/report year and at least one recognized citation signal; its mean reference-signal count is 4.10. V1 has year coverage of 0.09%, only 3.36% of rows have a recognized reference signal, and its mean count is 0.068. The OWR background format includes the source DOI, a dated literature review, and structured evidence links. Those observable inputs raise H and literature load by construction.

Therefore the large H (+1.761; g = 2.561) and literature-load (+0.997; g = 3.251) contrasts are chiefly measurement-regime differences. They should not be paraphrased as “humans worked 50 times harder on Oberwolfach problems” or “the true literature is larger.” H is an order-of-magnitude prior with unpublished effort unobserved, and literature load is a detector output.

### Ambiguity, prerequisites, breadth, and formalization

V2 contains fewer compound-scope statements than V1 (9.31% versus 20.01%) and more family-scope statements (13.85% versus 7.20%). Its median statement length is similar (228 versus 227 characters). The rule therefore reads many OWR formulations as more atomic or formally scoped, which helps explain lower ambiguity.

Category and lexical composition also differ. V1 has 12.77% algebraic geometry versus 5.62% in V2, while V2 has 19.83% `Miscellaneous` versus 6.13% in V1. Because P, B, F, TD, and V respond to field and cross-domain cues, V2's lower P and B should be read as a corpus-composition result, not proof that an arbitrary OWR problem requires less training. P and B survive source balancing; F and V-false do not meet the source-level equivalence-or-difference criterion.

### Exposure and collection prior

V1 and V2 both cluster near X3. The collection default supplies X2 and the word “workshop” in their backgrounds supplies a rounding increment; the V2-V1 source result is equivalent. X measures visibility, not resistance.

V2's collection prior is exactly 6.0 because every upstream OWR row carries the same default L3. Its zero variance is a rule/input artifact. V1 averages 6.147 because its frozen source cues vary. Neither value is an expert difficulty judgment.

## Are problems within a source more similar?

The table reports fixed-group eta-squared: the fraction of *observed* sum of squares attributable to differences among source-document means. It is descriptive, not a population intraclass-correlation estimate, and it is upward-biased when many groups are small.

| Dim | V0 eta2 | V1 eta2 | V2 eta2 |
|---|---:|---:|---:|
| D | 0.503 | 0.296 | 0.290 |
| CG | 0.178 | 0.105 | 0.187 |
| RG | 0.116 | 0.100 | 0.226 |
| TD | 0.717 | 0.818 | 0.557 |
| KB | 0.814 | 0.136 | 0.270 |
| SS | 0.118 | 0.115 | 0.216 |
| AI adjustment | 0.727 | 0.679 | 0.489 |
| AI difficulty | 0.671 | 0.559 | 0.418 |
| H | 0.791 | 0.173 | 0.360 |
| X | 0.863 | 0.034 | 0.149 |
| T | 0.663 | 0.544 | 0.421 |
| V true | 0.262 | 0.234 | 0.277 |
| V false | 0.532 | 0.473 | 0.360 |
| F | 0.821 | 0.852 | 0.533 |
| P | 0.765 | 0.914 | 0.605 |
| B | 0.560 | 0.542 | 0.510 |
| L | 0.244 | 0.257 | 0.313 |
| Ambiguity | 0.191 | 0.182 | 0.241 |
| Literature load | 0.748 | 0.275 | 0.318 |
| Collection prior | 0.791 | 0.214 | 0.000 |

For V2, report membership accounts descriptively for 29.0% of D variation, 55.7% of TD, 60.5% of P, 53.3% of F, 51.0% of B, and 41.8% of AI-difficulty variation. This is expected: a workshop report shares subject area, notation, maturity, editorial context, and literature conventions. The result matters for calibration and evaluation:

- Random problem-level train/test splits can leak source style and topic. Source-held-out splits by DOI are more demanding and more informative.
- Standard errors that treat all 6,673 V2 rows as independent are too optimistic for source-level generalization.
- A model calibrated globally can be systematically high or low within one report; report-level residual monitoring is appropriate.
- Source-balanced estimates should accompany row-weighted estimates whenever the target is “a typical source” rather than “a typical catalog row.”

V2 is diverse despite clustering. Its source HHI is 0.001531, corresponding to 653 inverse-HHI effective sources and 819 entropy-effective sources; its largest report contributes only 33 rows. V1 has 162 workshops and 106 inverse-HHI effective sources. V0 has 99 identified units but only 19.7 inverse-HHI effective sources, partly because legacy collection-level units are much larger. Source count alone is therefore not an adequate diversity measure.

## Statistical protocol

1. The analysis prespecifies 20 dimensions and three pairwise contrasts, for 60 tests.
2. The problem-weighted p-value is a two-sided unequal-variance normal approximation for the difference in means. Hedges' g, Cliff's delta, Wasserstein-1 distance, and SD ratios are also exported.
3. Benjamini-Hochberg correction is applied separately to the 60 row-weighted p-values and 60 source-macro p-values.
4. Source-macro comparisons use 99 V0, 162 V1, and 1,106 V2 identified units; T uses 99, 159, and 1,103 units because of null gates.
5. Source uncertainty intervals use 4,000 nonparametric source bootstrap replicates with deterministic seed `20260826`. Both 90% and 95% intervals are exported.
6. Fixed-group eta-squared measures observed source clustering; no random-effects population model is claimed.

These are scores for fixed, versioned corpora produced by a deterministic rule. P-values quantify incompatibility with an equal-mean working model under stated weighting assumptions; they do not measure scoring uncertainty, certify external replication, or establish a causal source effect. Source, vintage, status, metadata richness, and subject mix are confounded. The earlier v1.5 note used a different test family and multiplicity set, so its q-values should not be compared numerically one-for-one with this release.

## Files for reproduction and source-level use

- `data/OPDP_v1.6_Source_Provenance.json.gz`: one joinable source assignment per problem.
- `data/OPDP_v1.6_Cohort_Dimension_Summary.csv`: complete cohort means, SDs, quantiles, and valid n.
- `data/OPDP_v1.6_Cohort_Comparisons.csv`: all row and source-macro effects, p/q values, distances, intervals, and equivalence calls.
- `data/OPDP_v1.6_Source_Summary.csv`: one row per natural source document with status, category, QA, dimension means/SDs, and distances to V0/V1 centroids.
- `data/OPDP_v1.6_Source_Clustering.csv`: fixed-group eta-squared and within-source SD.
- `data/OPDP_v1.6_Source_Composition.csv`: coverage, HHI, effective-source counts, and source-size summaries.

## Recommended next analyses

1. Build source-held-out benchmark splits and publish report IDs in every evaluation row.
2. Fit hierarchical models with report/workshop random effects and category/status fixed effects; the present source-macro analysis is intentionally simpler.
3. Repeat the score pass blind to collection labels and compare it with the published pass to isolate source-prior effects.
4. Run duplicate and near-duplicate clustering before benchmark construction, including paraphrases across sources.
5. Collect empirical AI-plus-expert episodes on a stratified source-by-profile sample and recalibrate AI difficulty and T from outcomes.
6. Track residual calibration by source year, category, status, and evidence quality instead of forcing a single global calibration curve.

For individual problem selection, inspect the per-axis rationales, source text, status gate, confidence, and flags. Cohort means are useful for calibration; they are not substitutes for the problem-level audit trail.
