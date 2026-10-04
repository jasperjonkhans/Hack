"""Matching, claims and queries against the real database. Every change is rolled back."""

import dataclasses

import psycopg
import pytest
from conftest import RUN, isolated, load_json, load_text

from academic_db import claims, ingest, queries
from academic_db.identifiers import Identifier
from academic_db.ingest import save_record
from academic_db.normalize import from_arxiv, from_openalex, from_semantic_scholar

pytestmark = pytest.mark.db

OPENALEX = isolated(from_openalex(load_json("openalex_W2626778328.json")))
S2 = isolated(from_semantic_scholar(load_json("s2_attention.json")))
ARXIV = isolated(from_arxiv(load_text("arxiv_1706.03762.xml")))


def test_three_providers_merge_into_one_work(conn):
    first = save_record(conn, OPENALEX)
    second = save_record(conn, S2)
    third = save_record(conn, ARXIV)

    assert first.created_work and first.matched_by == "new"
    assert (second.work_id, second.matched_by) == (first.work_id, "arxiv")
    assert (third.work_id, third.matched_by) == (first.work_id, "arxiv")

    work = queries.get_work(conn, first.work_id)
    assert work["publication_year"] == 2017  # earliest, not OpenAlex's repost year
    assert work["primary_provider"] == "openalex"
    assert work["arxiv_id"] == ARXIV.arxiv_id
    assert [r["provider"] for r in work["records"]] == ["openalex", "semantic_scholar", "arxiv"]
    assert work["records"][1]["authors"][0]["name"] == "Ashish Vaswani"


def test_saving_again_updates_instead_of_duplicating(conn):
    first = save_record(conn, S2)
    again = save_record(conn, dataclasses.replace(S2, cited_by_count=1))
    assert (again.work_id, again.record_id, again.matched_by) == (first.work_id, first.record_id, "new")
    assert queries.get_work(conn, first.work_id)["cited_by_count"] == 1


def test_late_shared_id_merges_two_works(conn):
    # Without IDs in common, OpenAlex and arXiv land on separate works (different years block the title match).
    oa = save_record(conn, dataclasses.replace(OPENALEX, arxiv_id=None))
    ax = save_record(conn, ARXIV)
    assert oa.work_id != ax.work_id
    # Semantic Scholar shares the MAG ID with one and the arXiv ID with the other: they merge.
    s2 = save_record(conn, S2)
    assert s2.merged_work_ids == [max(oa.work_id, ax.work_id)]
    assert conn.execute("SELECT count(DISTINCT work_id) AS n FROM work_records WHERE work_id IN (%s, %s)",
                        (oa.work_id, ax.work_id)).fetchone()["n"] == 1


def test_title_fallback(conn):
    s2 = save_record(conn, dataclasses.replace(S2, arxiv_id=None, mag_id=None))
    ax = save_record(conn, dataclasses.replace(ARXIV, arxiv_id=None, provider_work_id=f"9999.99999~{RUN}"))
    assert (ax.work_id, ax.matched_by) == (s2.work_id, "title")
    assert {"reason": "title_match", "work_id": s2.work_id} in [
        {"reason": item["reason"], "work_id": item["work_id"]} for item in queries.review_queue(conn, limit=100)
    ]


def test_find_and_search(conn):
    saved = save_record(conn, OPENALEX)
    assert queries.find_work(conn, Identifier("openalex", OPENALEX.provider_work_id))["work_id"] == saved.work_id
    assert queries.find_work(conn, Identifier("arxiv", OPENALEX.arxiv_id))["work_id"] == saved.work_id
    found = queries.search_works(conn, f"attention NEED {RUN}")
    assert saved.work_id in [w["work_id"] for w in found]
    assert queries.search_works(conn, RUN, year_from=2030) == []


def test_claim_flow(conn):
    work = save_record(conn, OPENALEX).work_id
    claim = claims.add_claim(conn, f"Self-attention alone can reach state-of-the-art translation quality {RUN}.", "scout")
    assert claim["created"]
    assert claims.add_claim(conn, f"  self-attention ALONE can reach state-of-the-art translation quality {RUN}.  ",
                            "lead") == {"claim_id": claim["claim_id"], "created": False}

    claims.assess_claim(conn, claim_id=claim["claim_id"], confidence=0.6, verdict="inconclusive",
                        rationale="Only one paper so far.", evidence=[], assessed_by="verifier")
    claims.assess_claim(conn, claim_id=claim["claim_id"], confidence=0.85, verdict="supported",
                        rationale="Transformer beats prior SOTA on WMT14 En-De.",
                        evidence=[{"work_id": work, "stance": "supports", "note": "Table 2"}],
                        assessed_by="verifier")

    detail = claims.get_claim(conn, claim["claim_id"])
    assert (detail["verdict"], float(detail["confidence"]), detail["assessment_count"]) == ("supported", 0.85, 2)
    assert detail["assessments"][0]["evidence"][0]["work_id"] == work
    assert detail["assessments"][1]["verdict"] == "inconclusive"  # history kept
    assert [c["claim_id"] for c in claims.list_claims(conn, verdict="supported", query=RUN)] == [claim["claim_id"]]
    assert queries.get_work(conn, work)["claims"][0]["stance"] == "supports"


def test_assessment_rules(conn):
    claim = claims.add_claim(conn, f"A claim used to test validation rules {RUN}.", "scout")["claim_id"]
    with pytest.raises(ValueError, match="needs at least one evidence item with stance 'supports'"):
        claims.assess_claim(conn, claim_id=claim, confidence=0.9, verdict="supported", rationale="x",
                            evidence=[], assessed_by="verifier")
    with pytest.raises(ValueError, match="between 0 and 1"):
        claims.assess_claim(conn, claim_id=claim, confidence=1.5, verdict="inconclusive", rationale="x",
                            evidence=[], assessed_by="verifier")
    with pytest.raises(ValueError, match="not in the database"):
        claims.assess_claim(conn, claim_id=claim, confidence=0.9, verdict="contradicted", rationale="x",
                            evidence=[{"work_id": -1, "stance": "contradicts"}], assessed_by="verifier")


def test_merge_keeps_evidence(conn):
    oa = save_record(conn, dataclasses.replace(OPENALEX, arxiv_id=None)).work_id
    ax = save_record(conn, ARXIV).work_id
    claim = claims.add_claim(conn, f"Evidence should survive a merge {RUN}.", "scout")["claim_id"]
    claims.assess_claim(conn, claim_id=claim, confidence=0.7, verdict="supported", rationale="x",
                        evidence=[{"work_id": max(oa, ax), "stance": "supports"}], assessed_by="verifier")
    save_record(conn, S2)  # shares MAG with oa and arXiv with ax: merges them
    evidence = claims.get_claim(conn, claim)["assessments"][0]["evidence"]
    assert [e["work_id"] for e in evidence] == [min(oa, ax)]


def test_run_sql_is_read_only_and_single_statement():
    from academic_db import db

    with db.reader() as conn:
        assert queries.run_sql(conn, "SELECT id FROM providers ORDER BY priority")["rows"][0] == {"id": "openalex"}
    with db.reader() as conn, pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        queries.run_sql(conn, "INSERT INTO claims (text, created_by) VALUES ('x', 'test')")
    with db.reader() as conn, pytest.raises(psycopg.errors.SyntaxError, match="multiple commands"):
        queries.run_sql(conn, "SELECT 1; DELETE FROM claims")
    with db.reader() as conn:
        result = queries.run_sql(conn, "SELECT generate_series(1, 500) AS n", max_rows=10)
        assert len(result["rows"]) == 10 and result["truncated"]


def test_get_work_has_abstract_and_quality(conn):
    work_id = save_record(conn, OPENALEX).work_id
    save_record(conn, S2)
    save_record(conn, ARXIV)
    work = queries.get_work(conn, work_id)
    assert work["abstract"].startswith("The dominant sequence transduction models")
    assert work["abstract_source"] == "arxiv"  # authors' own text wins for abstracts
    assert work["quality"]["providers"] == ["openalex", "semantic_scholar", "arxiv"]
    assert work["quality"]["year_spread"] == OPENALEX.publication_year - 2017  # OpenAlex's repost year
    assert work["quality"]["is_retracted"] is False and work["is_retracted"] is False
    assert work["quality"]["preprint_only"] is False  # Semantic Scholar says conference paper


def test_claim_provenance_and_near_duplicates(conn):
    work_id = save_record(conn, ARXIV).work_id
    first = claims.add_claim(conn, f"Self-attention alone can reach state-of-the-art translation quality {RUN}.",
                             "scout", source_work_id=work_id, source_quote="We propose the Transformer...")
    assert first["created"]
    near = claims.add_claim(conn, f"Self-attention alone reaches state-of-the-art translation quality {RUN}.", "scout")
    assert near["claim_id"] is None and near["similar_claims"][0]["claim_id"] == first["claim_id"]
    forced = claims.add_claim(conn, f"Self-attention alone reaches state-of-the-art translation quality {RUN}.",
                              "scout", allow_similar=True)
    assert forced["created"]

    detail = claims.get_claim(conn, first["claim_id"])
    assert detail["source"]["work_id"] == work_id and detail["source_quote"] == "We propose the Transformer..."
    assert [c["claim_id"] for c in queries.get_work(conn, work_id)["claims_from_this_paper"]] == [first["claim_id"]]
    with pytest.raises(ValueError, match="not in the database"):
        claims.add_claim(conn, f"Unrelated claim with a missing source {RUN}.", "scout", source_work_id=-1)


def test_merge_moves_claim_sources(conn):
    oa = save_record(conn, dataclasses.replace(OPENALEX, arxiv_id=None)).work_id
    ax = save_record(conn, ARXIV).work_id
    claim = claims.add_claim(conn, f"A claim taken from the arXiv record {RUN}.", "scout",
                             source_work_id=max(oa, ax))["claim_id"]
    save_record(conn, S2)  # merges oa and ax
    assert claims.get_claim(conn, claim)["source_work_id"] == min(oa, ax)


def test_save_fetched_resolves_work_ids_after_merges(conn):
    papers = [
        ingest.PaperFetch(Identifier("openalex", OPENALEX.provider_work_id), {},
                          records={"openalex": dataclasses.replace(OPENALEX, arxiv_id=None)}),
        ingest.PaperFetch(Identifier("arxiv", ARXIV.arxiv_id), {}, records={"arxiv": ARXIV}),
        ingest.PaperFetch(Identifier("semantic_scholar", S2.provider_work_id), {}, records={"semantic_scholar": S2}),
        ingest.PaperFetch(Identifier("doi", f"10.99999/{RUN}/missing"), {}, status={"openalex": "not_found"}),
    ]
    for paper in papers:
        for provider in ("openalex", "semantic_scholar", "arxiv"):
            paper.status.setdefault(provider, "found" if provider in paper.records else "skipped")
    results = ingest.save_fetched(conn, papers)
    # The third save merges the first two works, so all three resolve to the surviving work.
    assert len({r.work_id for r in results[:3]}) == 1 and results[3].work_id is None
    assert results[0].providers["openalex"] == "saved" and results[3].providers["openalex"] == "not_found"
