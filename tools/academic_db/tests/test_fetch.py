"""fetch_all chains IDs across providers. Uses fixtures instead of the network."""

from conftest import load_json, load_text

from academic_db import providers
from academic_db.identifiers import Identifier
from academic_db.ingest import fetch_all
from academic_db.normalize import from_arxiv, from_openalex, from_semantic_scholar

OPENALEX = from_openalex(load_json("openalex_W2626778328.json"))
S2 = from_semantic_scholar(load_json("s2_attention.json"))
ARXIV = from_arxiv(load_text("arxiv_1706.03762.xml"))


def fake_fetch(calls):
    def fetch(provider, lookup):
        calls.append((provider, lookup))
        key, value = next(iter(lookup.items()))
        if provider == "openalex" and (key, value) in {("arxiv_id", "1706.03762"), ("openalex_id", "W2626778328")}:
            return OPENALEX
        if provider == "semantic_scholar" and (key, value) in {("arxiv_id", "1706.03762"), ("paper_id", S2.provider_work_id)}:
            return S2
        if provider == "arxiv" and value == "1706.03762":
            return ARXIV
        raise providers.NotFound(f"{provider} {lookup}")
    return fetch


def test_arxiv_id_reaches_all_three_providers():
    calls = []
    outcomes = fetch_all(Identifier("arxiv", "1706.03762"), fetch=fake_fetch(calls))
    assert [o.status for o in outcomes] == ["found", "found", "found"]


def test_falls_back_when_the_openalex_doi_is_unknown_elsewhere():
    # OpenAlex reports a repost DOI; Semantic Scholar does not know it, then succeeds by arXiv ID.
    calls = []
    fetch_all(Identifier("openalex", "W2626778328"), fetch=fake_fetch(calls))
    s2_calls = [lookup for provider, lookup in calls if provider == "semantic_scholar"]
    assert s2_calls == [{"doi": OPENALEX.doi}, {"arxiv_id": "1706.03762"}]


def test_semantic_scholar_id_is_resolved_first():
    calls = []
    outcomes = fetch_all(Identifier("semantic_scholar", S2.provider_work_id), fetch=fake_fetch(calls))
    assert calls[0][0] == "semantic_scholar"
    assert [o.status for o in outcomes] == ["found", "found", "found"]


def test_rate_limit_is_reported_not_retried():
    def fetch(provider, lookup):
        if provider == "semantic_scholar":
            raise providers.RateLimited("429")
        return fake_fetch([])(provider, lookup)

    outcomes = {o.provider: o for o in fetch_all(Identifier("arxiv", "1706.03762"), fetch=fetch)}
    assert outcomes["semantic_scholar"].status == "rate_limited"
    assert outcomes["openalex"].status == "found"


def test_unknown_everywhere():
    outcomes = fetch_all(Identifier("doi", "10.1234/nothing"), fetch=fake_fetch([]))
    assert {o.provider: o.status for o in outcomes} == {
        "openalex": "not_found", "semantic_scholar": "not_found", "arxiv": "skipped",
    }
