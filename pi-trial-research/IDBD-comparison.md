# IDBD versus Adam, RMSprop, and SGD with momentum

## Bottom line and evidence status

IDBD is a plausible specialist for **online linear prediction with persistent differences in how quickly coordinates should adapt**: some coefficients drift while others remain fixed or irrelevant. Its defining advantage is not merely having a learning rate per parameter; Adam and RMSprop also have coordinate-wise gains. IDBD uses an approximate meta-gradient to learn whether previous updates should have been larger or smaller.

There is no established universal ordering. In particular, a learned small step size can protect a stable coefficient and impede its recovery after an unexpected change. The following experiments are **proposed, not executed**. Predictions below are hypotheses unless explicitly attributed to an experiment in a primary source.

### What the literature actually establishes

- **Sutton (1992) [1]:** linear regression, 20 independent Gaussian inputs, five relevant coefficients whose signs change, and 15 permanently irrelevant inputs. IDBD substantially improved tracking over scalar-step LMS. A separate experiment found rates close to the best tested fixed rates for that particular task. This was not an Adam, RMSprop, or momentum comparison; relevance did not change, and the near-optimal-rate experiment ran for 250,000 observations.
- **Degris et al. (2024) [2]:** direct synthetic linear comparisons with Adam/RMSprop, ordinary SGD, and an oracle coordinate-wise SGD baseline. IDBD performed well on weight flipping and a one-dimensional noisy random-walk tracking task. Increasing drift could make RMSprop decrease its gain when the useful gain should increase. Their normalization-versus-optimization distinction is useful, but these examples do not establish dominance on arbitrary streams or over a broadly tuned momentum baseline.
- **Mahmood et al. (2012) [3]:** original IDBD's meta-step-size sensitivity motivated Autostep's meta-update normalization and aggregate-step safeguard. Autostep is a different optimizer, not evidence that vanilla IDBD is tuning-free.
- **Neural networks:** the original result is linear. Konen and Koch (2014) [8] found saturation-related instability in a straightforward nonlinear extension and added stabilization in n-IDBD. Their comparisons were not against modern Adam. Oak Lab's NetworkIDBD demonstration [9] is another, distinct neural variant; its qualitative noisy-MNIST visualizations do not establish a reproducible, equal-budget four-optimizer ranking. Adam [4] and momentum [6] have direct neural-network experimental evidence, but that evidence likewise does not settle the linear tracking comparison here.

## 1. Exact algorithms under comparison

Use one sample per update, a linear predictor, and half-squared loss. At observation t, all gradients are evaluated at the same pre-update weights:

e_t = y_t - x_t^T w_(t-1); L_t = e_t^2 / 2; g_t = -e_t x_t.

All vector arithmetic below is coordinate-wise unless a dot product is written. Initialize weights to zero. Initialize momentum, second moments, and IDBD traces to zero. Use float64, no regularization, weight decay, clipping, schedules, batch accumulation, or change-point resets in the main comparison.

### Original linear IDBD [1]

Initialize beta_i,0 = log(alpha_0), h_i,0 = 0. For every coordinate, in this order:

beta_i,t = beta_i,t-1 + theta e_t x_i,t h_i,t-1

alpha_i,t = exp(beta_i,t)

w_i,t = w_i,t-1 + alpha_i,t e_t x_i,t

h_i,t = h_i,t-1 max(0, 1 - alpha_i,t x_i,t^2) + alpha_i,t e_t x_i,t

Use the old h in the meta-update and the new alpha in both subsequent updates. Compute the residual once, before any coordinate updates. The positive-part operation belongs to the trace update; it is not gradient clipping or a bound on the total weight update. Do not silently introduce beta floors, bounded exponents, normalization, or Autostep safeguards.

Here h is an approximate sensitivity of the weight to its log step size, not an ordinary exponential-average gradient. The derivation neglects cross-coordinate sensitivities. Positive agreement between the current correction and h increases alpha; negative agreement decreases it. Uncorrelated corrections do not, by themselves, imply a negative expected meta-update.

### Classical SGD with momentum [6]

u_t = mu u_(t-1) + g_t

w_t = w_(t-1) - eta u_t

This is unnormalized heavy-ball momentum, not Nesterov momentum. Using (1-mu)g_t instead would change the numerical interpretation of eta. Include mu = 0 as a tuning boundary and ablation.

### Uncentered RMSprop [5]

v_t = rho v_(t-1) + (1-rho) g_t^2

w_t = w_(t-1) - eta g_t / (sqrt(v_t) + epsilon)

No momentum, centering, or bias correction. Fix epsilon = 1e-8 outside the square root. This specifies an implementation convention where library variants differ.

### Original bias-corrected Adam [4]

m_t = beta_1 m_(t-1) + (1-beta_1) g_t

v_t = beta_2 v_(t-1) + (1-beta_2) g_t^2

mhat_t = m_t / (1-beta_1^t); vhat_t = v_t / (1-beta_2^t)

w_t = w_(t-1) - eta mhat_t / (sqrt(vhat_t) + epsilon)

Use epsilon = 1e-8; no AMSGrad or AdamW. Adam with beta_1 = 0 is close to RMSprop after initialization, but its second-moment bias correction makes the specified algorithms different initially.

**Mechanistic distinction:** IDBD changes the magnitude of the current-gradient update using estimated consequences of past learning. RMSprop rescales the current gradient by its recent magnitude. Adam combines that rescaling with directional memory. Momentum changes the update direction using past gradients. A large gradient can reflect useful drift, noise, scale, or curvature; moment normalization does not identify which explanation is correct.

## 2. Concise comparison matrix

Except for the narrow cited results, these are falsifiable expectations, not measured rankings.

| Condition | IDBD | Adam / RMSprop | SGD + momentum |
|---|---|---|---|
| Repeated heterogeneous drift, independent nuisance inputs | Strongest candidate niche: preserve large tracking rates and reduce nuisance updates [1,2] | Gradient magnitude may not distinguish stable from changing coordinates; can still compete after tuning | One global rate trades tracking against nuisance variance; momentum can help persistent movement |
| Stationary, well-scaled, similar coordinate time scales | May tie; meta-learning can add cost without a useful degree of freedom | May tie; normalization need not help | Strong inexpensive baseline; momentum helps persistent directions |
| Independent observation noise | Smaller learned gains may lower excess error; noisy meta-updates can also destabilize | RMSprop damps large gradients; Adam additionally smooths directions, without identifying irreducible noise | Small eta and momentum can reduce variance; fixed large rates leave a noise floor |
| Unexpected late change or new relevance | Potentially slow after alpha has been suppressed | RMSprop avoids stale first-moment direction; Adam may initially lag | Momentum can overshoot reversals; tuned low momentum may win |
| Unequal feature scales | Can learn different rates, but initialization/transients matter | Often useful early magnitude normalization; not general invariance to feature reparameterization | Scalar rate is constrained by large-curvature coordinates |
| Strong correlation / near-collinearity | Diagonal update and approximate credit assignment are limiting | Coordinate-wise normalization also cannot whiten rotated curvature | Momentum can accelerate some low-noise quadratic dynamics, but cannot resolve non-identifiability |
| Rare active features | Trace and rate freeze while feature is absent; potential event-time advantage | Small decayed denominators may help rare gradients or create bursts; Adam has residual momentum | Residual velocity can move a weight without a current gradient; few observations constrain every method |

A statistical non-result is not a tie. A tie requires an uncertainty interval narrow enough to exclude practically important differences.

## 3. IDBD's operating limits and alternative explanations

1. **Selective adaptation is not feature selection.** The benefit arises when fitting noise in nuisance coordinates harms prediction while other coordinates require continuing updates. IDBD does not prune inputs or shrink weights directly; a small alpha can freeze an incorrect weight. A useful, already-learned stationary feature can also have a small alpha. With correlated inputs, a zero target coefficient need not mean a marginally useless feature. Test known-coefficient error and prediction risk, not alpha-based “relevance accuracy” alone. Better scale conditioning or a favorable initialization can explain an apparent win instead of selective tracking.

2. **Protection and reactivation conflict.** The trace contains alpha-scaled updates, so a tiny alpha can make subsequent meta-learning slow. This is a mechanism-based prediction, especially after long stationary periods; neither [1] nor its permanently irrelevant-feature experiment establishes rapid relevance reversal. It is not exact permanent freezing in real arithmetic. Underflow to zero is an additional implementation failure. Test late support changes and state-reset diagnostics.

3. **Meta-rate sensitivity is real, but retuning can address part of it.** The meta-update e*x*h has units of target squared [3]. Under y -> c*y, equivalent linear-IDBD trajectories require theta -> theta/c^2, with appropriately scaled weights/traces. Thus a fixed-theta failure is not evidence that the method cannot solve the rescaled task. Conversely, a universal default theta is not justified. Pure feature reparameterization x_i -> s_i*x_i has the corresponding alpha_i -> alpha_i/s_i^2; IDBD must learn that adjustment if initialized with a common alpha. Adam/RMSprop's gradient-magnitude normalization is not full invariance to changing feature coordinates.

4. **Positive rates and trace clipping do not guarantee stability.** On the current example, the linear weight update multiplies its residual by 1 - sum_i(alpha_i*x_i^2). Bounding each trace decay does not bound that sum. Large gradients can trigger large exponential meta-updates; Gaussian tails and sparse variance-compensated activations can expose this. Bad theta/alpha_0 choices are tuning failures; missing aggregate protection is a property of the specified algorithm. A clipped or normalized implementation must be reported as a separate variant. Autostep addresses these issues but is outside the main comparison [3].

5. **Diagonal geometry is a structural limitation.** IDBD omits cross-coordinate meta-sensitivities and cannot learn a general matrix preconditioner. Adam/RMSprop share the diagonal-geometry limitation, though not the identical meta-gradient approximation. Whitening may rescue all of them. At exact collinearity, recovering individual coefficients is statistically impossible even if prediction is excellent. This is not optimizer failure.

6. **Temporal agreement is fallible evidence.** Too-fast target changes may offer insufficient predictable structure; correlated gradient noise can imitate useful agreement. Very rare features supply few learning and meta-learning observations. No optimizer can anticipate an unobserved arbitrary coefficient change. The pilot's independent Gaussian observation noise does not establish robustness to outliers, autocorrelated gradients, or changing feature representations.

7. **Competitor weaknesses also need scope.** Adam/RMSprop have explicit convex non-convergence counterexamples [7], not universal failure on IID Gaussian regression. Stale momentum or second moments depend on tunable memory lengths. Stationary problems permit decaying-rate schedules that can reduce noise floors; the fixed-rate pilot deliberately measures online tracking, not the best possible scheduled stationary learner.

All four methods have O(d) update time and storage. IDBD has beta and h state plus exponentiation; Adam has two moment vectors; RMSprop and momentum each have one principal state vector. Constant factors and implementation quality, not asymptotic notation, decide runtime. Sample efficiency and computational efficiency must be reported separately.

## 4. One concrete pilot: controlled linear tracking

### Data-generating process

Use d = 20, T = 40,000, observations indexed from 1, no intercept, and y_t = x_t^T w*_t + sigma*epsilon_t with independent standard-normal epsilon_t.

Generate independent standard-normal innovations z before these transformations. Give pairs (1,2) and (3,4) covariance rho: replace the second component of each pair by rho*z_first + sqrt(1-rho^2)*z_second. Other coordinates remain independent. Call the resulting covariance C_rho.

Let A activate coordinates 1–8 in the base condition and all 20 in the high-nuisance condition. Coordinates 1–4 carry signal; other active coordinates have zero target coefficients. Keeping the allocated dimension fixed avoids changing implementation size when adding nuisance inputs.

Let D = diag(s_i). Features are x_i,t = s_i*A_i*M_pair(i),t*z_i,t/sqrt(p). There are ten fixed adjacent-coordinate pairs; independently for each pair and time, M is Bernoulli(p), shared by the two coordinates in that pair. Thus sparsity changes observation frequency while preserving population covariance, including the deliberately correlated pairs:

Sigma_x = D A C_rho A D.

Use b_t = (q_t, -q_t, 0.5, -0.5, 0, ..., 0) and

w*_t = D^(-1) b_t / sqrt(b_1^T C_rho b_1).

Endpoint signal variance is one in every condition. Changing feature units therefore does not change the clean prediction task. During gradual reversals, signal variance falls temporarily; do not rescale targets dynamically, which would also change the supposedly stable coefficients.

### Three target schedules

- **Stationary:** q_t = 1 throughout.
- **Abrupt:** q_t = (-1)^floor((t-1)/10000); changes at 10,001, 20,001, and 30,001.
- **Gradual:** at each of those times tau, interpolate from the previous sign to the next using q_t = q_old + (q_new-q_old)*min((t-tau+1)/2000, 1). Remain at the endpoint until the next transition.

The same underlying inputs and observation-noise innovations are reused across schedules and optimizers.

### Seven settings, each run under all three schedules

| Setting | Change from base | What it isolates |
|---|---|---|
| Base | rho=0, s_i=1, four active nuisance inputs, sigma=0.1, p=1 | Well-conditioned reference and stationary control |
| Correlation | rho=0.95 | Cross-coordinate coupling with fixed marginal variances and endpoint signal variance |
| Scaling | s_i=0.1 for odd i, 10 for even i | Feature units / diagonal conditioning, with identical clean targets to base |
| Irrelevance | Activate all 16 nuisance coordinates | Extra noise-fitting directions without extra signal |
| Noise | sigma=1 | Observation noise, not coefficient drift |
| Sparsity | p=0.1 | Fewer informative observations at matched covariance; also larger active-sample curvature |
| Combined | Apply all five changes | Interactions, only after inspecting the isolated manipulations |

This is 21 cells in one model, not a full factorial. The combined condition cannot identify individual interactions; any surprising combined result needs a targeted two-factor follow-up. Variance-preserving sparsity necessarily changes higher moments: a companion unamplified-mask diagnostic distinguishes rare observations from large activation bursts.

### Reproducibility and fair tuning

- Use NumPy Generator(PCG64(SeedSequence([seed, channel]))), with channels 0, 1, and 2 for the T-by-20 normal inputs, T-by-10 uniform mask draws, and T noise innovations respectively. Draw masks by comparing uniforms with p. Generate complete arrays once; save stream files, SHA-256 hashes, actual library versions, and the configuration manifest.
- Tuning seeds: 0–3. Evaluation seeds: 100–119. Pair seeds and saved streams across methods. Evaluation streams never select hyperparameters. Every run starts fresh; never carry optimizer state between streams.
- All weights and auxiliary states start as specified above. Common initial weights are comparable; equal numeric learning rates are not assumed to mean equal initial update magnitudes.
- Give every optimizer exactly 32 candidate configurations per cell: eight rates eta or alpha_0 = 10^linspace(-5,-1,8), crossed with four settings. IDBD theta in {1e-5,1e-4,1e-3,1e-2}; momentum mu in {0,0.5,0.9,0.99}; RMSprop rho in {0.9,0.99,0.999,0.9999}; Adam (beta_1,beta_2) in {0,0.9} x {0.99,0.999}.
- Select the minimum mean observed pre-update MSE across all T samples and the four tuning streams. Break exact ties lexicographically by the listed parameter tuple. These are **best-found settings within an equal budget**, not mathematical optima. Boundary winners or narrow isolated minima require a separately declared equal-budget expanded/refined search before strong conclusions.
- Freeze selections before evaluation. Report per-cell tuned results and, separately, the configuration selected on the base-abrupt cell transferred unchanged to every cell. Plot all 32 tuning outcomes, failure counts, and the fraction within 10% of the best tuning score. Report transfer degradation on held-out streams, so “robustness” is not inferred solely from the selected minimum.
- Count equal numbers of complete-stream trials, not equal wall-clock tuning time; report actual tuning compute as another cost. Use the same programming framework, precision, and core implementation quality for all methods.

### Measurements and failure policy

Log predictions before every update, with:

- Observed squared error e_t^2 and its cumulative sum; full-horizon MSE is primary. Also show initial learning, later tracking, and per-change windows separately rather than hiding warm-up costs.
- Clean per-sample error (x_t^T(w_(t-1)-w*_t))^2 and exact population excess risk R_t = (w_(t-1)-w*_t)^T Sigma_x (w_(t-1)-w*_t). The latter separates optimizer error from irreducible observation noise. Oracle coefficients/covariance are for scoring only.
- Recovery after each completed change: first point at which the trailing 500-sample mean of R is at most 0.02, sustained for another 500 samples. Endpoint signal variance is one, making this a common absolute target. Do not allow windows to start before the change completes; censor at the next change or stream end. Also report return to each method's pre-change risk, clearly labeled because a worse baseline can make that measure easier.
- Coordinate coefficient errors; IDBD beta, alpha, h, and e*x*h; RMSprop eta/(sqrt(v)+epsilon); Adam eta/(sqrt(vhat)+epsilon), mhat, and actual updates; momentum velocity. Adam's denominator gain alone is not its full learning rate relative to the current gradient. Group traces by drifting, stationary-useful, and nuisance coordinates.
- Instability: nonfinite states, maximum risk/update norm, IDBD aggregate gain sum(alpha*x^2), and failure fraction. Terminate a run on nonfinite state or R > 1e6 and give its tuning score +infinity. Retain the failure in evaluation reports; do not average only successful runs and call the result an overall win. No silent repair or clipping.
- Runtime: optimizer/core-training time per million samples and end-to-end time, separately from stream generation, rich logging, and tuning. Warm compilation, use one CPU thread on the same machine, five repetitions, and identical numbers of completed updates. Report memory and seconds-to-recovery alongside samples-to-recovery. Short failed runs do not count as fast successful learning.

Report paired differences across 20 evaluation seeds and seed-level bootstrap 95% intervals (10,000 resamples, bootstrap seed 4242), not confidence intervals treating dependent time steps as independent. Treat other cells as diagnostic when making a prespecified primary contrast: IDBD versus each comparator in high-nuisance abrupt tracking. Apply multiplicity control if making formal multiple-win claims. Define practical equivalence in advance, for example a paired risk-ratio interval entirely within [0.95,1.05]. Twenty seeds cannot establish extremely rare failure rates.

## 5. Hypotheses and outcome interpretation

| Experiment / contrast | Hypothesis and predicted outcome | Falsifying or contrary outcome | What it would / would not establish |
|---|---|---|---|
| Base stationary versus repeated changes | H1: IDBD's useful advantage is heterogeneous tracking, not generic faster descent. Gains should separate more meaningfully with recurrent drift; momentum may match or win stationary learning. | IDBD benefits only at startup, or equally with all adaptation disabled. | Supports/refutes the tracking explanation in this horizon; neither result ranks optimizers on deep networks. |
| Extra irrelevant features | H2: tuned IDBD's relative tracking benefit grows with nuisance count, accompanied by smaller nuisance updates. | No differential benefit, no selective gains, or matching performance after a single scalar-rate adjustment. | Tests selective adaptation; alpha separation without error improvement is not a strength. |
| Feature scaling | H3: moment methods may gain early; IDBD's transient depends on learning coordinate scales. Known rescaling should remove most of this difficulty. | Advantage persists unchanged after undoing D, or IDBD learns compensation immediately across initializations. | Distinguishes conditioning from relevance learning, not general coordinate invariance. |
| Correlation | H4: IDBD's advantage shrinks with strong coupling; all diagonal methods can suffer. | No loss across seeds/rates despite the ill-conditioned direction carrying signal. | Tests practical sensitivity in this construction; good prediction does not establish correct individual coefficients or an exact meta-gradient. |
| Observation noise | H5: nuisance suppression may reduce excess error, but larger noise can make IDBD tuning/stability worse; small-rate momentum may tie. | A robust IDBD win without suppression, or moment methods track equally with matched steady error. | Tests IID Gaussian noise tolerance. Raw MSE differences alone can be hidden by the noise floor; nothing here establishes outlier robustness. |
| Sparse observations | H6: event-conditioned IDBD memory can help, but activation bursts or insufficient exposures can erase that benefit. | No benefit at matched active-event counts, or an apparent benefit disappears without 1/sqrt(p) amplification. | Separates event memory from burst magnitude; no method can learn a missing feature's new effect without informative observations. |
| Combined condition | H7: isolated benefits may not add; scale/noise/burst interactions may dominate. | Additive effects accurately predict the combined results. | Detects interaction, not its cause; do not attribute a combined win solely to relevance learning. |
| Late relevance diagnostic below | H8: previously suppressed coordinates may reactivate slowly, and resetting their meta-state should help if suppression is causal. | Fast recovery despite tiny old gains, or resetting meta-state does not help. | Tests protection-versus-reactivation. Reset uses oracle change information and is not an eligible competitor. |

### Targeted ablations, not more leaderboard entries

- **Disable or freeze adaptation:** theta=0 gives plain constant-step LMS; this is already present as mu=0 in the momentum grid. Additionally freeze learned IDBD rates just before sample 20,001, keeping weights unchanged. Equality with the frozen run would attribute the benefit to a learned fixed diagonal profile, not continuing meta-adaptation.
- **Late relevance:** in the uncorrelated high-nuisance abrupt cell, swap coefficients 1 and 9 from sample 30,001 onward, after applying that scheduled sign change. Coordinate 9 was previously observed but irrelevant. Compare normal IDBD with a checkpoint copy that resets only beta_9 to log(alpha_0) and h_9 to zero, retaining weights. Measure both global-sample and active-observation recovery.
- **Conditioning controls:** undo known D in the scaling cell; separately whiten the correlation cell using its known covariance and transform target coefficients consistently. Improvement across all optimizers implicates geometry, not uniquely IDBD's approximation; whitening also changes the coordinate representation.
- **Momentum controls:** compare Adam beta_1=0 with beta_1=0.9, and momentum mu=0 with nonzero mu, using their best rate within the registered grid. If reversal costs disappear with short memory, blame the setting rather than an unavoidable property of the optimizer family.
- **Noise-fitting / initialization controls:** oracle-clamp truly irrelevant weights to zero as a non-competitive diagnostic. Repeat the key nuisance cell from shared nonzero initial weights w_i,0 ~ N(0,0.1^2)/s_i. If IDBD's benefit requires starting nuisance weights exactly at their true optimum, narrow the claim accordingly.
- **Unit check:** multiply all targets and noise by c=10. Compare unchanged IDBD theta against theta/c^2; the latter should preserve scaled trajectories. For corresponding trajectory checks, momentum retains eta; RMSprop/Adam require eta and epsilon multiplied by c. This separates predictable unit sensitivity from a failure to adapt.

A negative 40,000-sample result rules out a benefit at that horizon and budget, not a benefit after the much longer meta-learning periods used in some literature. A positive result establishes a useful operating region, not global superiority. The actionable outcome is a map of error, recovery, tuning fragility, and compute—not a single aggregate winner.

## Primary sources

[1] Sutton (1992), *Adapting Bias by Gradient Descent: An Incremental Version of Delta-Bar-Delta*. https://cdn.aaai.org/AAAI/1992/AAAI92-027.pdf

[2] Degris, Javed, Sharifnassab, Liu, Sutton (2024), *Step-size Optimization for Continual Learning* (arXiv manuscript). https://arxiv.org/abs/2401.17401

[3] Mahmood, Sutton, Degris, Pilarski (2012), *Tuning-Free Step-Size Adaptation*. https://sites.ualberta.ca/~pilarski/docs/papers/Mahmood_2012_Autostep_ICASSP.pdf

[4] Kingma and Ba (2015), *Adam: A Method for Stochastic Optimization*. https://arxiv.org/abs/1412.6980

[5] Tieleman and Hinton (2012), RMSprop, *Neural Networks for Machine Learning*, lecture 6. https://www.cs.toronto.edu/~tijmen/csc321/slides/lecture_slides_lec6.pdf

[6] Sutskever, Martens, Dahl, Hinton (2013), *On the Importance of Initialization and Momentum in Deep Learning*. https://proceedings.mlr.press/v28/sutskever13.html

[7] Reddi, Kale, Kumar (2018), *On the Convergence of Adam and Beyond*. https://arxiv.org/abs/1904.09237

[8] Konen and Koch (2014), *Adaptation in Nonlinear Learning Models for Nonstationary Tasks*. https://www.cmap.polytechnique.fr/~nikolaus.hansen/proceedings/2014/PPSN/papers/8672/86720292.pdf

[9] Oak Lab, *Learning from Experience Instead of Curated Datasets* (author research post; distinct from a fully specified comparative optimizer paper). https://www.oaklab.ai/posts/learning-from-experience-instead-of-curated-datasets
