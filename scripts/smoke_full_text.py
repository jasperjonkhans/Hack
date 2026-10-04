"""Live checks for the Verifier's full-text tools (Europe PMC, arXiv HTML, PDF, abstract fallback)."""
import os
import tempfile

from smoke_additional_search_tools import load, success

FT = load('full_text')


def europepmc():
    result = success(FT.check_quote('PMC3164645', 'The more prevalent C. neoformans is ubiquitously distributed worldwide and a common cause of meningitis in immunocompromised hosts'))
    assert (result['text_source'], result['verdict']) == ('europepmc_xml', 'exact'), result
    assert result['location']['section'] == 'Introduction'
    outline = success(FT.read_full_text('PMC3164645'))['outline']
    assert len(outline) > 5 and not any('References' in s['heading'] for s in outline)


def arxiv():
    result = success(FT.check_quote('1706.03762', 'We trained the base models for a total of 100,000 steps or 10 hours'))
    assert (result['text_source'], result['verdict']) == ('arxiv_html', 'near_exact'), result
    assert result['differences'] == [{'quote': '10', 'paper': '12'}]
    assert '5.2' in result['location']['section']
    altered = success(FT.check_quote('10.48550/arXiv.1706.03762', 'The Transformer uses only convolutions and dispenses with attention mechanisms entirely'))
    assert altered['verdict'] in ('partial', 'not_found'), altered


def pdf():
    # Pre-2000 arXiv paper without HTML: arXiv PDF; its text layer splits "supergravity".
    result = success(FT.check_quote('hep-th/9711200', 'a sector describing supergravity on the product of Anti-deSitter spacetimes'))
    assert (result['text_source'], result['verdict']) == ('pdf', 'exact'), result
    assert result['location']['section'] == 'Page 1'
    # Publisher PDF found via OpenAlex; not in Europe PMC or arXiv.
    result = success(FT.check_quote('10.5194/gmd-9-1937-2016', 'The Coupled Model Intercomparison Project'))
    assert result['text_source'] == 'pdf' and result['verdict'] == 'exact', result


def abstract_fallback():
    result = success(FT.check_quote('10.1038/nature14539', 'deep learning allows computational models that are composed of multiple processing layers'))
    assert (result['checked_against'], result['verdict']) == ('abstract', 'exact'), result


def main():
    os.environ.setdefault('SEARCH_STATE_DIR', tempfile.mkdtemp())
    failures = 0
    for check in (europepmc, arxiv, pdf, abstract_fallback):
        try:
            check()
            print(f'PASS {check.__name__}', flush=True)
        except (AssertionError, KeyError, IndexError) as exc:
            failures += 1
            print(f'FAIL {check.__name__}: {exc}', flush=True)
    return int(failures > 0)


if __name__ == '__main__':
    raise SystemExit(main())
