"""Open-access full text for the Verifier: read a paper's sections and check quotes against it.

Sources, tried in order: Europe PMC JATS XML (PMC open-access subset), arXiv HTML,
open-access PDFs (arXiv, OpenAlex and Unpaywall links; needs pypdf), then the
abstract alone. Parsed texts are cached on disk under SEARCH_STATE_DIR so
repeated checks on one paper fetch it once.
"""
from __future__ import annotations

import difflib
import hashlib
from html.parser import HTMLParser
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import re
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET

from omnigent_client.tools import tool

_spec = importlib.util.spec_from_file_location("_search_support", Path(__file__).with_name("search_support.py"))
_s = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_s)
_openalex = _s.load("openalex_search")
_arxiv = _s.load("arxiv_search")
_europepmc = _s.load("europepmc_search")
_unpaywall = _s.load("unpaywall_lookup")

_EPMC_REST = "https://www.ebi.ac.uk/europepmc/webservices/rest/"
_ARXIV_HTML = "https://arxiv.org/html/"
_ARXIV_ID = r"(?:\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7})(?:v\d+)?"
# Back matter that would only produce false quote matches.
_SKIP_SECTION = re.compile(r"^\W*(?:\d+\W*)?(?:references|bibliography|acknowledg|footnotes|author contributions|competing interests|supporting information|supplementary)", re.I)
_TOKEN = re.compile(r"\w+")
_MIN_QUOTE_TOKENS, _MAX_QUOTE_TOKENS = 4, 120
_SECTION_CHARS = 8000
_CONTEXT_CHARS = 250
_MAX_PDF_PAGES, _MAX_PDF_TRIES = 80, 3
_REFERENCES_LINE = re.compile(r"^\s*(?:\d+\.?\s*)?(?:references|bibliography|literature cited)\s*$", re.I | re.M)


_LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"})


def _clean(text):
    text = re.sub(r"\s+", " ", (text or "").translate(_LIGATURES)).strip()
    # Brackets left empty once citation markers are removed, e.g. "[, ]".
    text = re.sub(r"\s*[\[(][\s,;–-]*[\])]", "", text)
    # Separators left between removed markers, e.g. "regions [1], [2]." -> "regions,.".
    text = re.sub(r"(?:\s*[,;])+\s*(?=[.;:)]|$)", "", text)
    return re.sub(r"\s+([.,;:])", r"\1", text)


def _add(sections, heading, paragraphs):
    paragraphs = [p for p in (_clean(p) for p in paragraphs) if p]
    if paragraphs:
        sections.append({"heading": heading, "paragraphs": paragraphs})


# ── Europe PMC JATS XML ─────────────────────────────────────────

def _jats_text(node):
    """Element text without bibliography citation markers."""
    parts = [node.text or ""]
    for child in node:
        if not (child.tag == "xref" and child.get("ref-type") == "bibr"):
            parts.append(_jats_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _jats_sections(root):
    sections = []
    abstract = root.find("front/article-meta/abstract")
    if abstract is not None:
        _add(sections, "Abstract", [_jats_text(p) for p in abstract.iter("p")])

    def walk(sec, path):
        title = sec.find("title")
        heading = _clean(_jats_text(title)) if title is not None else ""
        if heading and _SKIP_SECTION.match(heading):
            return
        path = path + [heading] if heading else path
        paragraphs = []
        for child in sec:
            if child.tag == "p":
                paragraphs.append(_jats_text(child))
            elif child.tag not in ("sec", "title"):
                # Figure and table captions, lists, boxed text.
                paragraphs.extend(_jats_text(p) for p in child.iter("p"))
        _add(sections, " > ".join(path) or "Body", paragraphs)
        for child in sec.findall("sec"):
            walk(child, path)

    body = root.find("body")
    if body is not None:
        walk(body, [])
    return sections


# ── arXiv HTML (LaTeXML) ────────────────────────────────────────

class _LatexmlParser(HTMLParser):
    """Collect ltx_p paragraphs under their section headings, skipping citations and notes."""

    _SKIP_CLASSES = ("ltx_bibliography", "ltx_note", "ltx_authors", "ltx_page_footer")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sections, self.levels = [], {}
        self.skip_tag, self.skip_depth = None, 0
        self.heading_level, self.heading_parts = None, []
        self.para_parts = None

    def _path(self):
        return " > ".join(self.levels[k] for k in sorted(self.levels)) or "Body"

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if self.skip_tag:
            if tag == self.skip_tag:
                self.skip_depth += 1
            return
        if tag == "math":
            self._text(f" {attrs.get('alttext', '')} ")
            self.skip_tag, self.skip_depth = tag, 1
            return
        if tag in ("cite", "script", "style", "nav", "header", "footer") or any(c in self._SKIP_CLASSES for c in classes):
            self.skip_tag, self.skip_depth = tag, 1
            return
        if re.fullmatch(r"h[2-6]", tag) and "ltx_title" in classes:
            self.heading_level, self.heading_parts = int(tag[1]), []
        elif tag == "p" and "ltx_p" in classes:
            self.para_parts = []

    def handle_endtag(self, tag):
        if self.skip_tag:
            if tag == self.skip_tag:
                self.skip_depth -= 1
                if self.skip_depth == 0:
                    self.skip_tag = None
            return
        if self.heading_level and tag == f"h{self.heading_level}":
            heading = _clean("".join(self.heading_parts))
            if "abstract" in heading.lower() and self.heading_level == 6:
                self.levels = {2: "Abstract"}
            else:
                self.levels = {k: v for k, v in self.levels.items() if k < self.heading_level}
                self.levels[self.heading_level] = heading
            self.heading_level = None
        elif tag == "p" and self.para_parts is not None:
            path = self._path()
            if not any(_SKIP_SECTION.match(part) for part in path.split(" > ")):
                text = _clean("".join(self.para_parts))
                if self.sections and self.sections[-1]["heading"] == path:
                    if text:
                        self.sections[-1]["paragraphs"].append(text)
                else:
                    _add(self.sections, path, [text])
            self.para_parts = None

    def handle_data(self, data):
        if not self.skip_tag:
            self._text(data)

    def _text(self, data):
        if self.heading_level:
            self.heading_parts.append(data)
        elif self.para_parts is not None:
            self.para_parts.append(data)


# ── PDF (pypdf) ─────────────────────────────────────────────────

def _pdf_sections(data):
    """One section per page; the text layer only, so scanned PDFs yield nothing."""
    from pypdf import PdfReader  # Optional: without it the PDF step is skipped.

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        reader.decrypt("")
    pages = [page.extract_text() or "" for page in reader.pages[:_MAX_PDF_PAGES]]
    # Drop the reference list: from the last "References" heading onwards.
    for number in range(len(pages) - 1, -1, -1):
        found = list(_REFERENCES_LINE.finditer(pages[number]))
        if found and number >= len(pages) // 2:
            pages = pages[:number] + [pages[number][:found[-1].start()]]
            break
    sections = []
    for number, page in enumerate(pages, 1):
        page = re.sub(r"(\w)-\n(\w)", r"\1\2", page)  # Words split across lines.
        _add(sections, f"Page {number}", re.split(r"\n\s*\n", page))
    if sum(len(p) for s in sections for p in s["paragraphs"]) < 500:
        raise _s.SearchError("not_found", "The PDF has no usable text layer.")
    return sections


def _pdf_candidates(ids):
    """Open-access PDF links: arXiv, then OpenAlex, then Unpaywall (if CONTACT_EMAIL is set)."""
    links, attempts = [], []
    if ids.get("arxiv"):
        links.append("https://arxiv.org/pdf/" + ids["arxiv"])
    # The arXiv PDF is already first; OpenAlex often lists it again with a version suffix.
    links.extend(link for link in ids.get("_pdf_links") or [] if not (ids.get("arxiv") and "arxiv.org" in link))
    if ids.get("doi") and not ids["doi"].startswith("10.48550/"):
        found = _unpaywall.unpaywall_find_full_text(ids["doi"])
        if "error" in found:
            attempts.append({"source": "unpaywall", "result": found["error"]["code"]})
        else:
            links.extend(loc["pdf_url"] for loc in found["locations"] if loc.get("pdf_url"))
    unique = list(dict.fromkeys(link for link in links if link.startswith(("https://", "http://"))))
    return unique[:_MAX_PDF_TRIES], attempts


def _try_pdfs(ids, attempts):
    if importlib.util.find_spec("pypdf") is None:
        attempts.append({"source": "pdf", "result": "pypdf_not_installed"})
        return None
    links, lookup_attempts = _pdf_candidates(ids)
    attempts.extend(lookup_attempts)
    for url in links:
        host = urllib.parse.urlsplit(url).hostname or "pdf"
        provider = "arxiv" if host.endswith("arxiv.org") else "pdf"

        def build():
            data = _s.fetch(provider, url, headers={"Accept": "application/pdf"})
            if not data.startswith(b"%PDF-"):
                raise _s.SearchError("not_pdf", "The link returned a web page, not a PDF.")
            try:
                return _pdf_sections(data)
            except _s.SearchError:
                raise
            except Exception:  # pypdf raises many error types on damaged files.
                raise _s.SearchError("unreadable_pdf", "The PDF could not be parsed.") from None

        key = "pdf_" + hashlib.sha256(url.encode()).hexdigest()[:20]
        try:
            return {"text_source": "pdf", "url": url, "sections": _cached(key, build), "attempts": attempts}
        except _s.SearchError as exc:
            attempts.append({"source": "pdf", "url": url, "result": exc.details["code"]})
    return None


# ── Resolution and loading ──────────────────────────────────────

def _cache_path(key):
    root = Path(os.environ.get("SEARCH_STATE_DIR", Path.home() / ".cache/discovery-lab/search")) / "fulltext"
    root.mkdir(parents=True, exist_ok=True)
    return root / (re.sub(r"[^\w.-]", "_", key) + ".json")


def _cached(key, build):
    path = _cache_path(key)
    if path.exists():
        return json.loads(path.read_text())
    sections = build()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sections))
    tmp.replace(path)
    return sections


def _epmc_lookup(ids):
    """Europe PMC's PMCID and open-access flag for a DOI, PMID or PMCID."""
    if ids.get("pmcid"):
        query = f"PMCID:{ids['pmcid']}"
    elif ids.get("doi"):
        query = f'DOI:"{ids["doi"]}"'
    elif ids.get("pmid"):
        query = f"EXT_ID:{ids['pmid']} AND SRC:MED"
    else:
        return None
    params = {"query": query, "format": "json", "resultType": "lite", "pageSize": 1}
    data = _s.get_json("europepmc", _EPMC_REST + "search?" + urllib.parse.urlencode(params))
    hits = (data.get("resultList") or {}).get("result") or []
    return hits[0] if hits else None


def _resolve(paper_id):
    identifier = _s.text(paper_id, "paper_id")
    bare = re.sub(r"^(?:https?://arxiv\.org/(?:abs|pdf|html)/|arxiv:)", "", identifier, flags=re.I)
    if re.fullmatch(_ARXIV_ID, bare):
        return {"arxiv": bare}
    if re.fullmatch(r"PMC\d+", identifier, flags=re.I):
        return {"pmcid": identifier.upper()}
    if re.fullmatch(r"\d{1,9}", identifier):
        return {"pmid": identifier}
    arxiv_doi = re.fullmatch(r"10\.48550/arxiv\.(" + _ARXIV_ID + ")", _s.doi(identifier) or "", flags=re.I)
    if arxiv_doi:
        return {"arxiv": arxiv_doi.group(1), "doi": _s.doi(identifier)}
    short = re.sub(r"^https?://openalex\.org/", "", identifier, flags=re.I)
    doi = _s.doi(identifier)
    if not doi and not re.fullmatch(r"W\d+", short, flags=re.I):
        raise _s.SearchError("invalid_arguments", "paper_id must be a DOI, arXiv ID, PMCID, PMID or OpenAlex work ID.")
    found = _openalex.openalex_get_paper(doi or short)
    if "error" in found:
        return {"doi": doi} if doi else {"openalex": short}
    paper = found["paper"]
    ids = {"doi": paper.get("doi"), "openalex": paper["id"], "pmid": paper["identifiers"].get("pmid")}
    pmcid = re.search(r"(\d+)/?$", paper["identifiers"].get("pmcid") or "")
    if pmcid:
        ids["pmcid"] = "PMC" + pmcid.group(1)
    for link in paper.get("full_text_links") or []:
        match = re.search(r"arxiv\.org/(?:abs|pdf)/(" + _ARXIV_ID + r")", link)
        if match:
            ids["arxiv"] = re.sub(r"\.pdf$", "", match.group(1))
            break
    ids["_abstract"] = paper.get("abstract")
    ids["_pdf_links"] = [link for link in paper.get("full_text_links") or [] if link]
    return {k: v for k, v in ids.items() if v}


def _load(paper_id):
    ids = _resolve(paper_id)
    attempts = []
    if ids.keys() & {"doi", "pmid", "pmcid"}:
        try:
            hit = _epmc_lookup(ids)
        except _s.SearchError as exc:
            hit = None
            attempts.append({"source": "europepmc", "result": exc.details["code"]})
        if hit and hit.get("pmcid") and hit.get("isOpenAccess") == "Y":
            ids["pmcid"] = hit["pmcid"]
            url = _EPMC_REST + hit["pmcid"] + "/fullTextXML"
            try:
                sections = _cached("epmc_" + hit["pmcid"], lambda: _jats_sections(ET.fromstring(_s.fetch("europepmc", url))))
                return ids, {"text_source": "europepmc_xml", "url": url, "sections": sections, "attempts": attempts}
            except _s.SearchError as exc:
                attempts.append({"source": "europepmc", "result": exc.details["code"]})
        elif not attempts:
            attempts.append({"source": "europepmc", "result": "not_open_access" if hit else "not_indexed"})
    if ids.get("arxiv"):
        url = _ARXIV_HTML + ids["arxiv"]
        def build():
            parser = _LatexmlParser()
            parser.feed(_s.fetch("arxiv", url).decode("utf-8", errors="replace"))
            if sum(len(s["paragraphs"]) for s in parser.sections) < 3:
                raise _s.SearchError("not_found", "arXiv has no usable HTML for this paper.")
            return parser.sections
        try:
            return ids, {"text_source": "arxiv_html", "url": url, "sections": _cached("arxiv_" + ids["arxiv"], build), "attempts": attempts}
        except _s.SearchError as exc:
            attempts.append({"source": "arxiv_html", "result": exc.details["code"]})
    pdf = _try_pdfs(ids, attempts)
    if pdf:
        return ids, pdf
    abstract = ids.get("_abstract")
    if not abstract and ids.get("arxiv"):
        found = _arxiv.arxiv_get_paper(ids["arxiv"])
        abstract = None if "error" in found else found["paper"].get("abstract")
    if not abstract and (ids.get("pmid") or ids.get("pmcid")):
        found = _europepmc.europepmc_get_paper(ids.get("pmid") or ids["pmcid"])
        abstract = None if "error" in found else found["paper"].get("abstract")
    if not abstract:
        raise _s.SearchError("not_found", "No open-access full text or abstract is available for this paper.")
    return ids, {"text_source": "abstract", "url": None, "sections": [{"heading": "Abstract", "paragraphs": [_clean(abstract)]}], "attempts": attempts}


def _public_ids(ids):
    return {k: v for k, v in ids.items() if not k.startswith("_")}


# ── Quote matching ──────────────────────────────────────────────

def _norm(token):
    return unicodedata.normalize("NFKC", token).casefold()


def _best_window(quote, words):
    """Highest token-level similarity between the quote and any window of the text."""
    n, qset = len(quote), set(quote)
    norms = [w[0] for w in words]
    score = lambda i, size: difflib.SequenceMatcher(None, quote, norms[i:i + size], autojunk=False).ratio()
    if len(norms) <= n:
        return score(0, len(norms)), 0, len(norms)
    best, best_i = -1.0, 0
    overlap = sum(t in qset for t in norms[:n])
    for i in range(len(norms) - n + 1):
        if i:
            overlap += (norms[i + n - 1] in qset) - (norms[i - 1] in qset)
        if overlap * 2 >= n:
            ratio = score(i, n)
            if ratio > best:
                best, best_i = ratio, i
                if ratio == 1.0:
                    return 1.0, i, n
    if best < 0:
        return 0.0, 0, n
    # Paraphrases can be shorter or longer than the quote: refine size and start.
    best_size = n
    for start in range(max(0, best_i - n // 4), min(len(norms) - 1, best_i + n // 4) + 1):
        for size in range(max(1, int(n * 0.75)), int(n * 1.25) + 1):
            if start + size <= len(norms):
                ratio = score(start, size)
                if ratio > best:
                    best, best_i, best_size = ratio, start, size
    return best, best_i, best_size


def _split_word_match(quote, norms, start, size):
    """A window equal to the quote once spaces are ignored, e.g. PDF text "sup ergravity"."""
    target = "".join(quote)
    for i in range(max(0, start - 2), min(len(norms), start + 3)):
        for n in range(max(1, size - 2), size + 5):
            if "".join(norms[i:i + n]) == target:
                return i, n
    return None


def _differences(quote, matched):
    """Word-level edits from the quote to the paper, e.g. a changed number."""
    edits = difflib.SequenceMatcher(None, quote, matched, autojunk=False).get_opcodes()
    return [{"quote": " ".join(quote[i1:i2]) or None, "paper": " ".join(matched[j1:j2]) or None}
            for op, i1, i2, j1, j2 in edits if op != "equal"][:10]


def _verdict(score):
    if score == 1.0:
        return "exact"
    return "near_exact" if score >= 0.9 else "partial" if score >= 0.6 else "not_found"


@tool
@_s.guarded
def check_quote(paper_id: str, quote: str) -> dict:
    """Check whether a quoted passage really appears in a paper, and where.

    Fetches the paper's open-access full text (Europe PMC, arXiv or an open PDF),
    falling back to its abstract, and finds the closest passage. For PDFs the
    location section is the page number. Comparison ignores case,
    punctuation and citation markers. verdict: exact (same words), near_exact
    (score >= 0.9, minor wording differences), partial (>= 0.6, likely
    paraphrased or altered: read matched_text) or not_found. differences lists
    the changed words; one changed number can still score near_exact. A match shows the
    words are in the paper, not that they support a claim; judge that from
    context. If checked_against is 'abstract', not_found only means the quote
    is not in the abstract.

    Args:
        paper_id: DOI, arXiv ID, PMCID, PMID or OpenAlex work ID.
        quote: The passage to find, 4-120 words; quote the key sentence rather than a whole paragraph.
    """
    words_in_quote = [_norm(t) for t in _TOKEN.findall(_s.text(quote, "quote"))]
    if not _MIN_QUOTE_TOKENS <= len(words_in_quote) <= _MAX_QUOTE_TOKENS:
        raise _s.SearchError("invalid_arguments", f"quote must contain {_MIN_QUOTE_TOKENS}-{_MAX_QUOTE_TOKENS} words.")
    ids, text = _load(paper_id)
    words = []  # (normalised token, section index, paragraph index, start, end)
    for si, section in enumerate(text["sections"]):
        for pi, paragraph in enumerate(section["paragraphs"]):
            words.extend((_norm(m.group()), si, pi, m.start(), m.end()) for m in _TOKEN.finditer(paragraph))
    if not words:
        raise ValueError("empty text")
    score, start, size = _best_window(words_in_quote, words)
    if score < 1.0:
        split = _split_word_match(words_in_quote, [w[0] for w in words], start, size)
        if split:
            score, (start, size) = 1.0, split
    first, last = words[start], words[min(start + size, len(words)) - 1]
    section = text["sections"][first[1]]
    paragraph = section["paragraphs"][first[2]]
    if (first[1], first[2]) == (last[1], last[2]):
        matched = paragraph[first[3]:last[4]]
    else:
        matched = paragraph[first[3]:] + " … " + text["sections"][last[1]]["paragraphs"][last[2]][:last[4]]
    lo, hi = max(0, first[3] - _CONTEXT_CHARS), min(len(paragraph), first[3] + len(matched) + _CONTEXT_CHARS)
    return {"source": "full_text", "paper_id": paper_id, "identifiers": _public_ids(ids),
            "checked_against": "abstract" if text["text_source"] == "abstract" else "full_text",
            "text_source": text["text_source"], "text_url": text["url"],
            "verdict": _verdict(round(score, 3)), "match_score": round(score, 3),
            "location": {"section": section["heading"], "paragraph": first[2] + 1},
            "matched_text": matched,
            "differences": [] if score == 1.0 else _differences(words_in_quote, [w[0] for w in words[start:start + size]]),
            "context": ("…" if lo else "") + paragraph[lo:hi] + ("…" if hi < len(paragraph) else ""),
            "attempts": text["attempts"], "retrieved_at": _s.timestamp()}


@tool
@_s.guarded
def read_full_text(paper_id: str, section: int | None = None, offset: int = 0) -> dict:
    """Read a paper's open-access full text: its outline first, then one section at a time.

    Without section, returns the section outline (index, heading, length).
    With section, returns that section's text in pages of 8,000 characters.
    Falls back to the abstract when no full text is available.

    Args:
        paper_id: DOI, arXiv ID, PMCID, PMID or OpenAlex work ID.
        section: Section index from the outline, or null for the outline.
        offset: Character offset within the section; use next_offset from the previous call.
    """
    if section is not None:
        _s.integer(section, "section", 0, 10_000)
    _s.integer(offset, "offset", 0, 10_000_000)
    ids, text = _load(paper_id)
    out = {"source": "full_text", "paper_id": paper_id, "identifiers": _public_ids(ids),
           "text_source": text["text_source"], "text_url": text["url"], "attempts": text["attempts"]}
    if section is None:
        out["outline"] = [{"section": i, "heading": s["heading"], "chars": sum(len(p) for p in s["paragraphs"])}
                          for i, s in enumerate(text["sections"])]
        return out
    if section >= len(text["sections"]):
        raise _s.SearchError("invalid_arguments", f"section must be below {len(text['sections'])}; call without section for the outline.")
    body = "\n\n".join(text["sections"][section]["paragraphs"])
    chunk = body[offset:offset + _SECTION_CHARS]
    following = offset + len(chunk)
    out.update(section=section, heading=text["sections"][section]["heading"], text=chunk,
               next_offset=following if following < len(body) else None)
    return out
