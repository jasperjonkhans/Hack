"""zbMATH Open: the reviewed mathematics literature database, with MSC subject codes."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import urllib.parse

from omnigent_client.tools import tool

_spec = importlib.util.spec_from_file_location("_search_support", Path(__file__).with_name("search_support.py"))
_s = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_s)

_API_URL = "https://api.zbmath.org/v1/document"
_UNAVAILABLE = "contents unavailable"


def _paper(doc):
    links = doc.get("links") or []
    by_type = {}
    for link in links:
        by_type.setdefault(link.get("type"), []).append(link)
    arxiv = (by_type.get("arxiv") or [{}])[0].get("identifier")
    review = next((e for e in doc.get("editorial_contributions") or []
                   if e.get("text") and _UNAVAILABLE not in e["text"]), None)
    source = doc.get("source") or {}
    series = (source.get("series") or [{}])[0]
    year = re.match(r"\d{4}", str(doc.get("year") or ""))
    paper = _s.record("zbmath", str(doc["id"]), title=(doc.get("title") or {}).get("title"),
        authors=", ".join(a.get("name") for a in (doc.get("contributors") or {}).get("authors") or [] if a.get("name")),
        year=int(year.group()) if year else None,
        venue=series.get("title") or source.get("source"),
        doi_value=(by_type.get("doi") or [{}])[0].get("identifier"), url=doc.get("zbmath_url"),
        abstract=review and review["text"],
        full_text_links=["https://arxiv.org/abs/" + arxiv] if arxiv else [],
        work_type=(doc.get("document_type") or {}).get("description"),
        identifiers={"zbl": doc.get("identifier"), "arxiv": arxiv})
    # zbMATH texts are expert reviews or author summaries, often in LaTeX.
    paper["abstract_kind"] = review and review.get("contribution_type")
    paper["msc"] = [{"code": m.get("code"), "label": m.get("text")} for m in doc.get("msc") or []]
    paper["oeis_sequences"] = [link.get("identifier") for link in by_type.get("oeis") or []]
    paper["keywords"] = doc.get("keywords") or []
    return paper


@tool
@_s.guarded
def zbmath_search(query: str, limit: int = 10, from_year: int | None = None,
                  cursor: str | None = None) -> dict:
    """Search zbMATH Open, the curated database of mathematics literature since 1868.

    Results carry expert reviews, MSC subject codes and linked OEIS sequences.
    Plain words must all match (any field); quote phrases. Field prefixes narrow
    the search: ti: title, au: author, ab: review/summary, py: year or range,
    cc: MSC code, so: source. Example: 'ti:"prime gaps" cc:11N05 py:2015-2026'.
    Ordering is not a relevance ranking, so prefer precise queries.

    Args:
        query: zbMATH search string; plain words, quoted phrases and field prefixes.
        limit: Page size, 1-100. Keep unchanged when following a cursor.
        from_year: Inclusive publication year lower bound, or null for no bound.
        cursor: next_cursor from the previous page; null starts a new search. Keep other arguments unchanged.
    """
    query = _s.text(query)
    _s.integer(limit, "limit", 1, 100)
    _s.year(from_year)
    effective = query + (f" py:{from_year:04d}-3000" if from_year is not None else "")
    context = {"provider": "zbmath", "query": query, "provider_query": effective, "limit": limit, "from_year": from_year}
    position, consumed = _s.read_cursor(cursor, context, 0)
    params = {"search_string": effective, "results_per_page": limit, "page": position}
    try:
        data = _s.get_json("zbmath", _API_URL + "/_search?" + urllib.parse.urlencode(params))
    except _s.SearchError as exc:
        if exc.details["code"] != "not_found":
            raise
        # zbMATH answers HTTP 404 when a search has no matches.
        return _s.page("zbmath", query, context, [], 0, None, consumed, position)
    if not isinstance(data.get("result"), list):
        raise ValueError("missing zbMATH results")
    results = [_paper(doc) for doc in data["result"] if (doc.get("title") or {}).get("title")]
    return _s.page("zbmath", query, context, results, data["status"]["nr_total_results"],
                   position + 1, consumed, position, fetched_count=len(data["result"]))


@tool
@_s.guarded
def zbmath_get_paper(paper_id: str) -> dict:
    """Fetch one zbMATH Open record with its full review, MSC codes and links.

    Args:
        paper_id: zbMATH document ID (6383667), Zbl number (0923.11018) or zbmath.org URL.
    """
    identifier = re.sub(r"^https?://zbmath\.org/(?:\?q=an:)?|^zbl\s*", "", _s.text(paper_id, "paper_id"), flags=re.I)
    if not re.fullmatch(r"\d+|\d{4}\.\d{5}", identifier):
        raise _s.SearchError("invalid_arguments", "paper_id must be a zbMATH document ID or Zbl number.")
    try:
        data = _s.get_json("zbmath", _API_URL + "/" + identifier)
    except _s.SearchError as exc:
        if exc.details["code"] == "not_found":
            raise _s.SearchError("not_found", "zbMATH has no document with this ID.") from None
        raise
    if not isinstance(data.get("result"), dict):
        raise _s.SearchError("not_found", "zbMATH has no document with this ID.")
    return {"source": "zbmath", "paper": _paper(data["result"])}
