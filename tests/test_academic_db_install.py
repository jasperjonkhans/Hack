"""The VM runs academic-db-mcp with the locked dependencies, never a fresh resolve."""

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "oracle" / "deploy.sh"


def test_deploy_links_the_locked_entry_point():
    # `uv tool install` ignores uv.lock; on the VM it pulled mcp 2.x and the
    # server failed to start, so agents got none of its tools.
    script = DEPLOY.read_text()
    assert not re.search(r"^\s*uv tool install", script, re.M)
    assert '"$repo_dir/.venv/bin/academic-db-mcp" "$HOME/.local/bin/academic-db-mcp"' in script


def test_requirement_excludes_mcp_2():
    project = tomllib.loads((ROOT / "tools" / "academic_db" / "pyproject.toml").read_text())
    (mcp,) = [r for r in map(Requirement, project["project"]["dependencies"]) if r.name == "mcp"]
    assert not mcp.specifier.contains("2.0.0")
    assert mcp.specifier.contains("1.30.0")
