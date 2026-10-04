"""LeanInteract-backed, fail-closed proof verification for trusted Lean projects."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections.abc import Callable, Mapping
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from lean_interact import (
    Command,
    LeanREPLConfig,
    LeanServer,
    LocalProject,
)
from lean_interact.interface import CommandResponse, LeanError

# Same standard trust base as lean-lsp-mcp; native_decide/custom axioms are not accepted.
STANDARD_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})
MAX_CODE_BYTES = 100_000
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_']*(?:\.[A-Za-z_][A-Za-z0-9_']*)*\Z")
# Conservative policy, not a sandbox. These patterns also match inside comments/strings.
_UNSAFE_SOURCE = re.compile(
    r"\bset_option\s+debug\.|\bunsafe\b|@\[[^\]]*\b(?:implemented_by|extern)\b"
)

LEAN_PROOF_TOOL_SPEC = {
    "name": "lean_verify_proof",
    "description": (
        "Verify a named theorem in Lean 4 code. "
        "Returns compiler diagnostics and axiom dependencies. "
        "Only verified=true is success; sorry, custom axioms, and native_decide are rejected. "
        "Run only in a trusted, isolated Lean project; this is not a code sandbox."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Complete Lean source, including imports."},
            "theorem_name": {
                "type": "string",
                "description": "Fully qualified ASCII theorem name, e.g. MyProof.add_zero.",
            },
        },
        "required": ["code", "theorem_name"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class VerificationResult:
    status: Literal["verified", "rejected", "error", "timeout"]
    theorem_name: str
    reason: str
    diagnostics: list[dict] = field(default_factory=list)
    axioms: list[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        return self.status == "verified"

    def to_dict(self) -> dict:
        return {**asdict(self), "verified": self.verified}


class _Server(Protocol):
    def run(self, request: Command, *, timeout: float) -> CommandResponse | LeanError: ...

    def kill(self) -> None: ...


def _parse_axioms(response: CommandResponse, name: str) -> list[str] | None:
    """Require exactly one complete axiom report for the requested declaration."""
    reports = []
    prefix = rf"'{re.escape(name)}'"
    for message in response.messages:
        if message.severity != "info":
            continue
        text = " ".join(message.data.split())
        if re.fullmatch(prefix + r" does not depend on any axioms", text):
            reports.append([])
        elif match := re.fullmatch(prefix + r" depends on axioms: \[([^\[\]]*)\]", text):
            names = [part.strip() for part in match[1].split(",")] if match[1].strip() else []
            if not all(_NAME.fullmatch(axiom) for axiom in names):
                return None
            reports.append(sorted(set(names)))
    return reports[0] if len(reports) == 1 else None


class LeanProofTool:
    """Host-configured verifier. Each call owns a fresh REPL and always closes it.

    Provision LeanREPLConfig before constructing this tool. Toolchain download/build time
    is deliberately outside the shared compilation + axiom-query timeout.
    """

    def __init__(
        self,
        config: LeanREPLConfig | None = None,
        *,
        timeout_seconds: float = 30,
        server_factory: Callable[[], _Server] | None = None,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a finite positive number")
        if server_factory is None:
            if config is None:
                raise ValueError("Provide a provisioned LeanREPLConfig")

            def server_factory() -> _Server:
                return LeanServer(config)

        self._server_factory = server_factory
        self.timeout_seconds = timeout_seconds

    def execute(self, arguments: Mapping[str, object]) -> dict:
        """JSON tool-call adapter; project paths and trust policy remain host-controlled."""
        if not isinstance(arguments, Mapping) or set(arguments) != {"code", "theorem_name"}:
            return VerificationResult(
                "rejected", "", "Expected exactly code and theorem_name arguments"
            ).to_dict()
        return self.verify(arguments["code"], arguments["theorem_name"]).to_dict()

    def verify(self, code: object, theorem_name: object) -> VerificationResult:
        name = theorem_name if isinstance(theorem_name, str) else ""
        if not isinstance(code, str) or not code.strip():
            return VerificationResult("rejected", name, "code must be a nonempty string")
        if not name or not _NAME.fullmatch(name) or name.startswith("_root_."):
            return VerificationResult(
                "rejected", name, "Expected a fully qualified ASCII theorem name"
            )
        if len(code.encode("utf-8", errors="replace")) > MAX_CODE_BYTES:
            return VerificationResult("rejected", name, f"code exceeds {MAX_CODE_BYTES} bytes")
        if _UNSAFE_SOURCE.search(code):
            return VerificationResult(
                "rejected", name, "Source contains a disallowed trust override"
            )

        server = None
        diagnostics: list[dict] = []
        deadline = time.monotonic() + self.timeout_seconds

        def run(request: Command) -> CommandResponse:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Proof verification deadline exceeded")
            response = server.run(request, timeout=remaining)
            if isinstance(response, LeanError):
                raise RuntimeError(response.message)
            if not isinstance(response, CommandResponse):
                raise RuntimeError("Unexpected Lean response")
            diagnostics.extend(
                message.model_dump(by_alias=True, exclude_none=True)
                for message in response.messages
            )
            return response

        def check() -> VerificationResult:
            compiled = run(Command(cmd=code, declarations=True))
            if not compiled.lean_code_is_valid(allow_sorry=False):
                return VerificationResult(
                    "rejected", name, "Lean reported errors or an incomplete proof", diagnostics
                )
            # No vacuous success for empty code, definitions, axioms, or imported theorems.
            if not any(
                declaration.full_name == name and declaration.kind == "theorem"
                for declaration in compiled.declarations
            ):
                return VerificationResult(
                    "rejected",
                    name,
                    "Requested theorem was not declared in this source",
                    diagnostics,
                )
            checked = run(Command(cmd=f"#print axioms _root_.{name}", env=compiled.env))
            if not checked.lean_code_is_valid(allow_sorry=False):
                return VerificationResult("rejected", name, "Axiom check failed", diagnostics)
            axioms = _parse_axioms(checked, name)
            if axioms is None:
                return VerificationResult(
                    "error", name, "Missing or ambiguous axiom report", diagnostics
                )
            unexpected = sorted(set(axioms) - STANDARD_AXIOMS)
            if unexpected:
                return VerificationResult(
                    "rejected",
                    name,
                    f"Untrusted axioms: {', '.join(unexpected)}",
                    diagnostics,
                    axioms,
                )
            return VerificationResult(
                "verified", name, "Lean verified the theorem", diagnostics, axioms
            )

        try:
            server = self._server_factory()
            result = check()
        except TimeoutError as exc:
            result = VerificationResult("timeout", name, str(exc), diagnostics)
        except Exception as exc:
            # Backend/configuration failures must never become successful verification.
            result = VerificationResult("error", name, f"{type(exc).__name__}: {exc}", diagnostics)
        finally:
            if server is not None:
                try:
                    server.kill()
                except Exception as exc:
                    result = VerificationResult(
                        "error", name, f"REPL cleanup failed: {exc}", diagnostics
                    )
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=LEAN_PROOF_TOOL_SPEC["description"])
    parser.add_argument("--code-file", type=Path, required=True)
    parser.add_argument("--theorem-name", required=True)
    environment = parser.add_mutually_exclusive_group(required=True)
    environment.add_argument("--project", type=Path, help="Trusted, pre-built Lake project")
    environment.add_argument(
        "--lean-version", help="Explicit Lean version for standard-library proofs"
    )
    parser.add_argument(
        "--timeout", type=float, default=30, help="Query budget, excluding provisioning"
    )
    parser.add_argument(
        "--cache-dir", type=Path, default=Path.home() / ".cache" / "omnigent" / "lean"
    )
    args = parser.parse_args()
    try:
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("timeout must be a finite positive number")
        code = args.code_file.read_text(encoding="utf-8")
        # Keep provisioning logs off the machine-readable stdout channel.
        with redirect_stdout(sys.stderr):
            # Host-selected configuration, never a path supplied by the agent tool call.
            config = LeanREPLConfig(
                project=LocalProject(directory=str(args.project.resolve()), auto_build=False)
                if args.project
                else None,
                lean_version=args.lean_version,
                cache_dir=args.cache_dir,
                enable_parallel_elaboration=False,
            )
            result = LeanProofTool(config, timeout_seconds=args.timeout).verify(
                code, args.theorem_name
            )
    except Exception as exc:
        result = VerificationResult("error", args.theorem_name, f"{type(exc).__name__}: {exc}")
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    return 0 if result.verified else 1 if result.status == "rejected" else 2
