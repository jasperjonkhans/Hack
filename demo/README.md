# Omnigent framework development demo

A small starter for building your own role-based framework on **Databricks Omnigent** (`omnigent-ai/omnigent`), not the unrelated OmniAgent packages.

The custom core is `framework.py`: `Runtime`, `Role`, and `Team`. They compile role definitions into native Omnigent directory bundles. Omnigent owns the agent loop, providers, sessions, delegation, tool execution, and policy enforcement. No web app, database, fake responses, or additional orchestration service.

## YAML-first: no Python wrapper needed

The directly editable native agent definitions are:

- `team/config.yaml` — coordinator.
- `team/agents/planner/config.yaml` — planner.
- `team/agents/researcher/config.yaml` — researcher.
- `team/agents/builder/config.yaml` — builder.
- `team/agents/reviewer/config.yaml` — reviewer.

The uv project (`pyproject.toml`, `uv.lock`, and `.venv/`) lives at the repository root, one level above `demo/`. From that root, install/configure Omnigent with `uv sync` and `uv run omnigent setup`. Then run `cd demo/workspace && ../../.venv/bin/omnigent run ../team`.

Launch from the workspace because these portable YAMLs use `cwd: .` and relative sandbox paths. All prompts are inline; change each agent directly in its YAML. To use a different reviewer harness, change `executor.config.harness` in the reviewer's config. For Databricks, add `executor.model` and `executor.auth: {type: databricks, profile: YOUR_PROFILE}` to each agent, and install the Databricks extra.

The Python compiler below is optional. Its generated bundles and these hand-editable YAMLs are independent; editing one does not update the other.

## Compare the options

From this directory, run `python demo.py` or `python demo.py options`.

| Option | Roles | Harnesses | When to use |
| --- | --- | --- | --- |
| solo | Builder only | Claude SDK by default | Smallest starting point |
| team | Coordinator, planner, researcher, builder, reviewer | Claude SDK by default | Role-based workflow with one provider |
| mixed | Same five roles | Claude SDK + Codex reviewer | Compare a change across harnesses/providers |
| databricks | Same five roles | Claude SDK with Databricks auth | Use your existing workspace/model endpoint |

`python demo.py show team` displays every generated config and role instruction without installing anything or making an API call.

Other supported harness choices: `--harness codex`, `--harness pi`, or `--harness openai-agents`. Different harnesses can have different model identifiers, authentication, installation requirements, and tool behavior; these are not interchangeable model aliases. Omnigent supports more harnesses than this intentionally small demo exposes.

## Quick start

Run these commands from the repository root (the directory containing `pyproject.toml`):

1. Install the locked runtime: `uv sync` (Python version is specified by the root project).
2. Configure your provider: `uv run omnigent setup`.
3. Create a native bundle: `python demo/demo.py build team --workspace ./demo/workspace`.
4. Validate it offline: `uv run python demo/demo.py validate ./demo/generated/team`.
5. Inspect the launch without executing: `python demo/demo.py run ./demo/generated/team --prompt-file demo/TASK.md --dry-run`.
6. Launch the real team: `uv run python demo/demo.py run ./demo/generated/team --prompt-file demo/TASK.md`.

Omit `--prompt-file` to start an interactive session. `TASK.md` supplies a small Python task-tracker exercise; the actual agent implementation is created in `workspace/` only when you launch. A live run can edit files and incur provider charges. No live model calls were made while preparing this starter.

Bundles are generated into `generated/<option>/`. Build refuses to overwrite an existing directory; use a new `--output ./generated/team-v2` when experimenting. Workspace directories must already exist. Paths embedded in generated configs are absolute: rebuild after moving the project.

## Databricks-backed team

From the repository root, install the Databricks integration with `uv sync --extra databricks`. Authenticate the Databricks profile using your organization's supported CLI/auth workflow, then configure Omnigent with `uv run omnigent setup`.

Build with `python demo/demo.py build databricks --profile YOUR_PROFILE --model YOUR_MODEL_ENDPOINT --workspace ./demo/workspace`.

Launch with `uv run python demo/demo.py run ./demo/generated/databricks --prompt-file demo/TASK.md`.

Profile and endpoint are required, not guessed. They propagate to every role through native `executor.auth` and `executor.model` fields. Do not put tokens in role definitions or generated configs. This preset routes models through Databricks; it does not deploy a Databricks App or require managed Omnigent hosting.

## Mixed harnesses

Build with `python demo.py build mixed --workspace ./workspace`.

The coordinator, planner, researcher, and builder use Claude SDK; the reviewer uses Codex. Configure both providers/harnesses through Omnigent before launching. The reviewer's model is resolved separately rather than inheriting an incompatible Claude model identifier.

To use Pi for review instead: `python demo.py build mixed --reviewer-harness pi --output ./generated/pi-review`.

To pin models independently, use `--model` and `--reviewer-model`. `--reviewer-harness` also works with the team preset.

## How the roles collaborate

The coordinator delegates planning, then research, then implementation, then independent review. It uses Omnigent's `sys_session_send` and inbox/completion mechanism. Only the builder is assigned write access. A rejected review can trigger at most two repair/review cycles before asking the user.

This sequence is expressed in coordinator instructions, not a deterministic Python scheduler. Role instructions guide model behavior; they are not a guarantee that every step will succeed. The native runtime discovers sub-agents in `agents/<role>/config.yaml`.

## Make it yours

- Edit `roles.py` to change responsibilities, instructions, or preset composition.
- Construct your own `Role(name, description, instructions, writable=False, runtime=Runtime(...))`.
- Compose `Team(lead, (worker_a, worker_b))`; use `.on(Runtime(...))` to change all role runtimes.
- Call `.export(destination, workspace)` to generate a bundle consumable by the normal `omnigent run` command.
- `custom_team.py` is a small runnable example: `python custom_team.py`.
- Add native Omnigent MCP/function tools or skills to your bundle as needed; see the upstream agent spec rather than inventing another tool protocol.

Configs use JSON syntax inside `config.yaml` because JSON is valid YAML. This keeps show/build and the core dependency-free. The launcher expects compiler-generated configs; use `omnigent run <bundle>` directly if you rewrite configs into conventional YAML.

## Safety and limits

Default sandbox type is `auto`, with workspace read access and builder-only workspace write grants. Network access is enabled to support real harness/provider connections; there is no egress allowlist in this demo. Omnigent selects the platform sandbox, so host prerequisites must be met. This is a development baseline, not a hardened production configuration or a promise of a security boundary on every harness/platform.

Each role declares Omnigent's built-in blast-radius policy with push gating enabled. The prompt also forbids unrequested pushes, deployment, secret access, and dependency installation. Prompts are not access controls, and the blast-radius policy is not exhaustive protection.

`--sandbox none` explicitly disables OS isolation and prints a warning. With that option, read-only roles are read-only by instruction only. Never use it for untrusted tasks or repos. Do not treat this starter as production governance.

The core is slim; Omnigent itself installs its normal upstream dependencies. No attempt is made to replace or strip the real runtime.

## Checks and reference

From the repository root, run `uv run python -m unittest discover -s demo -v` for eleven checks, including native parsing and validation of all four presets and parent-directory environment discovery. Without Omnigent installed, `python -m unittest discover -s demo -v` runs the dependency-free checks and skips the integration check.

If the uv environment moves while Omnigent is running, its background host retains the old Python path. From the repository root, run `uv run omnigent stop`, then `uv run omnigent start --no-open`. Restart before retrying your agent; do not recreate a stale `demo/.venv` to hide the problem.

Originally validated against PyPI `omnigent==0.16.0`; the current environment's dependencies are recorded in the repository-root `uv.lock`. Live authentication, provider calls, sandbox startup, and multi-agent task completion require your environment and were not exercised.

Upstream: https://github.com/omnigent-ai/omnigent

Agent spec: https://github.com/omnigent-ai/omnigent/blob/main/docs/AGENT_YAML_SPEC.md

Policies: https://github.com/omnigent-ai/omnigent/blob/main/docs/POLICIES.md

Databricks: https://docs.databricks.com/aws/en/omnigent/
