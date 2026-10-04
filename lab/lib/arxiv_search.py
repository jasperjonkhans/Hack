"""arXiv discovery and lookup, with throttling shared across local worker processes."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re
from typing import Literal
import urllib.parse
import xml.etree.ElementTree as ET

from omnigent_client.tools import tool

_spec = importlib.util.spec_from_file_location("_search_support", Path(__file__).with_name("search_support.py"))
_s = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_s)

_API_URL = "https://export.arxiv.org/api/query"
_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom", "opensearch": "http://a9.com/-/spec/opensearch/1.1/"}
_SORTS = {"relevance": "relevance", "recent": "submittedDate"}
_ID = r"(?:\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7})(?:v\d+)?"


def _clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def _feed(params):
    root = ET.fromstring(_s.fetch("arxiv", _API_URL + "?" + urllib.parse.urlencode(params)))
    if root.tag != "{" + _NS["atom"] + "}feed":
        raise ValueError("not an Atom feed")
    for entry in root.findall("atom:entry", _NS):
        identifier = entry.findtext("atom:id", default="", namespaces=_NS)
        if "/api/errors" in identifier or entry.findtext("atom:title", namespaces=_NS) == "Error":
            raise _s.SearchError("invalid_query", _clean(entry.findtext("atom:summary", namespaces=_NS)) or "arXiv rejected this query.")
    return root


def _paper(entry):
    raw_id = _clean(entry.findtext("atom:id", namespaces=_NS)).rsplit("/abs/", 1)[-1]
    if not re.fullmatch(_ID, raw_id):
        raise ValueError("invalid arXiv paper ID")
    names = [_clean(a.findtext("atom:name", namespaces=_NS)) for a in entry.findall("atom:author", _NS)]
    published = entry.findtext("atom:published", default="", namespaces=_NS)
    doi_value = entry.findtext("arxiv:doi", namespaces=_NS)
    paper = _s.record("arxiv", raw_id, title=_clean(entry.findtext("atom:title", namespaces=_NS)),
        authors=", ".join(names), year=int(published[:4]) if published[:4].isdigit() else None,
        venue=entry.findtext("arxiv:journal_ref", namespaces=_NS), doi_value=doi_value,
        url="https://arxiv.org/abs/" + raw_id,
        abstract=_clean(entry.findtext("atom:summary", namespaces=_NS)),
        full_text_links=["https://arxiv.org/pdf/" + raw_id], work_type="preprint", is_preprint=True)
    paper["categories"] = [c.get("term") for c in entry.findall("atom:category", _NS)]
    return paper


def _query(query, mode, scope):
    if mode == "advanced":
        if scope != "all":
            raise _s.SearchError("invalid_arguments", "Advanced queries define their own fields; use search_scope='all'.")
        return query
    if re.search(r"\b(?:AND|OR|ANDNOT|NOT)\b|\b[a-zA-Z_]+:|[()]", query):
        raise _s.SearchError("invalid_arguments", "Boolean/field syntax requires query_mode='advanced', e.g. 'all:electron OR all:proton'.")
    if query.count('"') % 2:
        raise _s.SearchError("invalid_arguments", "Unbalanced quotation marks in query.")
    terms = re.findall(r'"[^"]+"|[^\s"]+', query)
    if not terms:
        raise _s.SearchError("invalid_arguments", "Provide at least one search term.")
    if scope == "title_abstract":
        return " AND ".join(f"(ti:{term} OR abs:{term})" for term in terms)
    prefix = "ti" if scope == "title" else "all"
    return " AND ".join(f"{prefix}:{term}" for term in terms)


@tool
@_s.guarded
def arxiv_search(query: str, limit: int = 10,
                 sort: Literal["relevance", "recent"] = "relevance",
                 query_mode: Literal["plain", "advanced"] = "plain",
                 search_scope: Literal["all", "title_abstract", "title"] = "all",
                 from_year: int | None = None, cursor: str | None = None) -> dict:
    """Search arXiv and return full abstracts, versioned IDs and pagination.

    Plain mode requires every word/quoted phrase. Advanced mode preserves arXiv
    Boolean syntax; use explicit fields and parentheses, e.g. 'ti:"prime gaps"'.
    arXiv records are repository versions and may also have a published DOI.

    Args:
        query: Plain terms/quoted phrases, or native arXiv syntax in advanced mode.
        limit: Page size, 1-100; keep unchanged when continuing.
        sort: Relevance or most recently submitted first.
        query_mode: plain builds an AND query; advanced passes native syntax unchanged.
        search_scope: all, title_abstract or title; advanced mode requires all and its own field prefixes.
        from_year: Inclusive submission year lower bound, or null. This is not the journal publication year.
        cursor: next_cursor from the previous page; null starts a new search with these arguments.
    """
    query = _s.text(query)
    _s.integer(limit, "limit", 1, 100)
    _s.choice(sort, "sort", _SORTS)
    _s.choice(query_mode, "query_mode", ("plain", "advanced"))
    _s.choice(search_scope, "search_scope", ("all", "title_abstract", "title"))
    _s.year(from_year)
    effective = _query(query, query_mode, search_scope)
    if from_year is not None:
        effective = f"({effective}) AND submittedDate:[{from_year:04d}01010000 TO 300001010000]"
    context = {"provider": "arxiv", "query": query, "provider_query": effective, "limit": limit, "sort": sort,
               "query_mode": query_mode, "search_scope": search_scope, "from_year": from_year}
    position, consumed = _s.read_cursor(cursor, context, 0)
    if position >= 30000:
        raise _s.SearchError("result_limit", "arXiv's 30,000-result boundary was reached; narrow the query.")
    root = _feed({"search_query": effective, "start": position, "max_results": min(limit, 30000-position), "sortBy": _SORTS[sort], "sortOrder": "descending"})
    results = [_paper(e) for e in root.findall("atom:entry", _NS)]
    total = int(root.findtext("opensearch:totalResults", namespaces=_NS))
    return _s.page("arxiv", query, context, results, total, position + len(results), consumed, position)


@tool
@_s.guarded
def arxiv_get_paper(paper_id: str) -> dict:
    """Fetch an arXiv paper's full abstract and access links, preserving its version.

    Args:
        paper_id: Modern or legacy arXiv ID, optionally versioned, or an arxiv.org/abs/ URL.
    """
    identifier = _s.text(paper_id, "paper_id")
    identifier = re.sub(r"^(?:https?://arxiv\.org/abs/|arxiv:)", "", identifier, flags=re.I)
    if not re.fullmatch(_ID, identifier):
        raise _s.SearchError("invalid_arguments", "paper_id must be an arXiv ID or abstract URL.")
    root = _feed({"id_list": identifier})
    entries = root.findall("atom:entry", _NS)
    if not entries:
        raise _s.SearchError("not_found", "No arXiv paper has this ID.")
    if len(entries) != 1:
        raise ValueError("unexpected lookup response")
    return {"source": "arxiv", "paper": _paper(entries[0])}
