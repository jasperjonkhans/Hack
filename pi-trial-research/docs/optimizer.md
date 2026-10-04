# Online linear IDBD

`IDBD(params, lr=0.01, meta_lr=0.01)` is classical IDBD.
`GuardedIDBD` has the same API plus exactly one change: the persistent
aggregate-gain safeguard from Mahmood et al. (2012) Autostep. It is **not full
Autostep**, tuning-free, or novel. Defaults are conveniences, not recommended
universal tuning values. Both are `torch.optim.Optimizer` subclasses.

## Contract

Only one scalar linear prediction per update, with half-squared error:
`e = y - xᵀw`, `g = -e*x`, `feature_sq = x²`. All groups represent coordinates
of that same prediction. This is not a minibatch or neural-network optimizer.
The caller must provide true same-example feature squares; their provenance
cannot be inferred from tensors. **Do not substitute squared gradients.**

```python
from idbd import IDBD
optimizer = IDBD([w], lr=0.01, meta_lr=0.001)
e = y - x.dot(w.detach())
w.grad = -e * x
optimizer.step(feature_sq={w: x.square()})
```

`step(closure=None, *, feature_sq: Mapping[Tensor, Tensor])` requires a
shape/dtype/device-exact curvature tensor for every gradient-bearing parameter.
No coercion or broadcasting. Dense real floating tensors only. Sparse, complex,
negative curvature, unknown mapping keys and unsupported group options are
rejected. Nonfinite inputs, state or update results raise `FloatingPointError`.
No floors, clamps on beta, gradient clipping, weight decay, scheduling,
higher-order differentiation, or implicit gradient-squared fallback are used.
Floating-point underflow may still produce zero rates; it is not repaired.

`grad=None` skips a parameter entirely. An absent feature instead has zero
gradient **and** zero curvature: the guard still shifts its beta. At zero
residual, zero gradient with nonzero curvature still decays the trace.
Curvature entries for known skipped parameters are ignored.

The optional closure runs exactly once under `torch.enable_grad()`. It must
zero and recompute gradients at the current weights for the fixed example and
return the loss. `step` returns that same loss (or `None` without a closure).
It does not clear/change gradients, recompute loss after updating, or build an
update graph. See `examples/linear.py`. Ordinary validation and numerical
failures are detected before optimizer weight/state mutation across all groups;
closure side effects are not rolled back.

## Equations and state

Initialize lazily on first participating step: `beta=log(lr)`, `h=0`.
With old trace and pre-update gradient:

```
b = beta - meta_lr * g * h_old
classical: beta_new = b
 guarded: beta_new = b - max(0, logsumexp(b + log(c)))
alpha = exp(beta_new)
w_new = w - alpha*g
h_new = h_old * max(0, 1 - alpha*c) - alpha*g
```

The guarded reduction includes all positive-curvature coordinates across all
participating groups; an empty reduction yields no shift. It is evaluated in
float64 log space per tensor, then reduced with host scalars, so CUDA and
mixed-device groups incur synchronization. Both methods retain state on each
parameter's own dtype/device. The cap is one up to floating-point rounding.
The shifted beta persists, including for absent features. The trace is a
heuristic diagonal trace, not differentiation through the guard.

The guard bounds same-example aggregate gain in finite exact arithmetic, not
future risk or arbitrary numerical overflow. Classical IDBD has no guard.
`IDBD(meta_lr=0)` is constant-step LMS; `GuardedIDBD(meta_lr=0)` may still
persistently reduce rates. When the cap is inactive both methods coincide.

Group overrides and `add_param_group` support `lr` and `meta_lr`. `lr` only
initializes rates: changing it later does not reset learned beta. Inspect
`optimizer.state[w]['beta']` and `['h']`; learned rates are `beta.exp()`.
Use standard `state_dict`/`load_state_dict` with matching parameter-group order
and the **same optimizer class**. Save and restore weights separately, as for
other PyTorch optimizers. State dictionaries do not encode algorithm identity.

## Explicit registration

Importing `idbd` changes no PyTorch attributes. Calling
`idbd.register_torch_optimizer()` installs process-local `torch.optim.IDBD`
(classical) and `torch.optim.GuardedIDBD` aliases. Repeated calls are harmless;
a conflicting existing attribute raises before either alias is installed.
No installed PyTorch file is modified.

## Tests

From the project root, with the supplied environment:

```sh
OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
  .venv/bin/python -m pytest -q tests/test_optimizer.py
```

Tests use an independent Python scalar/list recurrence, check ordering,
normalization and residual identity, validate serialization and the closure
contract, and conditionally exercise CUDA. No empirical superiority is claimed.
