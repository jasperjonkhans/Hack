"""Mimir, the shared server's only agent: lab/config.yaml with its Scout and Verifier sub-agents."""

import dataclasses
import importlib.util
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

import omnigent.server.app as server_app
import omnigent.stores.agent_store.sqlalchemy_store as agent_store_module
import yaml
from omnigent.entities.pagination import PagedList
from omnigent.server.app import _tar_gz_dir
from omnigent.server.bundles import validate_agent_bundle
from omnigent.spec import load, materialize_bundle
from omnigent.tools.manager import ToolManager

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "lab"
OVERRIDES = ROOT / "deploy" / "oracle" / "omnigent-overrides" / "sitecustomize.py"
READ_ONLY_DB_TOOLS = {"find_work", "search_works", "get_work", "list_claims", "get_claim"}


def dispatch_targets(spec):
    """The sub-agents the runner lets this agent start: sys_session_send's agent enum."""
    tools = ToolManager(spec, os_env_schema_only=True)
    assert "sys_session_create" not in tools.get_tool_names()  # no agents outside the bundle
    for schema in tools.get_tool_schemas():
        if schema["function"]["name"] == "sys_session_send":
            return schema["function"]["parameters"]["properties"]["agent"]["enum"]
    return None


def test_lead_delegates_to_scout_and_verifier():
    spec = load(LAB)
    assert spec.executor.config["harness"] == "pi"
    assert spec.skills_filter == "none"
    assert [sub.name for sub in spec.sub_agents] == ["scout", "verifier"]
    # Discovered sub-agents are not enough: Omnigent registers sys_session_send
    # only for agents named in tools.agents (or spawn: true).
    assert dispatch_targets(spec) == ["scout", "verifier"]


def test_lead_only_reads_the_database():
    (server,) = load(LAB).mcp_servers
    assert server.name == "academic_db"
    assert set(server.tools) == READ_ONLY_DB_TOOLS


def test_bundle_seeds_like_the_server_does():
    with tempfile.TemporaryDirectory() as tmp:
        data = _tar_gz_dir(materialize_bundle(LAB, Path(tmp) / "bundle"))
    spec = validate_agent_bundle(data, enforce_handler_allowlist=False)
    assert [sub.name for sub in spec.sub_agents] == ["scout", "verifier"]
    assert dispatch_targets(spec) == ["scout", "verifier"]
    names = tarfile.open(fileobj=BytesIO(data), mode="r:gz").getnames()
    for role in ("scout", "verifier"):
        assert any(n.startswith(f"./agents/{role}/tools/python/") for n in names), role


def test_server_offers_only_mimir():
    compose = yaml.safe_load((ROOT / "deploy" / "oracle" / "docker-compose.yaml").read_text())
    service = compose["services"]["omnigent"]
    assert service["environment"]["OMNIGENT_BUILTIN_AGENT_DIRS"] == "/agents/Mimir"
    assert "../../lab:/agents/Mimir:ro" in service["volumes"]
    assert not any("demo/team" in volume for volume in service["volumes"])
    assert service["environment"]["PYTHONPATH"] == "/opt/omnigent-overrides"
    assert "./omnigent-overrides:/opt/omnigent-overrides:ro" in service["volumes"]


def load_overrides(monkeypatch):
    """Import sitecustomize.py without installing its import hook globally."""
    monkeypatch.setattr("sys.meta_path", list(__import__("sys").meta_path))
    spec = importlib.util.spec_from_file_location("mimir_overrides", OVERRIDES)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_overrides_target_this_omnigent_version(monkeypatch):
    # Fails when an Omnigent upgrade renames what the overrides patch.
    overrides = load_overrides(monkeypatch)
    monkeypatch.setattr(server_app, "_ensure_default_agents", server_app._ensure_default_agents)
    monkeypatch.setattr(
        agent_store_module.SqlAlchemyAgentStore,
        "list",
        agent_store_module.SqlAlchemyAgentStore.list,
    )
    assert overrides.seed_only_mounted_agents(server_app)
    assert overrides.list_only_mounted_agents(agent_store_module)


def test_only_mounted_agents_are_seeded(monkeypatch):
    overrides = load_overrides(monkeypatch)
    seeded = []
    monkeypatch.setattr(
        server_app, "_ensure_extra_builtin_agents", lambda *args: seeded.append(args)
    )
    monkeypatch.setattr(server_app, "_ensure_default_agents", server_app._ensure_default_agents)
    overrides.seed_only_mounted_agents(server_app)
    server_app._ensure_default_agents("store", "artifacts", "cache")
    assert seeded == [("store", "artifacts", "cache")]  # no native, ACP, debby or polly agents


def test_picker_lists_only_mounted_agents(monkeypatch):
    overrides = load_overrides(monkeypatch)
    agents = [
        type("Agent", (), {"id": f"a{i}", "name": name})()
        for i, name in enumerate(["pi-native-ui", "Mimir", "hack-team", "debby"])
    ]
    store = agent_store_module.SqlAlchemyAgentStore
    monkeypatch.setattr(
        store,
        "list",
        lambda self, **kw: PagedList(data=agents, first_id="a0", last_id="a3", has_more=False),
    )
    overrides.list_only_mounted_agents(agent_store_module)
    monkeypatch.setenv("OMNIGENT_BUILTIN_AGENT_DIRS", "/agents/Mimir")
    page = store.list(None, limit=20)
    assert [agent.name for agent in page.data] == ["Mimir"]
    assert (page.first_id, page.last_id) == ("a1", "a1")
    assert dataclasses.is_dataclass(page)
