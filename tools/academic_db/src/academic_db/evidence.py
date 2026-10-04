"""Counter-evidence search for verifiers: what citing papers say, and full-text passages."""

from __future__ import annotations

from typing import Any

import httpx
import psycopg

from . import providers
from .identifiers import normalize_arxiv_id, normalize_doi, strip_openalex

MAX_CONTEXT_CHARS = 400
MAX_PASSAGE_CHARS = 1500
NO_KEY_HINT = "Semantic Scholar is rate-limiting anonymous requests; set S2_API_KEY in the repository .env"


def _work_ids(conn: psycopg.Connection, work_id: int) -> dict[str, str | None]:
    row = conn.execute(
        """
        SELECT w.doi, w.arxiv_id,
               max(r.provider_work_id) FILTER (WHERE r.provider = 'semantic_scholar') AS s2_id,
               max(r.provider_work_id) FILTER (WHERE r.provider = 'openalex') AS openalex_id
        FROM works w LEFT JOIN work_records r ON r.work_id = w.id
        WHERE w.id = %s
        GROUP BY w.id
        """,
        (work_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"work {work_id} does not exist")
    return row


def _stored(conn: psycopg.Connection, dois: list[str], arxiv_ids: list[str]) -> dict[str, int]:
    """Map DOIs / arXiv IDs that are already in the database to their work_id."""
    rows = conn.execute(
        "SELECT doi, arxiv_id, work_id FROM work_records WHERE doi = ANY (%s) OR arxiv_id = ANY (%s)",
        (dois, arxiv_ids),
    ).fetchall()
    stored = {}
    for row in rows:
        for value in (row["doi"], row["arxiv_id"]):
            if value:
                stored[value] = row["work_id"]
    return stored


def _identifier(doi: str | None, arxiv_id: str | None, fallback: str | None) -> str | None:
    return doi or (f"arXiv:{arxiv_id}" if arxiv_id else None) or fallback


def citing_statements(conn: psycopg.Connection, work_id: int, limit: int = 30) -> dict[str, Any]:
    """Papers citing a work, with the sentences where they mention it (Semantic Scholar), most informative first.

    Falls back to OpenAlex's most cited citing works (without sentences) when Semantic Scholar has none.
    """
    ids = _work_ids(conn, work_id)
    s2_key = ids["s2_id"] or (f"DOI:{ids['doi']}" if ids["doi"] else None) or (
        f"ARXIV:{ids['arxiv_id']}" if ids["arxiv_id"] else None)

    citing: list[dict[str, Any]] = []
    note = None
    if s2_key:
        try:
            for row in providers.semantic_scholar_citations(s2_key, min(1000, limit * 4)):
                paper = row.get("citingPaper") or {}
                external = paper.get("externalIds") or {}
                contexts = [c[:MAX_CONTEXT_CHARS] for c in (row.get("contexts") or [])[:3]]
                doi, arxiv_id = normalize_doi(external.get("DOI")), normalize_arxiv_id(external.get("ArXiv"))
                citing.append({
                    "identifier": _identifier(doi, arxiv_id, paper.get("paperId")),
                    "title": paper.get("title"), "year": paper.get("year"),
                    "cited_by_count": paper.get("citationCount"),
                    "intents": row.get("intents") or [], "influential": bool(row.get("isInfluential")),
                    "contexts": contexts, "_doi": doi, "_arxiv": arxiv_id,
                })
        except providers.RateLimited:
            note = NO_KEY_HINT
        except httpx.HTTPError as exc:
            note = f"Semantic Scholar citations failed: {exc}"

    source = "semantic_scholar"
    if not any(c["contexts"] for c in citing):
        if citing and note is None:
            note = "Semantic Scholar returned citing papers without citation sentences (usually needs S2_API_KEY)"
        # Without sentences, OpenAlex's most cited citing works are a better starting point.
        if ids["openalex_id"]:
            source, citing = "openalex", []
            for work in providers.openalex_citing_works(ids["openalex_id"], limit):
                doi = normalize_doi(work.get("doi"))
                citing.append({
                    "identifier": _identifier(doi, None, strip_openalex(work.get("id"))),
                    "title": work.get("title"), "year": work.get("publication_year"),
                    "cited_by_count": work.get("cited_by_count"), "intents": [], "influential": False,
                    "contexts": [], "_doi": doi, "_arxiv": None,
                })

    # Sentences first; among those, results and influential citations first.
    citing.sort(key=lambda c: (not c["contexts"], "result" not in c["intents"], not c["influential"],
                               -(c["cited_by_count"] or 0)))
    citing = citing[:limit]
    stored = _stored(conn, [c["_doi"] for c in citing if c["_doi"]], [c["_arxiv"] for c in citing if c["_arxiv"]])
    for c in citing:
        doi, arxiv_id = c.pop("_doi"), c.pop("_arxiv")
        c["stored_work_id"] = stored.get(doi) or stored.get(arxiv_id)
    return {"work_id": work_id, "source": source, "count": len(citing),
            "with_sentences": sum(1 for c in citing if c["contexts"]), "note": note, "citing": citing}


def search_passages(query: str, limit: int = 10) -> dict[str, Any]:
    """Full-text passages (title, abstract or body) that best match query, from Semantic Scholar."""
    try:
        rows = providers.semantic_scholar_snippets(query, limit)
    except providers.RateLimited as exc:
        raise ValueError(NO_KEY_HINT) from exc
    passages = []
    for row in rows:
        snippet, paper = row.get("snippet") or {}, row.get("paper") or {}
        corpus_id = paper.get("corpusId")
        passages.append({
            "identifier": f"CorpusId:{corpus_id}" if corpus_id else None,
            "title": paper.get("title"),
            "section": snippet.get("section") or snippet.get("snippetKind"),
            "text": (snippet.get("text") or "")[:MAX_PASSAGE_CHARS],
            "score": row.get("score"),
        })
    return {"query": query, "count": len(passages), "passages": passages}
