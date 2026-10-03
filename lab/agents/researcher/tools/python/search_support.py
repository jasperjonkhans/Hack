"""Shared HTTP, pagination and evidence utilities; no third-party HTTP dependencies."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import datetime as dt
from email.utils import parsedate_to_datetime
import functools
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import typing
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from omnigent_client.tools import tool


class SearchError(Exception):
    def __init__(self, code, message, *, retryable=False, status=None, retry_after=None):
        super().__init__(message)
        self.details = {"code": code, "message": message, "retryable": retryable}
        if status is not None:
            self.details["http_status"] = status
        if retry_after is not None:
            self.details["retry_after_seconds"] = round(max(0, retry_after), 1)


def guarded(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SearchError as exc:
            return {"error": exc.details}
        except (ValueError, TypeError, KeyError, AttributeError, ET.ParseError):
            return {"error": {"code": "invalid_response", "message": "The provider returned malformed or incomplete data.", "retryable": False}}
        except (OSError, sqlite3.Error):
            return {"error": {"code": "local_error", "message": "Cannot access the shared request limiter; check SEARCH_STATE_DIR permissions.", "retryable": False}}
    # Omnigent reads type hints from the wrapper when building its tool schema.
    wrapped.__annotations__ = typing.get_type_hints(fn)
    return wrapped


def text(value, name="query"):
    if not isinstance(value, str) or not value.strip():
        raise SearchError("invalid_arguments", f"{name} must be a non-empty string.")
    return value.strip()


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise SearchError("invalid_arguments", f"{name} must be an integer from {low} to {high}.")
    return value


def choice(value, name, choices):
    if not isinstance(value, str) or value not in choices:
        raise SearchError("invalid_arguments", f"{name} must be one of {', '.join(choices)}.")
    return value


def boolean(value, name):
    if type(value) is not bool:
        raise SearchError("invalid_arguments", f"{name} must be true or false.")


def year(value):
    if value is not None:
        integer(value, "from_year", 1, dt.date.today().year + 1)


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def doi(value):
    if not value:
        return None
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", str(value).strip(), flags=re.I).lower()
    return value if re.fullmatch(r"10\.\d{4,9}/\S+", value) else None


def number(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("invalid numeric metadata")
    return int(value)


def record(provider, identifier, *, title, authors=None, year=None, venue=None,
           doi_value=None, url=None, abstract=None, cited_by=None, full_text_links=None,
           is_preprint=None, is_retracted=None, work_type=None, identifiers=None):
    if not isinstance(identifier, str) or not identifier or not isinstance(title, str) or not title.strip():
        raise ValueError("missing paper identity")
    normal_doi = doi(doi_value)
    ids = {provider: identifier, **(identifiers or {})}
    if normal_doi:
        ids["doi"] = normal_doi
    ids = {k: str(v) for k, v in ids.items() if v}
    if "pmcid" in ids:
        ids["pmcid"] = ids["pmcid"].rstrip("/").rsplit("/", 1)[-1].upper()
    if "arxiv" in ids:
        ids["arxiv"] = re.sub(r"v\d+$", "", ids["arxiv"])
    canonical = "doi:" + normal_doi if normal_doi else provider + ":" + ids.get(provider, identifier)
    publication_year = number(year)
    count = number(cited_by)
    doi_url = "https://doi.org/" + normal_doi if normal_doi else None
    return {
        "source": provider, "id": identifier, "canonical_id": canonical, "identifiers": ids,
        "title": title.strip(), "authors": authors, "year": publication_year, "venue": venue,
        "type": work_type, "doi": normal_doi, "doi_url": doi_url, "url": url or doi_url,
        "abstract": abstract or None, "abstract_truncated": False,
        "full_text_links": list(dict.fromkeys(u for u in (full_text_links or []) if isinstance(u, str) and u.startswith(("https://", "http://")))),
        "is_preprint": is_preprint, "is_retracted": is_retracted,
        "cited_by": count,
        "citations_per_year": round(count / max(1, dt.date.today().year - publication_year + 1), 1) if count is not None and publication_year else None,
        "provenance": {"source": provider, "id": identifier, "retrieved_at": timestamp()},
    }


def _fingerprint(context):
    return hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()


def read_cursor(cursor, context, initial):
    if cursor is None:
        return initial, 0
    try:
        payload = json.loads(base64.urlsafe_b64decode(text(cursor, "cursor").encode()))
        if payload["fingerprint"] != _fingerprint(context) or type(payload["consumed"]) is not int or payload["consumed"] < 0:
            raise ValueError()
        position = payload["position"]
        if type(position) is not type(initial) or (isinstance(position, int) and position < 0) or position == "":
            raise ValueError()
        return position, payload["consumed"]
    except (ValueError, TypeError, KeyError, AttributeError):
        raise SearchError("invalid_arguments", "Invalid cursor, or query/filter/sort/limit changed. Restart without a cursor.") from None


def page(provider, query, context, results, total, position, consumed, previous, *, fetched_count=None):
    if type(total) is not int or total < 0:
        raise ValueError("missing result count")
    fetched_count = len(results) if fetched_count is None else fetched_count
    if type(fetched_count) is not int or fetched_count < len(results):
        raise ValueError("invalid fetched count")
    consumed += fetched_count
    if fetched_count == 0 and consumed < total:
        raise SearchError("invalid_response", "The provider returned an empty page before the reported end of results.", retryable=True)
    more = consumed < total
    if more and (position is None or position == previous):
        raise SearchError("invalid_response", "The provider omitted a usable continuation cursor.")
    cursor = None
    if more:
        cursor = base64.urlsafe_b64encode(json.dumps({"fingerprint": _fingerprint(context), "position": position, "consumed": consumed}).encode()).decode()
    return {"source": provider, "query": query, "effective_query": context,
            "total_matches": total, "returned_count": len(results), "fetched_count": fetched_count,
            "skipped_count": fetched_count - len(results), "has_more": more,
            "next_cursor": cursor, "results": results, "retrieved_at": timestamp()}


@contextmanager
def request_slot(provider, interval):
    """Serialise requests across threads and Omnigent subprocesses on this host."""
    root = Path(os.environ.get("SEARCH_STATE_DIR", Path.home() / ".cache/discovery-lab/search"))
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / f"{provider}.sqlite3", timeout=120)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS throttle (id INTEGER PRIMARY KEY, next_allowed REAL)")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("INSERT OR IGNORE INTO throttle VALUES (1, 0)")
        remaining = connection.execute("SELECT next_allowed FROM throttle WHERE id=1").fetchone()[0] - time.time()
        if remaining > 10:
            raise SearchError("rate_limited", "Provider cooldown is active; retry after the indicated delay.", retryable=True, retry_after=remaining)
        if remaining > 0:
            time.sleep(remaining)
        try:
            yield connection
        finally:
            connection.execute("UPDATE throttle SET next_allowed=MAX(next_allowed, ?) WHERE id=1", (time.time() + interval,))
            connection.commit()
    finally:
        connection.close()


def _retry_after(headers):
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0, float(value))
    except ValueError:
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


def fetch(provider, url, *, headers=None, retries=2):
    request = urllib.request.Request(url, headers={"User-Agent": "discovery-lab/0.2", **(headers or {})})
    for attempt in range(retries + 1):
        delay = None
        try:
            with request_slot(provider, 3.0 if provider == "arxiv" else 0.1) as connection:
                try:
                    with urllib.request.urlopen(request, timeout=30) as response:
                        return response.read()
                except urllib.error.HTTPError as exc:
                    status = exc.code
                    body = exc.read(16384).decode("utf-8", errors="replace").lower()
                    delay = _retry_after(exc.headers)
                    exhausted = status == 429 and (exc.headers.get("X-RateLimit-Remaining") == "0" or any(s in body for s in ("daily limit", "daily budget", "quota", "credits exhausted")))
                    if exhausted and delay is None:
                        try:
                            delay = max(0, float(exc.headers.get("X-RateLimit-Reset")))
                        except (ValueError, TypeError):
                            delay = None
                    exc.close()
                    if status == 429:
                        cooldown = delay if delay is not None else (60 if exhausted else 2 ** (attempt + 1))
                        connection.execute("UPDATE throttle SET next_allowed=MAX(next_allowed, ?) WHERE id=1", (time.time() + cooldown,))
                    code = "quota_exhausted" if exhausted else {400: "invalid_query", 401: "authentication", 403: "forbidden", 404: "not_found", 429: "rate_limited"}.get(status, "upstream_error")
                    transient = status in (429, 500, 502, 503, 504) and not exhausted
                    if not transient or attempt == retries or (delay is not None and delay > 10):
                        raise SearchError(code, f"{provider} returned HTTP {status}.", retryable=transient, status=status, retry_after=delay) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            if attempt == retries:
                raise SearchError("network_error", f"Cannot reach {provider}; retry later.", retryable=True) from None
        time.sleep(delay if delay is not None else 2 ** (attempt + 1))
    raise AssertionError("unreachable")


def get_json(provider, url, *, headers=None):
    data = json.loads(fetch(provider, url, headers=headers))
    if not isinstance(data, dict) or data.get("error") or data.get("errCode"):
        raise SearchError("invalid_response", f"{provider} returned an error or unexpected JSON structure.")
    return data


@tool
@guarded
def deduplicate_papers(papers: list[dict]) -> dict:
    """Group repeated search hits by stable identifiers, retaining every source/version.

    Shared DOI, PMID, PMCID or versionless arXiv ID links records; titles alone
    never establish identity. Counts measure works, not independent evidence.

    Args:
        papers: Combined results from searches, pages or paper-detail lookups.
    """
    if not isinstance(papers, list) or any(not isinstance(p, dict) or not p.get("source") or not p.get("id") for p in papers):
        raise SearchError("invalid_arguments", "papers must contain search records with source and id.")
    parents = list(range(len(papers)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    owners = {}
    for i, paper in enumerate(papers):
        ids = dict(paper.get("identifiers") or {})
        ids[paper["source"]] = paper["id"]
        normal_doi = doi(paper.get("doi") or ids.get("doi"))
        if normal_doi:
            ids["doi"] = normal_doi
        for namespace, value in ids.items():
            if not value or namespace not in ("doi", "pmid", "pmcid", "arxiv", "zbl", "openalex", "europepmc", "crossref", "zbmath", paper["source"]):
                continue
            value = str(value)
            if namespace == "arxiv":
                value = re.sub(r"v\d+$", "", value.rsplit("/abs/", 1)[-1])
            key = namespace + ":" + value
            if key in owners:
                parents[root(i)] = root(owners[key])
            else:
                owners[key] = i
    groups = {}
    for i, paper in enumerate(papers):
        groups.setdefault(root(i), []).append(paper)
    output = []
    for records in groups.values():
        normal_doi = next((doi(p.get("doi")) for p in records if doi(p.get("doi"))), None)
        canonical = "doi:" + normal_doi if normal_doi else records[0].get("canonical_id", records[0]["source"] + ":" + records[0]["id"])
        output.append({"canonical_id": canonical, "records": records,
                       "sources": sorted({p["source"] for p in records}),
                       "is_retracted": True if any(p.get("is_retracted") is True for p in records) else None})
    return {"input_count": len(papers), "unique_count": len(output), "groups": output}
