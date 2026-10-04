"""deploy.sh names the agent settings missing from the VM's .env, in step with .env.example."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "oracle" / "deploy.sh"
# Set per agent in its YAML, or defaulted: not for .env.
INTERNAL = {"ACADEMIC_DB_AGENT", "SEARCH_STATE_DIR"}


def check_env_source():
    return re.search(r"^check_env\(\) \{\n.*?^\}\n", DEPLOY.read_text(), re.M | re.S).group(0)


def checked_keys():
    loop = re.search(r"for key in (.*?); do", check_env_source(), re.S).group(1)
    return set(loop.replace("\\", " ").split())


def run_check(tmp_path, content):
    env = tmp_path / f"{len(list(tmp_path.iterdir()))}.env"
    if content is not None:
        env.write_text(content)
    script = f"set -euo pipefail\n{check_env_source()}check_env {env}\n"
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    return set(re.findall(r"::warning::(\w+) is not set", result.stdout))


def test_checks_every_setting_in_env_example():
    example = set(re.findall(r"^#? ?([A-Z][A-Z0-9_]+)=", (ROOT / ".env.example").read_text(), re.M))
    assert checked_keys() == example


def test_env_example_documents_every_setting_the_tools_read():
    read = set()
    for path in [*ROOT.glob("tools/*/src/**/*.py"), *ROOT.glob("lab/**/*.py")]:
        read |= set(re.findall(r'setting\("([A-Z][A-Z0-9_]+)"', path.read_text()))
    assert read - INTERNAL <= checked_keys()


def test_flags_missing_values_by_name(tmp_path):
    complete = "".join(f"{key}=value\n" for key in sorted(checked_keys()))
    assert run_check(tmp_path, complete) == set()
    assert run_check(tmp_path, None) == checked_keys()
    assert run_check(tmp_path, (ROOT / ".env.example").read_text()) == checked_keys()

    partial = (
        "ACADEMIC_DB_URL = 'postgresql://u:p@127.0.0.1:5432/academic'\n"
        "ACADEMIC_DB_READER_URL=postgresql://r:p@127.0.0.1:5432/academic\n"
        "ACADEMIC_DB_READER_URL=\n"  # the last assignment wins
        "CONTACT_EMAIL=me@example.org\n"
        "S2_API_KEY=   # comment only\n"
        "# OPENALEX_MAILTO=me@example.org\n"
        "OPENALEX_API_KEY=key\n"
    )
    missing = {"ACADEMIC_DB_READER_URL", "S2_API_KEY", "OPENALEX_MAILTO"}
    assert run_check(tmp_path, partial) == missing
