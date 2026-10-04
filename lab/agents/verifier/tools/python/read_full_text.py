"""Omnigent names a local tool after its file. Implementation: literature_tools/full_text.py."""
from literature_tools import search_support as _s

read_full_text = _s.export(_s.load("full_text").read_full_text, __name__)
