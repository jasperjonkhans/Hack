"""Omnigent names a local tool after its file. Implementation: literature_tools/zbmath_search.py."""
from literature_tools import search_support as _s

zbmath_search = _s.export(_s.load("zbmath_search").zbmath_search, __name__)
