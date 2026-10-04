"""Omnigent names a local tool after its file. Implementation: literature_tools/europepmc_search.py."""
from literature_tools import search_support as _s

europepmc_get_paper = _s.export(_s.load("europepmc_search").europepmc_get_paper, __name__)
