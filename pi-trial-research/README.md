# Linear IDBD: PyTorch prototype and bounded experiment

A working classical IDBD optimizer and a separately named `GuardedIDBD` prototype. The only modification is Autostep's persistent aggregate-gain safeguard. It is not a new algorithm or full Autostep.

The held-out pilot did **not** establish the requested tracking advantage over all tuned comparators. Guarded IDBD beat Adam/RMSprop but lost to classical IDBD; a stationary diagnostic was favorable and an extreme-burst regime was unreliable. See [RESULTS.md](RESULTS.md). The original [IDBD-comparison.md](IDBD-comparison.md) is unchanged.

## Install and test

Run from this directory. The measured environment used Python 3.13 and CPU-only PyTorch 2.10.0. `uv` is convenient, not a runtime dependency.

`uv venv --python 3.13 .venv`

`uv pip install --python .venv/bin/python --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match -r requirements.lock`

`uv pip install --python .venv/bin/python --no-deps -e .`

The two package indexes above are needed because the lock specifies the CPU wheel (`torch==2.10.0+cpu`). All requirements are pinned. Alternatively install your preferred PyTorch build and `.[test,benchmark]`; this is not the exact measured environment. No installed PyTorch files are modified.

`OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 .venv/bin/python -m pytest -q`

`OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 .venv/bin/python examples/linear.py`

## API

`from idbd import IDBD, GuardedIDBD, register_torch_optimizer`

`IDBD` is faithful classical linear IDBD. `GuardedIDBD` adds the persistent global safeguard. Both implement PyTorch's optimizer interface, with parameter groups and `state_dict` support.

`register_torch_optimizer()` explicitly exposes `torch.optim.IDBD` and `torch.optim.GuardedIDBD` for this process. Import alone does not register anything; repeated registration is safe and conflicting names are rejected.

Use `opt.step(feature_sq={weight: x.square()})` after the current scalar example's half-squared-loss backward (or equivalent analytical gradient). The additional input really is x², not squared gradients. A closure is optional and runs once with gradients enabled; it must recompute that same example's gradients and return the loss. See [docs/optimizer.md](docs/optimizer.md) and [examples/linear.py](examples/linear.py) for equations, return behavior, validation and checkpoint restoration.

Scope: scalar online linear prediction. This is not a generic neural-network optimizer, minibatch curvature estimator, or gradients-only substitute for Adam.

## Reproduce the experiment

[PROTOCOL.md](PROTOCOL.md) defines the prespecified contrast, streams, failure rules, seed separation and uncertainty calculations; [configs/pilot.json](configs/pilot.json) contains the complete grid. The CLI forces numerical libraries and PyTorch to one thread. No GPU is used. Run phases serially; budget approximately 16 minutes for completed tuning/evaluation on the measured machine, plus setup and profiling. Ask the machine owner before rerunning a long sweep.

Use a new output directory rather than overwriting the supplied evidence:

`.venv/bin/python benchmark.py throughput --out results/reproduction/throughput.json`

`.venv/bin/python benchmark.py explore --out results/reproduction/exploration.json`

`.venv/bin/python benchmark.py tune --out results/reproduction/tuning.json`

`.venv/bin/python benchmark.py freeze --tuning results/reproduction/tuning.json --out results/reproduction/frozen.json`

`.venv/bin/python benchmark.py evaluate --frozen results/reproduction/frozen.json --out results/reproduction/evaluation.json`

`.venv/bin/python benchmark.py summarize --evaluation results/reproduction/evaluation.json --out results/reproduction/summary.json`

For the supplied evidence, `.venv/bin/python audit_artifacts.py > results/audit.json` independently checks file hashes, raw group means, tuning selections and sensitivity using only the standard library. This is a post-evaluation verifier, not more training; it does not independently recalculate bootstrap quantiles.

Do not change the candidate or protocol after viewing evaluation. Freeze records implementation/test/protocol hashes, full config, selected configurations and tuning checksum; evaluation refuses changed sources or existing output. The manifest's tuning path is local: a moved checkout should create a new manifest from reproduced tuning. JSON scores for failed runs are null, with an explicit failure reason, never a successful-only average.

Artifacts in `results/`:

- `throughput.json`: five warmed core-loop repetitions per optimizer, scored-loop time, RSS and environment.
- `exploration.json`: seed-9101 diagnostic only, generated before tuning. Its pre-review failure diagnostics use the earlier schema; it is not held-out evidence.
- `tuning.json`: all 720 candidate-stream trials, selections and tuning sensitivity.
- `frozen.json`: immutable pre-evaluation manifest.
- `evaluation.json`: 360 held-out runs, including matched safeguard-removal ablations and two transfer checks; block risks, rate/state snapshots and censored recovery data.
- `summary.json`: paired seed-bootstrap estimates and failure-aware aggregate results.
- `audit.json`: independent raw-artifact reductions and integrity checks for final review.
- `streams/*.npz`: exact generated tuning/evaluation streams, locally archived but Git-ignored. Canonical array hashes are included in each run and streams can be regenerated.

The optimizer API is ordinary reusable Python. `benchmark.py` is a CPU benchmark CLI that sets thread limits at import time; do not import it into an application expecting different global thread settings.
