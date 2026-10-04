# Omnigent

Agent orchestration and tools for research, computation, and formal verification.

Development is tracked in the [issue tracker](https://github.com/jasperjonkhans/Hack/issues).

## Research team

- **Shared server:** open the web UI ([`deploy/oracle`](deploy/oracle/README.md)), pick **Mimir** and ask a research question. Its Lead ([`lab/config.yaml`](lab/config.yaml)) runs Scouts and Verifiers and answers from the academic database.
- **Locally:** start Pi from the repository root and run `/research <topic>`; it needs a local Omnigent server ([`team/README.md`](team/README.md)).

## Tools
 
- [`tools/academic_db`](tools/academic_db/README.md): MCP server giving agents read/write access to the shared academic works database (papers from OpenAlex, Semantic Scholar and arXiv, plus claims and confidence-scored verdicts). Copy `.env.example` to `.env` and fill in the connection strings.

## Development demo

The uv project and virtual environment live here at the repository root.
Agent YAML definitions live in `demo/team/`; details are in `demo/README.md`.

Install: `uv sync`

Configure provider: `uv run omnigent setup`

Run the YAML-defined team: `cd demo/workspace && ../../.venv/bin/omnigent run ../team`

Run demo tests from the repository root: `uv run python -m unittest discover -s demo -v`

After moving a virtual environment, restart stale Omnigent services from this
root: `uv run omnigent stop`, then `uv run omnigent start --no-open`.

## Lean tool and skill

- [Lean proof verification](docs/lean-proof-tool.md): LeanInteract-backed tool with compiler diagnostics, deadlines, and strict transitive axiom checks.
- [Lean skill and Omnigent integration](docs/lean-skill.md): proof development, statement review, and fail-closed verification, carried by the experiment worker in `team/agents/worker/`.

Install development dependencies: `uv sync --locked --extra dev` (Python 3.14+, matching the demo).

Fast Lean/skill tests: `uv run --locked pytest -m 'not integration'`.

The verifier lives in `omnigent_lean`, not `omnigent`: it coexists with the actual Omnigent runtime. The `omnigent-lean` CLI and `lean_verify_proof` tool name are unchanged. Lean execution requires a trusted, pre-provisioned environment and external worker isolation; neither the skill nor the tool is a sandbox.
