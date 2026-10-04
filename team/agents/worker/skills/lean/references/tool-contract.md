# Strict verifier contract

Call `lean_verify_proof` with exactly `code` and `theorem_name`. Source must be
nonempty and at most 100,000 UTF-8 bytes. The name must be fully qualified ASCII:
letters/underscores first, then letters/digits/underscores/apostrophes in each
component; dots separate namespace components. Do not prefix `_root_.` or use
escaped/Unicode names. Project, version, cache, and timeout are host settings,
not model-controlled arguments.

Every call has a fresh REPL. Include all source and helpers; previous calls do
not contribute an environment. Compilation and the axiom query share one
host-defined deadline, excluding provisioning. The target must be a `theorem`
declared in this submission, not a `def`, `axiom`, imported theorem, or `example`.

The result contains `status`, `verified`, `theorem_name`, `reason`, `diagnostics`,
and `axioms`. `verified: true` is the only acceptance signal. Diagnostics expose
`severity`, `data`, `pos`, and optional `endPos` (one-based lines, zero-based
columns). Axiom-query positions refer to a generated one-line command, not the
submitted proof. Harmless warnings are allowed and remain visible.

No errors or incomplete proofs may occur anywhere in the source. The target's
transitive axioms must be a subset of `propext`, `Classical.choice`, `Quot.sound`;
no axioms is valid too. Imported `sorryAx`, custom axioms, and `native_decide`
trust assumptions are rejected even if compilation otherwise succeeds. An empty
`axioms` list on a failed result is not an axiom-free proof certificate.

Source containing `set_option debug.*`, `unsafe`, `@[implemented_by ...]`, or
`@[extern ...]` is conservatively rejected even in comments/strings. This scan is
not a security audit. Imports/extensions must be trusted; source is executable.
Never hide a rejected construct in an import or relax the trust policy.

A verified theorem is only a statement about its formal proposition under this
trust base. Natural-language equivalence and non-vacuity need separate review.
