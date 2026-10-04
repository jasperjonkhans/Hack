"""Single-thread online linear benchmark. Run with --help; see PROTOCOL.md."""
from __future__ import annotations

import os
# Set before NumPy/PyTorch imports, including subprocess invocations of this CLI.
for _name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_name] = "1"

import argparse
import hashlib
import itertools
import json
import math
import platform
import resource
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from idbd import IDBD, GuardedIDBD

torch.set_num_threads(1)
torch.set_num_interop_threads(1)
ROOT = Path(__file__).resolve().parent
METHODS = ("guarded", "idbd", "adam", "momentum", "rmsprop")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, allow_nan=False) + "\n")


def environment():
    return dict(python=platform.python_version(), torch=torch.__version__, numpy=np.__version__,
                platform=platform.platform(), threads=torch.get_num_threads(),
                interop_threads=torch.get_num_interop_threads(),
                max_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024,
                thread_environment={n: os.environ[n] for n in
                    ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS")})


@dataclass
class Stream:
    x: np.ndarray
    y: np.ndarray
    target: np.ndarray
    changes: list[int]
    groups: dict[str, list[int]]
    stream_hash: str
    seed: int
    regime: str


def rng(seed, channel):
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence([seed, channel])))


def make_stream(cfg, seed, regime):
    n, d = cfg["samples"], cfg["dimension"]
    p, sigma = cfg["activation_probability"], cfg["noise_std"]
    z = rng(seed, 0).standard_normal((n, d))
    mask = rng(seed, 1).random((n, d)) < p
    x = z * mask / math.sqrt(p)
    q = np.ones(n)
    changes = [] if regime == "stationary" else [int(n * f) for f in cfg["change_fractions"]]
    if regime == "timing":
        changes = [int(n * f) for f in cfg["heldout_change_fractions"]]
    for at in changes:
        q[at:] *= -1
    target = np.zeros((n, d))
    target[:, 0], target[:, 1] = q, -q
    target[:, 2], target[:, 3] = 0.5, -0.5
    target /= math.sqrt(2.5)
    if regime == "shock":
        at = int(n * cfg["shock_fraction"])
        # One covariate intervention, not a change label delivered to any learner.
        x[at] = z[at] * cfg["shock_scale"]
    y = np.einsum("ij,ij->i", x, target) + sigma * rng(seed, 2).standard_normal(n)
    groups = {"drifting": [0, 1], "stable": [2, 3], "nuisance": list(range(4, d))}
    if regime == "permuted":
        perm = rng(seed, 7).permutation(d)
        x, target = x[:, perm].copy(), target[:, perm].copy()
        groups = {k: np.flatnonzero(np.isin(perm, v)).tolist() for k, v in groups.items()}
    h = hashlib.sha256()
    for arr in (x, y, target):
        h.update(np.ascontiguousarray(arr).tobytes())
    h.update(json.dumps(changes).encode())
    return Stream(x, y, target, changes, groups, h.hexdigest(), seed, regime)


def archive_stream(stream, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stream.regime}-{stream.seed}.npz"
    if not path.exists():
        np.savez_compressed(path, x=stream.x, y=stream.y, target=stream.target,
                            changes=stream.changes, stream_hash=stream.stream_hash)
    else:
        with np.load(path) as old:
            if str(old["stream_hash"]) != stream.stream_hash:
                raise ValueError(f"refusing stale stream archive: {path}")


def configurations(cfg, method):
    if method in ("guarded", "idbd"):
        return [dict(lr=lr, meta_lr=m) for lr, m in itertools.product(cfg["rates"], cfg["meta_rates"])]
    if method == "momentum":
        return [dict(lr=lr, momentum=m) for lr, m in itertools.product(cfg["rates"], cfg["momenta"])]
    if method == "rmsprop":
        return [dict(lr=lr, alpha=a) for lr, a in itertools.product(cfg["rates"], cfg["rms_decays"])]
    if method == "adam":
        return [dict(lr=lr, betas=b) for lr, b in itertools.product(cfg["rates"], cfg["adam_betas"])]
    raise ValueError(method)


def optimizer(method, w, hp):
    if method in ("guarded", "idbd"):
        return (GuardedIDBD if method == "guarded" else IDBD)([w], **hp)
    if method == "adam":
        return torch.optim.Adam([w], **{**hp, "betas": tuple(float(v) for v in hp["betas"])},
                                eps=1e-8, foreach=False)
    if method == "momentum":
        return torch.optim.SGD([w], **hp, foreach=False)
    if method == "rmsprop":
        return torch.optim.RMSprop([w], **hp, eps=1e-8, foreach=False)
    raise ValueError(method)


def state_finite(opt):
    return all(bool(torch.isfinite(value).all()) for state in opt.state.values()
               for value in state.values() if isinstance(value, torch.Tensor))


def gain_snapshot(method, opt, w, hp, groups):
    state = opt.state[w]
    out = {}
    if method in ("idbd", "guarded"):
        gain = state["beta"].exp()
        out["beta"] = state["beta"].tolist()
        out["h"] = state["h"].tolist()
        out["meaning"] = "learned current-gradient rate"
    elif method == "adam":
        t = float(state["step"])
        vhat = state["exp_avg_sq"] / (1 - hp["betas"][1] ** t)
        gain = hp["lr"] / (vhat.sqrt() + 1e-8)
        out["meaning"] = "denominator gain, NOT full current-gradient learning rate"
        out["mhat"] = (state["exp_avg"] / (1 - hp["betas"][0] ** t)).tolist()
    elif method == "rmsprop":
        gain = hp["lr"] / (state["square_avg"].sqrt() + 1e-8)
        out["meaning"] = "current-gradient denominator gain"
    else:
        gain = torch.full_like(w, hp["lr"])
        out["meaning"] = "nominal lr; momentum changes the update direction"
        if "momentum_buffer" in state:
            out["velocity"] = state["momentum_buffer"].tolist()
    out["gain"] = gain.tolist()
    out["group_mean_gain"] = {name: float(gain[inds].mean()) for name, inds in groups.items()}
    return out


def first_recovery(values, start, end, threshold, window, sustain):
    """Delay to a qualifying trailing-window endpoint, confirmed for sustain more endpoints.

    Every sample in every qualifying window lies at or after the change.
    None means right-censored, never a successful short recovery.
    """
    local = np.asarray(values[start:end], dtype=float)
    if len(local) < window + sustain:
        return None
    sums = np.concatenate(([0.0], np.cumsum(local)))
    means = (sums[window:] - sums[:-window]) / window
    good = means <= threshold
    run = 0
    for j, ok in enumerate(good):
        run = run + 1 if ok else 0
        if run >= sustain + 1:
            first = j - sustain
            return int(first + window)
    return None


def recovery_metrics(risks, stream, cfg):
    out = []
    for i, start in enumerate(stream.changes):
        end = stream.changes[i + 1] if i + 1 < len(stream.changes) else len(stream.y)
        before = float(np.mean(risks[max(0, start - cfg["recovery_window"]):start]))
        common = first_recovery(risks, start, end, cfg["recovery_threshold"],
                                cfg["recovery_window"], cfg["recovery_sustain"])
        own = first_recovery(risks, start, end, before,
                             cfg["recovery_window"], cfg["recovery_sustain"])
        out.append(dict(change=start, censor_after=end - start, common_samples=common,
                        own_baseline_samples=own, own_baseline_threshold=before))
    return out


@torch.no_grad()
def run_trial(stream, method, hp, cfg, rich=False):
    # This function is the only training worker. Oracle data are used only below for scoring.
    w = torch.nn.Parameter(torch.zeros(stream.x.shape[1], dtype=torch.float64))
    opt = optimizer(method, w, hp)
    x = torch.from_numpy(stream.x)
    c = x.square()
    view = w.detach().numpy()  # shared live view, updates remain in-place
    n = len(stream.y)
    risks, observed, clean = np.empty(n), np.empty(n), np.empty(n)
    snapshots, blocks = [], []
    max_risk = max_update = max_gain = 0.0
    update_norm_overflow = False
    guard_events = 0
    max_candidate_log_gain = None
    failure = None
    attempted = committed = valid = scored = 0
    started = time.perf_counter()
    for t in range(n):
        err = float(stream.y[t] - np.dot(stream.x[t], view))
        delta = view - stream.target[t]
        with np.errstate(over="ignore", invalid="ignore"):
            risk = float(np.dot(delta, delta))  # nominal population covariance = I
        if math.isfinite(risk):
            max_risk = max(max_risk, risk)
        if not math.isfinite(risk) or risk > cfg["risk_failure_threshold"] or not math.isfinite(err):
            failure = dict(at=t, reason="nonfinite pre-update value or excess-risk threshold",
                           risk=risk if math.isfinite(risk) else None, nonfinite_risk=not math.isfinite(risk))
            break
        risks[t], observed[t], clean[t] = risk, err * err, float(np.dot(stream.x[t], delta)) ** 2
        scored += 1
        w.grad = -err * x[t]
        old = w.detach().clone()
        attempted += 1
        if method in ("idbd", "guarded"):
            if rich:
                st = opt.state[w]
                beta = st["beta"] if "beta" in st else torch.full_like(w, math.log(hp["lr"]))
                h = st["h"] if "h" in st else torch.zeros_like(w)
                b = beta - hp["meta_lr"] * w.grad * h
                active = c[t] > 0
                lg = float(torch.logsumexp(b[active] + c[t][active].log(), 0)) if active.any() else -math.inf
                if math.isfinite(lg):
                    max_candidate_log_gain = lg if max_candidate_log_gain is None else max(max_candidate_log_gain, lg)
                guard_events += int(method == "guarded" and lg > 0)
            try:
                opt.step(feature_sq={w: c[t]})
            except FloatingPointError as exc:
                failure = dict(at=t, reason=str(exc))
                break
            if rich:
                gain = float(torch.sum(opt.state[w]["beta"].exp() * c[t]))
                if math.isfinite(gain):
                    max_gain = max(max_gain, gain)
        else:
            opt.step()
        committed += 1
        if not bool(torch.isfinite(w).all()) or not state_finite(opt):
            failure = dict(at=t, reason="nonfinite weight or optimizer state after update")
            break
        valid += 1
        update_norm = float(torch.linalg.vector_norm(w - old))
        if not math.isfinite(update_norm):
            # Squaring can overflow even when the true L2 norm is representable.
            update_norm = math.hypot(*(w - old).tolist())
        if math.isfinite(update_norm):
            max_update = max(max_update, update_norm)
        else:
            update_norm_overflow = True
        if rich and (t == 0 or (t + 1) % cfg["trace_every"] == 0):
            snap = gain_snapshot(method, opt, w, hp, stream.groups)
            snap.update(sample=t + 1, weights=w.tolist(),
                        group_coefficient_mse={k: float(np.mean((view[ix] - stream.target[t, ix]) ** 2))
                                               for k, ix in stream.groups.items()})
            snapshots.append(snap)
    elapsed = time.perf_counter() - started
    # completed_updates counts returned steps, including a step with nonfinite state;
    # valid_updates excludes such steps. Atomic IDBD failures do not commit a step.
    # Failed rows never acquire a finite full-stream score or enter successful-only rankings.
    out = dict(method=method, hp=hp, seed=stream.seed, regime=stream.regime,
               stream_hash=stream.stream_hash, failure=failure, completed_updates=committed,
               attempted_updates=attempted, valid_updates=valid, scored_samples=scored,
               seconds=elapsed, max_risk=max_risk, max_update_norm=max_update,
               update_norm_overflow=update_norm_overflow,
               mse=float(observed.mean()) if failure is None else None,
               clean_mse=float(clean.mean()) if failure is None else None,
               cumulative_excess_risk=float(risks.sum()) if failure is None else None,
               final_weights=w.tolist() if failure is None else None)
    if rich:
        for lo in range(0, scored, cfg["trace_every"]):
            hi = min(scored, lo + cfg["trace_every"])
            blocks.append(dict(start=lo, stop=hi, mse=float(observed[lo:hi].mean()),
                               risk=float(risks[lo:hi].mean())))
        out.update(recovery=recovery_metrics(risks, stream, cfg) if failure is None else [],
                   blocks=blocks, snapshots=snapshots, diagnostics_are_prefix=failure is not None,
                   guard_events=guard_events, max_aggregate_gain=max_gain if method in ("idbd", "guarded") else None,
                   max_candidate_log_gain=max_candidate_log_gain)
        for r in out["recovery"]:
            r["common_seconds_estimate"] = None if r["common_samples"] is None else r["common_samples"] * elapsed / n
    return out


def check_config(cfg):
    seeds = [set(cfg[k]) for k in ("exploration_seeds", "tuning_seeds", "evaluation_seeds")]
    assert all(not a & b for a, b in itertools.combinations(seeds, 2)), "overlapping seed splits"
    assert len({len(configurations(cfg, m)) for m in METHODS}) == 1, "unequal tuning budgets"
    assert cfg["dimension"] >= 5 and cfg["samples"] > 0 and 0 < cfg["activation_probability"] <= 1


def runtime_benchmark(cfg, out):
    """Equal-length successful updates; stream generation and warmup excluded from core."""
    stream = make_stream({**cfg, "samples": 1500}, cfg["exploration_seeds"][0], "stationary")
    x = torch.from_numpy(stream.x)
    c = x.square()
    rows = []
    for method in METHODS:
        hp = configurations(cfg, method)[0]
        core, total = [], []
        for repetition in range(6):
            w = torch.nn.Parameter(torch.zeros(cfg["dimension"], dtype=torch.float64))
            opt = optimizer(method, w, hp)
            start = time.perf_counter()
            with torch.no_grad():
                for t in range(len(x)):
                    e = stream.y[t] - float(torch.dot(x[t], w))
                    w.grad = -e * x[t]
                    if method in ("guarded", "idbd"):
                        opt.step(feature_sq={w: c[t]})
                    else:
                        opt.step()
            secs = time.perf_counter() - start
            assert torch.isfinite(w).all() and state_finite(opt)
            if repetition:
                core.append(secs)
        measured = run_trial(stream, method, hp, cfg, rich=True)
        assert measured["failure"] is None
        rows.append(dict(method=method, samples=len(x), repetitions=5, seconds=core,
                         core_seconds_per_million=float(np.median(core) / len(x) * 1e6),
                         scored_seconds_per_million=measured["seconds"] / len(x) * 1e6))
    save(out, dict(rows=rows, environment=environment()))
    for row in rows:
        print(row["method"], "core s/M", round(row["core_seconds_per_million"], 1),
              "scored s/M", round(row["scored_seconds_per_million"], 1))
    print("Peak RSS MiB", environment()["max_rss_mib"])


def explore(cfg, out):
    rows = []
    for seed, regime in itertools.product(cfg["exploration_seeds"], cfg["regimes"]):
        s = make_stream(cfg, seed, regime)
        for method in METHODS:
            hp = (dict(lr=0.01, meta_lr=0.01) if method in ("guarded", "idbd") else
                  dict(lr=0.01, betas=[0.9, 0.999]) if method == "adam" else
                  dict(lr=0.01, momentum=0.9) if method == "momentum" else dict(lr=0.01, alpha=0.99))
            row = run_trial(s, method, hp, cfg, rich=True)
            rows.append(row)
            print(regime, method, "MSE", row["mse"], "failure", row["failure"], flush=True)
    save(out, dict(config=cfg, rows=rows, environment=environment()))


def tune(cfg, out):
    started = time.perf_counter()
    records, selected, sensitivity = [], {}, {}
    for regime in cfg["regimes"]:
        streams = [make_stream(cfg, seed, regime) for seed in cfg["tuning_seeds"]]
        for s in streams:
            archive_stream(s, Path(out).parent / "streams")
        selected[regime], sensitivity[regime] = {}, {}
        for method in METHODS:
            outcomes = []
            for index, hp in enumerate(configurations(cfg, method)):
                rows = [run_trial(s, method, hp, cfg) for s in streams]
                score = math.inf if any(r["failure"] for r in rows) else float(np.mean([r["mse"] for r in rows]))
                outcomes.append((score, index, hp))
                records.append(dict(regime=regime, method=method, index=index, hp=hp,
                                    score=score if math.isfinite(score) else None, runs=rows))
            score, index, hp = min(outcomes, key=lambda r: (r[0], r[1]))
            if not math.isfinite(score):
                reason = f"all configurations failed: {regime}/{method}"
                save(out, dict(config=cfg, complete=False, failure_reason=reason, records=records,
                               selected=selected, sensitivity=sensitivity,
                               seconds=time.perf_counter() - started, environment=environment()))
                raise RuntimeError(reason)
            selected[regime][method] = hp
            finite = [v for v, _, _ in outcomes if math.isfinite(v)]
            sensitivity[regime][method] = dict(best_mse=score, selected_index=index,
                failures=len(outcomes) - len(finite), candidates=len(outcomes),
                fraction_within_10pct=sum(v <= score * 1.1 for v in finite) / len(outcomes),
                median_finite_mse=float(np.median(finite)),
                rate_boundary=hp["lr"] in (cfg["rates"][0], cfg["rates"][-1]))
            save(out, dict(config=cfg, complete=False, records=records, selected=selected,
                           sensitivity=sensitivity, seconds=time.perf_counter() - started, environment=environment()))
            print(regime, method, "best", round(score, 6), hp, flush=True)
    save(out, dict(config=cfg, complete=True, records=records, selected=selected, sensitivity=sensitivity,
                   seconds=time.perf_counter() - started, environment=environment()))


def frozen_sources():
    paths = ["benchmark.py", "idbd/optimizer.py", "idbd/__init__.py", "PROTOCOL.md", "DESIGN.md"]
    paths += [str(p.relative_to(ROOT)) for p in sorted((ROOT / "tests").glob("test_*.py"))]
    return {p: digest(ROOT / p) for p in paths}


def freeze(cfg, tuning_path, out):
    if Path(out).exists():
        raise FileExistsError("freeze manifest already exists; don't overwrite an evaluated protocol")
    tuning = json.loads(Path(tuning_path).read_text())
    assert tuning["complete"] and tuning["config"] == cfg
    save(out, dict(config=cfg, selected=tuning["selected"], sources=frozen_sources(),
                   tuning_sha256=digest(tuning_path), tuning_file=str(Path(tuning_path).resolve()),
                   created_unix=time.time(), environment=environment(),
                   evaluation_not_started=True))
    print("Frozen", out, digest(out))


def evaluate(frozen_path, out):
    if Path(out).exists():
        raise FileExistsError("evaluation output exists; refusing to overwrite")
    frozen = json.loads(Path(frozen_path).read_text())
    assert frozen_sources() == frozen["sources"], "source/protocol changed since freeze"
    assert digest(frozen["tuning_file"]) == frozen["tuning_sha256"], "tuning file changed"
    cfg = frozen["config"]
    check_config(cfg)
    rows = []
    started = time.perf_counter()
    regimes = cfg["regimes"] + ["permuted", "timing"]
    for regime, seed in itertools.product(regimes, cfg["evaluation_seeds"]):
        stream = make_stream(cfg, seed, regime)
        archive_stream(stream, Path(out).parent / "streams")
        base = "tracking" if regime in ("permuted", "timing") else regime
        for method in METHODS:
            row = run_trial(stream, method, frozen["selected"][base][method], cfg, rich=True)
            rows.append(row)
        # Same selected guarded hyperparameters, safeguard removed: no new tuning budget.
        hp = frozen["selected"][base]["guarded"]
        row = run_trial(stream, "idbd", hp, cfg, rich=True)
        row["method"] = "matched_unguarded"
        rows.append(row)
        save(out, dict(complete=False, frozen_sha256=digest(frozen_path), rows=rows,
                       seconds=time.perf_counter() - started, environment=environment()))
        print("evaluated", regime, seed, flush=True)
    save(out, dict(complete=True, frozen_sha256=digest(frozen_path), rows=rows,
                   seconds=time.perf_counter() - started, environment=environment()))


def bootstrap_pair(a, b, draws=10000):
    a, b = np.asarray(a), np.asarray(b)
    inds = rng(4242, 0).integers(0, len(a), (draws, len(a)))
    diffs = (a[inds] - b[inds]).mean(axis=1)
    ratios = a[inds].mean(axis=1) / b[inds].mean(axis=1)
    return dict(mean_difference=float((a - b).mean()), ci95_difference=np.quantile(diffs, [.025, .975]).tolist(),
                ratio_of_means=float(a.mean() / b.mean()), ci95_ratio=np.quantile(ratios, [.025, .975]).tolist(),
                simultaneous_ci98_75_ratio=np.quantile(ratios, [.00625, .99375]).tolist())


def summarize(evaluation_path, out):
    data = json.loads(Path(evaluation_path).read_text())
    assert data["complete"]
    rows = data["rows"]
    summaries, comparisons = {}, {}
    for regime in dict.fromkeys(r["regime"] for r in rows):
        summaries[regime], comparisons[regime] = {}, {}
        for method in (*METHODS, "matched_unguarded"):
            group = [r for r in rows if r["regime"] == regime and r["method"] == method]
            failed = sum(r["failure"] is not None for r in group)
            entry = dict(seeds=len(group), failures=failed)
            if not failed:
                rec = [c for r in group for c in r["recovery"]]
                recovered = [c["common_samples"] for c in rec if c["common_samples"] is not None]
                entry.update(mean_mse=float(np.mean([r["mse"] for r in group])),
                    mean_cumulative_risk=float(np.mean([r["cumulative_excess_risk"] for r in group])),
                    mean_runtime_seconds=float(np.mean([r["seconds"] for r in group])),
                    max_risk=max(r["max_risk"] for r in group),
                    recovered_changes=len(recovered), total_changes=len(rec),
                    conditional_median_recovery=float(np.median(recovered)) if recovered else None,
                    mean_guard_events=float(np.mean([r["guard_events"] for r in group])),
                    mean_final_group_gain={k: float(np.mean([r["snapshots"][-1]["group_mean_gain"][k] for r in group]))
                                           for k in ("drifting", "stable", "nuisance")})
            summaries[regime][method] = entry
        a = sorted([r for r in rows if r["regime"] == regime and r["method"] == "guarded"], key=lambda r: r["seed"])
        for method in (*METHODS[1:], "matched_unguarded"):
            b = sorted([r for r in rows if r["regime"] == regime and r["method"] == method], key=lambda r: r["seed"])
            assert [r["seed"] for r in a] == [r["seed"] for r in b]
            if not any(r["failure"] for r in a + b):
                comparisons[regime][method] = {metric: bootstrap_pair([r[metric] for r in a], [r[metric] for r in b])
                    for metric in ("mse", "cumulative_excess_risk")}
            else:
                comparisons[regime][method] = {"not_estimable": "failures retained; no successful-only contrast"}
    perm_diffs = []
    for method, seed in itertools.product((*METHODS, "matched_unguarded"), sorted({r["seed"] for r in rows})):
        pair = [next(r for r in rows if r["method"] == method and r["seed"] == seed and r["regime"] == rg)
                for rg in ("tracking", "permuted")]
        if not any(r["failure"] for r in pair):
            perm_diffs.append(abs(pair[0]["mse"] - pair[1]["mse"]))
    save(out, dict(summaries=summaries, comparisons=comparisons,
                  max_permutation_mse_delta=max(perm_diffs, default=None),
                  evaluation_sha256=digest(evaluation_path), environment=data["environment"]))
    for regime, methods in summaries.items():
        print(regime, {m: round(r["mean_mse"], 5) if not r["failures"] else f"{r['failures']} failed" for m, r in methods.items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["throughput", "explore", "tune", "freeze", "evaluate", "summarize"])
    parser.add_argument("--config", default="configs/pilot.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--tuning", default="results/tuning.json")
    parser.add_argument("--frozen", default="results/frozen.json")
    parser.add_argument("--evaluation", default="results/evaluation.json")
    args = parser.parse_args()
    if args.phase == "evaluate":
        evaluate(args.frozen, args.out)
    elif args.phase == "summarize":
        summarize(args.evaluation, args.out)
    else:
        cfg = json.loads(Path(args.config).read_text())
        check_config(cfg)
        if args.phase == "freeze":
            freeze(cfg, args.tuning, args.out)
        else:
            {"throughput": runtime_benchmark, "explore": explore, "tune": tune}[args.phase](cfg, args.out)


if __name__ == "__main__":
    main()
