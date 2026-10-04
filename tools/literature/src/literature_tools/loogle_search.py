"""Loogle: search Lean 4 Mathlib declarations by name, constant or type pattern."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import urllib.parse

from omnigent_client.tools import tool

_spec = importlib.util.spec_from_file_location("_search_support", Path(__file__).with_name("search_support.py"))
_s = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_s)

_API_URL = "https://loogle.lean-lang.org/json"
_DOCS_URL = "https://leanprover-community.github.io/mathlib4_docs/"


@tool
@_s.guarded
def loogle_search(query: str, limit: int = 20) -> dict:
    """Find existing Lean 4 / Mathlib theorems and definitions, to check what is already formalised.

    Query forms, combinable with commas (all must hold):
    - a constant: Nat.Prime
    - a name fragment in quotes: "gcd"
    - a type pattern with _ holes or ?a variables: _ * (_ ^ _)
    - a conclusion pattern: |- _ < _ → _
    Example: 'Nat.Prime, "succ"' or 'Real.sqrt, |- _ ≤ _'.
    Results reflect the Mathlib revision Loogle has indexed, which may lag.
    When truncated is true, narrow the query; this service has no pagination.

    Args:
        query: Loogle query; constants, quoted name fragments and/or type patterns separated by commas.
        limit: Maximum declarations to return, 1-200.
    """
    query = _s.text(query)
    _s.integer(limit, "limit", 1, 200)
    data = json.loads(_s.fetch("loogle", _API_URL + "?" + urllib.parse.urlencode({"q": query})))
    if not isinstance(data, dict):
        raise ValueError("unexpected Loogle response")
    if data.get("error"):
        hint = data.get("suggestions")
        if not isinstance(data["error"], str) or (hint is not None and (not isinstance(hint, list) or any(not isinstance(h, str) for h in hint))):
            raise ValueError("invalid Loogle error response")
        message = data["error"] + (f" Suggestions: {', '.join(hint[:5])}" if hint else "")
        raise _s.SearchError("invalid_query", message)
    if (not isinstance(data.get("hits"), list) or type(data.get("count")) is not int
            or data["count"] < len(data["hits"])):
        raise ValueError("invalid Loogle result envelope")
    for hit in data["hits"]:
        if not isinstance(hit, dict) or any(not isinstance(hit.get(k), str) or not hit[k].strip() for k in ("module", "name", "type")):
            raise ValueError("invalid Loogle declaration")
    results = []
    for hit in data["hits"][:limit]:
        module = hit["module"]
        results.append({"source": "loogle", "name": hit["name"], "type": (hit.get("type") or "").strip(),
                        "module": module, "doc": hit.get("doc"),
                        "url": f"{_DOCS_URL}{module.replace('.', '/')}.html#{hit['name']}"})
    truncated = data["count"] > len(results)
    out = {"source": "loogle", "query": query, "summary": (data.get("header") or "").strip(),
            "total_matches": data["count"], "returned_count": len(results),
            "truncated": truncated, "pagination_supported": False,
            "results": results, "retrieved_at": _s.timestamp()}

    if truncated:
        out["warning"] = "Only a subset of matches is returned. Narrow the query using constants, name fragments or type patterns; pagination is unavailable."
    return out
