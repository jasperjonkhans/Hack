"""Omnigent server customisations for the Mimir deployment.

Python imports this module at startup because its directory is on PYTHONPATH
(docker-compose.yaml). Omnigent has no setting for either change:

- The agent picker (GET /v1/agents) lists only the agents mounted through
  OMNIGENT_BUILTIN_AGENT_DIRS. Other rows, e.g. agents of older chats, stay in
  the database, so those chats still open.
- Omnigent's packaged agents (native UIs, ACP CLIs, debby, polly) are no longer
  seeded at startup; only the mounted agents are.

Each patch is applied right after Omnigent imports the module it changes, so
import order is untouched. If a future Omnigent release renames the patched
function, the patch logs a warning and does nothing: the picker then shows every
agent again instead of the server failing to start.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import sys
from importlib.abc import MetaPathFinder
from importlib.machinery import PathFinder
from pathlib import PurePosixPath

_log = logging.getLogger("mimir.overrides")


def mounted_agent_names() -> set[str]:
    """Agent names Omnigent derives from OMNIGENT_BUILTIN_AGENT_DIRS (dir name or file stem)."""
    names = set()
    for entry in os.environ.get("OMNIGENT_BUILTIN_AGENT_DIRS", "").split(os.pathsep):
        if entry.strip():
            path = PurePosixPath(entry.strip())
            names.add(path.stem if path.suffix in (".yaml", ".yml") else path.name)
    return names


def seed_only_mounted_agents(app_module) -> bool:
    """Make startup seeding register just the mounted agents."""
    seed_mounted = getattr(app_module, "_ensure_extra_builtin_agents", None)
    if seed_mounted is None or not hasattr(app_module, "_ensure_default_agents"):
        _log.warning("Omnigent's agent seeding changed; packaged agents will still be seeded")
        return False

    def _ensure_default_agents(agent_store, artifact_store, agent_cache):
        seed_mounted(agent_store, artifact_store, agent_cache)

    app_module._ensure_default_agents = _ensure_default_agents
    return True


def list_only_mounted_agents(store_module) -> bool:
    """Filter the picker's agent list to the mounted agents."""
    store = getattr(store_module, "SqlAlchemyAgentStore", None)
    if store is None or not hasattr(store, "list"):
        _log.warning("Omnigent's agent store changed; the picker will list every agent")
        return False
    list_all = store.list

    def list_mounted(self, *args, **kwargs):
        page = list_all(self, *args, **kwargs)
        allowed = mounted_agent_names()
        if not allowed:
            return page
        data = [agent for agent in page.data if agent.name in allowed]
        return dataclasses.replace(
            page,
            data=data,
            first_id=data[0].id if data else None,
            last_id=data[-1].id if data else None,
        )

    store.list = list_mounted
    return True


_PATCHES = {
    "omnigent.server.app": seed_only_mounted_agents,
    "omnigent.stores.agent_store.sqlalchemy_store": list_only_mounted_agents,
}


class _PatchAfterImport(MetaPathFinder):
    """Run a patch right after Omnigent executes one of the modules in _PATCHES."""

    def find_spec(self, name, path, target=None):
        patch = _PATCHES.get(name)
        if patch is None:
            return None
        spec = PathFinder.find_spec(name, path, target)
        if spec is None or spec.loader is None:
            return spec
        execute = spec.loader.exec_module

        def exec_module(module):
            execute(module)
            try:
                patch(module)
            except Exception:  # never stop the server over a customisation
                _log.exception("Mimir override for %s failed", name)

        spec.loader.exec_module = exec_module
        return spec


sys.meta_path.insert(0, _PatchAfterImport())
