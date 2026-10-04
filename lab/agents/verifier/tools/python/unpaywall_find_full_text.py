"""Omnigent names a local tool after its file. Implementation: literature_tools/unpaywall_lookup.py."""
from literature_tools import search_support as _s

unpaywall_find_full_text = _s.export(_s.load("unpaywall_lookup").unpaywall_find_full_text, __name__)
