# Results: aggregate-guarded IDBD pilot

## Conclusion

**Negative on the prespecified tracking goal.** Guarded IDBD did not demonstrate an advantage over all four best-found comparators. On held-out sparse tracking it had 13.2% higher MSE than classical IDBD, 32.6% lower MSE than Adam and 27.0% lower MSE than RMSprop. Its 2.7% higher mean MSE than momentum SGD was inconclusive, not an equivalence result.

There is a narrower favorable observation: in the stationary sparse control, guarded IDBD had lower MSE than all four tested selections. This is a diagnostic, finite-grid result, not a replacement primary claim. The selected guarded rates are on the grid boundary; schedules and full Autostep were not tested. A single extreme feature burst exposes substantial variability and a persistent-rate-depression cost. No universal superiority or novelty is claimed.

## Modification and scope

Classical IDBD is `idbd.IDBD`; the prototype is separately named `idbd.GuardedIDBD`. For the current scalar linear example, g=-e*x and c=x². Initialize beta=log(lr), h=0. With old h, form b=beta-theta*g*h. Classical uses beta_new=b. The prototype alone subtracts ell=max(0, log(sum(exp(b)*c))) from every participating coordinate's b, across all parameter groups, persistently. Then both use alpha=exp(beta_new), w_new=w-alpha*g and h_new=h_old*max(0,1-alpha*c)-alpha*g.

The stable implementation uses log-sum-exp and excludes zero c from that reduction. It retains the heuristic diagonal trace rather than differentiating through normalization. For finite exact arithmetic, aggregate gain <=1 prevents overshooting the current example; it does not guarantee low future risk or all numerical stability. Current feature squares are explicit inputs, not squared gradients or oracle relevance information.

This is the aggregate safeguard in Mahmood et al. (2012), *Tuning-Free Step-Size Adaptation*, §3/Table 1, Eqs. (6)–(7), applied to Sutton's classical recurrence. It omits Autostep's separate meta-update normalization and cannot inherit its tuning-free claims. See `research/sources/autostep2012.txt:260–343`, `DESIGN.md`, and `docs/optimizer.md`. Existing `IDBD-comparison.md` remains unchanged; this report covers the implementation and new experiment, not a replacement literature review.

## Evidence and experimental separation

One CPU thread, float64, d=20, 6000 observations, independent variance-preserving Bernoulli(0.1) feature masks, noise SD 0.3, unit endpoint signal variance. Two coefficients reverse, two are fixed, and 16 are nuisance coefficients. The stationary control removes reversals. The shock regime adds one x=30*z observation at index 2400. Risk uses the nominal identity covariance even at that intervention. Optimizers receive no coefficients, relevance groups, changes, future data or resets.

Exploration used seed 9101 and already showed a tracking loss; the candidate was not replaced to manufacture a win. Each optimizer/regime then received 16 configurations on the same tuning seeds 0–2, selected by mean pre-update MSE. Held-out seeds 100–111 were opened only after freezing code, tests, protocol, complete config and selections. `PROTOCOL.md` specifies recovery, censoring, failure handling, tie-breaking and uncertainty. This is smaller than the previous report's proposed experiment, deliberately not its entire matrix.

Freeze manifest SHA-256: `92eee700e20475f14b0b32adebdd05b15c2f6b460f5b602764aee2e0e89f259d`.

## Best-found held-out performance

Each entry is mean pre-update MSE / mean cumulative nominal excess risk over 12 paired seeds. Lower is better. Observation-noise variance is 0.09; cumulative risk excludes that noise floor.

| Optimizer | Sparse tracking (primary) | Stationary control | Extreme feature burst |
|---|---:|---:|---:|
| Guarded IDBD | 0.21975 / 770.25 | 0.10349 / 80.12 | 0.43538 / 2049.78 |
| Classical IDBD | 0.19409 / 612.81 | 0.11113 / 127.40 | 2.30839 / 13380.15 |
| Adam | 0.32601 / 1431.65 | 0.14754 / 341.74 | 0.39271 / 1818.30 |
| Momentum SGD | 0.21389 / 728.43 | 0.11228 / 132.97 | 0.52434 / 2498.53 |
| RMSprop | 0.30113 / 1295.13 | 0.11166 / 128.66 | 0.37892 / 1749.35 |

Primary guarded/comparator MSE ratios, with paired seed-bootstrap intervals (10000 resamples, seed 4242):

| Comparator | Ratio | 95% interval | 98.75% interval for four primary contrasts |
|---|---:|---:|---:|
| Classical IDBD | 1.1322 | [1.1024, 1.1632] | [1.0948, 1.1718] |
| Adam | 0.6741 | [0.6282, 0.7255] | [0.6182, 0.7414] |
| Momentum SGD | 1.0274 | [0.9843, 1.0719] | [0.9734, 1.0832] |
| RMSprop | 0.7298 | [0.6777, 0.7872] | [0.6639, 0.8047] |

The prespecified all-baseline practical-win criterion fails. Momentum's interval is not contained in the equivalence band [0.95,1.05], either. All cumulative-risk contrasts and paired differences are in `results/summary.json`; intervals resample seeds, not dependent time steps.

Stationary diagnostic ratios are 0.9312 [0.9223,0.9399] vs classical, 0.7014 [0.6855,0.7169] vs Adam, 0.9217 [0.9126,0.9304] vs momentum, and 0.9268 [0.9031,0.9495] vs RMSprop (descriptive 95% intervals, not a second confirmatory family). The benefit is not exclusively startup: after the first 500 samples, mean risk was 0.00476 guarded vs 0.01167 classical, 0.03651 Adam, 0.01188 SGD and 0.00666 RMSprop. This is a post-hoc block summary of the frozen runs, not further tuning.

Shock estimates are highly uncertain. Guarded/classical ratio 0.1886 has 95% interval [0.0522,1.9653]; ratios vs Adam and RMSprop are 1.1087 [0.7183,1.8287] and 1.1490 [0.7478,1.8836]. Classical's median seed MSE was 0.2453 but its maximum was 24.3247; guarded's median was 0.3110 and maximum 1.7985. The large classical mean is driven by a severe finite outlier, not proof of a robust guarded win. Twelve seeds cannot characterize this tail reliably.

## Mechanism, ablation, recovery and transfer

The primary guarded selection activated the safeguard about 9.9 times per 6000 steps. Maximum applied gain across all guarded evaluation runs was 1.0000000000000009 (roundoff). At the end of primary tracking, mean learned drifting/nuisance rates were 0.00910/0.00243 guarded versus 0.02015/0.00963 classical. This is consistent with the guard trading faster tracking for lower noise-fitting rates, not evidence that small rates identify relevance.

Removing the safeguard while retaining the selected guarded lr/theta caused failures in 11/12 primary, 12/12 stationary, 4/12 shock, 11/12 permuted and 11/12 timing runs. Those losses are retained with null full-stream scores, never averaged away. This proves those aggressive guarded settings depend on the safeguard; it does **not** prove superiority to retuned classical IDBD, which wins primary tracking. SGD includes mu=0 as the LMS boundary. No full-Autostep comparison was made.

All five separately tuned methods completed all 60 evaluation streams per method without hitting the declared instability threshold; the matched removal ablation failed 49/60. This is not absence of large finite losses: maximum shock risk was 194.8 for classical and 172.9 for SGD, versus 4.61 guarded. The stopping threshold was 1e6, not a quality threshold.

Common-threshold recovery on primary tracking (100-sample mean risk <=0.02, sustained for 100 further endpoints):

| Optimizer | Recovered changes / 36 | Conditional median samples |
|---|---:|---:|
| Guarded IDBD | 36/36 | 396.5 |
| Classical IDBD | 36/36 | 382.0 |
| Adam | 19/36 | 981.0 |
| Momentum SGD | 8/36 | 916.0 |
| RMSprop | 12/36 | 845.5 |

Unrecovered changes are right-censored, not discarded when counting successes; conditional medians are not unconditional speed rankings. Return to each method's own pre-change risk differs: guarded recovered 31/36 with conditional median 867 samples, classical 27/36 with median 557. Per-change own-baseline thresholds, censor horizons, and scored-time-based seconds estimates are stored in `evaluation.json`. No recovery event exists in the stationary control.

Persistent depression is visible around the shock: guarded mean drifting-coordinate alpha falls from 0.01367 at sample 2000 to 0.000739 at 2500 and remains 0.000721 at 3000. By the end it rebounds to 0.00856, while nuisance alpha averages 6.4e-7. These sparse snapshots support the intended failure mechanism but do not isolate its exact causal contribution. Guarded recovered 32/36 shock-regime changes; conditioning on these recoveries would hide its failures to recover.

Held-out coordinate permutations preserved MSE within 4.84e-12 across successful method/seed pairs. Shifted change times retained the qualitative ranking: guarded MSE 0.21902 vs classical 0.18930, Adam 0.31061, SGD 0.20256 and RMSprop 0.27991. Guarded/SGD ratio became 1.0812 [1.0579,1.1044]. The prototype is not an all-baseline winner at nearby timings.

## Tuning sensitivity

All methods received 16 configurations per regime. Entries below are number within 10% of best / number having at least one divergent tuning seed, each out of 16. Full scores and seed-level losses are in `results/tuning.json`.

| Optimizer | Tracking | Stationary | Shock |
|---|---:|---:|---:|
| Guarded IDBD | 2 / 0 | 8 / 0 | 2 / 0 |
| Classical IDBD | 1 / 5 | 4 / 4 | 2 / 7 |
| Adam | 2 / 0 | 4 / 0 | 2 / 0 |
| Momentum SGD | 3 / 7 | 2 / 7 | 2 / 7 |
| RMSprop | 1 / 0 | 1 / 0 | 1 / 0 |

Selected hyperparameters (tracking; stationary; shock):

- Guarded (lr,theta): (0.1,0.01); (0.1,0.1); (0.001,0.1).
- Classical (lr,theta): (0.01,0.01); (0.01,0.1); (0.01,0.001).
- Adam (lr,beta1,beta2): (0.01,0,0.999); (0.01,0,0.99); (0.01,0,0.999).
- SGD (lr,mu): (0.01,0.5); (0.01,0); (0.001,0.9).
- RMSprop (lr,rho): (0.01,0.999); (0.001,0.9999); (0.01,0.999).

Guarded tracking/stationary lr, several theta choices and several memory choices are boundaries. The grid is sparse and many minima are narrow. These are best-found settings within an equal budget, not mathematical optima; no stronger family-level claims or post-evaluation grid expansion are warranted. A broader separately preregistered study would need new held-out seeds.

## Correctness and compute cost

45 tests passed; 4 CUDA-only cases were skipped on this CPU-only installation. Independent scalar/list references test recurrence agreement, old-h/new-alpha ordering, initialization, inactive features, global multi-group normalization, closure behavior, registration conflicts, dtype/device and state restoration. Harness tests cover pre-update scoring, pairing/permutation, recovery censoring, failure evidence and freeze integrity. Editable installation was exercised.

Before scaling, five warmed equal-length 1500-step core repetitions per optimizer measured:

| Optimizer | Core seconds / million samples | Scored-loop seconds / million samples |
|---|---:|---:|
| Guarded IDBD | 190.3 | 279.9 |
| Classical IDBD | 147.1 | 242.6 |
| Adam | 55.6 | 104.6 |
| Momentum SGD | 26.0 | 53.5 |
| RMSprop | 45.8 | 90.0 |

These are the pre-sweep profile in `throughput.json`, not intrinsic algorithm complexity. Core includes prediction, analytical gradient and public optimizer validation; it excludes generation, scoring and rich traces. Guarded Python validation and host scalar reductions cost real time. It is less computationally efficient than the built-in baselines even where it is more sample-efficient. Scored-loop profiling preceded diagnostic-only logging fixes; measured per-run evaluation times are also retained.

Completed tuning: 599.0 seconds, 720 candidate-stream trials, 105 failed trials, 3737361 committed updates. Evaluation: 352.3 seconds, 360 runs, 1884819 committed updates. Combined completed phases: 15.85 single-core minutes. A prior tuning attempt consumed another 117.4 seconds before a diagnostic norm overflow broke JSON serialization; it was fixed/tested and the same complete tuning protocol rerun before evaluation. Setup, tests, exploration, short reproductions and profiling are additional overhead, not included in those phase totals. No parallel training or oversubscribed BLAS was used.

Initial profile peak RSS was 306.2 MiB; evaluation peak was 348.1 MiB. The experiment's arrays and JSON artifacts initially occupied about 26.3 MiB, with full per-step risk only transient and snapshots every 500 samples. Environment: Intel Core i7-1165G7 @ 2.80GHz, Python 3.13.15, torch 2.10.0+cpu, NumPy 2.5.3, Linux x86_64; versions/thread settings are recorded in every phase. All GPU performance/generalization is untested.

## Delivery and remaining limits

Run/install/test and reproduction commands are in `README.md`; machine-readable evidence is in `results/`. Post-evaluation `audit_artifacts.py` independently re-reduces saved runs without importing the optimizer; all source/provenance hashes, raw means, selections and sensitivity checks passed (`results/audit.json`). It does not independently recalculate bootstrap quantiles. The pre-evaluation independent review found three failure-observability defects, all fixed and regression-tested before freezing. The final independent evidence audit found no issues and returned **OK with notes**; its scope and execution limitations are recorded in `docs/review.md`.

Limitations: one synthetic diagonal linear family, one horizon, independent noise, zero initialization, three tuning seeds, twelve evaluation seeds, fixed learning rules without schedules, sparse finite grids, no full Autostep baseline, no rotated/correlated or neural tasks, and limited tail/failure power. Permutations are not a test of general feature reparameterization. The useful outcome is a working, tested optimizer and a measured boundary of this safeguard's usefulness—not a new universal optimizer or a rescued primary success claim.
