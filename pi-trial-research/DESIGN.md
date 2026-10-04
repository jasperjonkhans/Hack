# Linear IDBD prototype: approved design

Decision: use only the persistent aggregate-gain safeguard from Mahmood et al. (2012) Autostep, not full Autostep and not a novelty claim. User requested continuation after the recommended design was presented. Lead owns experimental conclusions. Existing `IDBD-comparison.md` is preserved byte-for-byte.

## Scope and public contract

Scalar online linear prediction with one observation and half-squared error: e = y - x^T w, loss = e²/2, g = -e x, curvature c = x². No weight decay, clipping, schedules, batch averaging, generic neural-network claims, or gradient-squared substitution.

Implement `idbd.IDBD(params, lr=..., meta_lr=...)` as faithful classical IDBD and `idbd.GuardedIDBD(params, lr=..., meta_lr=...)` as the separately named prototype. Both subclass `torch.optim.Optimizer` (directly or by inheritance). Group-specific lr and meta_lr are supported. `lr` initializes the learned rates; subsequently the log rates live in state. Importing the package has no global side effect. `idbd.register_torch_optimizer()` explicitly registers `torch.optim.IDBD` as classical IDBD; may also register `torch.optim.GuardedIDBD`. Registration is idempotent and refuses conflicting existing attributes. No installed torch files are changed.

Recommended step signature: `step(closure=None, *, feature_sq: Mapping[Tensor, Tensor])`. Same-example feature squares are supplied for every parameter with a gradient, matching shape, dtype and device. No broadcasting or silent coercion. Parameters with grad=None are skipped and need no curvature. A currently absent feature is represented by zero gradient AND zero feature square, not grad=None. Guard normalization applies to all participating coordinates, including these zero-feature coordinates. Validate inputs before mutating weights/state so ordinary contract errors do not partially update groups. Dense real floating tensors only; reject sparse/complex data and nonfinite/negative curvature. Unsupported modes raise clear errors.

The optional closure executes exactly once with grad enabled, must zero/recompute grads for this same fixed example, and returns the loss. Step runs updates without graph retention and returns the closure loss, or None without a closure. It never implicitly recomputes the loss, clears gradients, or requests higher-order autograd. Direct analytical gradient assignment is equivalent and is used for cheap linear experiments.

## Equations and update order

Initialize beta = log(lr), h = 0. All gradients and curvature are at pre-update weights. First compute, with OLD h:

b = beta - meta_lr * g * h

Classical: beta_new = b; alpha = exp(beta_new).

Guarded: compute log_gain = logsumexp(b_i + log(c_i)) over ALL participating coordinates and parameter groups with c_i>0; an empty sum has log_gain = -infinity. Let ell = max(0, log_gain), beta_new = b - ell, alpha = exp(beta_new). This is a stable implementation of dividing every stored alpha by max(1, sum_i(exp(b_i)*c_i)). It is persistent, not just a current-update cap. It may depress rates of currently absent features. All parameter groups belong to the same scalar prediction. Use a numerically stable global reduction; scalar conversion/synchronization across devices may be documented if needed.

For both:

w_new = w - alpha * g

h_new = h_old * max(0, 1 - alpha * c) - alpha * g

Use NEW alpha for both w and h; don't recompute g between them. Do not differentiate through the safeguard: this is an explicitly heuristic diagonal trace, following the source convention, not exact differentiation of the guarded map. Classical has no beta clamps/floors/hidden protections. Guard prevents finite aggregate sample overshoot, not every overflow in a meta-update, arbitrary noise, or nonlinear instability. Nonfinite failures must be reported rather than silently repaired.

## Correctness evidence

Incremental test-first vertical slices: independent simple scalar/list reference; initialization and first/second update; old-h/new-alpha ordering; trace positive part; zero-feature behavior; guarded aggregate normalization across groups including absent coordinates; theta=0/guard-off ablations; param-group overrides and add_param_group; mapping validation and no partial update on ordinary input errors; closure one call and return; state_dict load into fresh optimizer followed by identical continuation; float32/float64 CPU and CUDA conditionally if available; device/dtype consistency; registration subprocess isolation/conflict. Save weights separately from optimizer state as PyTorch normally requires.

## Experiment ownership and provisional niche

Lead implements benchmark harness after optimizer writer hands off. One single-thread CPU training worker, float64, analytical g=-e*x, no graphs. First measure throughput and RSS. Exploration, tuning and held-out seeds are disjoint. Start with three regimes only: variance-preserving sparse heterogeneous tracking, stationary control, and an extreme-burst nearby failure regime. Final dimensions, horizon, budgets, recovery thresholds, feature permutations and timing perturbations will be frozen before held-out evaluation in `PROTOCOL.md` and JSON configurations. Report measured losses, no promised victory. Optimizers receive only gradients and same-example feature squares, never oracle coefficients, masks, timing or future data.

## Environment and boundaries

Project: `/home/jasperjonkhans/Hack/pi-trial-research`; Git root is its parent. Local `.venv/bin/python` uses Python 3.13, CPU PyTorch 2.10.0, NumPy, pytest. Set OMP_NUM_THREADS=MKL_NUM_THREADS=OPENBLAS_NUM_THREADS=NUMEXPR_NUM_THREADS=1 before importing numerical packages and torch.set_num_threads(1). Do not modify parent repository configuration, install system packages, commit, or stage. Do not edit `IDBD-comparison.md` or `research/sources/`.
