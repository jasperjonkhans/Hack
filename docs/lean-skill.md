# Lean skill for the Omnigent setup

The canonical skill is [`team/agents/worker/skills/lean/SKILL.md`](../team/agents/worker/skills/lean/SKILL.md), carried by the research team's experiment worker.
It provides an incremental proof workflow, library-search guidance, statement-fidelity
review, strict-verifier instructions, bounded retries, and explicit completion evidence.
References travel with the skill; it has no dependency on slash commands, plugin
scripts, a specific harness, lean-lsp-mcp, or an external proof service.

## Existing skills researched

The skills.sh directory and `npx skills find lean` / `npx skills find lean4` were
checked, then the actual source skills and repository metadata were inspected.
Broad search results also contain unrelated Lean/startup/Linear skills; those were
not treated as theorem-proving sources. Popularity numbers are not quality proofs.

| Source | Evidence at research time | Ideas retained | Compatibility changes |
| --- | --- | --- | --- |
| [leanprover/skills, lean-proof](https://github.com/leanprover/skills/blob/7d3da0282e7b724b07620e45cf212f2e05e19334/skills/lean-proof/SKILL.md) | Official Lean organization; Apache-2.0; 72 repository stars. `lean-bisect` appeared with 19 installs, not a count for `lean-proof`. | Small checked steps, error priority, dependent-type awareness, cleanup. | Use the registered Omnigent verifier and its diagnostics, not an assumed editor/LSP interface. |
| [cameronfreer/lean4-skills, lean4](https://github.com/cameronfreer/lean4-skills/blob/b6243b85b9b0a0ddff5bb6773889044daf687f8e/plugins/lean4/skills/lean4/SKILL.md) | MIT; 455 stars; 433 installs reported for `lean4` by the skills CLI. | Library-first search, incremental checking, axiom-aware completion, proof repair. | Omit Claude-specific commands, plugin orchestration, helper scripts, and automatic delegation. Final acceptance comes from this PR's strict tool. |
| [matt-w-horn/lean-skills, lean-verification](https://github.com/matt-w-horn/lean-skills/blob/5e5d61caacae7f159c3e583e7efd7cbcdf0ca0ed/skills/lean-verification/SKILL.md) | Apache-2.0; 1 star; no reliable install count obtained. Treat as a low-confidence supplemental source. | Review the formal statement, non-vacuity, and separation of a build from the intended claim. | Do not equate compilation with strict acceptance; review the claim separately from the tool verdict. |

The new skill and adapter are original writing/code informed by these workflows;
no upstream skill text, scripts, or plugins are vendored. Links are pinned so the
comparison remains inspectable. External automation such as Aristotle is not used:
it would add authentication and a different trust/deployment boundary.

## Native bundle and tool registration

The experiment worker (`team/agents/worker/config.yaml`) carries the skill in
`skills/lean/` and the tool in `tools/python/lean_verify_proof.py`. Omnigent's
bundle parser discovers both, and the research controller (`team/runtime.py`)
ships a role's `skills/` and `tools/` folders with every task.

The top-level `skills:` key filters skills, and harnesses differ. Under Claude
SDK, bundled skills always load and `skills: none` only blocks host skills.
Under Pi, which the research team uses, `skills: none` loads no skills at all,
bundled ones included. So the worker names its bundled skill with
`skills: [lean]`: Pi then loads only `skills/lean/` and no skills installed on
the runner's machine. It is not a general security filter.

The worker's bundle registers `lean_verify_proof` through its native
`tools/python/lean_verify_proof.py` file and the public `omnigent_client.tools.tool`
decorator. The runtime discovers it and derives the existing strict two-field
schema (with additional descriptive Pydantic titles). The wrapper calls
`omnigent_lean.adapter.lean_verify_proof`, which converts kwargs into the mapping
accepted by `LeanProofTool.execute`.

Do not put a top-level `type: function` declaration into a `spec_version: 1`
directory bundle: the current bundle parser ignores that single-file YAML tool
syntax. For an existing single-file Omnigent agent YAML (without `spec_version`),
the documented `type: function`, `callable: omnigent_lean.adapter.lean_verify_proof`,
and `parameters` copied from `LEAN_PROOF_TOOL_SPEC["parameters"]` form a separate
supported integration (use the single-file format's flat `executor.harness`). That callable runs in-process; isolate the entire worker externally.

The extension installs under `omnigent_lean`, keeping the real `omnigent` package
and CLI intact. The initial PR's `omnigent.tools` imports are replaced with
`omnigent_lean.tools`; `omnigent-lean` and the JSON contracts remain unchanged.
The existing role compiler, team YAMLs, Databricks extra, and root environment
are preserved. No new orchestration loop is introduced.

## Host preparation and launch

Run from the repository root, in an externally isolated, resource-limited worker
without credentials. Do not treat the example's `os_env.sandbox: auto` as a
security guarantee for the verifier. Native local tools execute in a subprocess;
Omnigent chooses container/srt/plain execution according to its deployment and
tool sandbox configuration, with a possible unsandboxed fallback. The OS sandbox
for agent actions alone is not proof that the tool subprocess is isolated. Lean
source and trusted imports can execute code. Provider credentials require a
separately designed boundary before using untrusted proofs;
this example is not a hardened hostile-submission service.

1. Install with `uv sync --locked --extra dev` and configure the desired provider
   with `uv run omnigent setup` using your existing deployment/auth arrangement.
2. Provision Lean as described in [the proof tool guide](lean-proof-tool.md).
3. Set exactly one worker-owned environment variable:
   `OMNIGENT_LEAN_VERSION=v4.24.0` for the tested standard library, or
   `OMNIGENT_LEAN_PROJECT=/absolute/path/to/trusted-prebuilt-project` for local imports.
   Mathlib must already be installed and built in that project; it is not fetched
   or tested by this skill.
4. Optionally set `OMNIGENT_LEAN_CACHE=/absolute/path/to/repl-cache` and
   `OMNIGENT_LEAN_TIMEOUT=30`. The default cache is `~/.cache/omnigent/lean`.
   Configuration is captured on first successful provisioning; restart the worker
   to change it. Provisioning/download/build time is outside the query deadline.
5. Validate offline: `uv run --locked pytest tests/test_lean_skill.py`.
6. The worker runs under the research controller (`team/loop.py`, see
   `team/README.md`). A live run may incur provider costs; no live provider calls
   are required for compatibility tests.

The adapter provisions lazily once per Python process. A native local tool call
starts a new subprocess, so it constructs a new config on each call and reuses
the host's persistent on-disk REPL/build cache, not an in-memory verifier across
calls. Prewarm that cache before accepting work. In an in-process function
integration, hosts can call `omnigent_lean.adapter.provision()` at startup and
reuse the provisioned config in that same process.
Parsing/importing the bundle must not provision Lean or contact a provider.
Missing/conflicting environment choices, invalid deadlines, and provisioning
failures return `status: error, verified: false`, never a successful proof.
Each verification still owns a fresh REPL and performs the original strict checks.
Environment variables belong to the host: model arguments cannot override them.

## Add to an existing role/team

Copy the entire `team/agents/worker/skills/lean/` directory, including references,
into `skills/lean/` beside the receiving role's `config.yaml`, e.g.
`lab/agents/verifier/skills/lean/`. Copy the native wrapper into that role's
`tools/python/lean_verify_proof.py` and tell it to use the skill for Lean work.
Under Pi, also add `lean` to the role's `skills:` list (see above). Ensure
the installed `omnigent_lean` package and host environment are available in the
actual runner, not only the launching shell; restart an already-running host when
changing its environment. Do not copy `os_env`/write grants or the whole agent
config over an existing role. Skills do not register tools or grant permissions.

The same pattern applies to compiler-generated bundles after export: place the
skill and native tool file in each receiving role's own directory. Skill changes
are not automatically copied by `Team.export`, and parent tools/skills should not
be assumed to be inherited by every sub-agent. The repository skill is a bundle
asset; installing the Python wheel alone does not install it into agent bundles.

## Validation scope

Tests cover discovery/frontmatter/references through the real Omnigent parser,
validation of the native bundle on Claude SDK, Codex, Pi, and OpenAI Agents config
variants, schema parity (ignoring generated titles), native runner serialization
with a mocked verifier, actual tool-subprocess dispatch without host config, and
single-file function-tool dispatch. Host adapter tests exercise configuration,
fail-closed outcomes, caching, and no provisioning during parsing. Real Lean tests exercise the verifier and
adapter with the standard-library skill example. These are offline/config and
Lean checks, not end-to-end provider/auth/sandbox/multi-agent harness runs.
