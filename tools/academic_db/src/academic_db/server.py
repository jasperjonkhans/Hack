"""MCP server exposing the academic works database to agents.

Run with `academic-db-mcp` (stdio). Each Omnigent agent allow-lists the tools it may use.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import claims, config, db, ingest, queries
from .identifiers import parse_identifier
from .normalize import from_payload

INSTRUCTIONS = """\
Academic papers merged from OpenAlex (preferred), Semantic Scholar and arXiv, plus claims that the
team verifies against those papers.

- Each paper has one work_id. Its citations, open-access status and source come from the preferred
  provider; get_work shows every provider's values side by side when they disagree.
- Identifiers: DOI 10.1038/nature14539, arXiv 1706.03762, OpenAlex W2626778328, Semantic Scholar
  40-hex paper ID. URLs to any of these also work.
- Author and source IDs belong to one provider: pass the provider together with the ID.
- cited_by_count is null when no provider reports citations (arXiv-only papers), not zero.
- Papers must be in the database before a claim assessment can cite them: use import_work first.
"""

mcp = FastMCP("academic_db", instructions=INSTRUCTIONS, log_level="WARNING")
logging.getLogger("httpx").setLevel(logging.WARNING)

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)

WorkId = Annotated[int, Field(description="work_id from find_work, search_works or import_work")]
Limit = Annotated[int, Field(ge=1, le=100, description="Maximum rows to return (1-100)")]
WorkType = Annotated[
    str | None,
    Field(description="OpenAlex type, e.g. article, preprint, review, conference-paper, book-chapter, dataset"),
]


class EvidenceItem(BaseModel):
    work_id: int = Field(description="A work in the database that this assessment cites")
    stance: Literal["supports", "contradicts"] = Field(description="Whether the paper supports or contradicts the claim")
    note: str | None = Field(default=None, description="Where/how it does so, e.g. 'Table 2: BLEU 28.4 on WMT14 En-De'")


# ---------------------------------------------------------------- read: papers


@mcp.tool(annotations=READ)
def find_work(
    identifier: Annotated[str, Field(description="DOI, arXiv ID, OpenAlex W-ID, Semantic Scholar paper ID, or a URL to one")],
) -> dict[str, Any]:
    """Look up a paper already in the database by any identifier. Returns found=false if it is not stored."""
    ident = parse_identifier(identifier)
    with db.reader() as conn:
        work = queries.find_work(conn, ident)
    return db.jsonable({"found": work is not None, "work": work})


@mcp.tool(annotations=READ)
def search_works(
    query: Annotated[str, Field(description="Words that must all appear in the title (case-insensitive)")],
    year_from: Annotated[int | None, Field(description="Earliest publication year")] = None,
    year_to: Annotated[int | None, Field(description="Latest publication year")] = None,
    type: WorkType = None,
    open_access_only: Annotated[bool, Field(description="Only open-access papers")] = False,
    limit: Limit = 20,
) -> dict[str, Any]:
    """Search stored papers by title words, most cited first."""
    with db.reader() as conn:
        works = queries.search_works(conn, query, year_from=year_from, year_to=year_to, work_type=type,
                                     open_access_only=open_access_only, limit=limit)
    return db.jsonable({"count": len(works), "works": works})


@mcp.tool(annotations=READ)
def get_work(work_id: WorkId) -> dict[str, Any]:
    """Everything about one paper: canonical values, each provider's record and authors, and claims citing it."""
    with db.reader() as conn:
        work = queries.get_work(conn, work_id)
    if work is None:
        raise ValueError(f"work {work_id} does not exist")
    return db.jsonable(work)


@mcp.tool(annotations=READ)
def top_cited(
    year: Annotated[int | None, Field(description="Publication year")] = None,
    type: WorkType = None,
    source_id: Annotated[str | None, Field(description="Journal/venue ID: OpenAlex S-ID or Semantic Scholar venue UUID")] = None,
    limit: Limit = 10,
) -> dict[str, Any]:
    """Most cited stored papers, optionally for one year, type or journal."""
    with db.reader() as conn:
        works = queries.top_cited(conn, year=year, work_type=type, source_id=source_id, limit=limit)
    return db.jsonable({"count": len(works), "works": works})


@mcp.tool(annotations=READ)
def works_by_author(
    provider: Annotated[Literal["openalex", "semantic_scholar"], Field(description="Provider the author ID belongs to")],
    author_id: Annotated[str, Field(description="OpenAlex A-ID (A5001226970) or Semantic Scholar author ID (40348417)")],
    limit: Limit = 50,
) -> dict[str, Any]:
    """Stored papers by one author, newest first. Author IDs are provider-specific."""
    with db.reader() as conn:
        works = queries.works_by_author(conn, provider, author_id, limit)
    return db.jsonable({"count": len(works), "works": works})


@mcp.tool(annotations=READ)
def review_queue(limit: Limit = 50) -> dict[str, Any]:
    """Data-quality issues: papers linked only by title match, and providers disagreeing on the year by >1."""
    with db.reader() as conn:
        items = queries.review_queue(conn, limit)
    return db.jsonable({"count": len(items), "items": items})


@mcp.tool(annotations=READ)
def run_sql(
    sql: Annotated[str, Field(description="One read-only SQL statement (PostgreSQL)")],
    max_rows: Limit = 100,
) -> dict[str, Any]:
    """Run one read-only SQL query against the database (15 s limit).

    Tables: works, work_records, work_authors, providers, claims, claim_assessments, claim_evidence.
    Views: work_overview (one row per paper), claim_status (claims with their newest assessment).
    """
    with db.reader() as conn:
        result = queries.run_sql(conn, sql, max_rows)
    return db.jsonable(result)


# ---------------------------------------------------------------- write: papers


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
def import_work(
    identifier: Annotated[str, Field(description="DOI, arXiv ID, OpenAlex W-ID, Semantic Scholar paper ID, or a URL to one")],
) -> dict[str, Any]:
    """Fetch a paper from OpenAlex, Semantic Scholar and arXiv and save it (or refresh it if already stored).

    Records from different providers are merged into one work. Returns the work and what each provider returned.
    """
    ident = parse_identifier(identifier)
    outcomes = ingest.fetch_all(ident)
    saved: dict[str, ingest.SaveResult] = {}
    with db.writer() as conn:
        for outcome in outcomes:
            if outcome.record is not None:
                saved[outcome.provider] = ingest.save_record(conn, outcome.record)
    if not saved:
        details = "; ".join(f"{o.provider}: {o.status} ({o.detail})" for o in outcomes)
        raise ValueError(f"No provider returned {identifier!r}: {details}")
    work_id = next(iter(saved.values())).work_id
    with db.reader() as conn:
        work = queries.overview(conn, work_id)
    return db.jsonable({
        "work_id": work_id,
        "work": work,
        "providers": [ingest.summarize(o, saved.get(o.provider)) for o in outcomes],
    })


@mcp.tool(annotations=WRITE)
def save_work(
    provider: Annotated[Literal["openalex", "semantic_scholar", "arxiv"], Field(description="Where the payload came from")],
    payload: Annotated[
        dict[str, Any] | str,
        Field(description="One paper as the provider returned it: an OpenAlex work object, a Semantic Scholar "
                          "paper object (with externalIds, openAccessPdf, authors), or arXiv Atom <entry> XML"),
    ],
) -> dict[str, Any]:
    """Save one paper record you already have (e.g. from a search tool) without refetching it."""
    record = from_payload(provider, payload)
    with db.writer() as conn:
        result = ingest.save_record(conn, record)
    return db.jsonable({
        "work_id": result.work_id,
        "matched_by": result.matched_by,
        "created_work": result.created_work,
        "merged_work_ids": result.merged_work_ids,
    })


# ---------------------------------------------------------------- claims


@mcp.tool(annotations=WRITE)
def add_claim(
    text: Annotated[str, Field(description="One specific, checkable statement, e.g. 'Transformers outperform RNNs on WMT14 En-De'")],
) -> dict[str, Any]:
    """Record a claim to be verified. Returns the existing claim_id if the same text was already recorded."""
    with db.writer() as conn:
        return claims.add_claim(conn, text, config.agent_name())


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
def assess_claim(
    claim_id: Annotated[int, Field(description="Claim to assess")],
    confidence: Annotated[float, Field(ge=0, le=1, description="Confidence in the verdict, 0 to 1")],
    verdict: Annotated[Literal["supported", "contradicted", "inconclusive"], Field(description="Verdict within the tested conditions")],
    rationale: Annotated[str, Field(description="Why: what the evidence shows, its strength and limitations")],
    evidence: Annotated[
        list[EvidenceItem],
        Field(description="Papers cited. 'supported' needs a supporting paper, 'contradicted' a contradicting one"),
    ],
) -> dict[str, Any]:
    """Record a confidence-scored verdict on a claim, citing papers in the database. Earlier verdicts are kept."""
    with db.writer() as conn:
        result = claims.assess_claim(
            conn, claim_id=claim_id, confidence=confidence, verdict=verdict, rationale=rationale,
            evidence=[item.model_dump() for item in evidence], assessed_by=config.agent_name(),
        )
    return db.jsonable(result)


@mcp.tool(annotations=READ)
def get_claim(claim_id: Annotated[int, Field(description="Claim to show")]) -> dict[str, Any]:
    """A claim with every assessment (newest first) and the papers each one cites."""
    with db.reader() as conn:
        claim = claims.get_claim(conn, claim_id)
    if claim is None:
        raise ValueError(f"claim {claim_id} does not exist")
    return db.jsonable(claim)


@mcp.tool(annotations=READ)
def list_claims(
    query: Annotated[str | None, Field(description="Words that must all appear in the claim text")] = None,
    verdict: Annotated[
        Literal["supported", "contradicted", "inconclusive", "unassessed"] | None,
        Field(description="Filter by current verdict; 'unassessed' = not assessed yet"),
    ] = None,
    min_confidence: Annotated[float | None, Field(ge=0, le=1, description="Minimum current confidence")] = None,
    limit: Limit = 50,
) -> dict[str, Any]:
    """Claims with their current (newest) verdict and confidence, most recently updated first."""
    with db.reader() as conn:
        rows = claims.list_claims(conn, query=query, verdict=verdict, min_confidence=min_confidence, limit=limit)
    return db.jsonable({"count": len(rows), "claims": rows})


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
