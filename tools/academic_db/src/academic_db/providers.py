"""Fetch one paper from OpenAlex, Semantic Scholar or arXiv."""

from __future__ import annotations

import time
from typing import Any

import httpx

from . import config

S2_FIELDS = (
    "paperId,externalIds,title,year,publicationTypes,publicationVenue,"
    "citationCount,isOpenAccess,openAccessPdf,authors"
)
TIMEOUT = httpx.Timeout(20.0, connect=10.0)


class NotFound(Exception):
    pass


class RateLimited(Exception):
    pass


def _get(url: str, *, params: dict[str, str] | None = None, headers: dict[str, str] | None = None,
         retries: int = 2) -> httpx.Response:
    """GET with a short backoff on 429/5xx. Raises NotFound, RateLimited or httpx errors."""
    for attempt in range(retries + 1):
        response = httpx.get(url, params=params, headers=headers, timeout=TIMEOUT, follow_redirects=True)
        if response.status_code == 404:
            raise NotFound(url)
        if response.status_code == 429 or response.status_code >= 500:
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
                continue
            if response.status_code == 429:
                raise RateLimited(url)
        response.raise_for_status()
        return response
    raise AssertionError("unreachable")


def _openalex_params() -> dict[str, str]:
    mailto = config.setting("OPENALEX_MAILTO")
    return {"mailto": mailto} if mailto else {}


def openalex_work(*, doi: str | None = None, arxiv_id: str | None = None,
                  openalex_id: str | None = None) -> dict[str, Any]:
    base = "https://api.openalex.org/works"
    if openalex_id:
        return _get(f"{base}/{openalex_id}", params=_openalex_params()).json()
    if doi:
        return _get(f"{base}/doi:{doi}", params=_openalex_params()).json()
    if arxiv_id:
        # OpenAlex has no arXiv ID lookup; the arXiv landing page is listed as a location.
        landing = f"http://arxiv.org/abs/{arxiv_id}|https://arxiv.org/abs/{arxiv_id}"
        params = {"filter": f"locations.landing_page_url:{landing}", "per_page": "1"} | _openalex_params()
        results = _get(base, params=params).json().get("results") or []
        if not results:
            raise NotFound(f"OpenAlex has no work with arXiv ID {arxiv_id}")
        return results[0]
    raise ValueError("need doi, arxiv_id or openalex_id")


def semantic_scholar_paper(*, doi: str | None = None, arxiv_id: str | None = None,
                           paper_id: str | None = None) -> dict[str, Any]:
    if paper_id:
        key = paper_id
    elif doi:
        key = f"DOI:{doi}"
    elif arxiv_id:
        key = f"arXiv:{arxiv_id}"
    else:
        raise ValueError("need doi, arxiv_id or paper_id")
    api_key = config.setting("S2_API_KEY")
    return _get(
        f"https://api.semanticscholar.org/graph/v1/paper/{key}",
        params={"fields": S2_FIELDS},
        headers={"x-api-key": api_key} if api_key else None,
        retries=3,
    ).json()


def arxiv_entry(arxiv_id: str) -> str:
    """The Atom feed for one arXiv ID (normalize.from_arxiv extracts the entry)."""
    feed = _get("https://export.arxiv.org/api/query", params={"id_list": arxiv_id}).text
    if "<entry>" not in feed and "<entry " not in feed:
        raise NotFound(f"arXiv has no paper {arxiv_id}")
    return feed
