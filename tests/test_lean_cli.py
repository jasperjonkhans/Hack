import json
import sys
from unittest.mock import Mock

import pytest

from omnigent_lean.tools import lean


@pytest.mark.parametrize(
    "status,exit_code", [("verified", 0), ("rejected", 1), ("error", 2), ("timeout", 2)]
)
def test_cli_json_output_and_exit_codes(tmp_path, monkeypatch, capsys, status, exit_code):
    code_file = tmp_path / "proof.lean"
    code_file.write_text("theorem proof : True := by trivial", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omnigent-lean",
            "--code-file",
            str(code_file),
            "--theorem-name",
            "proof",
            "--lean-version",
            "v4.24.0",
        ],
    )
    factory = Mock()

    def provision(**kwargs):
        print("provisioning message")
        return factory(**kwargs)

    monkeypatch.setattr(lean, "LeanREPLConfig", provision)
    tool = Mock()
    tool.verify.return_value = lean.VerificationResult(status, "proof", "test")
    monkeypatch.setattr(lean, "LeanProofTool", Mock(return_value=tool))
    assert lean.main() == exit_code
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert result["verified"] is (status == "verified")
    assert "provisioning message" in output.err
    assert factory.call_args.kwargs["lean_version"] == "v4.24.0"
    assert factory.call_args.kwargs["enable_parallel_elaboration"] is False
    tool.verify.assert_called_once_with(code_file.read_text(), "proof")


def test_cli_missing_file_fails_before_provisioning(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omnigent-lean",
            "--code-file",
            str(tmp_path / "missing.lean"),
            "--theorem-name",
            "proof",
            "--lean-version",
            "v4.24.0",
        ],
    )
    factory = Mock()
    monkeypatch.setattr(lean, "LeanREPLConfig", factory)
    assert lean.main() == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"
    factory.assert_not_called()


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_cli_invalid_timeout_fails_before_provisioning(tmp_path, monkeypatch, capsys, timeout):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omnigent-lean",
            "--code-file",
            str(tmp_path / "proof.lean"),
            "--theorem-name",
            "proof",
            "--lean-version",
            "v4.24.0",
            "--timeout",
            timeout,
        ],
    )
    factory = Mock()
    monkeypatch.setattr(lean, "LeanREPLConfig", factory)
    assert lean.main() == 2
    assert json.loads(capsys.readouterr().out)["verified"] is False
    factory.assert_not_called()


def test_cli_local_project_is_host_selected_and_prebuilt(tmp_path, monkeypatch, capsys):
    code_file = tmp_path / "proof.lean"
    code_file.write_text("theorem proof : True := by trivial")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "omnigent-lean",
            "--code-file",
            str(code_file),
            "--theorem-name",
            "proof",
            "--project",
            str(tmp_path),
        ],
    )
    project = Mock()
    monkeypatch.setattr(lean, "LocalProject", project)
    monkeypatch.setattr(lean, "LeanREPLConfig", Mock())
    tool = Mock()
    tool.verify.return_value = lean.VerificationResult("verified", "proof", "test")
    monkeypatch.setattr(lean, "LeanProofTool", Mock(return_value=tool))
    assert lean.main() == 0
    assert json.loads(capsys.readouterr().out)["verified"] is True
    project.assert_called_once_with(directory=str(tmp_path.resolve()), auto_build=False)
