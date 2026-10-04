import pytest
from conftest import load_json, load_text

from academic_db.normalize import from_arxiv, from_arxiv_feed, from_openalex, from_payload, from_semantic_scholar


def test_openalex_attention():
    rec = from_openalex(load_json("openalex_W2626778328.json"))
    assert rec.provider_work_id == "W2626778328"
    assert rec.arxiv_id == "1706.03762"  # from locations; OpenAlex ids has no arXiv entry
    assert rec.mag_id == 2626778328
    assert rec.doi and rec.doi == rec.doi.lower() and rec.doi.startswith("10.")
    assert rec.cited_by_count and rec.cited_by_count > 0
    assert rec.authors and rec.authors[0].author_id.startswith("A")


def test_openalex_journal_paper():
    rec = from_openalex(load_json("openalex_nature14539.json"))
    assert rec.doi == "10.1038/nature14539"
    assert rec.source_id == "S137773608"
    assert rec.title == "Deep learning"
    assert rec.oa_status in {"diamond", "gold", "green", "hybrid", "bronze", "closed"}


def test_semantic_scholar_attention():
    rec = from_semantic_scholar(load_json("s2_attention.json"))
    assert rec.provider_work_id == "204e3073870fae3d05bcbc2f6a8e263d9b72e776"
    assert rec.arxiv_id == "1706.03762"
    assert rec.mag_id == 2626778328
    assert rec.doi is None
    assert rec.oa_url is None  # API returns "" when there is no PDF
    assert rec.oa_status is None
    assert rec.type == "conference-paper"  # Conference beats JournalArticle
    assert rec.publication_year == 2017
    assert rec.authors[0].name == "Ashish Vaswani" and rec.authors[0].author_id == "40348417"


def test_semantic_scholar_uppercase_doi_and_status():
    rec = from_semantic_scholar(load_json("s2_s2orc.json"))
    assert rec.doi == "10.18653/v1/2020.acl-main.447"
    assert rec.oa_status == "gold"
    assert rec.oa_url.endswith(".pdf")
    assert rec.source_id == "1e33b3be-b2ab-46e9-96e8-d4eb4bad6e44"


@pytest.mark.parametrize(
    ("types", "expected"),
    [(["Journal Article", "Review"], "review"), (["JournalArticle"], "article"), (["News"], "other"), ([], None)],
)
def test_semantic_scholar_type_mapping(types, expected):
    paper = load_json("s2_attention.json") | {"publicationTypes": types}
    assert from_semantic_scholar(paper).type == expected


def test_arxiv_feed():
    rec = from_arxiv(load_text("arxiv_1706.03762.xml"))
    assert rec.provider_work_id == "1706.03762"
    assert rec.title == "Attention Is All You Need"  # line breaks collapsed
    assert rec.publication_year == 2017
    assert (rec.type, rec.is_oa, rec.oa_status) == ("preprint", True, "green")
    assert rec.oa_url.startswith("https://arxiv.org/pdf/1706.03762")
    assert rec.authors[0].name == "Ashish Vaswani" and rec.authors[0].author_id is None
    assert rec.raw["entry_xml"].startswith("<entry")


def test_from_payload_accepts_arxiv_raw_dict():
    entry_xml = from_arxiv(load_text("arxiv_1706.03762.xml")).raw["entry_xml"]
    assert from_payload("arxiv", {"entry_xml": entry_xml}).provider_work_id == "1706.03762"


def test_missing_title_is_rejected():
    with pytest.raises(ValueError, match="no title"):
        from_semantic_scholar(load_json("s2_attention.json") | {"title": "  "})


def test_wrong_payload_shape():
    with pytest.raises(ValueError, match="Unsupported payload"):
        from_payload("openalex", "<entry/>")


def test_abstracts():
    openalex = from_openalex(load_json("openalex_W2626778328.json"))
    assert openalex.abstract.startswith("The dominant sequence transduction models")  # rebuilt from inverted index
    arxiv = from_arxiv(load_text("arxiv_1706.03762.xml"))
    assert arxiv.abstract.startswith("The dominant sequence transduction models") and "\n" not in arxiv.abstract
    assert from_semantic_scholar(load_json("s2_attention.json") | {"abstract": " An abstract. "}).abstract == "An abstract."


def test_arxiv_feed_with_several_entries_skips_error_entries():
    feed = load_text("arxiv_1706.03762.xml")
    entry = feed[feed.index("<entry"):feed.index("</entry>") + len("</entry>")]
    error = ("<entry><id>http://arxiv.org/api/errors#incorrect_id_format_for_9999</id>"
             "<title>Error</title></entry>")
    two = feed.replace(entry, entry + error + entry.replace("1706.03762", "1810.04805"))
    assert [r.provider_work_id for r in from_arxiv_feed(two)] == ["1706.03762", "1810.04805"]
