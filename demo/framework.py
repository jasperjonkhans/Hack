"""Development starter: role definitions -> native Omnigent bundles.

No custom agent loop, provider client, message bus, or persistence layer.
"""
from dataclasses import dataclass, field, replace
import json
from pathlib import Path
import re


@dataclass(frozen=True)
class Runtime:
    harness: str = "claude-sdk"
    model: str | None = None
    profile: str | None = None

    def config(self) -> dict:
        result = {"harness": self.harness}
        if self.model:
            result["model"] = self.model
        if self.profile:
            result["auth"] = {"type": "databricks", "profile": self.profile}
        return result


@dataclass(frozen=True)
class Role:
    name: str
    description: str
    instructions: str
    writable: bool = False
    runtime: Runtime = field(default_factory=Runtime)

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", self.name):
            raise ValueError(f"Invalid role name: {self.name!r}")

    def config(self, workspace: Path, sandbox: str) -> dict:
        executor = self.runtime.config()
        harness = executor.pop("harness")
        return {
            "spec_version": 1,
            "name": self.name,
            "description": self.description,
            "instructions": "INSTRUCTIONS.md",
            "executor": {"type": "omnigent", "config": {"harness": harness}, **executor},
            "os_env": {
                "type": "caller_process",
                "cwd": str(workspace.resolve()),
                "sandbox": {
                    "type": sandbox,
                    "read_paths": [str(workspace.resolve())],
                    "write_paths": [str(workspace.resolve())] if self.writable else [],
                    "allow_network": True,
                },
            },
            "guardrails": {"policies": {"blast_radius": {
                "type": "function",
                "on": ["tool_call"],
                "function": {
                    "path": "omnigent.inner.nessie.policies.blast_radius",
                    "arguments": {"gate_pushes": True},
                },
            }}},
        }


@dataclass(frozen=True)
class Team:
    lead: Role
    roles: tuple[Role, ...] = ()

    def __post_init__(self):
        names = [r.name for r in (self.lead, *self.roles)]
        if len(names) != len(set(names)):
            raise ValueError("Role names must be unique")

    def documents(self, workspace: Path, sandbox: str = "auto") -> dict[Path, str]:
        """JSON is valid YAML; keep the compiler dependency-free."""
        if sandbox not in {"auto", "none"}:
            raise ValueError("Sandbox must be auto or none")
        files = {}
        for folder, role in [(Path("."), self.lead), *[
            (Path("agents") / r.name, r) for r in self.roles
        ]]:
            files[folder / "config.yaml"] = json.dumps(
                role.config(workspace, sandbox), indent=2
            ) + "\n"
            files[folder / "INSTRUCTIONS.md"] = role.instructions.strip() + "\n"
        return files

    def export(self, destination: Path, workspace: Path, sandbox: str = "auto") -> Path:
        """Never overwrite an existing bundle or modify the target workspace."""
        if not workspace.is_dir():
            raise ValueError(f"Workspace does not exist: {workspace}")
        destination = destination.resolve()
        destination.mkdir(parents=True, exist_ok=False)
        for relative, content in self.documents(workspace, sandbox).items():
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return destination

    def on(self, runtime: Runtime) -> "Team":
        return Team(replace(self.lead, runtime=runtime), tuple(
            replace(role, runtime=runtime) for role in self.roles
        ))
