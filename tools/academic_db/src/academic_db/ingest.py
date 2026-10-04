"""Save normalized records: match them to a work, upsert, refresh (spec sections 6 and 7)."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from types import ModuleType
from typing import Any

import httpx
import psycopg
from psycopg.types.json import Jsonb

from . import providers
from .identifiers import Identifier
from .normalize import PROVIDERS, Record, from_arxiv_feed, from_openalex, from_semantic_scholar

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
                          publication_year, type, source_id, cited_by_count, is_oa, oa_status, oa_url,
                          abstract, raw)
VALUES (%(work_id)s, %(provider)s, %(provider_work_id)s, %(matched_by)s, %(doi)s, %(arxiv_id)s, %(mag_id)s,
        %(title)s, %(publication_year)s, %(type)s, %(source_id)s, %(cited_by_count)s, %(is_oa)s,
        %(oa_status)s, %(oa_url)s, %(abstract)s, %(raw)s)
ON CONFLICT (provider, provider_work_id) DO UPDATE SET
  doi = EXCLUDED.doi, arxiv_id = EXCLUDED.arxiv_id, mag_id = EXCLUDED.mag_id, title = EXCLUDED.title,
  publication_year = EXCLUDED.publication_year, type = EXCLUDED.type, source_id = EXCLUDED.source_id,
  cited_by_count = EXCLUDED.cited_by_count, is_oa = EXCLUDED.is_oa, oa_status = EXCLUDED.oa_status,
  oa_url = EXCLUDED.oa_url, abstract = EXCLUDED.abstract, raw = EXCLUDED.raw, fetched_at = now()
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


# Which provider's answer to the requested identifier defines the paper, most reliable first.
ANCHORS = {
    "arxiv": ("arxiv", "semantic_scholar", "openalex"),
    "doi": ("openalex", "semantic_scholar"),
    "openalex": ("openalex",),
    "semantic_scholar": ("semantic_scholar",),
    "pmid": ("openalex", "semantic_scholar"),
    "pmcid": ("semantic_scholar",),  # OpenAlex does not expose PMCIDs
}
LEARNED = ("doi", "arxiv", "pmid", "pmcid")  # IDs an accepted record teaches us about the paper
_WORD = re.compile(r"[a-z0-9]+")


def same_title(a: str, b: str) -> bool:
    """Titles describe the same paper: 80% of the shorter title's words appear in the other.

    Tolerates case, punctuation and an extra subtitle; rejects provider records whose metadata
    belongs to another paper (OpenAlex sometimes merges unrelated records).
    """
    words_a, words_b = set(_WORD.findall(a.lower())), set(_WORD.findall(b.lower()))
    if not words_a or not words_b:
        return False
    return len(words_a & words_b) / min(len(words_a), len(words_b)) >= 0.8


@dataclass
class PaperFetch:
    """One requested paper: the IDs known for it so far and what each provider returned."""

    identifier: Identifier
    known: dict[str, str]
    records: dict[str, Record] = field(default_factory=dict)  # accepted records, saved later
    status: dict[str, str] = field(default_factory=dict)  # found | not_found | mismatch | rate_limited | error | skipped
    detail: dict[str, str] = field(default_factory=dict)
    anchor: Record | None = None

    def keys_for(self, provider: str) -> list[tuple[str, str]]:
        """IDs this provider can look the paper up by, most specific first."""
        usable = {
            "openalex": ("openalex", "doi", "pmid", "arxiv"),
            "semantic_scholar": ("semantic_scholar", "doi", "pmid", "pmcid", "arxiv"),
            "arxiv": ("arxiv",),
        }[provider]
        return [(kind, self.known[kind]) for kind in usable if self.known.get(kind)]

    def matches(self, rec: Record) -> bool:
        return (
            (rec.provider in ("openalex", "semantic_scholar") and self.known.get(rec.provider) == rec.provider_work_id)
            or (rec.doi is not None and self.known.get("doi") == rec.doi)
            or (rec.arxiv_id is not None and self.known.get("arxiv") == rec.arxiv_id)
            or (rec.pmid is not None and self.known.get("pmid") == rec.pmid)
            or (rec.pmcid is not None and self.known.get("pmcid") == rec.pmcid)
        )

    def accept(self, provider: str, candidates: list[Record]) -> bool:
        """Keep the candidate that describes the anchored paper; returns whether one was kept."""
        if not candidates:
            self.status[provider] = "not_found"
            return False
        if self.anchor is None:
            self.status[provider] = "found"  # provisional until an anchor is chosen
            self.records[provider] = candidates[0]
            return True
        match = next((r for r in candidates if same_title(r.title, self.anchor.title)), None)
        if match is None:
            self.status[provider] = "mismatch"
            self.detail[provider] = f"returned a different paper: {candidates[0].title[:120]!r}"
            return False
        self.status[provider] = "found"
        self.records[provider] = match
        for kind, value in zip(LEARNED, (match.doi, match.arxiv_id, match.pmid, match.pmcid), strict=True):
            if value and kind not in self.known:
                self.known[kind] = value
        return True

    def choose_anchor(self) -> None:
        """After the first round: the most reliable answer to the requested ID defines the paper."""
        found = dict(self.records)
        self.records.clear()
        anchor_provider = next((p for p in ANCHORS[self.identifier.kind] if p in found), None)
        if anchor_provider is None:
            return
        self.anchor = found[anchor_provider]
        for provider, rec in found.items():
            self.accept(provider, [rec])


def _normalize_all(normalize: Callable[[Any], Record], payloads: Iterable[Any]) -> list[Record]:
    records = []
    for payload in payloads:
        try:
            records.append(normalize(payload))
        except ValueError:  # e.g. a record without a title
            continue
    return records


def _fetch_batch(provider: str, wanted: dict[str, set[str]],
                 client: ModuleType) -> tuple[list[Record], dict[tuple[str, str], Record]]:
    """Records from one batched request, plus (kind, value) -> record where the provider answers per key."""
    if provider == "openalex":
        works = client.openalex_works(openalex_ids=sorted(wanted["openalex"]), dois=sorted(wanted["doi"]),
                                      arxiv_ids=sorted(wanted["arxiv"]), pmids=sorted(wanted["pmid"]))
        return _normalize_all(from_openalex, works), {}
    if provider == "semantic_scholar":
        # The batch endpoint answers in request order, so each answer maps back to the key that asked for it.
        asked = [(kind, v) for kind in ("semantic_scholar", "doi", "pmid", "pmcid", "arxiv") for v in sorted(wanted[kind])]
        prefix = {"semantic_scholar": "", "doi": "DOI:", "pmid": "PMID:", "pmcid": "PMCID:", "arxiv": "ARXIV:"}
        # Semantic Scholar takes PMCIDs as bare digits.
        answers = client.semantic_scholar_papers(
            [prefix[kind] + (value.removeprefix("PMC") if kind == "pmcid" else value) for kind, value in asked]
        )
        by_key = {}
        for key, payload in zip(asked, answers, strict=True):
            records = _normalize_all(from_semantic_scholar, [payload] if payload else [])
            if records:
                by_key[key] = records[0]
        return list(by_key.values()), by_key
    return [rec for feed in client.arxiv_feeds(sorted(wanted["arxiv"])) for rec in from_arxiv_feed(feed)], {}


def _run_round(papers: list[PaperFetch], tried: dict[str, set[tuple[str, str]]], client: ModuleType,
               *, only_requested: bool) -> bool:
    """One batched request per provider; returns whether any record was accepted."""
    progress = False
    for provider in PROVIDERS:
        wanted: dict[str, set[str]] = {k: set() for k in ("openalex", "semantic_scholar", "doi", "arxiv", "pmid", "pmcid")}
        asking: list[tuple[PaperFetch, list[tuple[str, str]]]] = []
        for paper in papers:
            if provider in paper.records or paper.status.get(provider) in ("rate_limited", "error"):
                continue
            keys = paper.keys_for(provider)
            if only_requested:
                keys = [k for k in keys if k == (paper.identifier.kind, paper.identifier.value)]
            new_keys = [key for key in keys if key not in tried[provider]]
            if new_keys:
                asking.append((paper, new_keys))
                for kind, value in new_keys:
                    wanted[kind].add(value)
                    tried[provider].add((kind, value))
        if not asking:
            continue
        try:
            found, by_key = _fetch_batch(provider, wanted, client)
        except (providers.RateLimited, httpx.HTTPError, ValueError) as exc:
            status = "rate_limited" if isinstance(exc, providers.RateLimited) else "error"
            for paper, _ in asking:
                paper.status[provider] = status
                paper.detail[provider] = f"{type(exc).__name__}: {exc}"[:200]
            continue
        for paper, keys in asking:
            candidates = [by_key[key] for key in keys if key in by_key] or [r for r in found if paper.matches(r)]
            progress |= paper.accept(provider, candidates)
    return progress


def fetch_many(identifiers: list[Identifier], client: ModuleType = providers) -> list[PaperFetch]:
    """Fetch papers from every provider with one batched request per provider per round.

    Round one asks every provider for the requested identifier only, and the most reliable answer
    becomes the paper's anchor. Later rounds use IDs revealed by accepted records (e.g. a DOI from
    Semantic Scholar finds the OpenAlex work), and accept only records whose title matches the anchor.
    """
    papers = [PaperFetch(ident, {ident.kind: ident.value}) for ident in identifiers]
    tried: dict[str, set[tuple[str, str]]] = {p: set() for p in PROVIDERS}

    _run_round(papers, tried, client, only_requested=True)
    for paper in papers:
        paper.choose_anchor()
    for _ in range(2):
        if not _run_round([p for p in papers if p.anchor], tried, client, only_requested=False):
            break

    for paper in papers:
        for provider in PROVIDERS:
            paper.status.setdefault(provider, "skipped")
    return papers


@dataclass(frozen=True)
class Imported:
    identifier: Identifier
    work_id: int | None
    providers: dict[str, str]  # provider -> saved | not_found | mismatch | rate_limited | error | skipped
    details: dict[str, str]  # why a provider was not saved, when known


def save_fetched(conn: psycopg.Connection, papers: list[PaperFetch]) -> list[Imported]:
    """Save every fetched record, then resolve each paper's final work_id (saves can merge works)."""
    saved: set[tuple[str, str]] = set()
    for paper in papers:
        for rec in paper.records.values():
            key = (rec.provider, rec.provider_work_id)
            if key not in saved:
                save_record(conn, rec)
                saved.add(key)

    results = []
    for paper in papers:
        work_id = None
        if paper.records:
            rec = next(iter(paper.records.values()))
            work_id = conn.execute(
                "SELECT work_id FROM work_records WHERE provider = %s AND provider_work_id = %s",
                (rec.provider, rec.provider_work_id),
            ).fetchone()["work_id"]
        statuses = {p: "saved" if p in paper.records else paper.status[p] for p in PROVIDERS}
        details = {p: d for p, d in paper.detail.items() if p not in paper.records}
        results.append(Imported(paper.identifier, work_id, statuses, details))
    return results
