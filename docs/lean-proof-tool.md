# Lean proof tool

Implements [issue #2](https://github.com/jasperjonkhans/Hack/issues/2): Omnigent's agents can formally verify a proof using Lean 4.

## Existing implementations used

This tool uses [LeanInteract 0.11.5](https://github.com/augustepoiroux/LeanInteract/tree/976edd7d38a99e1ea4c2dfabeb8ad98baffca3c8), not a new Lean subprocess/REPL protocol. LeanInteract manages the REPL, structured compiler diagnostics, query timeouts, and process-tree cleanup.

The verification policy follows [lean-lsp-mcp's axiom verifier](https://github.com/oOo0oOo/lean-lsp-mcp/blob/f00f625810d183dbb597a3677d2d5a83b084f761/src/lean_lsp_mcp/verify.py): compiling a snippet is insufficient; inspect the named theorem's transitive axioms with `#print axioms`. Our default is strict rejection rather than classifying extra axioms for the agent to decide. Implementation code is original; the dependencies remain governed by their upstream licenses.

## Install and provision

Requirements: Python 3.10+, [uv](https://docs.astral.sh/uv/), Git, and [elan](https://github.com/leanprover/elan) with `lake` on `PATH`. Lean 4.24.0 is the tested toolchain. Other versions require a compatible LeanInteract REPL and have not been validated here.

Install Python dependencies: `uv sync --locked --extra dev`.

For standard-library proofs, install the tested toolchain: `elan toolchain install leanprover/lean4:v4.24.0`.

For Mathlib or other imports, supply an existing trusted Lake project. Build it first with `lake build` in that project's directory (and `lake exe cache get` if it uses Mathlib). Its `lean-toolchain` selects the Lean version; the verifier does not select another version or fetch arbitrary projects from agent input.

Provision `LeanREPLConfig` once at host startup. It may download/build a version-compatible REPL, requiring network access and disk space. This provisioning is outside the verification timeout. Use a writable persistent cache and a resource-limited worker for deployment. After provisioning, each verification creates and closes its own REPL; code and environment state are not reused between calls.

## Register with Omnigent

There is no existing orchestrator/tool registry in this repository yet. The module exposes a framework-neutral JSON schema and callable handler so any of Omnigent's agents can use the same verifier. No new agent framework or orchestration loop is introduced.

Import `LeanProofTool` and `LEAN_PROOF_TOOL_SPEC` from `omnigent.tools`.

Create a host-owned configuration with `LeanREPLConfig(lean_version="v4.24.0", cache_dir=cache_path, enable_parallel_elaboration=False)` for the standard library. For a pre-built project instead, use `LeanREPLConfig(project=LocalProject(directory=project_path, auto_build=False), cache_dir=cache_path, enable_parallel_elaboration=False)`; import these configuration classes from `lean_interact`.

Construct `tool = LeanProofTool(config, timeout_seconds=30)`.

Register `LEAN_PROOF_TOOL_SPEC` with the host tool registry and dispatch its `lean_verify_proof` calls to `tool.execute(arguments)`. The host controls project paths, toolchain, cache, and timeout; these are not part of the agent's input schema. The spec is a generic function definition; providers that use an outer function envelope can wrap it in `{"type": "function", "function": LEAN_PROOF_TOOL_SPEC}`.

Example arguments: `{"code": "theorem add_zero (n : Nat) : n + 0 = n := by rfl", "theorem_name": "add_zero"}`.

For a direct Python call, `tool.verify(code, theorem_name)` returns a `VerificationResult`; `.to_dict()` returns the JSON-serializable result.

## Contract

Inputs:

- `code`: complete, nonempty Lean source, including any imports, limited to 100,000 UTF-8 bytes.
- `theorem_name`: fully qualified ASCII declaration name, e.g. `Arithmetic.add_zero`. Namespace components may contain letters, underscores, digits after the first character, and apostrophes. Unicode/escaped Lean names and a leading `_root_.` are not supported.

Outputs:

- `verified`: the only success signal. `true` only when `status` is `verified`.
- `status`: `verified`, `rejected`, `timeout`, or `error`.
- `theorem_name` and `reason`: target and outcome explanation.
- `diagnostics`: compiler messages using LeanInteract's `severity`, `data`, `pos`, and optional `endPos`; source positions retain the upstream REPL convention (one-based lines, zero-based columns). The axiom query's diagnostics refer to its generated one-line command, not the submitted source.
- `axioms`: sorted transitive dependencies, when an axiom report was obtained.

A proof is accepted only if:

1. Lean reports no compilation errors, outstanding sorries, or sorry warnings anywhere in the submitted source. Harmless warnings are retained and allowed.
2. The requested declaration is a named `theorem` declared in this submission, not a definition, axiom, anonymous example, or merely imported theorem.
3. A second query in the same environment reports the requested theorem's axioms, with no errors or missing/ambiguous output.
4. Every reported axiom is one of `propext`, `Classical.choice`, or `Quot.sound`. An axiom-free theorem is accepted too. `sorryAx`, custom axioms, and the compiler-trusting axioms used by `native_decide` are rejected, including dependencies hidden in imports.

Compilation and the axiom query share one deadline. Backend failures and cleanup failures return `error`; an exhausted query budget returns `timeout`. Neither is proof rejection evidence, and neither is success. Invalid agent arguments are rejected before a REPL starts. The wrapper always attempts process cleanup.

## CLI

The CLI writes one result JSON object to stdout, and provisioning logs to stderr.

Verify a standard-library proof: `uv run omnigent-lean --lean-version v4.24.0 --code-file examples/add_zero.lean --theorem-name add_zero`.

Verify with a trusted project: `uv run omnigent-lean --project /path/to/lean-project --code-file /path/to/proof.lean --theorem-name MyProof.result`.

Optional host flags: `--timeout 30` and `--cache-dir /path/to/writable/repl-cache`. The default cache is `~/.cache/omnigent/lean`. Exit codes: 0 for verified, 1 for rejected, 2 for timeout/infrastructure failure (or invalid CLI syntax).

## Security and trust boundary

Lean source is executable code: elaborators, tactics, imports, and `#eval` can execute computation and potentially access the filesystem/network. Neither this tool nor LeanInteract is a sandbox. Use only a trusted project/toolchain/import closure and run generated source in an externally isolated, resource-limited worker without credentials. This is not a service suitable for arbitrary hostile submissions.

As an additional conservative guard, source containing `set_option debug.*`, `unsafe`, `@[implemented_by ...]`, or `@[extern ...]` is rejected. This text scan also matches comments/strings and may over-reject legitimate code. It is not an exhaustive security or soundness audit and does not inspect imported source. Custom command elaborators can invalidate assumptions about diagnostics; imports and extensions are part of the trust boundary. No automatic sandbox, memory cap, or provisioning deadline is claimed.

Verification establishes the submitted Lean theorem under the stated trust base. It does not establish that its formal statement faithfully represents an agent's natural-language claim, or that the theorem says anything useful. The agent/orchestrator must separately review the statement.

## Validation

Fast tests (no Lean or network): `uv run pytest -m 'not integration'`.

Real Lean tests: `OMNIGENT_LEAN_VERSION=v4.24.0 uv run pytest -m integration`. Optionally set `OMNIGENT_LEAN_CACHE` to reuse a pre-provisioned REPL cache. Without the version variable integration tests are explicitly skipped; with it missing tooling is a failure, not a skip.

Lint and format: `uv run ruff check .` and `uv run ruff format --check .`.

Integration tests cover valid/invalid proofs, placeholders, namespaces, standard/custom/native axioms, host project imports, imported `sorryAx` without compiler warnings, deadline exhaustion, and environment isolation. CI runs unit tests on Python 3.10 and 3.14 and real Lean tests on Python 3.12 with Lean 4.24.0. Mathlib-specific proofs are not included in the test suite.
