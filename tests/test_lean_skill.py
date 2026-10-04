"""Offline checks with the real Omnigent bundle parser and native tool loader."""

import importlib.util
import json
import re
import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from omnigent.inner.pi_executor import _resolve_pi_skill_args
from omnigent.spec import load
from omnigent.tools._runner import _invoke_tool, _serialize_result
from omnigent.tools.base import ToolContext
from omnigent.tools.builtins.load_skill import list_skill_resources
from omnigent.tools.local import load_local_python_tools
from omnigent.tools.local_callable import LocalCallableTool

from omnigent_lean import adapter
from omnigent_lean.tools import LEAN_PROOF_TOOL_SPEC, VerificationResult

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "team" / "agents" / "worker"  # the experiment worker carries the Lean skill
TOOL_FILE = BUNDLE / "tools" / "python" / "lean_verify_proof.py"


def load_tools(bundle):
    spec = load(bundle)
    return load_local_python_tools(
        spec.local_tools, bundle, srt_available=False, uv_available=False
    )


def without_titles(value):
    """Pydantic adds descriptive titles; the contract is otherwise identical."""
    if isinstance(value, dict):
        return {key: without_titles(item) for key, item in value.items() if key != "title"}
    if isinstance(value, list):
        return [without_titles(item) for item in value]
    return value


@pytest.mark.parametrize("harness", ["claude-sdk", "codex", "pi", "openai-agents"])
def test_native_bundle_discovers_skill_and_tool_without_provisioning(
    tmp_path, monkeypatch, harness
):
    bundle = tmp_path / "lean"
    shutil.copytree(BUNDLE, bundle)
    config_file = bundle / "config.yaml"
    config = yaml.safe_load(config_file.read_text())
    config["executor"]["config"]["harness"] = harness
    if harness != "pi":
        config["executor"]["config"].pop("context_files", None)  # Pi-only option
    config_file.write_text(yaml.safe_dump(config))
    provision = Mock(side_effect=AssertionError("Parsing must not provision Lean"))
    monkeypatch.setattr(adapter, "provision", provision)
    spec = load(bundle)
    assert spec.executor.config["harness"] == harness
    assert spec.skills_filter == ["lean"]
    assert [skill.name for skill in spec.skills] == ["lean"]
    skill = spec.skills[0]
    assert "Use when" in skill.description
    assert len(skill.description) <= 1024
    assert skill.user_invocable
    assert skill.skill_dir == bundle / "skills" / "lean"
    resources = list_skill_resources(skill)
    assert "references/tool-contract.md" in resources
    assert "references/proof-strategies.md" in resources
    assert len((skill.skill_dir / "SKILL.md").read_text().splitlines()) < 100
    for target in re.findall(r"\]\((references/[^)]+)\)", skill.content):
        assert (skill.skill_dir / target).is_file()
    assert [tool.name for tool in spec.local_tools] == ["lean_verify_proof"]
    tools = load_tools(bundle)
    assert [tool.name() for tool in tools] == ["lean_verify_proof"]
    assert (
        without_titles(tools[0].get_schema()["function"]["parameters"])
        == (LEAN_PROOF_TOOL_SPEC["parameters"])
    )
    provision.assert_not_called()


def test_pi_loads_the_bundled_skill_and_no_host_skills():
    # Under Pi, skills: none would load no skills at all, bundled ones included.
    args = _resolve_pi_skill_args(load(BUNDLE).skills_filter, BUNDLE)
    assert args == ["--no-skills", "--skill", str(BUNDLE / "skills" / "lean")]


def test_worker_writes_only_inside_its_task_directory():
    # The controller points cwd at the task's artifact directory (team/runtime.py).
    config = yaml.safe_load((BUNDLE / "config.yaml").read_text())
    assert config["os_env"]["sandbox"]["type"] == "auto"
    assert config["os_env"]["cwd"] == "."
    assert config["os_env"]["sandbox"]["write_paths"] == ["."]


@pytest.mark.parametrize("status", ["verified", "rejected", "timeout", "error"])
def test_native_runner_round_trips_skill_example_and_verdict(monkeypatch, status):
    module_spec = importlib.util.spec_from_file_location("lean_bundle_tool", TOOL_FILE)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    tool = Mock()
    tool.execute.return_value = VerificationResult(status, "Arithmetic.add_zero", "test").to_dict()
    monkeypatch.setattr(adapter, "_tool", tool)
    skill = load(BUNDLE).skills[0]
    example = json.loads(re.search(r"```json\n(.*?)\n```", skill.content, re.S)[1])
    target = module.lean_verify_proof
    result = json.loads(_serialize_result(target, _invoke_tool(target, example)))
    assert result["status"] == status
    assert result["verified"] is (status == "verified")
    tool.execute.assert_called_once_with(example)


def test_real_native_subprocess_fails_closed_without_host_config(monkeypatch, tmp_path):
    monkeypatch.delenv("OMNIGENT_LEAN_VERSION", raising=False)
    monkeypatch.delenv("OMNIGENT_LEAN_PROJECT", raising=False)
    wrapper = load_tools(BUNDLE)[0]
    # No Lean execution is possible: the worker has no host configuration.
    result = json.loads(
        wrapper.invoke(
            json.dumps({"code": "theorem proof : True := by trivial", "theorem_name": "proof"}),
            ToolContext("test-task", "test-agent", workspace=tmp_path),
        )
    )
    assert result["status"] == "error"
    assert not result["verified"]
    assert "exactly one" in result["reason"]


def test_single_file_function_registration_uses_plain_kwargs_adapter(tmp_path, monkeypatch):
    source = tmp_path / "lean.yaml"
    source.write_text(
        yaml.safe_dump(
            {
                "name": "lean_function",
                "prompt": "Only report success when verified=true.",
                "executor": {"harness": "claude-sdk"},
                "tools": {
                    "lean_verify_proof": {
                        "type": "function",
                        "callable": "omnigent_lean.adapter.lean_verify_proof",
                        "parameters": LEAN_PROOF_TOOL_SPEC["parameters"],
                    }
                },
            }
        )
    )
    provision = Mock()
    provision.return_value.execute.return_value = VerificationResult(
        "verified", "proof", "test"
    ).to_dict()
    monkeypatch.setattr(adapter, "provision", provision)
    spec = load(source)
    provision.assert_not_called()
    wrapper = LocalCallableTool(spec.local_tools[0])
    arguments = {"code": "theorem proof : True := by trivial", "theorem_name": "proof"}
    result = json.loads(wrapper.invoke(json.dumps(arguments), ToolContext("test", "test")))
    assert result["verified"] is True
    provision.return_value.execute.assert_called_once_with(arguments)


def test_extension_does_not_shadow_runtime_package():
    import omnigent

    import omnigent_lean

    assert "src/omnigent_lean" in Path(omnigent_lean.__file__).as_posix()
    assert "src/omnigent_lean" not in Path(omnigent.__file__).as_posix()
    assert not (ROOT / "src" / "omnigent").exists()
