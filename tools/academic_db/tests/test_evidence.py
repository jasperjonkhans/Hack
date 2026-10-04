"""Counter-evidence tools with the provider calls faked; database lookups are real and rolled back."""

import pytest
from conftest import RUN, isolated, load_json

from academic_db import evidence, providers
from academic_db.ingest import save_record
from academic_db.normalize import from_openalex, from_semantic_scholar

pytestmark = pytest.mark.db

S2 = isolated(from_semantic_scholar(load_json("s2_attention.json")))
CITING = isolated(from_openalex(load_json("openalex_nature14539.json")))


def citation(title, *, contexts=(), intents=(), influential=False, doi=None, cites=0):
    return {"contexts": list(contexts), "intents": list(intents), "isInfluential": influential,
            "citingPaper": {"paperId": f"p-{title}", "title": title, "year": 2020, "citationCount": cites,
                            "externalIds": {"DOI": doi} if doi else {}}}


def test_citing_statements_puts_result_sentences_first_and_marks_stored_papers(conn, monkeypatch):
    work_id = save_record(conn, S2).work_id
    stored_id = save_record(conn, CITING).work_id
    asked = []

    def fake_citations(key, limit):
        asked.append(key)
        return [
            citation("no sentence", cites=999),
            citation("background mention", contexts=["We build on the Transformer [1]."], intents=["background"]),
            citation("result mention", contexts=["Unlike [1], we find no gain on low-resource pairs."],
                     intents=["result"], doi=CITING.doi.upper()),
        ]

    monkeypatch.setattr(providers, "semantic_scholar_citations", fake_citations)
    result = evidence.citing_statements(conn, work_id, limit=10)

    assert asked == [S2.provider_work_id]  # the stored Semantic Scholar ID is used
    assert [c["title"] for c in result["citing"]] == ["result mention", "background mention", "no sentence"]
    assert result["with_sentences"] == 2 and result["source"] == "semantic_scholar"
    assert result["citing"][0]["stored_work_id"] == stored_id
    assert result["citing"][0]["identifier"] == CITING.doi


def test_citing_statements_falls_back_to_openalex_when_rate_limited(conn, monkeypatch):
    oa = isolated(from_openalex(load_json("openalex_W2626778328.json")))
    work_id = save_record(conn, oa).work_id

    def limited(key, limit):
        raise providers.RateLimited(key)

    monkeypatch.setattr(providers, "semantic_scholar_citations", limited)
    monkeypatch.setattr(providers, "openalex_citing_works", lambda openalex_id, limit: [
        {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1/x", "title": "Citing work",
         "publication_year": 2021, "cited_by_count": 5},
    ])
    result = evidence.citing_statements(conn, work_id)
    assert result["source"] == "openalex" and "S2_API_KEY" in result["note"]
    assert result["citing"][0]["identifier"] == "10.1/x" and result["citing"][0]["contexts"] == []


def test_search_passages(monkeypatch):
    monkeypatch.setattr(providers, "semantic_scholar_snippets", lambda query, limit: [
        {"score": 0.9, "snippet": {"text": "x" * 5000, "section": "Results"},
         "paper": {"corpusId": 13756489, "title": f"A paper {RUN}"}},
    ])
    result = evidence.search_passages("transformers do not help", 5)
    passage = result["passages"][0]
    assert passage["identifier"] == "CorpusId:13756489" and passage["section"] == "Results"
    assert len(passage["text"]) == evidence.MAX_PASSAGE_CHARS


def test_search_passages_explains_missing_key(monkeypatch):
    def limited(query, limit):
        raise providers.RateLimited(query)

    monkeypatch.setattr(providers, "semantic_scholar_snippets", limited)
    with pytest.raises(ValueError, match="S2_API_KEY"):
        evidence.search_passages("anything")


def test_citing_statements_prefers_openalex_ranking_without_sentences(conn, monkeypatch):
    oa = isolated(from_openalex(load_json("openalex_W2626778328.json")))
    work_id = save_record(conn, oa).work_id
    save_record(conn, S2)  # same paper: shares the arXiv and MAG IDs
    monkeypatch.setattr(providers, "semantic_scholar_citations", lambda key, limit: [citation("recent, no sentence")])
    monkeypatch.setattr(providers, "openalex_citing_works", lambda openalex_id, limit: [
        {"id": "https://openalex.org/W9", "doi": None, "title": "Highly cited follow-up",
         "publication_year": 2019, "cited_by_count": 9000},
    ])
    result = evidence.citing_statements(conn, work_id)
    assert result["source"] == "openalex" and "S2_API_KEY" in result["note"]
    assert [c["title"] for c in result["citing"]] == ["Highly cited follow-up"]
    assert result["citing"][0]["identifier"] == "W9"
