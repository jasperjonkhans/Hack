import copy
import json

import numpy as np
import pytest

import benchmark as b


@pytest.fixture
def cfg():
    return dict(samples=100, dimension=6, activation_probability=0.2, noise_std=0.3,
        change_fractions=[0.25, 0.5, 0.75], heldout_change_fractions=[0.19, 0.56, 0.83],
        shock_fraction=0.4, shock_scale=30, rates=[0.001, 0.01], meta_rates=[0.001, 0.01],
        momenta=[0, 0.9], rms_decays=[0.9, 0.99], adam_betas=[[0, 0.99], [0.9, 0.999]],
        exploration_seeds=[9101], tuning_seeds=[0, 1], evaluation_seeds=[100, 101],
        regimes=["tracking", "stationary", "shock"], recovery_window=3,
        recovery_sustain=2, recovery_threshold=0.02, risk_failure_threshold=1e6, trace_every=10)


def test_seed_splits_and_equal_configuration_budgets(cfg):
    b.check_config(cfg)
    assert {len(b.configurations(cfg, m)) for m in b.METHODS} == {4}
    bad = copy.deepcopy(cfg)
    bad["evaluation_seeds"] = [0]
    with pytest.raises(AssertionError, match="overlapping"):
        b.check_config(bad)


def test_stream_reproducibility_and_permutation(cfg):
    base = b.make_stream(cfg, 100, "tracking")
    again = b.make_stream(cfg, 100, "tracking")
    perm = b.make_stream(cfg, 100, "permuted")
    assert base.stream_hash == again.stream_hash
    np.testing.assert_array_equal(base.y, perm.y)
    np.testing.assert_allclose(np.einsum("ij,ij->i", base.x, base.target),
                               np.einsum("ij,ij->i", perm.x, perm.target))
    for method in b.METHODS:
        hp = b.configurations(cfg, method)[0]
        a = b.run_trial(base, method, hp, cfg)
        p = b.run_trial(perm, method, hp, cfg)
        assert a["mse"] == pytest.approx(p["mse"], rel=1e-12)
        assert a["cumulative_excess_risk"] == pytest.approx(p["cumulative_excess_risk"], rel=1e-12)


def test_timing_changes_targets_not_inputs_or_noise(cfg):
    base, shifted = [b.make_stream(cfg, 100, regime) for regime in ("tracking", "timing")]
    np.testing.assert_array_equal(base.x, shifted.x)
    assert base.changes != shifted.changes
    np.testing.assert_allclose(base.y - np.einsum("ij,ij->i", base.x, base.target),
        shifted.y - np.einsum("ij,ij->i", shifted.x, shifted.target), atol=1e-14)


def test_scoring_is_preupdate_and_risk_has_no_noise_floor(cfg):
    stream = b.Stream(np.array([[2.0], [2.0]]), np.array([2.0, 2.0]),
                      np.array([[1.0], [1.0]]), [], {"all": [0]}, "manual", 3, "manual")
    row = b.run_trial(stream, "idbd", dict(lr=0.1, meta_lr=0), cfg)
    # w0=0; e0=2; w1=.4; e1=1.2; w2=.64. Population covariance is nominal I.
    assert row["mse"] == pytest.approx((4 + 1.44) / 2)
    assert row["cumulative_excess_risk"] == pytest.approx(1 + 0.36)
    assert row["final_weights"] == pytest.approx([0.64])


def test_guard_has_measured_interventions_and_removal_changes_behavior(cfg):
    stream = b.make_stream(cfg, 9101, "tracking")
    hp = dict(lr=0.3, meta_lr=0)
    a = b.run_trial(stream, "guarded", hp, cfg, rich=True)
    c = b.run_trial(stream, "idbd", hp, cfg, rich=True)
    assert a["failure"] is None
    assert a["guard_events"] > 0
    assert a["max_aggregate_gain"] <= 1 + 1e-12
    assert c["failure"] is not None or c["mse"] != pytest.approx(a["mse"])


def test_recovery_does_not_use_prechange_window_and_censors():
    values = np.r_[np.zeros(10), np.ones(5), np.zeros(10)]
    assert b.first_recovery(values, 10, 25, 0.01, 3, 2) == 8
    assert b.first_recovery(values, 10, 18, 0.01, 3, 2) is None
    assert b.first_recovery(values, 10, 15, 0.01, 3, 2) is None


def test_failed_trial_has_no_usable_full_stream_score(cfg):
    stream = b.make_stream(cfg, 100, "tracking")
    row = b.run_trial(stream, "momentum", dict(lr=100, momentum=0), cfg)
    assert row["failure"] is not None
    assert row["mse"] is None and row["cumulative_excess_risk"] is None
    json.dumps(row, allow_nan=False)


def test_failure_keeps_diagnostics_and_threshold_crossing(cfg):
    stream = b.make_stream(cfg, 100, "tracking")
    row = b.run_trial(stream, "idbd", dict(lr=10, meta_lr=0), cfg, rich=True)
    assert row["failure"] is not None
    assert row["max_risk"] > cfg["risk_failure_threshold"]
    assert "max_aggregate_gain" in row and "snapshots" in row
    assert row["max_aggregate_gain"] > 1
    assert row["mse"] is None
    json.dumps(row, allow_nan=False)


def test_extreme_finite_update_norm_remains_json_serializable(cfg):
    actual = {**cfg, "samples": 6000, "dimension": 20, "activation_probability": 0.1}
    stream = b.make_stream(actual, 0, "tracking")
    row = b.run_trial(stream, "idbd", dict(lr=0.001, meta_lr=0.1), actual)
    assert row["failure"] is not None
    assert row["mse"] is None
    json.dumps(row, allow_nan=False)
    assert np.isfinite(row["max_update_norm"])


def test_postupdate_failure_counts_committed_not_valid(cfg, monkeypatch):
    stream = b.make_stream(cfg, 100, "tracking")
    monkeypatch.setattr(b, "state_finite", lambda opt: False)
    row = b.run_trial(stream, "momentum", dict(lr=0.001, momentum=0), cfg)
    assert row["failure"]["at"] == 0
    assert row["attempted_updates"] == row["completed_updates"] == 1
    assert row["valid_updates"] == 0


def test_all_failed_tuning_saves_evidence(cfg, tmp_path, monkeypatch):
    cfg = {**cfg, "regimes": ["tracking"]}
    monkeypatch.setattr(b, "run_trial", lambda *a, **k: {"failure": {"reason": "synthetic"}, "mse": None})
    out = tmp_path / "tuning.json"
    with pytest.raises(RuntimeError, match="all configurations failed"):
        b.tune(cfg, out)
    saved = json.loads(out.read_text())
    assert not saved["complete"] and saved["failure_reason"]
    assert len(saved["records"]) == len(b.configurations(cfg, "guarded"))
    assert all(row["score"] is None for row in saved["records"])


def test_bootstrap_pairs_seed_rows_not_timesteps():
    out = b.bootstrap_pair([1, 2, 3], [2, 4, 6])
    assert out["ratio_of_means"] == 0.5
    assert out["ci95_ratio"] == [0.5, 0.5]


def test_freeze_refuses_overwrite_and_evaluation_detects_change(cfg, tmp_path, monkeypatch):
    tuning, frozen = tmp_path / "tuning.json", tmp_path / "frozen.json"
    b.save(tuning, dict(complete=True, config=cfg, selected={}))
    monkeypatch.setattr(b, "frozen_sources", lambda: {"file": "oldhash"})
    b.freeze(cfg, tuning, frozen)
    with pytest.raises(FileExistsError):
        b.freeze(cfg, tuning, frozen)
    monkeypatch.setattr(b, "frozen_sources", lambda: {"file": "changed"})
    with pytest.raises(AssertionError, match="changed since freeze"):
        b.evaluate(frozen, tmp_path / "eval.json")
