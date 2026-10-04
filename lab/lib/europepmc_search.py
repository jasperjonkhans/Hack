"""Europe PMC discovery and detail lookup for biomedical papers and preprints."""
from __future__ import annotations

import html
import importlib.util
from pathlib import Path
import re
from typing import Literal
import urllib.parse

from omnigent_client.tools import tool

_spec = importlib.util.spec_from_file_location("_search_support", Path(__file__).with_name("search_support.py"))
_s = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_s)

_API_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_SORTS = {"relevance": None, "citations": "CITED desc", "recent": "P_PDATE_D desc"}
_SCOPES = {"all": None, "title_abstract": "TITLE_ABS", "title": "TITLE"}


def _clean(value):
    # Strip markup before unescaping so literal mathematical '<'/'>' survive.
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def _get_json(params):
    data = _s.get_json("europepmc", _API_URL + "?" + urllib.parse.urlencode({"format": "json", "resultType": "core", **params}))
    if type(data.get("hitCount")) is not int or not isinstance(data.get("resultList"), dict) or not isinstance(data["resultList"].get("result"), list):
        raise ValueError("missing search results")
    return data


def _paper(paper):
    if not paper.get("source") or not paper.get("id"):
        raise ValueError("missing source/ID")
    identifier = f"{paper['source']}:{paper['id']}"
    publication_types = (paper.get("pubTypeList") or {}).get("pubType") or []
    journal = (paper.get("journalInfo") or {}).get("journal") or {}
    links = [link.get("url") for link in (paper.get("fullTextUrlList") or {}).get("fullTextUrl", []) if link.get("availabilityCode") == "OA"]
    if paper.get("pmcid") and paper.get("isOpenAccess") == "Y":
        links.append("https://europepmc.org/articles/" + paper["pmcid"])
    retracted = paper.get("isRetracted")
    status = True if "Retracted Publication" in publication_types or retracted == "Y" else (False if retracted == "N" else None)
    result = _s.record("europepmc", identifier, title=_clean(paper.get("title")),
        authors=paper.get("authorString"), year=paper.get("pubYear"),
        venue=journal.get("title") or (paper.get("bookOrReportDetails") or {}).get("publisher"),
        doi_value=paper.get("doi"), url=f"https://europepmc.org/article/{paper['source']}/{paper['id']}",
        abstract=_clean(paper.get("abstractText")), cited_by=paper.get("citedByCount"),
        is_preprint=paper["source"] == "PPR", is_retracted=status,
        work_type="preprint" if paper["source"] == "PPR" else (publication_types[0] if publication_types else None),
        full_text_links=links, identifiers={"pmid": paper.get("pmid") or (paper["id"] if paper["source"] == "MED" else None), "pmcid": paper.get("pmcid")})
    result["pmid"] = result["identifiers"].get("pmid")
    result["publication_types"] = publication_types
    return result


@tool
@_s.guarded
def europepmc_search(query: str, limit: int = 10,
                     sort: Literal["relevance", "citations", "recent"] = "relevance",
                     from_year: int | None = None, include_preprints: bool = True,
                     search_scope: Literal["all", "title_abstract", "title"] = "title_abstract",
                     query_mode: Literal["plain", "advanced"] = "plain",
                     detail: Literal["compact", "full"] = "compact",
                     cursor: str | None = None) -> dict:
    """Search biomedical literature with full abstracts and cursor pagination.

    Scope is independent of sorting. Advanced mode supports native Europe PMC
    syntax, e.g. 'TITLE:"malaria vaccine" AND AUTH:Smith'.

    Args:
        query: Plain words/quoted phrases, or native syntax when query_mode is advanced.
        limit: Page size, 1-100; keep unchanged when following a cursor.
        sort: Relevance, citation count or most recent publication first.
        from_year: Inclusive publication year lower bound, or null.
        include_preprints: Whether to include source PPR (preprints).
        search_scope: title_abstract (default), title or all; advanced mode requires all and defines its own fields.
        query_mode: plain for words/phrases; advanced for native fields, Boolean operators or ranges.
        detail: compact (default) gives identifiers, status and a 200-character snippet for shortlisting; full gives complete abstracts and links.
        cursor: next_cursor from the previous page; null starts a new search with these arguments.
    """
    query = _s.text(query)
    _s.choice(detail, "detail", ("compact", "full"))
    _s.integer(limit, "limit", 1, 100)
    _s.choice(sort, "sort", _SORTS)
    _s.choice(search_scope, "search_scope", _SCOPES)
    _s.choice(query_mode, "query_mode", ("plain", "advanced"))
    _s.year(from_year)
    _s.boolean(include_preprints, "include_preprints")
    if query_mode == "advanced":
        if search_scope != "all":
            raise _s.SearchError("invalid_arguments", "Advanced queries define their own fields; use search_scope='all'.")
        effective = f"({query})"
    else:
        if re.search(r"\b(?:AND|OR|NOT)\b|\b[a-zA-Z_]+:|[()\[\]]", query) or query.count('"') % 2:
            raise _s.SearchError("invalid_arguments", "Use query_mode='advanced' and search_scope='all' for field/Boolean syntax; balance quotation marks.")
        prefix = _SCOPES[search_scope]
        effective = f"{prefix}:({query})" if prefix else f"({query})"
    if from_year is not None:
        effective += f" AND PUB_YEAR:[{from_year} TO 3000]"
    if not include_preprints:
        effective += " AND NOT SRC:PPR"
    context = {"provider": "europepmc", "query": query, "provider_query": effective, "limit": limit,
               "sort": sort, "from_year": from_year, "include_preprints": include_preprints,
               "search_scope": search_scope, "query_mode": query_mode}
    position, consumed = _s.read_cursor(cursor, context, "*")
    params = {"query": effective, "pageSize": limit, "cursorMark": position}
    if _SORTS[sort]:
        params["sort"] = _SORTS[sort]
    data = _get_json(params)
    return _s.page("europepmc", query, context, [_paper(p) for p in data["resultList"]["result"]],
                   data["hitCount"], data.get("nextCursorMark"), consumed, position, detail=detail)


@tool
@_s.guarded
def europepmc_get_paper(paper_id: str) -> dict:
    """Retrieve full metadata, abstract, publication types and open-access links.

    Args:
        paper_id: Source-qualified ID from a search (e.g. MED:31452104 or PPR:PPR123), or a bare PMID.
    """
    identifier = _s.text(paper_id, "paper_id")
    if identifier.isdigit():
        identifier = "MED:" + identifier
    match = re.fullmatch(r"([A-Z]+):([A-Za-z0-9._-]+)", identifier)
    if not match:
        raise _s.SearchError("invalid_arguments", "paper_id must be a PMID or source-qualified Europe PMC ID.")
    source, local_id = match.groups()
    url = _API_URL.removesuffix("/search") + f"/article/{source}/{local_id}?format=json&resultType=core"
    data = _s.get_json("europepmc", url)
    if data["hitCount"] == 0:
        raise _s.SearchError("not_found", "No Europe PMC paper has this ID.")
    if data["hitCount"] != 1 or not isinstance(data.get("result"), dict):
        raise ValueError("ambiguous ID lookup")
    paper = _paper(data["result"])
    if paper["id"] != identifier:
        raise ValueError("ID lookup mismatch")
    return {"source": "europepmc", "paper": paper}
