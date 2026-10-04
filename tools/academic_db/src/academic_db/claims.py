"""Claims and the Verifier's confidence assessments."""

from __future__ import annotations

from typing import Any, Literal

import psycopg

Verdict = Literal["supported", "contradicted", "inconclusive"]
Stance = Literal["supports", "contradicts"]
VERDICTS = ("supported", "contradicted", "inconclusive")
STANCES = ("supports", "contradicts")

CLAIM_COLUMNS = (
    "id AS claim_id, text, created_by, created_at, verdict, confidence, rationale, "
    "assessed_by, assessed_at, assessment_count, source_work_id, source_quote"
)

# Trigram similarity at which two claims are probably the same statement in different words.
SIMILAR_THRESHOLD = 0.6


def similar_claims(conn: psycopg.Connection, text: str, limit: int = 5) -> list[dict[str, Any]]:
    with conn.transaction():
        conn.execute("SELECT set_config('pg_trgm.similarity_threshold', %s, true)", (str(SIMILAR_THRESHOLD),))
        return conn.execute(
            "SELECT id AS claim_id, text, round(similarity(text, %(t)s)::numeric, 2) AS similarity "
            "FROM claims WHERE text %% %(t)s ORDER BY similarity(text, %(t)s) DESC LIMIT %(n)s",
            {"t": text, "n": limit},
        ).fetchall()


def add_claim(conn: psycopg.Connection, text: str, created_by: str, *, source_work_id: int | None = None,
              source_quote: str | None = None, allow_similar: bool = False) -> dict[str, Any]:
    """Record a claim with the paper and passage it came from.

    Identical text returns the existing claim. Near-duplicates are returned instead of creating a new
    claim unless allow_similar is set, so parallel scouts do not make verifiers check one claim twice.
    """
    text = text.strip()
    if not text:
        raise ValueError("claim text is empty")
    quote = (source_quote or "").strip() or None
    with conn.transaction():
        existing = conn.execute(
            "SELECT id FROM claims WHERE md5(lower(btrim(text))) = md5(lower(btrim(%s)))", (text,)
        ).fetchone()
        if existing:
            return {"claim_id": existing["id"], "created": False}
        if source_work_id is not None and conn.execute(
                "SELECT 1 FROM works WHERE id = %s", (source_work_id,)).fetchone() is None:
            raise ValueError(f"source work {source_work_id} is not in the database; import it first")
        if not allow_similar and (similar := similar_claims(conn, text)):
            return {"claim_id": None, "created": False, "similar_claims": similar,
                    "hint": "Reuse one of these claim_ids, or call again with allow_similar=true if it is different"}
        row = conn.execute(
            "INSERT INTO claims (text, created_by, source_work_id, source_quote) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT ((md5(lower(btrim(text))))) DO NOTHING RETURNING id",
            (text, created_by, source_work_id, quote),
        ).fetchone()
        if row is None:  # inserted concurrently by another agent
            row = conn.execute(
                "SELECT id FROM claims WHERE md5(lower(btrim(text))) = md5(lower(btrim(%s)))", (text,)
            ).fetchone()
            return {"claim_id": row["id"], "created": False}
    return {"claim_id": row["id"], "created": True}


def assess_claim(conn: psycopg.Connection, *, claim_id: int, confidence: float, verdict: str,
                 rationale: str, evidence: list[dict[str, Any]], assessed_by: str) -> dict[str, Any]:
    """Add a new assessment (earlier ones are kept) with the papers it cites."""
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    if not rationale.strip():
        raise ValueError("rationale is required")

    items = []
    for item in evidence:
        stance = item.get("stance")
        if stance not in STANCES:
            raise ValueError(f"evidence stance must be one of {', '.join(STANCES)}")
        items.append((int(item["work_id"]), stance, (item.get("note") or "").strip() or None))
    work_ids = [work_id for work_id, _, _ in items]
    if len(set(work_ids)) != len(work_ids):
        raise ValueError("each work may appear only once in evidence")
    stances = {stance for _, stance, _ in items}
    if verdict == "supported" and "supports" not in stances:
        raise ValueError("a 'supported' verdict needs at least one evidence item with stance 'supports'")
    if verdict == "contradicted" and "contradicts" not in stances:
        raise ValueError("a 'contradicted' verdict needs at least one evidence item with stance 'contradicts'")

    with conn.transaction():
        if conn.execute("SELECT 1 FROM claims WHERE id = %s", (claim_id,)).fetchone() is None:
            raise ValueError(f"claim {claim_id} does not exist")
        found = {row["id"] for row in conn.execute("SELECT id FROM works WHERE id = ANY (%s)", (work_ids,))}
        missing = sorted(set(work_ids) - found)
        if missing:
            raise ValueError(f"works not in the database: {missing}. Save them first (import_work / save_work).")
        assessment_id = conn.execute(
            "INSERT INTO claim_assessments (claim_id, confidence, verdict, rationale, assessed_by) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (claim_id, confidence, verdict, rationale.strip(), assessed_by),
        ).fetchone()["id"]
        if items:
            with conn.cursor() as cur:
                cur.executemany(
                    "INSERT INTO claim_evidence (assessment_id, work_id, stance, note) VALUES (%s, %s, %s, %s)",
                    [(assessment_id, work_id, stance, note) for work_id, stance, note in items],
                )
    return {"assessment_id": assessment_id, "claim_id": claim_id, "verdict": verdict, "confidence": confidence}


def get_claim(conn: psycopg.Connection, claim_id: int) -> dict[str, Any] | None:
    claim = conn.execute(f"SELECT {CLAIM_COLUMNS} FROM claim_status WHERE id = %s", (claim_id,)).fetchone()
    if claim is None:
        return None
    claim["source"] = conn.execute(
        "SELECT id AS work_id, title, publication_year, doi, arxiv_id FROM works WHERE id = %s",
        (claim["source_work_id"],),
    ).fetchone() if claim["source_work_id"] else None
    claim["assessments"] = conn.execute(
        """
        SELECT a.id AS assessment_id, a.verdict, a.confidence, a.rationale, a.assessed_by, a.assessed_at,
               coalesce((SELECT json_agg(json_build_object('work_id', e.work_id, 'title', w.title,
                                                           'publication_year', w.publication_year,
                                                           'doi', w.doi, 'arxiv_id', w.arxiv_id,
                                                           'stance', e.stance, 'note', e.note)
                                         ORDER BY e.stance DESC, e.work_id)
                         FROM claim_evidence e JOIN works w ON w.id = e.work_id
                         WHERE e.assessment_id = a.id), '[]'::json) AS evidence
        FROM claim_assessments a
        WHERE a.claim_id = %s
        ORDER BY a.assessed_at DESC, a.id DESC
        """,
        (claim_id,),
    ).fetchall()
    return claim


def list_claims(conn: psycopg.Connection, *, query: str | None = None, verdict: str | None = None,
                min_confidence: float | None = None, limit: int = 50) -> list[dict[str, Any]]:
    conditions, params = ["true"], []
    for word in (query or "").split():
        conditions.append("text ILIKE %s")
        params.append("%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if verdict == "unassessed":
        conditions.append("verdict IS NULL")
    elif verdict:
        if verdict not in VERDICTS:
            raise ValueError(f"verdict must be one of {', '.join(VERDICTS)} or 'unassessed'")
        conditions.append("verdict = %s")
        params.append(verdict)
    if min_confidence is not None:
        conditions.append("confidence >= %s")
        params.append(min_confidence)
    params.append(max(1, min(int(limit), 100)))
    return conn.execute(
        f"SELECT {CLAIM_COLUMNS} FROM claim_status WHERE {' AND '.join(conditions)} "
        "ORDER BY coalesce(assessed_at, created_at) DESC, id DESC LIMIT %s",
        params,
    ).fetchall()
