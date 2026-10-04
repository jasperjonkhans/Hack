"""Offline regression tests. Run with Omnigent's Python via unittest discovery."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

LAB = Path(__file__).resolve().parents[1] / 'lab'
TOOLS = LAB.parent / 'tools/literature/src/literature_tools'

def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

OA = load('openalex_search')
EP = load('europepmc_search')
AX = load('arxiv_search')
SUP = load('search_support')

def work(identifier='W1'):
    return {'id': 'https://openalex.org/' + identifier, 'title': 'A paper',
            'abstract_inverted_index': {'word': list(range(200))},
            'doi': 'https://doi.org/10.1234/ABC', 'is_retracted': True}

def oa_page(identifier='W1', total=2, cursor='next'):
    return json.dumps({'meta': {'count': total, 'next_cursor': cursor}, 'results': [work(identifier)]}).encode()

class SearchTests(unittest.TestCase):
    def test_full_abstract_and_normalised_doi_and_status(self):
        with patch.object(OA._s, 'fetch', return_value=oa_page()):
            paper = OA.openalex_search('test', detail='full')['results'][0]
        self.assertGreater(len(paper['abstract']), 500)
        self.assertFalse(paper['abstract_truncated'])
        self.assertEqual(paper['doi'], '10.1234/abc')
        self.assertTrue(paper['is_retracted'])

    def test_compact_is_default_and_keeps_identity_and_status(self):
        with patch.object(OA._s, 'fetch', return_value=oa_page()):
            paper = OA.openalex_search('test')['results'][0]
        self.assertNotIn('abstract', paper)
        self.assertLessEqual(len(paper['snippet']), 201)
        self.assertTrue(paper['snippet'].endswith('…'))
        self.assertEqual(paper['doi'], '10.1234/abc')
        self.assertTrue(paper['is_retracted'])
        self.assertIn('openalex', paper['identifiers'])

    def test_detail_can_change_between_pages(self):
        with patch.object(OA._s, 'fetch', side_effect=[oa_page(), oa_page('W2', cursor=None)]):
            first = OA.openalex_search('test')
            second = OA.openalex_search('test', detail='full', cursor=first['next_cursor'])
        self.assertNotIn('error', second)
        self.assertIn('abstract', second['results'][0])

    def test_empty_or_null_cursor_starts_a_new_search(self):
        for cursor in ('', 'null', 'None'):
            with patch.object(OA._s, 'fetch', return_value=oa_page()):
                self.assertNotIn('error', OA.openalex_search('test', cursor=cursor), cursor)

    def test_invalid_detail_is_rejected(self):
        self.assertEqual(OA.openalex_search('test', detail='short')['error']['code'], 'invalid_arguments')

    def test_cursor_retrieves_next_page_and_finishes(self):
        with patch.object(OA._s, 'fetch', side_effect=[oa_page(), oa_page('W2', cursor=None)]):
            first = OA.openalex_search('test')
            second = OA.openalex_search('test', cursor=first['next_cursor'])
        self.assertTrue(first['has_more'])
        self.assertFalse(second['has_more'])
        self.assertIsNone(second['next_cursor'])
        self.assertNotEqual(first['results'][0]['id'], second['results'][0]['id'])

    def test_cursor_cannot_be_reused_for_different_search(self):
        with patch.object(OA._s, 'fetch', return_value=oa_page()):
            first = OA.openalex_search('test')
        self.assertEqual(OA.openalex_search('other', cursor=first['next_cursor'])['error']['code'], 'invalid_arguments')

    def test_sort_does_not_change_search_scope(self):
        from urllib.parse import parse_qs, urlsplit
        with patch.object(OA._s, 'fetch', return_value=oa_page()) as fetch:
            OA.openalex_search('test', sort='citations')
            citations = parse_qs(urlsplit(fetch.call_args.args[1]).query)
            OA.openalex_search('test', sort='relevance')
            relevance = parse_qs(urlsplit(fetch.call_args.args[1]).query)
        self.assertEqual(citations['search.title_and_abstract'], relevance['search.title_and_abstract'])
        self.assertNotIn('filter', citations)

    def test_invalid_inputs_are_rejected_before_network(self):
        for module, fn in ((OA, OA.openalex_search), (EP, EP.europepmc_search), (AX, AX.arxiv_search)):
            for args in ({'query': ''}, {'query': 'x', 'limit': 0}, {'query': 'x', 'limit': True}, {'query': 'x', 'sort': 'bad'}, {'query': 'x', 'cursor': 'bad'}):
                with self.subTest(fn=fn.__name__, args=args), patch.object(module._s, 'fetch') as fetch:
                    self.assertEqual(fn(**args)['error']['code'], 'invalid_arguments')
                    fetch.assert_not_called()

    def test_malformed_json_is_not_an_empty_search(self):
        for module, fn in ((OA, OA.openalex_search), (EP, EP.europepmc_search)):
            for body in (b'<html>', b'{"error":"bad"}', b'{}', b'[]'):
                with self.subTest(fn=fn.__name__, body=body), patch.object(module._s, 'fetch', return_value=body):
                    self.assertEqual(fn('test')['error']['code'], 'invalid_response')

    def test_empty_valid_response_is_success(self):
        with patch.object(OA._s, 'fetch', return_value=b'{"meta":{"count":0},"results":[]}'):
            result = OA.openalex_search('test')
        self.assertEqual(result['results'], [])
        self.assertFalse(result['has_more'])

    def test_empty_page_cannot_claim_search_is_complete(self):
        with patch.object(OA._s, 'fetch', return_value=b'{"meta":{"count":20},"results":[]}'):
            result = OA.openalex_search('test')
        self.assertEqual(result['error']['code'], 'invalid_response')
        self.assertTrue(result['error']['retryable'])

    def test_missing_continuation_is_an_error(self):
        with patch.object(OA._s, 'fetch', return_value=oa_page(cursor=None)):
            self.assertEqual(OA.openalex_search('test')['error']['code'], 'invalid_response')

    def test_arxiv_error_feed_is_not_a_paper(self):
        feed = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/api/errors#bad</id><title>Error</title><summary>bad query</summary></entry></feed>'
        with patch.object(AX._s, 'fetch', return_value=feed):
            self.assertEqual(AX.arxiv_search('test')['error']['code'], 'invalid_query')

    def test_arxiv_boolean_requires_advanced_mode(self):
        self.assertEqual(AX.arxiv_search('electron OR proton')['error']['code'], 'invalid_arguments')

    def test_arxiv_advanced_query_is_preserved(self):
        from urllib.parse import parse_qs, urlsplit
        feed = b'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:o="http://a9.com/-/spec/opensearch/1.1/"><o:totalResults>0</o:totalResults></feed>'
        with patch.object(AX._s, 'fetch', return_value=feed) as fetch:
            result = AX.arxiv_search('all:electron OR all:proton', query_mode='advanced')
        self.assertNotIn('error', result)
        self.assertEqual(parse_qs(urlsplit(fetch.call_args.args[1]).query)['search_query'], ['all:electron OR all:proton'])

    def test_europepmc_handles_nullable_metadata_and_retractions(self):
        data = {'hitCount': 1, 'resultList': {'result': [{'source': 'MED', 'id': '123', 'title': 'Study', 'journalInfo': None, 'bookOrReportDetails': None, 'pubTypeList': {'pubType': ['Retracted Publication']}, 'abstractText': '<p>p &lt; 0.05</p>'}]}}
        data = {'hitCount': 1, 'result': data['resultList']['result'][0]}
        with patch.object(EP._s, 'fetch', return_value=json.dumps(data).encode()) as fetch:
            paper = EP.europepmc_get_paper('123')['paper']
        self.assertIn('/article/MED/123?', fetch.call_args.args[1])
        self.assertTrue(paper['is_retracted'])
        self.assertEqual(paper['abstract'], 'p < 0.05')
        self.assertEqual(paper['identifiers']['pmid'], '123')

    def test_openalex_lookup_accepts_doi_url(self):
        with patch.object(OA._s, 'fetch', return_value=json.dumps(work()).encode()):
            self.assertEqual(OA.openalex_get_paper('https://doi.org/10.1234/abc')['paper']['doi'], '10.1234/abc')

    def test_agent_tool_files_load_in_omnigent(self):
        from omnigent.tools.local import _import_tool_module, _extract_decorated_functions
        expected = {
            'scout': {'openalex_search', 'openalex_get_paper', 'arxiv_search', 'arxiv_get_paper',
                      'europepmc_search', 'europepmc_get_paper', 'crossref_search',
                      'zbmath_search', 'zbmath_get_paper', 'loogle_search', 'read_full_text'},
            'verifier': {'crossref_get_paper', 'openalex_get_paper', 'arxiv_get_paper',
                         'europepmc_get_paper', 'zbmath_get_paper', 'unpaywall_find_full_text',
                         'loogle_search', 'check_quote', 'read_full_text'},
        }
        for agent, names in expected.items():
            # Omnigent copies only the agent folder when it is run on its own.
            with tempfile.TemporaryDirectory() as directory:
                copy = Path(directory) / agent
                shutil.copytree(LAB / 'agents' / agent, copy, ignore=shutil.ignore_patterns('__pycache__'))
                found = set()
                for path in (copy / 'tools/python').glob('*.py'):
                    module = _import_tool_module(agent_name=agent, tool_path=path.resolve())
                    names_in_file = [name for name, _, _ in _extract_decorated_functions(agent_name=agent, tool_path=path, module=module)]
                    # Omnigent only dispatches a local tool whose name matches its file name.
                    self.assertEqual(names_in_file, [path.stem], path.name)
                    found |= set(names_in_file)
            self.assertEqual(found, names, agent)

class TransportTests(unittest.TestCase):
    def test_authentication_failure_is_not_retried(self):
        error = urllib.error.HTTPError('https://example.org', 401, 'Denied', {}, io.BytesIO(b''))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SEARCH_STATE_DIR=directory), patch.object(SUP.urllib.request, 'urlopen', side_effect=error) as fetch:
            with self.assertRaises(SUP.SearchError) as caught:
                SUP.fetch('openalex', 'https://example.org')
        self.assertEqual(caught.exception.details['code'], 'authentication')
        self.assertFalse(caught.exception.details['retryable'])
        self.assertEqual(fetch.call_count, 1)

    def test_long_retry_after_returns_delay_without_sleeping(self):
        error = urllib.error.HTTPError('https://example.org', 429, 'Busy', {'Retry-After': '120'}, io.BytesIO(b''))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SEARCH_STATE_DIR=directory), patch.object(SUP.urllib.request, 'urlopen', side_effect=error), patch.object(SUP.time, 'sleep') as sleep:
            with self.assertRaises(SUP.SearchError) as caught:
                SUP.fetch('openalex', 'https://example.org')
        self.assertEqual(caught.exception.details['retry_after_seconds'], 120)
        sleep.assert_not_called()

    def test_temporary_failure_recovers_after_retry_after(self):
        error = urllib.error.HTTPError('https://example.org', 503, 'Busy', {'Retry-After': '2'}, io.BytesIO(b''))
        response = io.BytesIO(b'{}')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SEARCH_STATE_DIR=directory), patch.object(SUP.urllib.request, 'urlopen', side_effect=[error, response]), patch.object(SUP.time, 'sleep') as sleep:
            result = SUP.fetch('openalex', 'https://example.org')
        self.assertEqual(result, b'{}')
        self.assertIn(unittest.mock.call(2.0), sleep.call_args_list)

    def test_daily_quota_does_not_retry(self):
        error = urllib.error.HTTPError('https://example.org', 429, 'Quota', {'X-RateLimit-Remaining': '0'}, io.BytesIO(b'daily budget exceeded'))
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, SEARCH_STATE_DIR=directory), patch.object(SUP.urllib.request, 'urlopen', side_effect=error) as fetch:
            with self.assertRaises(SUP.SearchError) as caught:
                SUP.fetch('openalex', 'https://example.org')
        self.assertEqual(caught.exception.details['code'], 'quota_exhausted')
        self.assertEqual(fetch.call_count, 1)

    def test_spacing_is_shared_between_subprocesses(self):
        code = '''import importlib.util,sys,time
s=importlib.util.spec_from_file_location('support',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
with m.request_slot('test',0.08):
 print(time.time(),flush=True)
'''
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, 'SEARCH_STATE_DIR': directory}
            processes = [subprocess.Popen([sys.executable, '-c', code, str(TOOLS / 'search_support.py')], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
            outputs = [p.communicate(timeout=15) for p in processes]
        self.assertEqual([p.returncode for p in processes], [0, 0, 0], outputs)
        starts = sorted(float(out) for out, err in outputs)
        self.assertGreaterEqual(starts[1] - starts[0], 0.07)
        self.assertGreaterEqual(starts[2] - starts[1], 0.07)

if __name__ == '__main__':
    unittest.main()
