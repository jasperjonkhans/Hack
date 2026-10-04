"""Omnigent names a local tool after its file. Implementation: literature_tools/full_text.py."""
from literature_tools import search_support as _s

check_quote = _s.export(_s.load("full_text").check_quote, __name__)
