"""The research-team built-in agent: lab/config.yaml with its Scout and Verifier sub-agents."""

import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

import yaml
from omnigent.server.app import _tar_gz_dir
from omnigent.server.bundles import validate_agent_bundle
from omnigent.spec import load, materialize_bundle
from omnigent.tools.builtins.spawn import _build_sys_session_send_schema

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "lab"
READ_ONLY_DB_TOOLS = {"find_work", "search_works", "get_work", "list_claims", "get_claim"}


def test_lead_delegates_to_scout_and_verifier():
    spec = load(LAB)
    assert spec.executor.config["harness"] == "pi"
    assert spec.skills_filter == "none"
    assert [sub.name for sub in spec.sub_agents] == ["scout", "verifier"]
    schema = _build_sys_session_send_schema({sub.name: sub for sub in spec.sub_agents})
    assert "'scout'" in repr(schema) and "'verifier'" in repr(schema)


def test_lead_only_reads_the_database():
    (server,) = load(LAB).mcp_servers
    assert server.name == "academic_db"
    assert set(server.tools) == READ_ONLY_DB_TOOLS


def test_bundle_seeds_like_the_server_does():
    with tempfile.TemporaryDirectory() as tmp:
        data = _tar_gz_dir(materialize_bundle(LAB, Path(tmp) / "bundle"))
    spec = validate_agent_bundle(data, enforce_handler_allowlist=False)
    assert [sub.name for sub in spec.sub_agents] == ["scout", "verifier"]
    names = tarfile.open(fileobj=BytesIO(data), mode="r:gz").getnames()
    for role in ("scout", "verifier"):
        assert any(n.startswith(f"./agents/{role}/tools/python/") for n in names), role


def test_server_mounts_lab_as_research_team():
    compose = yaml.safe_load((ROOT / "deploy" / "oracle" / "docker-compose.yaml").read_text())
    service = compose["services"]["omnigent"]
    agent_dirs = service["environment"]["OMNIGENT_BUILTIN_AGENT_DIRS"].split(":")
    assert "/agents/research-team" in agent_dirs
    assert "../../lab:/agents/research-team:ro" in service["volumes"]
