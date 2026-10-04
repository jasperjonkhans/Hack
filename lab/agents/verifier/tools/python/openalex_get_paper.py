"""Omnigent names a local tool after its file. Implementation: literature_tools/openalex_search.py."""
from literature_tools import search_support as _s

openalex_get_paper = _s.export(_s.load("openalex_search").openalex_get_paper, __name__)
