"""OpenAlex discovery and full-metadata lookup. Optional OPENALEX_API_KEY/MAILTO."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re
from typing import Literal
import urllib.parse

from omnigent_client.tools import tool

# Omnigent imports each tool by absolute path, without adding siblings to sys.path.
_spec = importlib.util.spec_from_file_location("_search_support", Path(__file__).with_name("search_support.py"))
_s = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_s)

_API_URL = "https://api.openalex.org/works"
_SORTS = {"relevance": "relevance_score:desc", "citations": "cited_by_count:desc", "recent": "publication_date:desc"}
_SCOPES = {"all": "search", "title_abstract": "search.title_and_abstract", "title": "search.title"}
_SELECT = "id,doi,title,publication_year,cited_by_count,authorships,primary_location,type,abstract_inverted_index,is_retracted,locations,ids"


def _get_json(path, params):
    params = dict(params)
    headers = {}
    if api_key := _s.setting("OPENALEX_API_KEY"):
        headers["Authorization"] = "Bearer " + api_key
    if mailto := _s.setting("OPENALEX_MAILTO"):
        params["mailto"] = mailto
    url = path + "?" + urllib.parse.urlencode(params)
    if len(url.encode()) > 4094:
        raise _s.SearchError("invalid_arguments", "Query URL exceeds OpenAlex's limit; split OR clauses into smaller searches and deduplicate their results.")
    return _s.get_json("openalex", url, headers=headers)


def _abstract(index):
    if not index:
        return None
    positions = [(pos, word) for word, places in index.items() for pos in places]
    return " ".join(word for _, word in sorted(positions))


def _paper(work):
    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    names = [(a.get("author") or {}).get("display_name") for a in (work.get("authorships") or [])]
    ids = work.get("ids") or {}
    pmid = (ids.get("pmid") or "").rstrip("/").rsplit("/", 1)[-1] or None
    locations = work.get("locations") or [primary]
    links = [loc.get("pdf_url") for loc in locations if loc]
    links += [loc.get("landing_page_url") for loc in locations if loc and loc.get("is_oa")]
    preprint = True if work.get("type") == "preprint" else (False if primary.get("version") == "publishedVersion" else None)
    return _s.record("openalex", work["id"], title=work.get("title"),
        authors=", ".join(n for n in names if n), year=work.get("publication_year"),
        venue=source.get("display_name"), doi_value=work.get("doi"),
        url=work.get("doi") or primary.get("landing_page_url") or work["id"],
        abstract=_abstract(work.get("abstract_inverted_index")), cited_by=work.get("cited_by_count"),
        work_type=work.get("type"), is_preprint=preprint, is_retracted=work.get("is_retracted"),
        full_text_links=links, identifiers={"pmid": pmid, "pmcid": ids.get("pmcid")})


@tool
@_s.guarded
def openalex_search(query: str, limit: int = 10,
                    sort: Literal["relevance", "citations", "recent"] = "relevance",
                    from_year: int | None = None,
                    search_scope: Literal["all", "title_abstract", "title"] = "title_abstract",
                    detail: Literal["compact", "full"] = "compact",
                    cursor: str | None = None) -> dict:
    """Search scholarly works; return full available abstracts and a continuation cursor.

    Search scope stays fixed when sort changes. Citation counts describe attention,
    not evidence quality. Use openalex_get_paper to refresh a candidate by ID/DOI.

    Args:
        query: OpenAlex terms or Boolean query, e.g. '(malaria OR dengue) AND vaccine'.
        limit: Page size, 1-100. Keep unchanged when following a cursor.
        sort: Relevance, citation count or most recent publication first.
        from_year: Inclusive publication year lower bound, or null for no bound.
        search_scope: title_abstract (default), title, or all (includes full text/keywords).
        detail: compact (default) gives identifiers, status and a 200-character snippet for shortlisting; full gives complete abstracts and links.
        cursor: next_cursor from the previous page; null starts a new search. Keep other arguments unchanged.
    """
    query = _s.text(query)
    _s.choice(detail, "detail", ("compact", "full"))
    _s.integer(limit, "limit", 1, 100)
    _s.choice(sort, "sort", _SORTS)
    _s.choice(search_scope, "search_scope", _SCOPES)
    _s.year(from_year)
    context = {"provider": "openalex", "query": query, "limit": limit, "sort": sort, "from_year": from_year, "search_scope": search_scope}
    position, consumed = _s.read_cursor(cursor, context, "*")
    params = {"per-page": limit, "sort": _SORTS[sort], "select": _SELECT, _SCOPES[search_scope]: query, "cursor": position}
    if from_year is not None:
        params["filter"] = f"from_publication_date:{from_year:04d}-01-01"
    data = _get_json(_API_URL, params)
    if not isinstance(data.get("results"), list) or not isinstance(data.get("meta"), dict):
        raise ValueError("missing results/meta")
    return _s.page("openalex", query, context, [_paper(w) for w in data["results"]],
                   data["meta"].get("count"), data["meta"].get("next_cursor"), consumed, position, detail=detail)


@tool
@_s.guarded
def openalex_get_paper(paper_id: str) -> dict:
    """Retrieve a work's full available abstract, identifiers, status and access links.

    Args:
        paper_id: OpenAlex ID/URL (W2741809807) or DOI/DOI URL.
    """
    identifier = _s.text(paper_id, "paper_id")
    short_id = re.sub(r"^https?://openalex\.org/", "", identifier, flags=re.I)
    if re.fullmatch(r"W\d+", short_id, flags=re.I):
        path_id = short_id.upper()
    elif _s.doi(identifier):
        path_id = "https://doi.org/" + _s.doi(identifier)
    else:
        raise _s.SearchError("invalid_arguments", "paper_id must be an OpenAlex work ID or DOI.")
    data = _get_json(_API_URL + "/" + urllib.parse.quote(path_id, safe="/:"), {"select": _SELECT})
    return {"source": "openalex", "paper": _paper(data)}
