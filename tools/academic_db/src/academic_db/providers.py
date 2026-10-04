"""Fetch papers from OpenAlex, Semantic Scholar and arXiv in batches (one request per provider per chunk)."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator, Sequence
from typing import Any

import httpx

from . import config

S2_FIELDS = (
    "paperId,externalIds,title,abstract,year,publicationTypes,publicationVenue,"
    "citationCount,isOpenAccess,openAccessPdf,authors"
)
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
OPENALEX_CHUNK = 50  # values per OR-filter
ARXIV_CHUNK = 50
S2_CHUNK = 500  # /paper/batch maximum
ARXIV_INTERVAL = 3.0  # arXiv asks for at least 3 s between requests

_arxiv_lock = threading.Lock()
_arxiv_last = 0.0


class RateLimited(Exception):
    pass


def _chunks(values: Sequence[str], size: int) -> Iterator[list[str]]:
    for start in range(0, len(values), size):
        yield list(values[start:start + size])


def _request(method: str, url: str, *, retries: int = 2, **kwargs: Any) -> httpx.Response:
    """HTTP request with a short backoff on 429/5xx. Raises RateLimited or httpx errors."""
    for attempt in range(retries + 1):
        response = httpx.request(method, url, timeout=TIMEOUT, follow_redirects=True, **kwargs)
        if response.status_code == 429 or response.status_code >= 500:
            if attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            if response.status_code == 429:
                raise RateLimited(url)
        response.raise_for_status()
        return response
    raise AssertionError("unreachable")


def _openalex_params() -> dict[str, str]:
    mailto = config.setting("OPENALEX_MAILTO")
    return {"mailto": mailto} if mailto else {}


def _openalex_filter(filter_value: str) -> list[dict[str, Any]]:
    params = {"filter": filter_value, "per_page": "200"} | _openalex_params()
    return _request("GET", "https://api.openalex.org/works", params=params).json().get("results") or []


def openalex_works(*, openalex_ids: Sequence[str] = (), dois: Sequence[str] = (),
                   arxiv_ids: Sequence[str] = (), pmids: Sequence[str] = ()) -> list[dict[str, Any]]:
    """Full OpenAlex work objects for any of the given IDs (not found ones are simply absent)."""
    works: list[dict[str, Any]] = []
    for chunk in _chunks(openalex_ids, OPENALEX_CHUNK):
        works += _openalex_filter("openalex:" + "|".join(chunk))
    for chunk in _chunks(pmids, OPENALEX_CHUNK):
        works += _openalex_filter("pmid:" + "|".join(chunk))
    # Commas and pipes separate filter values, so DOIs containing them are looked up one by one.
    plain = [d for d in dois if "," not in d and "|" not in d]
    for chunk in _chunks(plain, OPENALEX_CHUNK):
        works += _openalex_filter("doi:" + "|".join(chunk))
    for doi in (d for d in dois if d not in plain):
        response = httpx.get(f"https://api.openalex.org/works/doi:{doi}", params=_openalex_params(), timeout=TIMEOUT)
        if response.status_code == 200:
            works.append(response.json())
    # OpenAlex has no arXiv ID field; the arXiv landing page is listed as a location.
    for chunk in _chunks(arxiv_ids, OPENALEX_CHUNK // 2):
        urls = "|".join(f"http://arxiv.org/abs/{a}|https://arxiv.org/abs/{a}" for a in chunk)
        works += _openalex_filter("locations.landing_page_url:" + urls)
    return works


def _s2_headers() -> dict[str, str] | None:
    api_key = config.setting("S2_API_KEY")
    return {"x-api-key": api_key} if api_key else None


def semantic_scholar_papers(keys: Sequence[str]) -> list[dict[str, Any] | None]:
    """Papers for keys like 'DOI:10.1/x', 'ARXIV:1706.03762', 'CorpusId:123' or a paperId, aligned with keys."""
    headers = _s2_headers()
    papers: list[dict[str, Any] | None] = []
    for chunk in _chunks(keys, S2_CHUNK):
        response = _request(
            "POST", "https://api.semanticscholar.org/graph/v1/paper/batch",
            params={"fields": S2_FIELDS}, json={"ids": chunk}, headers=headers, retries=3,
        )
        papers += response.json()
    return papers


def arxiv_feeds(arxiv_ids: Sequence[str]) -> list[str]:
    """Atom feeds covering the given IDs (normalize.from_arxiv_feed extracts the entries)."""
    global _arxiv_last
    feeds = []
    for chunk in _chunks(arxiv_ids, ARXIV_CHUNK):
        with _arxiv_lock:
            wait = ARXIV_INTERVAL - (time.monotonic() - _arxiv_last)
            if wait > 0:
                time.sleep(wait)
            try:
                response = _request(
                    "GET", "https://export.arxiv.org/api/query",
                    params={"id_list": ",".join(chunk), "max_results": str(len(chunk))},
                )
            finally:
                _arxiv_last = time.monotonic()
        feeds.append(response.text)
    return feeds


CITING_FIELDS = (
    "contexts,intents,isInfluential,citingPaper.paperId,citingPaper.title,citingPaper.year,"
    "citingPaper.externalIds,citingPaper.citationCount"
)


def semantic_scholar_citations(paper_key: str, limit: int) -> list[dict[str, Any]]:
    """Papers citing paper_key, with the sentences that mention it ('contexts') when S2 has them."""
    response = _request(
        "GET", f"https://api.semanticscholar.org/graph/v1/paper/{paper_key}/citations",
        params={"fields": CITING_FIELDS, "limit": str(limit)}, headers=_s2_headers(), retries=3,
    )
    return response.json().get("data") or []


def openalex_citing_works(openalex_id: str, limit: int) -> list[dict[str, Any]]:
    """Most cited works citing openalex_id (no citation sentences; used when S2 has none)."""
    params = {
        "filter": f"cites:{openalex_id}", "sort": "cited_by_count:desc", "per_page": str(limit),
        "select": "id,doi,ids,title,publication_year,cited_by_count,locations",
    } | _openalex_params()
    return _request("GET", "https://api.openalex.org/works", params=params).json().get("results") or []


def semantic_scholar_snippets(query: str, limit: int) -> list[dict[str, Any]]:
    """Passages (~500 words) from paper titles, abstracts and body text that best match query."""
    response = _request(
        "GET", "https://api.semanticscholar.org/graph/v1/snippet/search",
        params={"query": query, "limit": str(limit)}, headers=_s2_headers(), retries=3,
    )
    return response.json().get("data") or []
