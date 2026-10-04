"""Read-only queries behind the read tools. Each takes a connection and returns plain rows."""

from __future__ import annotations

from typing import Any

import psycopg

from .identifiers import Identifier

MAX_LIMIT = 100

# work_overview plus the quality flags every list shows, so agents can skip retracted papers early.
WORKS = (
    "(SELECT o.*, q.is_retracted, q.preprint_only FROM work_overview o "
    "LEFT JOIN work_quality q ON q.work_id = o.id) AS w"
)
OVERVIEW_COLUMNS = (
    "w.id AS work_id, w.title, w.publication_year, w.type, w.doi, w.arxiv_id, w.mag_id, "
    "w.primary_provider, w.cited_by_count, w.is_oa, w.oa_status, w.oa_url, w.source_id, "
    "w.is_retracted, w.preprint_only"
)

_FIND_BY = {
    "doi": "SELECT work_id FROM work_records WHERE doi = %(v)s",
    "arxiv": "SELECT work_id FROM work_records WHERE arxiv_id = %(v)s",
    "openalex": "SELECT work_id FROM work_records WHERE provider = 'openalex' AND provider_work_id = %(v)s",
    "semantic_scholar": (
        "SELECT work_id FROM work_records WHERE provider = 'semantic_scholar' "
        "AND (provider_work_id = %(v)s OR 'CorpusId:' || (raw -> 'externalIds' ->> 'CorpusId') = %(v)s)"
    ),
    "pmid": (
        "SELECT work_id FROM work_records WHERE (provider = 'openalex' AND "
        "raw -> 'ids' ->> 'pmid' = 'https://pubmed.ncbi.nlm.nih.gov/' || %(v)s) "
        "OR (provider = 'semantic_scholar' AND raw -> 'externalIds' ->> 'PubMed' = %(v)s)"
    ),
    "pmcid": (
        "SELECT work_id FROM work_records WHERE provider = 'semantic_scholar' "
        "AND raw -> 'externalIds' ->> 'PubMedCentral' = substr(%(v)s, 4)"
    ),
}


def _limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


def _like(word: str) -> str:
    escaped = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def overview(conn: psycopg.Connection, work_id: int) -> dict[str, Any] | None:
    return conn.execute(f"SELECT {OVERVIEW_COLUMNS} FROM {WORKS} WHERE w.id = %s", (work_id,)).fetchone()


def find_work(conn: psycopg.Connection, identifier: Identifier) -> dict[str, Any] | None:
    return conn.execute(
        f"SELECT {OVERVIEW_COLUMNS} FROM {WORKS} WHERE w.id IN ({_FIND_BY[identifier.kind]})",
        {"v": identifier.value},
    ).fetchone()


def search_works(conn: psycopg.Connection, query: str, *, year_from: int | None = None,
                 year_to: int | None = None, work_type: str | None = None,
                 open_access_only: bool = False, limit: int = 20) -> list[dict[str, Any]]:
    words = query.split()
    if not words:
        raise ValueError("query must contain at least one word")
    conditions = ["title ILIKE %s"] * len(words)
    params: list[Any] = [_like(w) for w in words]
    if year_from is not None:
        conditions.append("publication_year >= %s")
        params.append(year_from)
    if year_to is not None:
        conditions.append("publication_year <= %s")
        params.append(year_to)
    if work_type:
        conditions.append("type = %s")
        params.append(work_type)
    if open_access_only:
        conditions.append("is_oa")
    params.append(_limit(limit))
    return conn.execute(
        f"SELECT {OVERVIEW_COLUMNS} FROM {WORKS} WHERE {' AND '.join(conditions)} "
        "ORDER BY cited_by_count DESC NULLS LAST, publication_year DESC NULLS LAST, w.id LIMIT %s",
        params,
    ).fetchall()


def get_work(conn: psycopg.Connection, work_id: int) -> dict[str, Any] | None:
    work = overview(conn, work_id)
    if work is None:
        return None
    work["records"] = conn.execute(
        """
        SELECT r.provider, r.provider_work_id, r.matched_by, r.title, r.publication_year, r.type,
               r.doi, r.arxiv_id, r.mag_id, r.source_id, r.cited_by_count, r.is_oa, r.oa_status,
               r.oa_url, r.fetched_at,
               coalesce((SELECT json_agg(json_build_object('position', a.position, 'name', a.author_name,
                                                           'author_id', a.author_id) ORDER BY a.position)
                         FROM work_authors a WHERE a.work_record_id = r.id), '[]'::json) AS authors
        FROM work_records r JOIN providers p ON p.id = r.provider
        WHERE r.work_id = %s
        ORDER BY p.priority, r.fetched_at DESC
        """,
        (work_id,),
    ).fetchall()
    # Abstracts follow their own preference: arXiv is the authors' text, while OpenAlex's rebuilt abstracts are
    # sometimes front matter (e.g. author lists) rather than the abstract.
    abstract = conn.execute(
        """
        SELECT abstract, provider FROM work_records
        WHERE work_id = %s AND abstract IS NOT NULL
        ORDER BY array_position(ARRAY['arxiv', 'semantic_scholar', 'openalex'], provider), fetched_at DESC
        LIMIT 1
        """,
        (work_id,),
    ).fetchone()
    work["abstract"] = abstract["abstract"] if abstract else None
    work["abstract_source"] = abstract["provider"] if abstract else None
    work["quality"] = conn.execute(
        "SELECT providers, is_retracted, preprint_only, year_spread, title_matched FROM work_quality "
        "WHERE work_id = %s",
        (work_id,),
    ).fetchone()
    work["claims_from_this_paper"] = conn.execute(
        "SELECT id AS claim_id, text, verdict, confidence FROM claim_status WHERE source_work_id = %s ORDER BY id",
        (work_id,),
    ).fetchall()
    work["claims"] = conn.execute(
        """
        SELECT cs.id AS claim_id, cs.text, e.stance, e.note,
               a.id = cs.assessment_id AS in_current_assessment,
               cs.verdict AS current_verdict, cs.confidence AS current_confidence
        FROM claim_evidence e
        JOIN claim_assessments a ON a.id = e.assessment_id
        JOIN claim_status cs ON cs.id = a.claim_id
        WHERE e.work_id = %s
        ORDER BY a.assessed_at DESC
        """,
        (work_id,),
    ).fetchall()
    return work


def top_cited(conn: psycopg.Connection, *, year: int | None = None, work_type: str | None = None,
              source_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    conditions, params = ["cited_by_count IS NOT NULL"], []
    if year is not None:
        conditions.append("publication_year = %s")
        params.append(year)
    if work_type:
        conditions.append("type = %s")
        params.append(work_type)
    if source_id:
        conditions.append("w.id IN (SELECT work_id FROM work_records WHERE source_id = %s)")
        params.append(source_id)
    params.append(_limit(limit))
    return conn.execute(
        f"SELECT {OVERVIEW_COLUMNS} FROM {WORKS} WHERE {' AND '.join(conditions)} "
        "ORDER BY cited_by_count DESC, w.id LIMIT %s",
        params,
    ).fetchall()


def works_by_author(conn: psycopg.Connection, provider: str, author_id: str,
                    limit: int = 50) -> list[dict[str, Any]]:
    return conn.execute(
        f"""
        SELECT {OVERVIEW_COLUMNS}, a.author_name, a.position AS author_position
        FROM work_authors a
        JOIN work_records r ON r.id = a.work_record_id
        JOIN {WORKS} ON w.id = r.work_id
        WHERE r.provider = %s AND a.author_id = %s
        ORDER BY w.publication_year DESC NULLS LAST, w.id
        LIMIT %s
        """,
        (provider, author_id, _limit(limit)),
    ).fetchall()


def review_queue(conn: psycopg.Connection, limit: int = 50) -> list[dict[str, Any]]:
    return conn.execute(
        """
        (SELECT 'title_match' AS reason, r.work_id, w.title,
                r.provider || ' record titled: ' || r.title AS detail
         FROM work_records r JOIN works w ON w.id = r.work_id
         WHERE r.matched_by = 'title')
        UNION ALL
        (SELECT 'year_disagreement', r.work_id, w.title,
                string_agg(r.provider || ': ' || r.publication_year, ', ' ORDER BY r.provider)
         FROM work_records r JOIN works w ON w.id = r.work_id
         GROUP BY r.work_id, w.title
         HAVING max(r.publication_year) - min(r.publication_year) > 1)
        ORDER BY work_id
        LIMIT %s
        """,
        (_limit(limit),),
    ).fetchall()


def run_sql(conn: psycopg.Connection, sql: str, max_rows: int = 100) -> dict[str, Any]:
    """One read-only statement. Multiple statements are rejected (prepared statements allow one)."""
    max_rows = _limit(max_rows)
    with conn.cursor() as cur:
        cur.execute("SET LOCAL statement_timeout = '15s'")
        cur.execute(sql, prepare=True)
        if cur.description is None:
            return {"columns": [], "rows": [], "truncated": False}
        rows = cur.fetchmany(max_rows + 1)
        return {
            "columns": [column.name for column in cur.description],
            "rows": rows[:max_rows],
            "truncated": len(rows) > max_rows,
        }
