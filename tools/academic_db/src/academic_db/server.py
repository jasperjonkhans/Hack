"""MCP server exposing the academic works database to agents.

Run with `academic-db-mcp` (stdio). Each Omnigent agent allow-lists the tools it may use.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from . import claims, config, db, evidence, ingest, queries
from .identifiers import parse_identifier
from .normalize import from_payload

INSTRUCTIONS = """\
Academic papers merged from OpenAlex (preferred), Semantic Scholar and arXiv, plus claims that the
team verifies against those papers.

- Each paper has one work_id. Its citations, open-access status and source come from the preferred
  provider; get_work shows every provider's values side by side when they disagree.
- Identifiers: DOI 10.1038/nature14539, arXiv 1706.03762, OpenAlex W2626778328, Semantic Scholar
  40-hex paper ID or CorpusId:N. URLs to any of these also work.
- Lists are compact; read a paper's abstract with get_work only when you need it.
- is_retracted and preprint_only are checked by code: skip retracted papers rather than re-checking them.
- Author and source IDs belong to one provider: pass the provider together with the ID.
- cited_by_count is null when no provider reports citations (arXiv-only papers), not zero.
- Papers must be in the database before a claim or assessment can cite them: use import_works first.
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
    note: str | None = Field(default=None, description="How it does so, in your words, e.g. 'BLEU 28.4 on WMT14 En-De'")
    quote: str | None = Field(default=None, description="Exact passage from the paper that supports/contradicts the claim")
    location: str | None = Field(default=None, description="Where the passage is, e.g. 'Section 4.2' or check_quote's location")
    match_score: float | None = Field(default=None, ge=0, le=1,
                                      description="How closely the quote matches the paper's text, 0-1 (from check_quote)")


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


IMPORT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)
IdentifierText = Annotated[
    str, Field(description="DOI, arXiv ID, OpenAlex W-ID, Semantic Scholar paper ID or CorpusId:N, or a URL to one")
]


def _import(identifiers: list[str]) -> list[ingest.Imported]:
    papers = ingest.fetch_many([parse_identifier(i) for i in identifiers])
    with db.writer() as conn:
        return ingest.save_fetched(conn, papers)


@mcp.tool(annotations=IMPORT)
def import_works(
    identifiers: Annotated[list[IdentifierText], Field(min_length=1, max_length=50, description="1-50 papers to save")],
) -> dict[str, Any]:
    """Fetch papers from OpenAlex, Semantic Scholar and arXiv in one batch and save them (or refresh them).

    Records of the same paper are merged into one work. A provider record whose title does not match the paper
    you asked for is reported as 'mismatch' and not saved. Returns each paper's work_id and what each provider
    returned. Use this for a shortlist from a search instead of saving papers one by one.
    """
    results = _import(identifiers)
    papers = [{"identifier": text, "work_id": r.work_id, "providers": r.providers} | ({"details": r.details} if r.details else {})
              for text, r in zip(identifiers, results, strict=True)]
    with db.reader() as conn:
        for paper in papers:
            if paper["work_id"]:
                work = queries.overview(conn, paper["work_id"])
                paper |= {"title": work["title"], "publication_year": work["publication_year"],
                          "is_retracted": work["is_retracted"]}
    warnings = sorted({f"{provider}: rate limited" + (" (set S2_API_KEY)" if provider == "semantic_scholar" else "")
                       for r in results for provider, status in r.providers.items() if status == "rate_limited"})
    return db.jsonable({
        "saved": sum(1 for p in papers if p["work_id"]),
        "not_found": [p["identifier"] for p in papers if not p["work_id"]],
        "warnings": warnings,
        "papers": papers,
    })


@mcp.tool(annotations=IMPORT)
def import_work(identifier: IdentifierText) -> dict[str, Any]:
    """Fetch one paper from OpenAlex, Semantic Scholar and arXiv and save it (or refresh it if already stored)."""
    (result,) = _import([identifier])
    if result.work_id is None:
        raise ValueError(f"No provider returned {identifier!r}: {result.providers} {result.details}")
    with db.reader() as conn:
        work = queries.overview(conn, result.work_id)
    return db.jsonable({"work_id": result.work_id, "work": work, "providers": result.providers}
                       | ({"details": result.details} if result.details else {}))


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
    source_work_id: Annotated[int | None, Field(description="Paper the claim comes from (import it first)")] = None,
    source_quote: Annotated[str | None, Field(description="The passage in that paper that states it")] = None,
    allow_similar: Annotated[bool, Field(description="Create it even if similar claims exist")] = False,
) -> dict[str, Any]:
    """Record a claim to verify, with the paper and passage it came from.

    Identical text returns the existing claim_id. If similar claims exist, they are returned instead and nothing
    is created: reuse one of them, or call again with allow_similar=true.
    """
    with db.writer() as conn:
        return db.jsonable(claims.add_claim(conn, text, config.agent_name(), source_work_id=source_work_id,
                                            source_quote=source_quote, allow_similar=allow_similar))


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


# ---------------------------------------------------------------- counter-evidence


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
def citing_statements(
    work_id: WorkId,
    limit: Annotated[int, Field(ge=1, le=100, description="Citing papers to return (1-100)")] = 30,
) -> dict[str, Any]:
    """What later papers say about this one: citing papers with the sentences that mention it.

    The fastest way to find replications, critiques and contradictions of a source. Papers you want to cite as
    evidence must be saved first (import_works with their identifier); stored_work_id shows those already saved.
    """
    with db.reader() as conn:
        return db.jsonable(evidence.citing_statements(conn, work_id, limit))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True))
def search_passages(
    query: Annotated[str, Field(description="The claim, or a reversed version of it, e.g. 'X does not improve Y'")],
    limit: Annotated[int, Field(ge=1, le=50, description="Passages to return (1-50)")] = 10,
) -> dict[str, Any]:
    """Full-text passages (from titles, abstracts and paper bodies) that best match a query.

    Finds papers that discuss a claim even when their title does not. Import a paper with its identifier before
    citing it.
    """
    return db.jsonable(evidence.search_passages(query, limit))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
