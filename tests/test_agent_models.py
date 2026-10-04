"""Every Pi agent pins the same OpenRouter model; no agent is left on the old one."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
AGENTS = [
    "lab",
    "lab/agents/scout",
    "lab/agents/verifier",
    "lab/agents/hypothesizer",
    "lab/agents/worker",
    "team",
    "team/agents/worker",
]


def test_agents_pin_one_openrouter_model():
    executors = {
        a: yaml.safe_load((ROOT / a / "config.yaml").read_text())["executor"] for a in AGENTS
    }
    models = {executor["model"] for executor in executors.values()}
    # Sub-agents don't inherit the Lead's model, so they must pin the same one.
    assert models == {"deepseek/deepseek-v4.1-flash"}, models
    for agent, executor in executors.items():
        assert executor["auth"] == {"type": "provider", "name": "openrouter"}, agent
    # Through Pi, Omnigent sends model ids containing "claude" to an
    # Anthropic-format endpoint that the OpenRouter credential doesn't cover.
    assert not any("claude" in model for model in models)
