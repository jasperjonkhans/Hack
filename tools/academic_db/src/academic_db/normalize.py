"""Turn one provider payload into a normalized record (spec section 5)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Literal

from .identifiers import (
    arxiv_id_from_doi,
    normalize_arxiv_id,
    normalize_doi,
    strip_openalex,
)

Provider = Literal["openalex", "semantic_scholar", "arxiv"]
PROVIDERS: tuple[Provider, ...] = ("openalex", "semantic_scholar", "arxiv")

OA_STATUSES = {"diamond", "gold", "green", "hybrid", "bronze", "closed"}

# First match wins (spec section 5.3).
S2_TYPES = (
    ("Review", "review"),
    ("Book", "book"),
    ("BookSection", "book-chapter"),
    ("Dataset", "dataset"),
    ("Editorial", "editorial"),
    ("LettersAndComments", "letter"),
    ("Conference", "conference-paper"),
    ("JournalArticle", "article"),
)

ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
# Keep readable prefixes when an entry is re-serialized into raw.
ET.register_namespace("", ATOM[1:-1])
ET.register_namespace("arxiv", ARXIV[1:-1])


@dataclass(frozen=True)
class Author:
    name: str
    author_id: str | None = None


@dataclass(frozen=True)
class Record:
    provider: Provider
    provider_work_id: str
    title: str
    raw: dict[str, Any]
    doi: str | None = None
    arxiv_id: str | None = None
    mag_id: int | None = None
    publication_year: int | None = None
    type: str | None = None
    source_id: str | None = None
    cited_by_count: int | None = None
    is_oa: bool | None = None
    oa_status: str | None = None
    oa_url: str | None = None
    authors: tuple[Author, ...] = field(default_factory=tuple)


def _text(value: Any) -> str | None:
    """Empty strings become None; whitespace runs collapse to one space."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _oa_status(value: Any) -> str | None:
    status = (_text(value) or "").lower()
    return status if status in OA_STATUSES else None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _require_title(provider: str, work_id: str, title: str | None) -> str:
    if not title:
        raise ValueError(f"{provider} record {work_id} has no title; records without a title are skipped")
    return title


def from_openalex(work: dict[str, Any]) -> Record:
    work_id = strip_openalex(work.get("id"))
    if not work_id:
        raise ValueError("OpenAlex payload has no id")
    doi = normalize_doi(work.get("doi"))

    arxiv_id = arxiv_id_from_doi(doi)
    for location in work.get("locations") or []:
        if arxiv_id:
            break
        location_id = location.get("id") or ""
        if location_id.startswith("pmh:oai:arXiv.org:"):
            arxiv_id = normalize_arxiv_id(location_id.removeprefix("pmh:oai:arXiv.org:"))
        elif "arxiv.org/abs/" in (location.get("landing_page_url") or ""):
            arxiv_id = normalize_arxiv_id(location["landing_page_url"])

    open_access = work.get("open_access") or {}
    source = (work.get("primary_location") or {}).get("source") or {}
    authors = tuple(
        Author(name=name, author_id=strip_openalex((a.get("author") or {}).get("id")))
        for a in work.get("authorships") or []
        if (name := _text((a.get("author") or {}).get("display_name") or a.get("raw_author_name")))
    )
    return Record(
        provider="openalex",
        provider_work_id=work_id,
        title=_require_title("openalex", work_id, _text(work.get("title") or work.get("display_name"))),
        raw=work,
        doi=doi,
        arxiv_id=arxiv_id,
        mag_id=_int((work.get("ids") or {}).get("mag")),
        publication_year=_int(work.get("publication_year")),
        type=_text(work.get("type")),
        source_id=strip_openalex(source.get("id")),
        cited_by_count=_int(work.get("cited_by_count")),
        is_oa=open_access.get("is_oa"),
        oa_status=_oa_status(open_access.get("oa_status")),
        oa_url=_text(open_access.get("oa_url")),
        authors=authors,
    )


def _s2_type(types: list[str] | None) -> str | None:
    if not types:
        return None
    compact = {t.replace(" ", "") for t in types if t}
    for s2_type, canonical in S2_TYPES:
        if s2_type in compact:
            return canonical
    return "other"


def from_semantic_scholar(paper: dict[str, Any]) -> Record:
    paper_id = _text(paper.get("paperId"))
    if not paper_id:
        raise ValueError("Semantic Scholar payload has no paperId")
    external = paper.get("externalIds") or {}
    pdf = paper.get("openAccessPdf") or {}
    authors = tuple(
        Author(name=name, author_id=_text(a.get("authorId")))
        for a in paper.get("authors") or []
        if (name := _text(a.get("name")))
    )
    return Record(
        provider="semantic_scholar",
        provider_work_id=paper_id,
        title=_require_title("semantic_scholar", paper_id, _text(paper.get("title"))),
        raw=paper,
        doi=normalize_doi(external.get("DOI")),
        arxiv_id=normalize_arxiv_id(external.get("ArXiv")),
        mag_id=_int(external.get("MAG")),
        publication_year=_int(paper.get("year")),
        type=_s2_type(paper.get("publicationTypes")),
        source_id=_text((paper.get("publicationVenue") or {}).get("id")),
        cited_by_count=_int(paper.get("citationCount")),
        is_oa=paper.get("isOpenAccess"),
        oa_status=_oa_status(pdf.get("status")),
        oa_url=_text(pdf.get("url")),
        authors=authors,
    )


def from_arxiv(entry_xml: str) -> Record:
    """Parse one arXiv Atom <entry> (or a feed containing exactly one entry)."""
    root = ET.fromstring(entry_xml)
    entry = root if root.tag == f"{ATOM}entry" else root.find(f"{ATOM}entry")
    if entry is None:
        raise ValueError("arXiv payload contains no <entry>")
    arxiv_id = normalize_arxiv_id(entry.findtext(f"{ATOM}id"))
    if not arxiv_id:
        raise ValueError("arXiv entry has no valid id (the API returns an error entry for unknown IDs)")
    published = entry.findtext(f"{ATOM}published") or ""
    pdf_url = next(
        (link.get("href") for link in entry.findall(f"{ATOM}link") if link.get("title") == "pdf"),
        None,
    )
    authors = tuple(
        Author(name=name)
        for author in entry.findall(f"{ATOM}author")
        if (name := _text(author.findtext(f"{ATOM}name")))
    )
    return Record(
        provider="arxiv",
        provider_work_id=arxiv_id,
        title=_require_title("arxiv", arxiv_id, _text(entry.findtext(f"{ATOM}title"))),
        raw={"entry_xml": ET.tostring(entry, encoding="unicode")},
        doi=normalize_doi(entry.findtext(f"{ARXIV}doi")),
        arxiv_id=arxiv_id,
        publication_year=_int(published[:4]),
        type="preprint",
        is_oa=True,
        oa_status="green",
        oa_url=_text(pdf_url),
        authors=authors,
    )


def from_payload(provider: str, payload: dict[str, Any] | str) -> Record:
    """Dispatch on provider. arXiv takes entry XML (or {"entry_xml": ...}); the others take JSON objects."""
    if provider == "openalex" and isinstance(payload, dict):
        return from_openalex(payload)
    if provider == "semantic_scholar" and isinstance(payload, dict):
        return from_semantic_scholar(payload)
    if provider == "arxiv":
        xml = payload.get("entry_xml") if isinstance(payload, dict) else payload
        if isinstance(xml, str):
            return from_arxiv(xml)
    raise ValueError(
        f"Unsupported payload for provider {provider!r}: expected a JSON object for openalex/semantic_scholar "
        "and Atom <entry> XML for arxiv"
    )
