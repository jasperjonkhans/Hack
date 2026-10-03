"""Host-configured adapter for Omnigent bundle tools and plain function tools.

Provision on first use per Python process. Configuration comes only from the
worker's environment, never from model-controlled tool arguments. Native bundle
tools call this in a subprocess; single-file function tools run it in-process.
Neither path guarantees isolation: configure an externally isolated worker.
"""

from __future__ import annotations

import math
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from threading import Lock

from lean_interact import LeanREPLConfig, LocalProject

from omnigent_lean.tools import LeanProofTool, VerificationResult

_lock = Lock()
_tool: LeanProofTool | None = None


def provision() -> LeanProofTool:
    """Return a worker-scoped verifier; restart the worker to change configuration."""
    global _tool
    with _lock:
        if _tool is not None:
            return _tool
        project = os.environ.get("OMNIGENT_LEAN_PROJECT")
        version = os.environ.get("OMNIGENT_LEAN_VERSION")
        if bool(project) == bool(version):
            raise ValueError("Set exactly one of OMNIGENT_LEAN_PROJECT or OMNIGENT_LEAN_VERSION")
        timeout = float(os.environ.get("OMNIGENT_LEAN_TIMEOUT", "30"))
        # Validate the deadline before any downloads/builds.
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("OMNIGENT_LEAN_TIMEOUT must be a finite positive number")
        cache = Path(
            os.environ.get("OMNIGENT_LEAN_CACHE", str(Path.home() / ".cache/omnigent/lean"))
        ).expanduser()
        with redirect_stdout(sys.stderr):
            config = LeanREPLConfig(
                project=LocalProject(
                    directory=str(Path(project).expanduser().resolve()), auto_build=False
                )
                if project
                else None,
                lean_version=version,
                cache_dir=cache,
                enable_parallel_elaboration=False,
            )
        tool = LeanProofTool(config, timeout_seconds=timeout)
        _tool = tool
        return tool


def lean_verify_proof(code: str, theorem_name: str) -> dict:
    """Verify complete Lean source and a named theorem; only verified=true is success.

    Reject incomplete proofs and non-standard axioms. Never treat an infrastructure
    error or timeout as proof success or as evidence the mathematical claim is false.
    """
    try:
        return provision().execute({"code": code, "theorem_name": theorem_name})
    except Exception as exc:
        name = theorem_name if isinstance(theorem_name, str) else ""
        return VerificationResult("error", name, f"{type(exc).__name__}: {exc}").to_dict()
