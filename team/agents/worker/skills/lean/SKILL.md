---
name: lean
description: Develop and review Lean 4 proofs using Omnigent's strict proof verifier. Use when formalizing mathematical claims, editing .lean files, searching Lean or Mathlib lemmas, fixing tactic or type errors, or checking whether a theorem really proves the requested claim.
---

# Lean proof development and verification

## Preconditions

- Use the registered `lean_verify_proof` tool; a skill does not register tools.
- The host selects a trusted Lean toolchain/project, cache, and query deadline.
  Ask the host if these are missing; never select arbitrary imports/projects,
  install dependencies, change the toolchain, or disable isolation to get success.
- Lean source can execute code. The verifier is not a sandbox; the worker must
  be externally isolated and resource-limited, without credentials.
- Read [the tool contract](references/tool-contract.md) before the first call.
  Read [proof strategies](references/proof-strategies.md) when a proof is stuck.

## Workflow

1. Restate the intended claim and agree on its Lean statement before proving it.
   Check quantifiers, types, coercions, domains, and hypotheses. Do not quietly
   weaken the goal, assume the conclusion, or introduce inconsistent hypotheses.
2. Inspect available source and project conventions using the harness's existing
   read/search tools, if exposed. Reuse library lemmas rather than reproving them;
   confirm names and types in the actual pinned environment. Mathlib tactics are
   available only if the host project already provides their imports.
3. Submit complete source with imports and an ASCII-named `theorem`. Each call
   starts fresh: include required helpers every time. Use a fully qualified name
   for namespace members, without a leading `_root_.`.
4. Build the proof incrementally, checking after each meaningful tactic/change.
   Fix syntax errors, then type/instance errors, then tactic failures, then
   remaining goals. An unsolved-goal message on `by` may precede a later tactic
   error; fix the concrete tactic failure first. Keep changes small and targeted.
5. Use diagnostics and goal state to choose the next step. Never use `sorry`,
   `admit`, custom axioms, `native_decide`, or trust overrides to force acceptance.
   Even unused incomplete helpers cause rejection. Do not retry identical input
   without new evidence. After three non-progressing attempts, report the blocker
   and ask for guidance rather than launching an unbounded search.
6. Review the statement against the original claim again; check vacuous cases and
   whether hypotheses are satisfiable. Verification of a weaker/different theorem
   is not verification of the requested claim. Flag uncertainty explicitly.
7. Verify the final exact source after any cleanup. Only `verified: true` permits
   reporting formal success; a green build, warning-free output, or empty axioms
   list alone does not. If the tool is unavailable, report the proof as unverified.

## Minimal tool call

Tool: `lean_verify_proof`

```json
{"code":"namespace Arithmetic\ntheorem add_zero (n : Nat) : n + 0 = n := by rfl\nend Arithmetic","theorem_name":"Arithmetic.add_zero"}
```

This standard-library example needs no Mathlib. Do not replace the named theorem
with an anonymous `example` or request a theorem supplied only by an import.

## Interpret outcomes

- `verified`: require `verified: true`, then separately assess statement fidelity.
- `rejected`: inspect `reason` and `diagnostics`; repair the proof/statement or
  explain a policy limitation. Rejection does not by itself refute the claim.
- `timeout`: unresolved; simplify the proof or ask the host about the budget.
- `error`: infrastructure/configuration/cleanup failure; report it to the host.
  Neither timeout nor error is proof success or evidence of mathematical falsity.

## Completion report

Include the intended claim, exact formal statement and complete final source,
fully qualified theorem name, observed status/verified flag, transitive axioms,
and remaining assumptions or statement-fidelity concerns. If work is incomplete,
state what remains and do not describe the claim as formally verified.

This is harness-neutral: use only tools actually exposed in the current session.
It requires no Claude plugin commands, Pi extensions, MCP server, external proof
service, API key, or automatic delegation. Host integration and upstream sources
are documented in the repository's `docs/lean-skill.md`.
