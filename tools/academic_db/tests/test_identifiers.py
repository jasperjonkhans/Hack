import pytest

from academic_db.identifiers import (
    Identifier,
    arxiv_id_from_doi,
    normalize_arxiv_id,
    normalize_doi,
    parse_identifier,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://doi.org/10.1038/Nature14539", "10.1038/nature14539"),
        ("http://dx.doi.org/10.1038/nature14539", "10.1038/nature14539"),
        ("doi:10.18653/V1/2020.ACL-MAIN.447", "10.18653/v1/2020.acl-main.447"),
        ("  10.1038/nature14539 ", "10.1038/nature14539"),
        ("", None),
        ("not a doi", None),
    ],
)
def test_normalize_doi(raw, expected):
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1706.03762", "1706.03762"),
        ("arXiv:1706.03762v5", "1706.03762"),
        ("http://arxiv.org/abs/1706.03762v7", "1706.03762"),
        ("https://arxiv.org/pdf/1706.03762v5.pdf", "1706.03762"),
        ("cond-mat/0410550", "cond-mat/0410550"),
        ("math.GT/0309136v2", "math.GT/0309136"),
        ("W2626778328", None),
    ],
)
def test_normalize_arxiv_id(raw, expected):
    assert normalize_arxiv_id(raw) == expected


def test_arxiv_id_from_doi():
    assert arxiv_id_from_doi("10.48550/arxiv.1706.03762") == "1706.03762"
    assert arxiv_id_from_doi("10.1038/nature14539") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1038/nature14539", Identifier("doi", "10.1038/nature14539")),
        ("https://doi.org/10.48550/arXiv.1706.03762", Identifier("arxiv", "1706.03762")),
        ("arXiv:1706.03762v5", Identifier("arxiv", "1706.03762")),
        ("https://openalex.org/W2626778328", Identifier("openalex", "W2626778328")),
        ("w2626778328", Identifier("openalex", "W2626778328")),
        (
            "https://www.semanticscholar.org/paper/Attention-is-All-you-Need/204e3073870fae3d05bcbc2f6a8e263d9b72e776",
            Identifier("semantic_scholar", "204e3073870fae3d05bcbc2f6a8e263d9b72e776"),
        ),
    ],
)
def test_parse_identifier(raw, expected):
    assert parse_identifier(raw) == expected


def test_parse_identifier_rejects_garbage():
    with pytest.raises(ValueError, match="Unrecognized identifier"):
        parse_identifier("attention is all you need")
