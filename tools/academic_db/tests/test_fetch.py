"""fetch_many batches requests per provider and chains IDs across providers. Uses fixtures, not the network."""

from types import SimpleNamespace

from conftest import load_json, load_text

from academic_db import providers
from academic_db.identifiers import Identifier
from academic_db.ingest import fetch_many

OPENALEX = load_json("openalex_W2626778328.json")
S2 = load_json("s2_attention.json")
ARXIV_FEED = load_text("arxiv_1706.03762.xml")
S2_ID = S2["paperId"]


def fake_client(calls, *, s2_rate_limited=False):
    def openalex_works(openalex_ids=(), dois=(), arxiv_ids=()):
        calls.append(("openalex", tuple(openalex_ids), tuple(dois), tuple(arxiv_ids)))
        return [OPENALEX] if "W2626778328" in openalex_ids or "1706.03762" in arxiv_ids else []

    def semantic_scholar_papers(keys):
        calls.append(("semantic_scholar", tuple(keys)))
        if s2_rate_limited:
            raise providers.RateLimited("429")
        return [S2 if key in ("ARXIV:1706.03762", S2_ID, "CorpusId:13756489") else None for key in keys]

    def arxiv_feeds(arxiv_ids):
        calls.append(("arxiv", tuple(arxiv_ids)))
        return [ARXIV_FEED] if "1706.03762" in arxiv_ids else []

    return SimpleNamespace(openalex_works=openalex_works, semantic_scholar_papers=semantic_scholar_papers,
                           arxiv_feeds=arxiv_feeds)


def test_arxiv_id_reaches_all_three_providers_in_one_round():
    calls = []
    (paper,) = fetch_many([Identifier("arxiv", "1706.03762")], client=fake_client(calls))
    assert paper.status == {"openalex": "found", "semantic_scholar": "found", "arxiv": "found"}
    assert [c[0] for c in calls] == ["openalex", "semantic_scholar", "arxiv"]  # one request each


def test_semantic_scholar_falls_back_to_arxiv_key_in_the_same_batch():
    # OpenAlex reports a repost DOI that Semantic Scholar does not know; the arXiv key in the same request works.
    calls = []
    (paper,) = fetch_many([Identifier("openalex", "W2626778328")], client=fake_client(calls))
    s2_requests = [c for c in calls if c[0] == "semantic_scholar"]
    assert len(s2_requests) == 1
    assert "ARXIV:1706.03762" in s2_requests[0][1] and any(k.startswith("DOI:") for k in s2_requests[0][1])
    assert paper.status["semantic_scholar"] == "found"


def test_corpus_id_resolves_through_semantic_scholar_first():
    calls = []
    (paper,) = fetch_many([Identifier("semantic_scholar", "CorpusId:13756489")], client=fake_client(calls))
    assert calls[0][0] == "semantic_scholar"
    assert paper.status == {"openalex": "found", "semantic_scholar": "found", "arxiv": "found"}


def test_many_papers_share_one_request_per_provider():
    calls = []
    papers = fetch_many(
        [Identifier("arxiv", "1706.03762"), Identifier("doi", "10.1234/missing"), Identifier("arxiv", "2101.00001")],
        client=fake_client(calls),
    )
    assert [p.status["openalex"] for p in papers] == ["found", "not_found", "not_found"]
    openalex_calls = [c for c in calls if c[0] == "openalex"]
    assert len(openalex_calls) == 1
    assert openalex_calls[0][2] == ("10.1234/missing",) and set(openalex_calls[0][3]) == {"1706.03762", "2101.00001"}


def test_rate_limit_is_reported_and_not_retried():
    calls = []
    (paper,) = fetch_many([Identifier("arxiv", "1706.03762")], client=fake_client(calls, s2_rate_limited=True))
    assert paper.status == {"openalex": "found", "semantic_scholar": "rate_limited", "arxiv": "found"}
    assert sum(1 for c in calls if c[0] == "semantic_scholar") == 1


def test_unknown_everywhere():
    (paper,) = fetch_many([Identifier("doi", "10.1234/nothing")], client=fake_client([]))
    assert paper.status == {"openalex": "not_found", "semantic_scholar": "not_found", "arxiv": "skipped"}
    assert paper.records == {}


def test_corrupted_provider_record_is_rejected_and_the_right_one_found_later():
    # Real case (BERT): OpenAlex's work behind arXiv 1810.04805 carries another paper's title and DOI.
    bert_title = "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding"
    corrupted = OPENALEX | {"id": "https://openalex.org/W2896457183", "doi": "https://doi.org/10.4230/lipics.cosit.2022.18",
                            "title": "AI-Assisted Pipeline for Dynamic Generation of Trustworthy Health Explanations",
                            "locations": [{"landing_page_url": "http://arxiv.org/abs/1810.04805"}]}
    correct = OPENALEX | {"id": "https://openalex.org/W2963341956", "doi": "https://doi.org/10.18653/v1/n19-1423",
                          "title": bert_title, "locations": []}
    s2_bert = S2 | {"paperId": "df2b0e26d0599ce3e70df8a9da02e51594e0e992", "title": bert_title,
                    "externalIds": {"ArXiv": "1810.04805", "DOI": "10.18653/v1/N19-1423"}}
    calls = []

    client = SimpleNamespace(
        openalex_works=lambda openalex_ids=(), dois=(), arxiv_ids=(): (
            calls.append(("openalex", tuple(dois), tuple(arxiv_ids)))
            or ([corrupted] if "1810.04805" in arxiv_ids else [])
            + ([correct] if "10.18653/v1/n19-1423" in dois else [])
            + ([corrupted] if "10.4230/lipics.cosit.2022.18" in dois else [])),
        semantic_scholar_papers=lambda keys: [s2_bert if k == "ARXIV:1810.04805" else None for k in keys],
        arxiv_feeds=lambda ids: [ARXIV_FEED.replace("1706.03762", "1810.04805")
                                 .replace("Attention Is All You Need", bert_title)] if ids else [],
    )
    (paper,) = fetch_many([Identifier("arxiv", "1810.04805")], client=client)

    assert paper.anchor.provider == "arxiv"
    assert paper.records["openalex"].provider_work_id == "W2963341956"  # found again via S2's real DOI
    assert paper.records["semantic_scholar"].title == bert_title
    assert paper.status == {"openalex": "found", "semantic_scholar": "found", "arxiv": "found"}
    assert ("openalex", ("10.18653/v1/n19-1423",), ()) in calls  # never queried the corrupted DOI
    assert all("10.4230/lipics.cosit.2022.18" not in c[1] for c in calls)


def test_mismatch_is_reported_with_the_other_title():
    wrong = OPENALEX | {"title": "A completely different paper about protein folding"}
    client = SimpleNamespace(openalex_works=lambda **kw: [wrong] if "1706.03762" in kw["arxiv_ids"] else [],
                             semantic_scholar_papers=lambda keys: [None for _ in keys],
                             arxiv_feeds=lambda ids: [ARXIV_FEED] if ids else [])
    (paper,) = fetch_many([Identifier("arxiv", "1706.03762")], client=client)
    assert paper.status["openalex"] == "mismatch" and "protein folding" in paper.detail["openalex"]
    assert "openalex" not in paper.records
