"""Read-only independent reductions of saved artifacts; no optimizer imports/training.

Run: .venv/bin/python audit_artifacts.py > results/audit.json
This post-evaluation verifier is not part of the frozen training implementation.
"""
import hashlib
import json
import math
import statistics as stats
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"


def load(name):
    return json.loads((RESULTS / name).read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def close(a, b):
    return math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)


def main():
    frozen, tuning, evaluation, summary = [load(n) for n in
        ("frozen.json", "tuning.json", "evaluation.json", "summary.json")]
    hashes = dict(source_mismatches=[p for p, h in frozen["sources"].items() if sha(ROOT / p) != h],
        tuning_matches=sha(RESULTS / "tuning.json") == frozen["tuning_sha256"],
        frozen_matches=sha(RESULTS / "frozen.json") == evaluation["frozen_sha256"],
        evaluation_matches=sha(RESULTS / "evaluation.json") == summary["evaluation_sha256"],
        original_comparison_unchanged=sha(ROOT / "IDBD-comparison.md") ==
            "7305524c0b18d70f45622dc3e7a80d3b81b82889327bef9b87a3cdf63826f885")
    assert not hashes["source_mismatches"] and all(v for k, v in hashes.items() if k != "source_mismatches")
    rows = evaluation["rows"]
    cfg = frozen["config"]
    methods = ["guarded", "idbd", "adam", "momentum", "rmsprop", "matched_unguarded"]
    regimes = cfg["regimes"] + ["permuted", "timing"]
    actual_keys = [(r["regime"], r["seed"], r["method"]) for r in rows]
    expected_keys = {(rg, seed, m) for rg in regimes for seed in cfg["evaluation_seeds"] for m in methods}
    assert len(rows) == len(set(actual_keys)) and set(actual_keys) == expected_keys
    means = {}
    for rg in regimes:
        means[rg] = {}
        for m in methods:
            group = [r for r in rows if (r["regime"], r["method"]) == (rg, m)]
            failures = sum(r["failure"] is not None for r in group)
            s = summary["summaries"][rg][m]
            assert s["seeds"] == len(group) and s["failures"] == failures
            result = dict(rows=len(group), failures=failures)
            if not failures:
                result.update(mse=stats.mean(r["mse"] for r in group),
                              cumulative_risk=stats.mean(r["cumulative_excess_risk"] for r in group))
                assert close(result["mse"], s["mean_mse"])
                assert close(result["cumulative_risk"], s["mean_cumulative_risk"])
                if m != "guarded":
                    a = summary["summaries"][rg]["guarded"]
                    assert close(a["mean_mse"] / result["mse"],
                                 summary["comparisons"][rg][m]["mse"]["ratio_of_means"])
            else:
                assert all(r["mse"] is None and r["cumulative_excess_risk"] is None
                           for r in group if r["failure"])
            means[rg][m] = result
            selection_regime = "tracking" if rg in ("permuted", "timing") else rg
            selection_method = "guarded" if m == "matched_unguarded" else m
            assert all(r["hp"] == frozen["selected"][selection_regime][selection_method] for r in group)
        for seed in cfg["evaluation_seeds"]:
            assert len({r["stream_hash"] for r in rows if r["regime"] == rg and r["seed"] == seed}) == 1
    selection_checks = {}
    for rg in cfg["regimes"]:
        selection_checks[rg] = {}
        for m in methods[:-1]:
            records = [r for r in tuning["records"] if (r["regime"], r["method"]) == (rg, m)]
            assert len(records) == 16
            for r in records:
                assert {t["seed"] for t in r["runs"]} == set(cfg["tuning_seeds"])
                expected = None if any(t["failure"] for t in r["runs"]) else stats.mean(t["mse"] for t in r["runs"])
                assert expected is None and r["score"] is None or expected is not None and close(expected, r["score"])
            best = min((r for r in records if r["score"] is not None), key=lambda r: (r["score"], r["index"]))
            assert frozen["selected"][rg][m] == tuning["selected"][rg][m] == best["hp"]
            near = sum(r["score"] is not None and r["score"] <= 1.1 * best["score"] for r in records)
            failed = sum(r["score"] is None for r in records)
            assert tuning["sensitivity"][rg][m]["fraction_within_10pct"] == near / 16
            assert tuning["sensitivity"][rg][m]["failures"] == failed
            selection_checks[rg][m] = dict(near_best=near, failed_configs=failed, selected_index=best["index"])
    posthoc = dict(stationary_later_risk={}, shock_mse={}, tracking_own_recovery={}, shock_guarded_drift_gain={})
    for m in methods[:-1]:
        group = [r for r in rows if r["regime"] == "stationary" and r["method"] == m]
        posthoc["stationary_later_risk"][m] = stats.mean(b["risk"] for r in group for b in r["blocks"] if b["start"] >= 500)
        group = [r for r in rows if r["regime"] == "shock" and r["method"] == m]
        posthoc["shock_mse"][m] = dict(median=stats.median(r["mse"] for r in group), maximum=max(r["mse"] for r in group))
        group = [r for r in rows if r["regime"] == "tracking" and r["method"] == m]
        rec = [c["own_baseline_samples"] for r in group for c in r["recovery"] if c["own_baseline_samples"] is not None]
        posthoc["tracking_own_recovery"][m] = dict(count=len(rec), conditional_median=stats.median(rec))
    for t in [2000, 2500, 3000, 6000]:
        posthoc["shock_guarded_drift_gain"][t] = stats.mean(s["group_mean_gain"]["drifting"] for r in rows
            if r["regime"] == "shock" and r["method"] == "guarded" for s in r["snapshots"] if s["sample"] == t)
    posthoc["guarded_max_gain"] = max(r["max_aggregate_gain"] for r in rows if r["method"] == "guarded")
    trials = [t for r in tuning["records"] for t in r["runs"]]
    counts = dict(tuning_candidates=len(tuning["records"]), tuning_trials=len(trials),
        tuning_failures=sum(t["failure"] is not None for t in trials),
        tuning_committed_updates=sum(t["completed_updates"] for t in trials),
        evaluation_rows=len(rows), evaluation_committed_updates=sum(r["completed_updates"] for r in rows),
        tuning_seconds=tuning["seconds"], evaluation_seconds=evaluation["seconds"],
        evaluation_peak_rss_mib=evaluation["environment"]["max_rss_mib"])
    print(json.dumps(dict(hashes=hashes, group_means=means, selection_checks=selection_checks,
                         posthoc=posthoc, counts=counts, all_assertions_passed=True), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
