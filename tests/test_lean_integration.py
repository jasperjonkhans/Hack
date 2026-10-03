"""Real Lean tests: opt in with OMNIGENT_LEAN_VERSION=v4.24.0."""

import os
import subprocess
from pathlib import Path

import pytest
from lean_interact import LeanREPLConfig, LocalProject

from omnigent.tools.lean import LeanProofTool

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def config():
    version = os.environ.get("OMNIGENT_LEAN_VERSION")
    if not version:
        pytest.skip("Set OMNIGENT_LEAN_VERSION to run real Lean integration tests")
    return LeanREPLConfig(
        lean_version=version,
        cache_dir=os.environ.get(
            "OMNIGENT_LEAN_CACHE", str(Path.home() / ".cache" / "omnigent" / "lean")
        ),
        enable_parallel_elaboration=False,
    )


@pytest.mark.parametrize(
    "code,name,verified",
    [
        ("theorem proof (n : Nat) : n + 0 = n := by rfl", "proof", True),
        ("-- sorry in a comment is harmless\ntheorem proof : True := by trivial", "proof", True),
        ("namespace Demo\ntheorem proof : True := by trivial\nend Demo", "Demo.proof", True),
        ("theorem proof : False := by trivial", "proof", False),
        ("theorem proof : False := by sorry", "proof", False),
        ("theorem proof : False := by admit", "proof", False),
        ("axiom cheat : False\ntheorem proof : False := cheat", "proof", False),
        ("theorem proof : True := by native_decide", "proof", False),
        ("def proof : Nat := 1", "proof", False),
        ("import Init", "Nat.add_zero", False),
        ("theorem other : True := by trivial", "proof", False),
        ("theorem unused : False := by sorry\ntheorem proof : True := by trivial", "proof", False),
        ("theorem proof (p : Prop) : p ∨ ¬ p := Classical.em p", "proof", True),
    ],
)
def test_real_lean_verdict(config, code, name, verified):
    result = LeanProofTool(config).verify(code, name)
    assert result.verified is verified, result.to_dict()
    assert result.status == ("verified" if verified else "rejected"), result.to_dict()


def test_real_query_timeout(config):
    # A deliberately tiny query budget guarantees deadline exhaustion without a costly proof.
    result = LeanProofTool(config, timeout_seconds=0.000001).verify(
        "theorem proof : True := by trivial", "proof"
    )
    assert result.status == "timeout"
    assert not result.verified


def test_no_environment_leaks_between_calls(config):
    tool = LeanProofTool(config)
    assert tool.verify("theorem previous : True := by trivial", "previous").verified
    result = tool.verify("theorem proof : True := previous", "proof")
    assert not result.verified
    assert result.status == "rejected"


@pytest.fixture(scope="module")
def local_config(config, tmp_path_factory):
    directory = tmp_path_factory.mktemp("lean-project")
    (directory / "lean-toolchain").write_text(f"leanprover/lean4:{config.lean_version}\n")
    (directory / "lakefile.toml").write_text(
        'name = "proof_project"\n[[lean_lib]]\nname = "Hidden"\n'
    )
    (directory / "Hidden.lean").write_text(
        "namespace Hidden\n"
        "theorem unfinished : False := by sorry\n"
        "theorem complete : True := by trivial\n"
        "end Hidden\n"
    )
    subprocess.run(
        ["lake", "build", "Hidden"], cwd=directory, check=True, capture_output=True, timeout=120
    )
    return LeanREPLConfig(
        project=LocalProject(directory=directory, auto_build=False),
        cache_dir=config.cache_dir,
        enable_parallel_elaboration=False,
    )


def test_imported_sorry_is_detected_transitively(local_config):
    result = LeanProofTool(local_config).verify(
        "import Hidden\ntheorem proof : False := Hidden.unfinished", "proof"
    )
    assert result.status == "rejected", result.to_dict()
    assert "sorryAx" in result.axioms
    # Compiling this source gives no sorry warning: only the axiom query catches it.
    assert not any(d["severity"] == "warning" for d in result.diagnostics)


def test_host_project_imports_are_available(local_config):
    result = LeanProofTool(local_config).verify(
        "import Hidden\ntheorem proof : True := Hidden.complete", "proof"
    )
    assert result.verified, result.to_dict()
