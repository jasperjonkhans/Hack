"""Native directory-bundle tool; dependencies come from the host's locked runtime."""

from omnigent_client.tools import tool

from omnigent_lean import adapter


@tool
def lean_verify_proof(code: str, theorem_name: str) -> dict:
    """Verify a named Lean theorem; only verified=true is success.

    Reject incomplete proofs and non-standard axioms. Run only with a trusted
    toolchain/import closure inside an externally isolated, resource-limited worker.

    Args:
        code: Complete Lean source, including imports.
        theorem_name: Fully qualified ASCII theorem name, e.g. MyProof.add_zero.
    """
    return adapter.lean_verify_proof(code, theorem_name)
