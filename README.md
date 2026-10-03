# Omnigent

Agent orchestration and tools for research, computation, and formal verification.

Development is tracked in the [issue tracker](https://github.com/jasperjonkhans/Hack/issues).

## Tools

- [Lean proof verification](docs/lean-proof-tool.md): a Python/JSON tool backed by LeanInteract, with compiler diagnostics, timeouts, and strict axiom checks. Rejects incomplete proofs and non-standard trust assumptions.

Install: `uv sync --locked --extra dev` (Python 3.10+).

Fast tests: `uv run pytest -m 'not integration'`.

See the tool documentation for Lean provisioning, Omnigent registration, CLI usage, and the security/trust boundary.
