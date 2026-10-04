"""Omnigent names a local tool after its file. Implementation: literature_tools/crossref_search.py."""
from literature_tools import search_support as _s

crossref_search = _s.export(_s.load("crossref_search").crossref_search, __name__)
