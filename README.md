# Omnigent

Agent orchestration and tools for research, computation, and formal verification.

Development is tracked in the [issue tracker](https://github.com/jasperjonkhans/Hack/issues).

## Development demo

The uv project and virtual environment live here at the repository root.
Agent YAML definitions live in `demo/team/`; details are in `demo/README.md`.

Install: `uv sync`

Configure provider: `uv run omnigent setup`

Run the YAML-defined team: `cd demo/workspace && ../../.venv/bin/omnigent run ../team`

Run tests from the repository root: `uv run python -m unittest discover -s demo -v`

After moving a virtual environment, restart stale Omnigent services from this
root: `uv run omnigent stop`, then `uv run omnigent start --no-open`.
