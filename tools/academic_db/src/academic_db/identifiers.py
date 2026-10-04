"""Recognize and normalize paper identifiers (spec section 5.1)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

IdKind = Literal["doi", "arxiv", "openalex", "semantic_scholar"]

_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)
_ARXIV_URL = re.compile(r"^(?:https?://)?(?:www\.|export\.)?arxiv\.org/(?:abs|pdf)/", re.IGNORECASE)
_ARXIV_VERSION = re.compile(r"v\d+$")
# New style 1706.03762 / 2101.00001, old style cond-mat/0410550 or math.GT/0309136
_ARXIV_ID = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})$")
_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.IGNORECASE)
_OPENALEX_WORK = re.compile(r"^(?:https?://openalex\.org/)?(W\d+)$", re.IGNORECASE)
_S2_PAPER = re.compile(r"(?:^|/)([0-9a-f]{40})$", re.IGNORECASE)
_S2_CORPUS = re.compile(r"^corpus_?id:\s*(\d+)$", re.IGNORECASE)


def normalize_doi(value: str | None) -> str | None:
    """'https://doi.org/10.1038/Nature14539' -> '10.1038/nature14539'."""
    if not value:
        return None
    doi = _DOI_PREFIX.sub("", value.strip()).strip().lower()
    return doi if doi.startswith("10.") else None


def normalize_arxiv_id(value: str | None) -> str | None:
    """'arXiv:1706.03762v5' or 'https://arxiv.org/abs/1706.03762v5' -> '1706.03762'."""
    if not value:
        return None
    arxiv_id = value.strip()
    arxiv_id = re.sub(r"^arxiv:\s*", "", arxiv_id, flags=re.IGNORECASE)
    arxiv_id = _ARXIV_URL.sub("", arxiv_id)
    arxiv_id = arxiv_id.removesuffix(".pdf")
    arxiv_id = _ARXIV_VERSION.sub("", arxiv_id)
    return arxiv_id if _ARXIV_ID.match(arxiv_id) else None


def arxiv_id_from_doi(doi: str | None) -> str | None:
    """'10.48550/arxiv.1706.03762' -> '1706.03762'."""
    match = _ARXIV_DOI.match(doi or "")
    return normalize_arxiv_id(match.group(1)) if match else None


def strip_openalex(value: str | None) -> str | None:
    """'https://openalex.org/W2626778328' -> 'W2626778328' (same for S… and A… IDs)."""
    if not value:
        return None
    return value.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Identifier:
    kind: IdKind
    value: str


def parse_identifier(text: str) -> Identifier:
    """Classify user input as a DOI, arXiv ID, OpenAlex work ID or Semantic Scholar paper ID."""
    value = text.strip()
    if match := _OPENALEX_WORK.match(value):
        return Identifier("openalex", match.group(1).upper())
    doi = normalize_doi(value)
    if doi:
        arxiv_id = arxiv_id_from_doi(doi)
        return Identifier("arxiv", arxiv_id) if arxiv_id else Identifier("doi", doi)
    arxiv_id = normalize_arxiv_id(value)
    if arxiv_id:
        return Identifier("arxiv", arxiv_id)
    if match := _S2_PAPER.search(value):
        return Identifier("semantic_scholar", match.group(1).lower())
    if match := _S2_CORPUS.match(value):
        return Identifier("semantic_scholar", f"CorpusId:{match.group(1)}")
    raise ValueError(
        f"Unrecognized identifier {text!r}. Use a DOI (10.1038/nature14539), arXiv ID (1706.03762), "
        "OpenAlex work ID (W2626778328), Semantic Scholar paper ID (40 hex characters) or CorpusId:N, "
        "or a URL to one."
    )
