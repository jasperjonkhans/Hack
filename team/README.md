# Research MVP

Pi-backed Omnigent agents with a small Python controller for #12.

- `config.yaml`: slim lead; plans and reviews supplied reports, with no shell/file-execution tools.
- `agents/worker/config.yaml`: generic experiment worker, scoped to its task artifact directory.
- `loop.py`: phases, dispatch, three-round limits, approval and checkpoints.
- `runtime.py`: native `omnigent-client` sessions/SSE adapter for a local server and runner.
- `research.ts` and `../.pi/extensions/research.ts`: Pi command/tools that start the same controller and display progress/results in the current chat.

**Not implemented here:** scout, verifier, literature search or the evidence database. The lead can scope the direction and prepare todos; the loop then pauses before dispatch if `agents/scout/config.yaml` or `agents/verifier/config.yaml` is absent. No research round is consumed. Add those external roles and resume without replanning. Persistent evidence/flags remain #6's responsibility. Run checkpoints and task reports are not a replacement database.

## Loop

Research: lead scope/todos → at most five parallel scouts → verifiers read findings from shared memory → lead reviews all reports. Stop early or after three total rounds; report gaps and propose question, hypothesis and plan.

Pause for explicit approval. Experiments: lead todos → workers → lead analysis. Stop early or after three total rounds; retain reports and artifact references. A proposed scope/plan change pauses for renewed approval without resetting counters.

Agents never own native spawning. An experiment worker can request `{"subtasks": ["objective"]}` once; the controller runs generic helper workers at depth one and then asks the parent to finish. Child workers cannot request further children. Otherwise tasks return `status` (`completed`, `blocked`, `failed`), `summary`, `output_refs`, and `limitations`. Requests include a run/memory namespace and JSON contract; task requests also include an objective and artifact directory. The lead also receives prior reports.

## Use in Pi

From the repository root, launch `omni pi` (or `.venv/bin/omnigent pi`) and allow this project's Pi extensions to load. If Pi is already open, grant project trust if needed and run `/reload`.

- Ask Pi to research a topic normally: it has a `research` tool and a guideline to invoke the YAML lead automatically.
- Or run `/research <direction>` explicitly; `/research` alone asks for a direction.
- `/research status` shows the current run, scope, todos, gaps, and proposal without advancing it.
- `/research approve` displays the scope, question, hypothesis and plan for explicit confirmation, approves the exact current plan ID, then starts experiments.
- `/research resume` continues a paused run; it does not grant approval.
- `/research cancel` interrupts the current controller and requests cancellation of active agent calls.

The controller runs in the background while Pi remains interactive. Its footer shows round progress and its final checkpoint is added to the chat without triggering another model turn. Run references follow the current Pi session branch. Leaving, replacing or reloading the session stops its controller; interrupted dispatched rounds are not replayed automatically. The model-facing tools can start research or read status, never approve experiments.

The extension uses Omnigent's native Pi bridge server URL when present, otherwise `http://localhost:6767`. Configure the Pi/provider backend and an online Pi-capable local runner. No remote/managed hosts are supported in this starter. Full research still requires the external roles above.

## Direct CLI (optional)

Use the repository's installed environment. Start the local server/runner with `.venv/bin/omnigent start`, and inspect its URL with `.venv/bin/omnigent server status`.

After the external research roles are available:

- Start research: `.venv/bin/python team/loop.py --run team/.runs/demo start "Research direction"`
- Inspect the proposal: `.venv/bin/python team/loop.py --run team/.runs/demo status`
- Approve the exact printed plan ID: `.venv/bin/python team/loop.py --run team/.runs/demo approve PLAN_ID`
- Run experiments: `.venv/bin/python team/loop.py --run team/.runs/demo resume`

Override `--server`, `--runner` or `--timeout` if needed. `--json` returns machine-readable state with the current plan ID. Approval includes the scope, question, hypothesis and experiment plan; it is an explicit local user action, not an agent decision. Approval itself makes no model calls.

State and reports live under the selected run directory. Each worker writes to `artifacts/TASK_ID/`. A per-run process lock prevents concurrent controllers; atomic checkpoints consume a round before dispatch. Interrupted/failed controller runs require inspection and a new run, not automatic replay of potentially executed experiments. Agent timeouts request server-side cancellation.

The controller guarantees scheduling/gates, not scientific correctness. OS isolation uses Omnigent's `auto` sandbox and its platform prerequisites; this is a development starter, not hardened execution of untrusted experiments. No live model calls were made to validate it.

## Offline checks

- `.venv/bin/python -m unittest discover -s team -v`
- `node --test team/test_research.ts` (Node 22.18+ with native TypeScript support)
- `.venv/bin/python -m unittest discover -s demo -v`

These use scripted agents/processes. They verify dispatch, limits, approval and Pi command behavior, not scientific correctness or live provider execution.
