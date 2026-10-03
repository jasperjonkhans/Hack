import json
from unittest.mock import Mock

import pytest
from lean_interact.interface import CommandResponse, DeclarationInfo, LeanError

from omnigent.tools.lean import LEAN_PROOF_TOOL_SPEC, MAX_CODE_BYTES, LeanProofTool


def response(*, messages=(), declarations=(), sorries=()):
    result = CommandResponse(
        env=7,
        messages=[
            {"severity": severity, "data": text, "pos": {"line": 1, "column": 0}}
            for severity, text in messages
        ],
    )
    # Only these two declaration fields are read by the verifier; real metadata is tested
    # in test_lean_integration.py against the actual REPL.
    return result.model_copy(
        update={
            "declarations": [
                DeclarationInfo.model_construct(full_name=name, kind=kind)
                for name, kind in declarations
            ],
            "sorries": list(sorries),
        }
    )


def compiled(name="proof", kind="theorem", **kwargs):
    return response(declarations=[(name, kind)], **kwargs)


def report(axioms=None, name="proof"):
    text = (
        f"'{name}' does not depend on any axioms"
        if axioms is None
        else f"'{name}' depends on axioms: [{', '.join(axioms)}]"
    )
    return response(messages=[("info", text)])


def tool_with(*results):
    server = Mock()
    server.run.side_effect = results
    return LeanProofTool(server_factory=lambda: server), server


@pytest.mark.parametrize("axioms", [None, [], ["propext", "Classical.choice", "Quot.sound"]])
def test_accepts_kernel_checked_theorem(axioms):
    tool, server = tool_with(compiled(), report(axioms))
    result = tool.execute({"code": "theorem proof : True := by trivial", "theorem_name": "proof"})
    assert result["verified"] is True
    assert result["status"] == "verified"
    assert result["axioms"] == sorted(axioms or [])
    assert json.loads(json.dumps(result)) == result
    assert server.run.call_args_list[0].args[0].env is None
    assert server.run.call_args_list[0].args[0].declarations is True
    assert server.run.call_args_list[1].args[0].env == 7
    assert server.run.call_args_list[1].args[0].cmd == "#print axioms _root_.proof"
    server.kill.assert_called_once()


@pytest.mark.parametrize(
    "axiom", ["sorryAx", "assumption", "Lean.ofReduceBool", "proof._native.native_decide.ax"]
)
def test_rejects_untrusted_axioms(axiom):
    tool, server = tool_with(compiled(), report([axiom]))
    result = tool.verify("theorem proof : True := by trivial", "proof")
    assert not result.verified
    assert result.status == "rejected"
    assert result.axioms == [axiom]
    server.kill.assert_called_once()


@pytest.mark.parametrize(
    "severity,text",
    [
        ("error", "type mismatch"),
        ("warning", "declaration uses 'sorry'"),
        ("warning", "declaration uses `sorry`"),
    ],
)
def test_rejects_errors_and_sorry_without_axiom_query(severity, text):
    tool, server = tool_with(compiled(messages=[(severity, text)]))
    result = tool.verify("theorem proof : True := by sorry", "proof")
    assert not result.verified
    assert result.diagnostics[0]["data"] == text
    assert result.diagnostics[0]["pos"]["line"] == 1
    assert server.run.call_count == 1
    server.kill.assert_called_once()


def test_rejects_reported_sorry_even_without_warning():
    tool, _ = tool_with(compiled(sorries=[Mock(start_pos=None, end_pos=None)]))
    assert not tool.verify("theorem proof : True := by sorry", "proof").verified


@pytest.mark.parametrize("kind", ["def", "axiom", "example"])
def test_target_must_be_a_theorem(kind):
    tool, server = tool_with(compiled(kind=kind))
    assert not tool.verify("def proof := 1", "proof").verified
    assert server.run.call_count == 1


def test_missing_or_imported_target_is_rejected():
    tool, _ = tool_with(response())
    assert not tool.verify("import Init", "proof").verified


def test_namespace_target():
    tool, _ = tool_with(compiled(name="Foo.proof"), report(name="Foo.proof"))
    assert tool.verify(
        "namespace Foo\ntheorem proof : True := by trivial\nend Foo", "Foo.proof"
    ).verified


@pytest.mark.parametrize(
    "bad_report",
    [
        [],
        [("info", "'other' does not depend on any axioms")],
        [("info", "'proof' depends on axioms: [sorryAx")],
        [("warning", "'proof' does not depend on any axioms")],
        [("info", "'proof' does not depend on any axioms")] * 2,
        [("info", "'proof' depends on axioms: [bad name]")],
        [("info", "'proof' depends on axioms: [,]")],
        [("info", "'proof' depends on axioms: [propext,]")],
    ],
)
def test_fails_closed_on_missing_or_malformed_axiom_report(bad_report):
    tool, _ = tool_with(compiled(), response(messages=bad_report))
    result = tool.verify("theorem proof : True := by trivial", "proof")
    assert result.status == "error"
    assert not result.verified


def test_axiom_query_errors_are_not_success():
    tool, _ = tool_with(compiled(), response(messages=[("error", "unknown constant")]))
    assert not tool.verify("theorem proof : True := by trivial", "proof").verified


@pytest.mark.parametrize(
    "failure,status",
    [
        (TimeoutError("deadline"), "timeout"),
        (LeanError(message="REPL failure"), "error"),
        (ConnectionAbortedError("REPL died"), "error"),
        (ValueError("invalid JSON"), "error"),
    ],
)
@pytest.mark.parametrize("phase", ["compile", "axioms"])
def test_backend_failures_and_cleanup(failure, status, phase):
    tool, server = tool_with(*([compiled()] if phase == "axioms" else []), failure)
    result = tool.verify("theorem proof : True := by trivial", "proof")
    assert result.status == status
    assert not result.verified
    server.kill.assert_called_once()


def test_startup_failure():
    factory = Mock(side_effect=FileNotFoundError("lake"))
    result = LeanProofTool(server_factory=factory).verify(
        "theorem proof : True := by trivial", "proof"
    )
    assert result.status == "error"


def test_timeout_is_one_shared_budget(monkeypatch):
    clock = iter([100, 101, 115])
    monkeypatch.setattr("omnigent.tools.lean.time.monotonic", lambda: next(clock))
    tool, server = tool_with(compiled(), report())
    assert tool.verify("theorem proof : True := by trivial", "proof").verified
    assert [call.kwargs["timeout"] for call in server.run.call_args_list] == [29, 15]


def test_exhausted_deadline_does_not_start_second_query(monkeypatch):
    clock = iter([100, 101, 131])
    monkeypatch.setattr("omnigent.tools.lean.time.monotonic", lambda: next(clock))
    tool, server = tool_with(compiled(), report())
    assert tool.verify("theorem proof : True := by trivial", "proof").status == "timeout"
    assert server.run.call_count == 1
    server.kill.assert_called_once()


@pytest.mark.parametrize(
    "code,name",
    [
        ("", "proof"),
        (None, "proof"),
        (42, "proof"),
        ("x", None),
        ("x", ""),
        ("x", "proof\n#eval 1"),
        ("x", "_root_.proof"),
        ("x" * (MAX_CODE_BYTES + 1), "proof"),
        ("set_option debug.skipKernelTC true", "proof"),
        ("unsafe def x := 1", "proof"),
        ("@[implemented_by trick] theorem proof : True := by trivial", "proof"),
        ('@[extern "trick"] def x := 1', "proof"),
    ],
)
def test_invalid_input_does_not_start_lean(code, name):
    factory = Mock()
    assert not LeanProofTool(server_factory=factory).verify(code, name).verified
    factory.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [None, {}, {"code": "x"}, {"code": "x", "theorem_name": "proof", "project": "/tmp"}],
)
def test_tool_adapter_validates_arguments(arguments):
    factory = Mock()
    assert LeanProofTool(server_factory=factory).execute(arguments)["verified"] is False
    factory.assert_not_called()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, "30"])
def test_timeout_validation(timeout):
    with pytest.raises(ValueError):
        LeanProofTool(server_factory=Mock(), timeout_seconds=timeout)


def test_schema_has_no_agent_controlled_paths_or_trust_overrides():
    assert set(LEAN_PROOF_TOOL_SPEC["parameters"]["properties"]) == {"code", "theorem_name"}
    assert LEAN_PROOF_TOOL_SPEC["parameters"]["additionalProperties"] is False


def test_repeated_calls_do_not_share_environments():
    servers = [Mock(), Mock()]
    for server in servers:
        server.run.side_effect = [compiled(), report()]
    factory = Mock(side_effect=servers)
    tool = LeanProofTool(server_factory=factory)
    for _ in range(2):
        assert tool.verify("theorem proof : True := by trivial", "proof").verified
    assert factory.call_count == 2
    for server in servers:
        assert server.run.call_args_list[0].args[0].env is None
        server.kill.assert_called_once()


def test_cleanup_failure_is_an_error_not_success():
    tool, server = tool_with(compiled(), report())
    server.kill.side_effect = RuntimeError("unable to close REPL")
    result = tool.verify("theorem proof : True := by trivial", "proof")
    assert result.status == "error"
    assert not result.verified
    assert "cleanup" in result.reason


def test_unexpected_response_is_an_error():
    tool, server = tool_with(object())
    assert tool.verify("theorem proof : True := by trivial", "proof").status == "error"
    server.kill.assert_called_once()


def test_harmless_warnings_do_not_reject_proofs():
    tool, _ = tool_with(compiled(messages=[("warning", "unused variable")]), report())
    result = tool.verify("theorem proof : True := by trivial", "proof")
    assert result.verified
    assert result.diagnostics[0]["severity"] == "warning"


def test_requires_a_provisioned_config():
    with pytest.raises(ValueError, match="LeanREPLConfig"):
        LeanProofTool()
