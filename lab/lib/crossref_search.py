"""Crossref DOI registry: bibliographic search, authoritative DOI metadata and retraction notices.

Set CONTACT_EMAIL to join Crossref's polite pool (more reliable rate limits).
"""
from __future__ import annotations

import html
import importlib.util
import os
from pathlib import Path
import re
from typing import Literal
import urllib.parse

from omnigent_client.tools import tool

_spec = importlib.util.spec_from_file_location("_search_support", Path(__file__).with_name("search_support.py"))
_s = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_s)

_API_URL = "https://api.crossref.org/works"
_SORTS = {"relevance": "relevance", "citations": "is-referenced-by-count", "recent": "published"}
_SCOPES = {"all": "query", "bibliographic": "query.bibliographic"}
# `updated-by` cannot be selected in searches, so retraction status needs crossref_get_paper.
_SELECT = "DOI,title,author,issued,container-title,type,is-referenced-by-count,abstract,update-to"
_WITHDRAWN = {"retraction", "withdrawal", "removal"}
_RELATIONS = ("has-preprint", "is-preprint-of", "is-version-of", "has-version")


def _get(url, params=None):
    params = dict(params or {})
    if os.environ.get("CONTACT_EMAIL"):
        params["mailto"] = os.environ["CONTACT_EMAIL"]
    data = _s.get_json("crossref", url + ("?" + urllib.parse.urlencode(params) if params else ""))
    if data.get("status") != "ok" or not isinstance(data.get("message"), dict):
        raise ValueError("unexpected Crossref envelope")
    return data["message"]


def _strip_jats(value):
    if not value:
        return None
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", html.unescape(value)).strip()
    return re.sub(r"^Abstract\s+", "", value) or None


def _notices(entries):
    seen = {}
    for entry in entries or []:
        key = (entry.get("type"), _s.doi(entry.get("DOI")))
        if key not in seen:
            date = (entry.get("updated") or {}).get("date-time")
            seen[key] = {"type": key[0], "notice_doi": key[1], "date": date[:10] if date else None}
    return list(seen.values())


def _paper(item, full=False):
    names = [" ".join(p for p in (a.get("given"), a.get("family")) if p) or a.get("name") for a in item.get("author") or []]
    parts = (item.get("issued") or {}).get("date-parts") or [[None]]
    paper = _s.record("crossref", item["DOI"], title=_strip_jats((item.get("title") or [None])[0]),
        authors=", ".join(n for n in names if n), year=parts[0][0],
        venue=(item.get("container-title") or [None])[0], doi_value=item["DOI"],
        abstract=_strip_jats(item.get("abstract")), cited_by=item.get("is-referenced-by-count"),
        work_type=item.get("type"), is_preprint=item.get("type") == "posted-content")
    # A notice (e.g. "Retraction: ...") lists the works it amends.
    paper["amends"] = _notices(item.get("update-to"))
    if full:
        paper["notices"] = _notices(item.get("updated-by"))
        paper["is_retracted"] = any(n["type"] in _WITHDRAWN for n in paper["notices"])
        relation = item.get("relation") or {}
        paper["related_versions"] = {k: [r.get("id") for r in relation[k]] for k in _RELATIONS if relation.get(k)}
        paper["reference_count"] = item.get("references-count")
    return paper


@tool
@_s.guarded
def crossref_search(query: str, limit: int = 10,
                    sort: Literal["relevance", "citations", "recent"] = "relevance",
                    search_scope: Literal["all", "bibliographic"] = "all",
                    author_query: str | None = None, from_year: int | None = None,
                    detail: Literal["compact", "full"] = "compact",
                    cursor: str | None = None) -> dict:
    """Search the Crossref DOI registry (all disciplines, publisher-supplied metadata).

    Best for resolving a citation string to its DOI: pass the whole reference
    with search_scope='bibliographic'. Abstracts are often missing. Search hits
    do not include retraction status; confirm candidates with crossref_get_paper.

    Args:
        query: Free-text terms, or a full citation string in bibliographic scope.
        limit: Page size, 1-100. Keep unchanged when following a cursor.
        sort: Relevance, citation count or most recent publication first.
        search_scope: all searches every field; bibliographic matches titles, authors, venues and years.
        author_query: Fuzzy author-search hint, e.g. 'Maynard'; NOT an exact author filter. Verify returned authors.
        from_year: Inclusive publication year lower bound, or null for no bound.
        detail: compact (default) gives identifiers, status and a 200-character snippet for shortlisting; full gives complete abstracts and links.
        cursor: next_cursor from the previous page; null starts a new search. Keep other arguments unchanged.
    """
    query = _s.text(query)
    _s.choice(detail, "detail", ("compact", "full"))
    _s.integer(limit, "limit", 1, 100)
    _s.choice(sort, "sort", _SORTS)
    _s.choice(search_scope, "search_scope", _SCOPES)
    _s.year(from_year)
    if author_query is not None:
        author_query = _s.text(author_query, "author_query")
    context = {"provider": "crossref", "query": query, "limit": limit, "sort": sort,
               "search_scope": search_scope, "author_query": author_query, "from_year": from_year}
    position, consumed = _s.read_cursor(cursor, context, "*")
    params = {_SCOPES[search_scope]: query, "rows": limit, "sort": _SORTS[sort], "order": "desc",
              "select": _SELECT, "cursor": position}
    if author_query:
        params["query.author"] = author_query
    if from_year is not None:
        params["filter"] = f"from-pub-date:{from_year:04d}"
    message = _get(_API_URL, params)
    if not isinstance(message.get("items"), list):
        raise ValueError("missing Crossref items")
    results = [_paper(item) for item in message["items"] if item.get("title")]
    out = _s.page("crossref", query, context, results, message.get("total-results"),
                  message.get("next-cursor"), consumed, position, fetched_count=len(message["items"]), detail=detail)
    if author_query:
        out["warning"] = "author_query is a fuzzy search hint, not an author constraint. Verify authors and topic relevance in each result."
    return out


@tool
@_s.guarded
def crossref_get_paper(doi: str) -> dict:
    """Fetch the registered metadata for one DOI, including retraction and correction notices.

    Use this to verify a citation's title, authors, venue and year, and to check
    whether the work was retracted, withdrawn or corrected (Crossref includes
    Retraction Watch data). is_retracted=false means no withdrawal notice is
    registered, not a guarantee.

    Args:
        doi: DOI, DOI URL or 'doi:' prefixed DOI, e.g. 10.1038/s41586-021-03819-2.
    """
    normal = _s.doi(_s.text(doi, "doi"))
    if not normal:
        raise _s.SearchError("invalid_arguments", "doi must look like 10.xxxx/....")
    try:
        item = _get(_API_URL + "/" + urllib.parse.quote(normal, safe="/"))
    except _s.SearchError as exc:
        if exc.details["code"] == "not_found":
            raise _s.SearchError("not_found", "Crossref has no record of this DOI; it may be registered with another agency (e.g. DataCite) or be invalid.") from None
        raise
    return {"source": "crossref", "paper": _paper(item, full=True)}
