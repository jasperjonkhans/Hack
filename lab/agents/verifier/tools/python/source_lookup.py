"""Verifier's source lookup tools. Implementations live in lab/lib/."""
from __future__ import annotations

import importlib.util
from pathlib import Path

# lab/agents/verifier/tools/python/ -> lab/lib/ (Omnigent ships the whole bundle).
_spec = importlib.util.spec_from_file_location("_search_support", Path(__file__).resolve().parents[4] / "lib" / "search_support.py")
_s = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_s)

_openalex = _s.load("openalex_search")
_arxiv = _s.load("arxiv_search")
_europepmc = _s.load("europepmc_search")
_crossref = _s.load("crossref_search")
_zbmath = _s.load("zbmath_search")
_unpaywall = _s.load("unpaywall_lookup")
_loogle = _s.load("loogle_search")

crossref_get_paper = _s.export(_crossref.crossref_get_paper, __name__)
openalex_get_paper = _s.export(_openalex.openalex_get_paper, __name__)
arxiv_get_paper = _s.export(_arxiv.arxiv_get_paper, __name__)
europepmc_get_paper = _s.export(_europepmc.europepmc_get_paper, __name__)
zbmath_get_paper = _s.export(_zbmath.zbmath_get_paper, __name__)
unpaywall_find_full_text = _s.export(_unpaywall.unpaywall_find_full_text, __name__)
loogle_search = _s.export(_loogle.loogle_search, __name__)
