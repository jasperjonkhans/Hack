"""Save normalized records: match them to a work, upsert, refresh (spec sections 6 and 7)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

import httpx
import psycopg
from psycopg.types.json import Jsonb

from . import providers
from .identifiers import Identifier
from .normalize import PROVIDERS, Record, from_arxiv, from_openalex, from_semantic_scholar

# Serializes all saves so concurrent agents cannot create duplicate works.
INGEST_LOCK = "academic_db.ingest"

_TITLE_MATCH = """
SELECT w.id
FROM works w
WHERE w.title_norm = lower(regexp_replace(%(title)s, '[^[:alnum:]]+', '', 'g'))
  AND w.publication_year BETWEEN %(year)s - 1 AND %(year)s + 1
  AND NOT EXISTS (SELECT 1 FROM work_records r WHERE r.work_id = w.id AND r.provider = %(provider)s)
  AND (w.doi      IS NULL OR %(doi)s::text      IS NULL OR w.doi      = %(doi)s)
  AND (w.arxiv_id IS NULL OR %(arxiv_id)s::text IS NULL OR w.arxiv_id = %(arxiv_id)s)
  AND (w.mag_id   IS NULL OR %(mag_id)s::bigint IS NULL OR w.mag_id   = %(mag_id)s)
LIMIT 2
"""

_UPSERT = """
INSERT INTO work_records (work_id, provider, provider_work_id, matched_by, doi, arxiv_id, mag_id, title,
                          publication_year, type, source_id, cited_by_count, is_oa, oa_status, oa_url, raw)
VALUES (%(work_id)s, %(provider)s, %(provider_work_id)s, %(matched_by)s, %(doi)s, %(arxiv_id)s, %(mag_id)s,
        %(title)s, %(publication_year)s, %(type)s, %(source_id)s, %(cited_by_count)s, %(is_oa)s,
        %(oa_status)s, %(oa_url)s, %(raw)s)
ON CONFLICT (provider, provider_work_id) DO UPDATE SET
  doi = EXCLUDED.doi, arxiv_id = EXCLUDED.arxiv_id, mag_id = EXCLUDED.mag_id, title = EXCLUDED.title,
  publication_year = EXCLUDED.publication_year, type = EXCLUDED.type, source_id = EXCLUDED.source_id,
  cited_by_count = EXCLUDED.cited_by_count, is_oa = EXCLUDED.is_oa, oa_status = EXCLUDED.oa_status,
  oa_url = EXCLUDED.oa_url, raw = EXCLUDED.raw, fetched_at = now()
  -- work_id and matched_by are kept on refresh
RETURNING id
"""


@dataclass(frozen=True)
class SaveResult:
    work_id: int
    record_id: int
    provider: str
    provider_work_id: str
    matched_by: str
    created_work: bool
    merged_work_ids: list[int]


def save_record(conn: psycopg.Connection, rec: Record) -> SaveResult:
    """Match one record to a work (creating or merging works as needed), upsert it, refresh the work."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (INGEST_LOCK,))
        existing = conn.execute(
            "SELECT id, work_id, matched_by FROM work_records WHERE provider = %s AND provider_work_id = %s",
            (rec.provider, rec.provider_work_id),
        ).fetchone()

        # Which works share an ID with this record, per key (lookups go through records, spec 6.2).
        by_key: dict[str, set[int]] = {}
        for key, column, value in (("doi", "doi", rec.doi), ("arxiv", "arxiv_id", rec.arxiv_id),
                                   ("mag", "mag_id", rec.mag_id)):
            if value is not None:
                rows = conn.execute(
                    f"SELECT DISTINCT work_id FROM work_records WHERE {column} = %s", (value,)
                ).fetchall()
                by_key[key] = {row["work_id"] for row in rows}
        candidates = set().union(*by_key.values())
        if existing:
            candidates.add(existing["work_id"])

        created, merged = False, []
        if candidates:
            work_id = min(candidates)
            merged = sorted(candidates - {work_id})
            if merged:
                conn.execute("SELECT merge_works(%s, %s)", (work_id, merged))
            if existing:
                matched_by = existing["matched_by"]
            else:
                matched_by = next(key for key in ("doi", "arxiv", "mag") if by_key.get(key))
        elif title_match := _match_by_title(conn, rec):
            work_id, matched_by = title_match, "title"
        else:
            work_id = conn.execute("INSERT INTO works (title) VALUES (%s) RETURNING id", (rec.title,)).fetchone()["id"]
            matched_by, created = "new", True

        fields = asdict(rec)
        fields.pop("authors")
        record_id = conn.execute(
            _UPSERT, fields | {"work_id": work_id, "matched_by": matched_by, "raw": Jsonb(rec.raw)}
        ).fetchone()["id"]

        conn.execute("DELETE FROM work_authors WHERE work_record_id = %s", (record_id,))
        if rec.authors:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO work_authors (work_record_id, position, author_id, author_name) "
                    "VALUES (%s, %s, %s, %s)",
                    [(record_id, i, a.author_id, a.name) for i, a in enumerate(rec.authors)],
                )

        conn.execute("SELECT refresh_work(%s)", (work_id,))

    return SaveResult(work_id, record_id, rec.provider, rec.provider_work_id, matched_by, created, merged)


def _match_by_title(conn: psycopg.Connection, rec: Record) -> int | None:
    """Exactly one compatible work with the same normalized title and a year within ±1 (spec 6.3)."""
    if rec.publication_year is None:
        return None
    rows = conn.execute(
        _TITLE_MATCH,
        {"title": rec.title, "year": rec.publication_year, "provider": rec.provider,
         "doi": rec.doi, "arxiv_id": rec.arxiv_id, "mag_id": rec.mag_id},
    ).fetchall()
    return rows[0]["id"] if len(rows) == 1 else None


@dataclass
class FetchOutcome:
    provider: str
    status: str  # found | not_found | rate_limited | error | skipped
    detail: str | None = None
    record: Record | None = None


def _lookups(provider: str, known: dict[str, str]) -> list[dict[str, str]]:
    """Ways to look a paper up at a provider, given the IDs known so far (most specific first)."""
    options = {
        "openalex": (("openalex_id", "openalex"), ("doi", "doi"), ("arxiv_id", "arxiv")),
        "semantic_scholar": (("paper_id", "semantic_scholar"), ("doi", "doi"), ("arxiv_id", "arxiv")),
        "arxiv": (("arxiv_id", "arxiv"),),
    }[provider]
    return [{arg: known[kind]} for arg, kind in options if known.get(kind)]


def _fetch(provider: str, lookup: dict[str, str]) -> Record:
    if provider == "openalex":
        return from_openalex(providers.openalex_work(**lookup))
    if provider == "semantic_scholar":
        return from_semantic_scholar(providers.semantic_scholar_paper(**lookup))
    return from_arxiv(providers.arxiv_entry(lookup["arxiv_id"]))


def fetch_all(identifier: Identifier,
              fetch: Callable[[str, dict[str, str]], Record] = _fetch) -> list[FetchOutcome]:
    """Fetch a paper from every provider, using IDs each answer reveals to query the others.

    For example an arXiv ID finds the OpenAlex work (via its arXiv location) and the Semantic
    Scholar paper; a DOI from one provider is tried at the next.
    """
    known: dict[str, str] = {identifier.kind: identifier.value}
    outcomes = {p: FetchOutcome(p, "skipped", "no identifier this provider can look up") for p in PROVIDERS}
    tried: dict[str, set[tuple[str, str]]] = {p: set() for p in PROVIDERS}
    # A Semantic Scholar ID is only usable at Semantic Scholar, so ask it first to learn DOI/arXiv.
    order = list(PROVIDERS)
    if identifier.kind == "semantic_scholar":
        order = ["semantic_scholar", "openalex", "arxiv"]

    for _ in range(2):  # second round retries with IDs discovered in the first
        progress = False
        for provider in order:
            if outcomes[provider].status in ("found", "rate_limited", "error"):
                continue
            for lookup in _lookups(provider, known):
                key = next(iter(lookup.items()))
                if key in tried[provider]:
                    continue
                tried[provider].add(key)
                try:
                    rec = fetch(provider, lookup)
                except providers.NotFound:
                    outcomes[provider] = FetchOutcome(provider, "not_found", f"not found by {key[0]}={key[1]}")
                    continue
                except providers.RateLimited:
                    outcomes[provider] = FetchOutcome(
                        provider, "rate_limited", "rate limited; set S2_API_KEY for Semantic Scholar"
                    )
                    break
                except (httpx.HTTPError, ValueError) as exc:
                    outcomes[provider] = FetchOutcome(provider, "error", str(exc))
                    break
                outcomes[provider] = FetchOutcome(provider, "found", record=rec)
                for kind, value in (("doi", rec.doi), ("arxiv", rec.arxiv_id)):
                    if value and kind not in known:
                        known[kind] = value
                progress = True
                break
        if not progress:
            break
    return [outcomes[p] for p in PROVIDERS]


def summarize(outcome: FetchOutcome, saved: SaveResult | None) -> dict[str, Any]:
    summary: dict[str, Any] = {"provider": outcome.provider, "status": "saved" if saved else outcome.status}
    if outcome.detail:
        summary["detail"] = outcome.detail
    if saved:
        summary |= {"provider_work_id": saved.provider_work_id, "matched_by": saved.matched_by,
                    "created_work": saved.created_work}
        if saved.merged_work_ids:
            summary["merged_work_ids"] = saved.merged_work_ids
    return summary
