"""Live checks for the additional tools; requires CONTACT_EMAIL for Unpaywall."""
import importlib.util
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / 'lab/agents/researcher/tools/python'


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def success(result):
    assert 'error' not in result, result.get('error')
    return result


def crossref():
    m = load('crossref_search')
    first = success(m.crossref_search('prime gaps', author_query='James Maynard', limit=3))
    assert 'fuzzy' in first['warning']
    assert first['next_cursor']
    second = success(m.crossref_search('prime gaps', author_query='James Maynard', limit=3, cursor=first['next_cursor']))
    first_ids = {p['id'] for p in first['results']}
    second_ids = {p['id'] for p in second['results']}
    assert second_ids - first_ids, 'Crossref continuation made no progress'
    merged = success(load('search_support').deduplicate_papers(first['results'] + second['results']))
    assert merged['unique_count'] == len(first_ids | second_ids)
    if first_ids & second_ids:
        print(f'  Crossref upstream overlap: {len(first_ids & second_ids)} DOI(s); deduplication verified', flush=True)
    assert success(m.crossref_get_paper('10.1038/nature14539'))['paper']['doi'] == '10.1038/nature14539'


def zbmath():
    m = load('zbmath_search')
    first = success(m.zbmath_search('ti:"prime gaps"', limit=3, from_year=2020))
    assert first['next_cursor']
    assert all(p['year'] >= 2020 for p in first['results'])
    second = success(m.zbmath_search('ti:"prime gaps"', limit=3, from_year=2020, cursor=first['next_cursor']))
    assert not ({p['id'] for p in first['results']} & {p['id'] for p in second['results']})
    assert success(m.zbmath_get_paper(first['results'][0]['id']))['paper']['id'] == first['results'][0]['id']


def loogle():
    m = load('loogle_search')
    result = success(m.loogle_search('Nat.Prime', limit=2))
    assert len(result['results']) == 2
    assert result['truncated'] and result['warning']
    assert result['pagination_supported'] is False
    error = m.loogle_search('NoSuchName_xyz')['error']
    assert error['code'] == 'invalid_query'


def unpaywall():
    m = load('unpaywall_lookup')
    for doi in ('10.1038/nature14539', '10.1371/journal.pone.0000308'):
        result = success(m.unpaywall_find_full_text(doi))
        assert result['doi'] == doi
        assert type(result['is_oa']) is bool
        if result['is_oa']:
            assert result['locations'][0] == result['best_location']
        else:
            assert result['locations'] == []
        print(f"  {doi}: is_oa={result['is_oa']}, locations={len(result['locations'])}", flush=True)


def main():
    failures = 0
    for check in (crossref, zbmath, loogle, unpaywall):
        try:
            check()
            print(f'PASS {check.__name__}', flush=True)
        except (AssertionError, KeyError, IndexError) as exc:
            failures += 1
            print(f'FAIL {check.__name__}: {exc}', flush=True)
    return int(failures > 0)


if __name__ == '__main__':
    raise SystemExit(main())
