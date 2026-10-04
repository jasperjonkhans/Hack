from unittest.mock import Mock

import pytest

from omnigent_lean import adapter
from omnigent_lean.tools import VerificationResult


@pytest.fixture(autouse=True)
def clean_worker(monkeypatch):
    monkeypatch.setattr(adapter, "_tool", None)
    for name in (
        "OMNIGENT_LEAN_PROJECT",
        "OMNIGENT_LEAN_VERSION",
        "OMNIGENT_LEAN_CACHE",
        "OMNIGENT_LEAN_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("project,version", [(None, None), ("/trusted", "v4.24.0")])
def test_requires_exactly_one_host_environment(monkeypatch, project, version):
    if project:
        monkeypatch.setenv("OMNIGENT_LEAN_PROJECT", project)
    if version:
        monkeypatch.setenv("OMNIGENT_LEAN_VERSION", version)
    config = Mock()
    monkeypatch.setattr(adapter, "LeanREPLConfig", config)
    result = adapter.lean_verify_proof("theorem proof : True := by trivial", "proof")
    assert result["status"] == "error"
    assert not result["verified"]
    assert "exactly one" in result["reason"]
    config.assert_not_called()


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf", "not-a-number"])
def test_bad_deadline_fails_before_provisioning(monkeypatch, timeout):
    monkeypatch.setenv("OMNIGENT_LEAN_VERSION", "v4.24.0")
    monkeypatch.setenv("OMNIGENT_LEAN_TIMEOUT", timeout)
    config = Mock()
    monkeypatch.setattr(adapter, "LeanREPLConfig", config)
    result = adapter.lean_verify_proof("theorem proof : True := by trivial", "proof")
    assert result["status"] == "error"
    assert not result["verified"]
    config.assert_not_called()


def test_standard_library_provisions_once_per_worker(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OMNIGENT_LEAN_VERSION", "v4.24.0")
    monkeypatch.setenv("OMNIGENT_LEAN_CACHE", str(tmp_path))
    monkeypatch.setenv("OMNIGENT_LEAN_TIMEOUT", "12.5")
    config = Mock()

    def provision(**kwargs):
        print("provisioning log")
        return config(**kwargs)

    monkeypatch.setattr(adapter, "LeanREPLConfig", provision)
    tool = adapter.provision()
    # Host changes take effect only after a worker restart.
    monkeypatch.setenv("OMNIGENT_LEAN_VERSION", "different-version")
    assert adapter.provision() is tool
    assert tool.timeout_seconds == 12.5
    config.assert_called_once_with(
        project=None,
        lean_version="v4.24.0",
        cache_dir=tmp_path,
        enable_parallel_elaboration=False,
    )
    output = capsys.readouterr()
    assert not output.out
    assert "provisioning log" in output.err


def test_project_is_host_selected_and_never_auto_built(monkeypatch, tmp_path):
    monkeypatch.setenv("OMNIGENT_LEAN_PROJECT", str(tmp_path))
    project = Mock()
    config = Mock()
    monkeypatch.setattr(adapter, "LocalProject", project)
    monkeypatch.setattr(adapter, "LeanREPLConfig", config)
    adapter.provision()
    project.assert_called_once_with(directory=str(tmp_path.resolve()), auto_build=False)
    assert config.call_args.kwargs["project"] is project.return_value
    assert config.call_args.kwargs["lean_version"] is None


@pytest.mark.parametrize("status", ["verified", "rejected", "timeout", "error"])
def test_adapter_preserves_verdict_and_only_exposes_two_arguments(monkeypatch, status):
    tool = Mock()
    tool.execute.return_value = VerificationResult(status, "proof", "test").to_dict()
    monkeypatch.setattr(adapter, "_tool", tool)
    result = adapter.lean_verify_proof("complete source", "proof")
    assert result["status"] == status
    assert result["verified"] is (status == "verified")
    tool.execute.assert_called_once_with({"code": "complete source", "theorem_name": "proof"})
    with pytest.raises(TypeError):
        adapter.lean_verify_proof("code", "proof", project="/model-selected")


def test_provisioning_failure_is_not_cached_and_can_be_retried(monkeypatch):
    monkeypatch.setenv("OMNIGENT_LEAN_VERSION", "v4.24.0")
    config = Mock(side_effect=RuntimeError("missing worker tooling"))
    monkeypatch.setattr(adapter, "LeanREPLConfig", config)
    result = adapter.lean_verify_proof("code", "proof")
    assert result["status"] == "error"
    assert not result["verified"]
    assert "missing worker tooling" in result["reason"]
    assert adapter._tool is None
    config.side_effect = None
    assert adapter.provision() is adapter.provision()
    assert config.call_count == 2
