"""Unpaywall: find legal open-access copies of a DOI. Requires CONTACT_EMAIL."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import urllib.parse

from omnigent_client.tools import tool

_spec = importlib.util.spec_from_file_location("_search_support", Path(__file__).with_name("search_support.py"))
_s = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_s)

_API_URL = "https://api.unpaywall.org/v2/"


def _location(loc):
    if not isinstance(loc, dict):
        raise ValueError("invalid OA location")
    for field in ("url_for_pdf", "url_for_landing_page"):
        if loc.get(field) is not None and not isinstance(loc[field], str):
            raise ValueError("invalid location URL")
    if not any(isinstance(loc.get(k), str) and loc[k].startswith(("https://", "http://")) for k in ("url_for_pdf", "url_for_landing_page")):
        raise ValueError("OA location has no usable link")
    return {"pdf_url": loc.get("url_for_pdf"), "landing_page_url": loc.get("url_for_landing_page"),
            "version": loc.get("version"), "license": loc.get("license"),
            "host_type": loc.get("host_type"), "repository": loc.get("repository_institution")}


@tool
@_s.guarded
def unpaywall_find_full_text(doi: str) -> dict:
    """Find legal open-access full-text copies (PDF or landing page) for a DOI.

    Locations are ordered best first. version tells you which text you get:
    publishedVersion (final), acceptedVersion (peer-reviewed manuscript) or
    submittedVersion (preprint, not peer reviewed). is_oa=false means no legal
    free copy is known to Unpaywall. Other legitimate repositories may still
    have a copy; a negative result is not a reason to stop searching.

    Args:
        doi: DOI, DOI URL or 'doi:' prefixed DOI, e.g. 10.1038/s41586-021-03819-2.
    """
    normal = _s.doi(_s.text(doi, "doi"))
    if not normal:
        raise _s.SearchError("invalid_arguments", "doi must look like 10.xxxx/....")
    email = _s.setting("CONTACT_EMAIL")
    if not email:
        raise _s.SearchError("configuration", "Set CONTACT_EMAIL in the environment or the repository-root .env; Unpaywall requires an email address.")
    url = _API_URL + urllib.parse.quote(normal, safe="/") + "?" + urllib.parse.urlencode({"email": email})
    try:
        data = _s.get_json("unpaywall", url)
    except _s.SearchError as exc:
        if exc.details["code"] == "not_found":
            raise _s.SearchError("not_found", "Unpaywall has no record of this DOI.") from None
        raise
    if (_s.doi(data.get("doi")) != normal or type(data.get("is_oa")) is not bool
            or not isinstance(data.get("oa_locations"), list)
            or "best_oa_location" not in data
            or data.get("oa_status") not in ("gold", "green", "hybrid", "bronze", "closed")):
        raise ValueError("invalid Unpaywall record")
    best = _location(data["best_oa_location"]) if data["best_oa_location"] is not None else None
    locations = [_location(loc) for loc in data["oa_locations"]]
    if data["is_oa"] != (best is not None) or (not data["is_oa"] and locations):
        raise ValueError("inconsistent OA status")
    if best is not None:
        locations = [best] + [loc for loc in locations if loc != best]
    return {"source": "unpaywall", "doi": normal, "title": data.get("title"), "year": data.get("year"),
            "journal": data.get("journal_name"), "is_oa": data["is_oa"],
            "oa_status": data.get("oa_status"),
            "best_location": best,
            "locations": locations, "retrieved_at": _s.timestamp()}
