"""Smoke-test the literature search tools in tools/literature against the live APIs.

Run with Omnigent's Python (the tools import omnigent_client):

    ~/.local/share/uv/tools/omnigent/bin/python scripts/smoke_search_tools.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from omnigent_client.tools import get_tool_metadata

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools/literature/src/literature_tools"
CASES = {
    "openalex_search": [
        {"query": "pyrethroid resistance Anopheles", "limit": 3},
        {"query": "transformer language models", "limit": 3, "sort": "citations", "from_year": 2020},
    ],
    "arxiv_search": [
        {"query": "graph neural networks molecular property prediction", "limit": 3},
        {"query": 'cat:math.NT AND abs:"prime gaps"', "limit": 3, "sort": "recent", "query_mode": "advanced"},
    ],
    "europepmc_search": [
        {"query": "kdr mutation pyrethroid resistance", "limit": 3},
        {"query": "malaria vaccine", "limit": 3, "sort": "citations", "from_year": 2021, "include_preprints": False},
    ],
}


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name)


def main() -> int:
    failures = 0
    for name, cases in CASES.items():
        fn = load(name)
        meta = get_tool_metadata(fn)
        if meta is None:
            print(f"\n=== {name}: NO @tool METADATA")
            failures += 1
            continue
        props = meta.json_schema["properties"]
        undocumented = [k for k, v in props.items() if not v.get("description")]
        print(f"\n=== {name}  params: {list(props)}  undocumented: {undocumented or 'none'}")
        for args in cases:
            out = fn(**args, detail="full")
            ok = "error" not in out and len(out["results"]) > 0
            failures += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {json.dumps(args)}  → {out.get('total_matches')} matches")
            if not ok:
                print("   ", out)
                continue
            results = out["results"]
            checks = {
                "unique provider IDs": len({r["id"] for r in results}) == len(results),
                "full abstracts": all(not r["abstract_truncated"] for r in results),
                "year filter": all(r["year"] is not None and r["year"] >= args["from_year"] for r in results) if args.get("from_year") else True,
                "preprints excluded": all(r["is_preprint"] is False for r in results) if args.get("include_preprints") is False else True,
            }
            if out["next_cursor"]:
                next_page = fn(**args, detail="full", cursor=out["next_cursor"])
                checks["next page"] = "error" not in next_page and bool(next_page["results"])
                checks["pages do not overlap"] = "error" not in next_page and not ({r["id"] for r in results} & {r["id"] for r in next_page["results"]})
            spec = importlib.util.spec_from_file_location(name, TOOLS_DIR / f"{name}.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            detail = getattr(module, name.replace("_search", "_get_paper"))(results[0]["id"])
            checks["paper lookup"] = "error" not in detail and detail["paper"]["id"] == results[0]["id"]
            for check, passed in checks.items():
                failures += not passed
                print(f"    {'PASS' if passed else 'FAIL'} {check}")
                if not passed and check == "paper lookup":
                    print(detail)
    benchmarks = [
        ("openalex_search", '"Deep learning"', "doi", "10.1038/nature14539"),
        ("arxiv_search", '"Attention Is All You Need"', "arxiv", "1706.03762"),
        ("europepmc_search", '"The PRISMA 2020 statement"', "doi", "10.1136/bmj.n71"),
    ]
    for name, query, namespace, expected in benchmarks:
        out = load(name)(query=query, limit=10, search_scope="title")
        found = "error" not in out and any(r["identifiers"].get(namespace) == expected for r in out["results"])
        failures += not found
        print(f"{'PASS' if found else 'FAIL'} known-paper retrieval: {name} {expected}")
        if "error" in out:
            print(out)
    print(f"\n{failures} failing case(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
