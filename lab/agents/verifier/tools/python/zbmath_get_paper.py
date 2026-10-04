"""Omnigent names a local tool after its file. Implementation: literature_tools/zbmath_search.py."""
from literature_tools import search_support as _s

zbmath_get_paper = _s.export(_s.load("zbmath_search").zbmath_get_paper, __name__)
