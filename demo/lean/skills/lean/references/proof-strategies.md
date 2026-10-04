# Focused proof strategies

## Search before invention

Inspect the pinned library using available read/search tools; search by goal
shape and type as well as plausible names. Confirm candidates with `#check` in
the same submitted source, retaining the target theorem. Do not invent lemma
names or import Mathlib when only the standard library is provisioned. Optional
LSP/search tools may help if already exposed, but this skill does not require
them or claim to provide them.

## Choose from the actual goal

- Definitional equality: try `rfl`.
- A matching hypothesis/lemma: `exact`; a reducible goal: `apply`.
- Introductions and structures: `intro`, `constructor`, `rcases`, `cases`.
- Equalities and simplification: `rw [lemma]`, `simp only [lemma]`.
- Recursive statements: `induction`, with one branch checked at a time.
- Natural/integer linear arithmetic: `omega` if available in the imports.
- Polynomial/ordered arithmetic: `ring`, `linarith`, `nlinarith`, `norm_num`
  only with the appropriate, already-installed Mathlib imports.
- A decidable proposition: `decide` is kernel-checked; do not use
  `native_decide`, which this verifier rejects.

Use one change at a time. Do not cycle blindly through every tactic. If typeclass
synthesis fails, inspect the types and required instances before adding tactics.
For dependent rewrites, inspect the context: `rw` can invalidate dependencies;
consider `subst`, `cases`, or generalizing dependent hypotheses before induction.
Do not increase heartbeats or ask for a longer deadline without evidence.

## Finish without changing the claim

Remove exploratory checks and unnecessary helpers, but preserve the exact formal
statement and hypotheses. Replace tactic suggestions with explicit stable proofs
where useful. Re-run the strict verifier on the final source. Proof golfing is
optional and must never trade statement fidelity or trust policy for brevity.
