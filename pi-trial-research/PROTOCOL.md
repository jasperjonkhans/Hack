# Small IDBD pilot protocol

Status: candidate and protocol fixed after the seed-9101 exploration and independent pre-evaluation review; no held-out results viewed. User approved the bounded single-core sweep. The final version of this file, all implementation/test sources, full configuration and tuning selections will be SHA-256 frozen by `benchmark.py freeze` before evaluation. Any subsequent source change invalidates that manifest. This is a deliberately smaller pilot, not execution of the 21-cell proposal in `IDBD-comparison.md`.

## Prespecified question

Does classical IDBD plus the persistent Autostep aggregate-gain safeguard beat best-found equal-budget classical IDBD, Adam, momentum SGD and RMSprop in sparse heterogeneous tracking? No universal superiority, tuning-free behavior, new algorithm, or neural-network generality is claimed. `DESIGN.md` and `docs/optimizer.md` define the recurrences and explicit curvature API.

## Streams and information boundary

All indices here are zero-based. T=6000, d=20, float64 CPU, zero initial weights/state. Independent PCG64 channels from SeedSequence([seed, channel]): Gaussian input innovations (0), independent coordinate Bernoulli masks (1), Gaussian observation noise (2), held-out permutation (7).

x_i = Bernoulli(0.1) * Normal(0,1) / sqrt(0.1). Nominal covariance is I; unlike the previous report's larger proposal, masks here are independent by coordinate, not paired. Target w* = (q, -q, 0.5, -0.5, 0,...)/sqrt(2.5), endpoint signal variance 1. y = x^T w* + 0.3*Normal(0,1).

Only three separately tuned regimes:

- `tracking` (primary niche): q reverses at 1500, 3000, 4500.
- `stationary` (control): q=1, otherwise identical innovations and masks.
- `shock` (nearby potential failure): same tracking plus one observation at 2400 replaced by x=30*z, then ordinary feature distribution resumes. This tests whether persistent normalization depresses future learning. It is an intervention on covariates, not an observation-noise outlier. Risk continues to use the nominal identity covariance; observed MSE and clean sampled error include the exceptional sample.

Two untuned held-out checks transfer the tracking selections: `permuted` permutes all feature and target coordinates consistently using channel 7, preserving y; `timing` moves reversals to 1140, 3360, 4980. These check coordinate labels and schedule overfitting; permutation is not a rotation/conditioning test.

Optimizers see current gradient only, plus same-sample x² for IDBD variants. They never see coefficients, masks/relevance groups, future examples, change labels, or resets. Oracle targets are read only by the scorer. Inputs are generated in advance solely for pairing/storage, not supplied en bloc to the optimizers. Analytical gradients exactly match half-squared-loss backward; no graphs are retained. IDBD legitimately receives additional feature information required by its recurrence; this is not a gradients-only API comparison.

## Separation, budgets, and selection

- Exploration seed: 9101 only. One hand-chosen configuration per method/regime; outputs are diagnostic, not confirmatory. Throughput uses this exploration seed on a shortened stationary stream.
- Tuning seeds: 0,1,2. Each method/regime receives exactly 16 configurations, each evaluated on the same three full streams; divergent runs may terminate early but consume their full candidate slot.
- Held-out seeds: 100–111 (12 paired seeds), never used for choosing the candidate, generator or hyperparameters. Shorter than the prior report's proposed 20-seed, 40000-step experiment; uncertainty will be correspondingly limited.

Rates = {0.0001,0.001,0.01,0.1}, crossed with four family settings: IDBD/guarded theta={0.0001,0.001,0.01,0.1}; SGD momentum={0,0.5,0.9,0.99}; RMSprop rho={0.9,0.99,0.999,0.9999}; Adam (beta1,beta2) in {0,0.9} x {0.99,0.999}. All are exactly as in config order. SGD is unnormalized heavy ball with no Nesterov/dampening; RMSprop is uncentered/no momentum; Adam is bias-corrected/no AMSGrad/weight decay. Epsilon=1e-8 outside square root. Built-in PyTorch baselines use foreach=False.

Select minimum mean full-horizon observed pre-update MSE on tuning seeds, per regime, with exact ties resolved by configuration index. Any failed seed makes that configuration's score infinity (serialized null with explicit failure records). No successful-only selection. Report all candidate scores, failure counts, fraction within 10% of best, and rate-boundary winners. These are best-found finite-grid configurations, not global optima. Boundary/sparse-grid results cannot justify strong optimizer-family superiority. No post-evaluation grid expansion is eligible for the same held-out evidence.

`matched_unguarded` is an additional untuned ablation: remove the safeguard from the selected guarded configuration, leaving lr/theta unchanged. Separately tuned classical IDBD is the stronger practical ablation. SGD's mu=0 boundary tests constant-step LMS within its grid. Full Autostep is not implemented or ranked; the experiment cannot show improvement over it.

## Measurements and failures

At pre-update weights record e², clean sampled squared error, and exact nominal excess risk R=||w-w*||². Primary selection/contrast is mean observed MSE; report cumulative excess risk sum(R), separating observation noise. No post-update loss substituted for online prediction error. Store 500-observation block means, rates and coefficient errors at 500-step intervals (plus first update); retain full per-step risk only transiently for recovery. Group labels (drifting, stable, nuisance) are used only in logging. Adam's denominator gain is not its effective current-gradient learning rate; momentum velocities and actual update norms remain distinct.

Recovery: first endpoint of a trailing 100-sample mean R<=0.02 whose condition persists for 100 further endpoints, with every window beginning at or after the change. Report the delay to that first endpoint, confirmed by the later samples; minimum delay is 100. Censor at the next change or stream end. Also report return to that method's own pre-change 100-sample mean risk, explicitly a different/easier or harder criterion. Right-censored events are never converted to successes or silently omitted; conditional median recovery is labeled conditional and accompanied by counts. Seconds-to-recovery is an estimate from measured scored-run seconds/sample, not an independent stopwatch measurement.

Instability: nonfinite weights/any optimizer tensor state, nonfinite gradient/meta-update, or pre-update R>1e6. IDBD raises FloatingPointError rather than silently clamping/repairing; harness records failure. Failed evaluation rows retain a null full-stream score and explicit failure; summaries do not average only survivors. Record max finite pre-update risk (including a threshold-crossing failure value)/update norm, guarded intervention count, max uncapped log aggregate gain and applied aggregate gain. Retain prefix diagnostics on failed rich runs and label them as prefixes. Count attempted calls, returned/committed calls (`completed_updates`) and finite-valid updates separately. An all-configurations-failed tuning method saves its failure evidence before aborting. Underflow is not repaired.

## Uncertainty and claims

Pair seeds (not dependent observations). Use 10000 seed-level bootstrap resamples with seed 4242, 95% intervals for paired mean differences and ratios of means. Four prespecified primary family contrasts also receive 98.75% ratio intervals (Bonferroni-style approximate simultaneous coverage). Other cells, ablation and timing checks are diagnostics. A convincing all-baseline niche advantage requires at least 5% lower primary MSE, all four upper simultaneous ratio bounds below 0.95, no hidden failures and no unresolved boundary-grid warning. Practical equivalence requires a 95% ratio interval entirely inside [0.95,1.05]. Lack of evidence is not a tie. Negative and mixed outcomes are publishable; no selectively promoted diagnostic replaces the primary contrast.

## Resource and reproducibility policy

One serial CPU worker; OMP/MKL/OpenBLAS/NumExpr and PyTorch intra/inter-op threads=1. CPU-only torch; no accelerator. Measure five warmed equal-length core-loop repetitions per optimizer (1500 samples each) and separately scored end-to-end loop time, environment versions and process peak RSS before the sweep. Core time includes prediction/gradient/update and optimizer validation, excludes generation/scoring/rich traces; all methods get the same number of successful steps. Tuning/evaluation wall time includes generation/logging/serialization; seconds/M core and scored time are distinct from sample efficiency. Python prototype overhead is not an algorithmic complexity result.

Ask user before the main sweep, quoting estimates from measured throughput. Stop rather than scale threads. Save exact generated arrays as local compressed NPZ under ignored results/streams/, canonical array/changes hashes per run, configuration, every tuning result and evaluation summary, plus freeze manifest. Arrays can be regenerated from seeds/config/recorded software; archives are not embedded in Git. Frozen source checks reject silent implementation/protocol edits and outputs refuse evaluation overwrite.
