"""Omnigent names a local tool after its file. Implementation: literature_tools/loogle_search.py."""
from literature_tools import search_support as _s

loogle_search = _s.export(_s.load("loogle_search").loogle_search, __name__)
