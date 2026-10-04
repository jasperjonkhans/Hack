"""Omnigent names a local tool after its file. Implementation: literature_tools/arxiv_search.py."""
from literature_tools import search_support as _s

arxiv_get_paper = _s.export(_s.load("arxiv_search").arxiv_get_paper, __name__)
