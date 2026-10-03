"""Tools callable by Omnigent's agents."""

from .lean import LEAN_PROOF_TOOL_SPEC, LeanProofTool, VerificationResult

__all__ = ["LEAN_PROOF_TOOL_SPEC", "LeanProofTool", "VerificationResult"]
