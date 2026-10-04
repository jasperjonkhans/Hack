"""Local Omnigent sessions adapter. No role implementations or fake model responses."""
from __future__ import annotations

import asyncio
from contextlib import suppress
import io
import json
from pathlib import Path
import re
import tarfile
from urllib.parse import urlparse

import yaml


def bundle(config_path, workspace):
    """Package one role, disabling agent-owned spawning; the controller dispatches."""
    config_path = Path(config_path)
    config = yaml.safe_load(config_path.read_text())
    config["spawn"] = False
    config.setdefault("tools", {})["agents"] = []
    if "os_env" in config:
        config["os_env"]["cwd"] = str(Path(workspace).resolve())
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        data = yaml.safe_dump(config).encode()
        entry = tarfile.TarInfo("config.yaml")
        entry.size = len(data)
        archive.addfile(entry, io.BytesIO(data))
        # Keep external roles' instructions/MCP/tools, but never bundle their sub-agents.
        for child in config_path.parent.iterdir():
            if child.name in ("tools", "skills", "mcp_servers") or child.suffix == ".md":
                archive.add(child, arcname=child.name)
    return payload.getvalue()


def json_object(text):
    """Parse an agent's JSON reply, tolerating a Markdown code fence around it."""
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    return json.loads(fenced.group(1) if fenced else stripped)


def assistant_text(output):
    messages = [item for item in output
                if item.get("type") == "message" and item.get("role") == "assistant"]
    if not messages:
        return ""
    return "".join(block.get("text", "") for block in messages[-1].get("content", [])
                   if block.get("type") in ("output_text", "text"))


# Research roles live with their literature tools in lab/agents/, outside team/.
LAB_ROLES = ("scout", "verifier")


class OmnigentRuntime:
    def __init__(self, team, server, runner=None, timeout=900):
        if urlparse(server).hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("This starter uses a local server/runner and local artifact paths only")
        if timeout <= 0:
            raise ValueError("Timeout must be positive")
        self.team, self.server, self.runner, self.timeout = Path(team), server, runner, timeout
        self.client = None

    def config_path(self, role):
        if role == "lead":
            return self.team / "config.yaml"
        if role in LAB_ROLES:
            return self.team.parent / "lab" / "agents" / role / "config.yaml"
        return self.team / "agents" / role / "config.yaml"

    def preflight(self, phase):
        roles = {"lead": ("lead",), "research": ("lead", "scout", "verifier"),
                 "experiments": ("lead", "worker")}[phase]
        missing = [str(self.config_path(role)) for role in roles if not self.config_path(role).is_file()]
        if missing:
            raise FileNotFoundError("Missing agent configs: " + ", ".join(missing))

    async def __aenter__(self):
        from omnigent_client import OmnigentClient
        self.client = OmnigentClient(base_url=self.server)
        return self

    async def __aexit__(self, *exc):
        await self.client.close()

    async def invoke(self, role, request, workspace):
        from omnigent.server.schemas import (
            CancelledEvent, CompletedEvent, FailedEvent, IncompleteEvent, OutputItemDoneEvent,
        )
        path = self.config_path(role)
        runner = self.runner or await self.client.sessions.resolve_online_runner(harness="pi")
        if runner is None:
            raise RuntimeError("No online Pi runner; start Omnigent and register a local runner first")
        chat = await self.client.sessions_chat(bundle(path, workspace))
        await self.client.sessions.bind_runner(chat.session_id, runner_id=runner)
        messages = []
        try:
            async with asyncio.timeout(self.timeout):
                async for event in chat.send(json.dumps(request)):
                    if isinstance(event, (FailedEvent, IncompleteEvent, CancelledEvent)):
                        raise RuntimeError(f"Agent {role} did not complete: {event.type}")
                    if isinstance(event, OutputItemDoneEvent):
                        messages.append(event.item)
                    if isinstance(event, CompletedEvent):
                        text = assistant_text(event.response.output) or assistant_text(messages)
                        result = json_object(text)
                        if not isinstance(result, dict):
                            raise ValueError(f"Agent {role} must return a JSON object")
                        return {**result, "session_id": chat.session_id}
            raise RuntimeError(f"Agent {role} stream ended without a completed response")
        except BaseException:
            # Closing an SSE subscriber alone does not stop server-side agent work.
            with suppress(Exception):
                await chat.cancel()
            raise
