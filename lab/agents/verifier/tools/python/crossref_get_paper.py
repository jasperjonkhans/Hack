"""Omnigent names a local tool after its file. Implementation: literature_tools/crossref_search.py."""
from literature_tools import search_support as _s

crossref_get_paper = _s.export(_s.load("crossref_search").crossref_get_paper, __name__)
