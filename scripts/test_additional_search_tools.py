"""Regression coverage for Crossref, zbMATH, Unpaywall and Loogle."""
import json
import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from test_search_tools import load

CR = load('crossref_search')
ZB = load('zbmath_search')
UP = load('unpaywall_lookup')
LO = load('loogle_search')
SUP = load('search_support')


def crossref_page(items, total=2, cursor='next'):
    return json.dumps({'status': 'ok', 'message': {'items': items, 'total-results': total, 'next-cursor': cursor}}).encode()


def oa_record(**changes):
    return {'doi': '10.1234/test', 'is_oa': False, 'oa_status': 'closed', 'oa_locations': [], 'best_oa_location': None, **changes}


def unpaywall_response(data):
    with patch.dict(os.environ, CONTACT_EMAIL='test@example.org'), patch.object(UP._s, 'fetch', return_value=json.dumps(data).encode()):
        return UP.unpaywall_find_full_text('10.1234/test')


class CrossrefTests(unittest.TestCase):
    def test_author_hint_is_explicit_in_schema_and_result(self):
        from omnigent_client.tools import get_tool_metadata
        properties = get_tool_metadata(CR.crossref_search).json_schema['properties']
        self.assertIn('author_query', properties)
        self.assertNotIn('author', properties)
        with patch.object(CR._s, 'fetch', return_value=crossref_page([], total=0)) as fetch:
            result = CR.crossref_search('prime gaps', author_query='James Maynard')
        self.assertIn('fuzzy', result['warning'])
        self.assertEqual(parse_qs(urlsplit(fetch.call_args.args[1]).query)['query.author'], ['James Maynard'])

    def test_skipped_record_on_final_page_does_not_create_false_continuation(self):
        items = [{'DOI': '10.1234/a', 'title': ['A']}, {'DOI': '10.1234/b'}]
        with patch.object(CR._s, 'fetch', return_value=crossref_page(items)):
            result = CR.crossref_search('test', limit=2)
        self.assertFalse(result['has_more'])
        self.assertIsNone(result['next_cursor'])
        self.assertEqual(result['returned_count'], 1)
        self.assertEqual(result['fetched_count'], 2)
        self.assertEqual(result['skipped_count'], 1)

    def test_fully_skipped_page_can_continue_to_valid_page(self):
        pages = [crossref_page([{'DOI': '10.1234/a'}]), crossref_page([{'DOI': '10.1234/b', 'title': ['B']}], cursor=None)]
        with patch.object(CR._s, 'fetch', side_effect=pages):
            first = CR.crossref_search('test', limit=1)
            second = CR.crossref_search('test', limit=1, cursor=first['next_cursor'])
        self.assertEqual(first['results'], [])
        self.assertTrue(first['has_more'])
        self.assertEqual(second['results'][0]['title'], 'B')
        self.assertFalse(second['has_more'])


class ZbmathTests(unittest.TestCase):
    def test_doi_less_repeated_record_is_one_work(self):
        paper = SUP.record('zbmath', '123', title='A')
        result = SUP.deduplicate_papers([paper, paper])
        self.assertEqual(result['unique_count'], 1)
        self.assertEqual(len(result['groups'][0]['records']), 2)

    def test_zbl_links_records_with_different_provider_ids(self):
        papers = [SUP.record('zbmath', '123', title='A', identifiers={'zbl': '0923.11018'}), SUP.record('zbmath', '456', title='A', identifiers={'zbl': '0923.11018'})]
        self.assertEqual(SUP.deduplicate_papers(papers)['unique_count'], 1)

    def test_provider_ids_are_namespaced(self):
        papers = [SUP.record('zbmath', '123', title='A'), SUP.record('other', '123', title='A')]
        self.assertEqual(SUP.deduplicate_papers(papers)['unique_count'], 2)

    def test_skipped_final_record_completes_search(self):
        data = {'result': [{'id': 123, 'title': {'title': 'A'}}, {'id': 456}], 'status': {'nr_total_results': 2}}
        with patch.object(ZB._s, 'fetch', return_value=json.dumps(data).encode()):
            result = ZB.zbmath_search('test', limit=2)
        self.assertFalse(result['has_more'])
        self.assertEqual(result['skipped_count'], 1)


class UnpaywallTests(unittest.TestCase):
    def test_malformed_or_mismatched_records_do_not_mean_closed_access(self):
        for data in ({}, oa_record(doi='10.1234/other'), oa_record(is_oa='false'), oa_record(oa_locations=None), oa_record(best_oa_location=[]), oa_record(is_oa=True, oa_status="green", best_oa_location={}), oa_record(is_oa=True)):
            with self.subTest(data=data):
                self.assertEqual(unpaywall_response(data)['error']['code'], 'invalid_response')

    def test_valid_closed_record_remains_a_valid_negative(self):
        result = unpaywall_response(oa_record())
        self.assertFalse(result['is_oa'])
        self.assertEqual(result['locations'], [])

    def test_best_location_is_first_and_not_duplicated(self):
        best = {'url_for_pdf': 'https://example.org/final.pdf', 'version': 'publishedVersion'}
        other = {'url_for_pdf': 'https://example.org/draft.pdf', 'version': 'submittedVersion'}
        result = unpaywall_response(oa_record(is_oa=True, oa_status='green', best_oa_location=best, oa_locations=[other, best]))
        self.assertEqual(result['locations'][0], result['best_location'])
        self.assertEqual(len(result['locations']), 2)
        self.assertEqual(result['locations'][0]['pdf_url'], best['url_for_pdf'])

    def test_negative_lookup_does_not_instruct_agent_to_stop_searching(self):
        self.assertNotIn('do not look for one elsewhere', UP.unpaywall_find_full_text.__doc__)
        self.assertIn('Other legitimate repositories', UP.unpaywall_find_full_text.__doc__)


class LoogleTests(unittest.TestCase):
    def test_malformed_payload_is_not_an_empty_result(self):
        for data in ({}, {'hits': []}, {'count': 0}, {'hits': [], 'count': -1}, {'hits': [], 'count': True}, {'hits': [{}], 'count': 1}):
            with self.subTest(data=data), patch.object(LO._s, 'fetch', return_value=json.dumps(data).encode()):
                self.assertEqual(LO.loogle_search('Nat.Prime')['error']['code'], 'invalid_response')

    def test_valid_empty_result_is_not_truncated(self):
        with patch.object(LO._s, 'fetch', return_value=b'{"hits":[],"count":0}'):
            result = LO.loogle_search('"unknownword"')
        self.assertEqual(result['results'], [])
        self.assertFalse(result['truncated'])
        self.assertNotIn('warning', result)

    def test_provider_and_caller_truncation_are_reported(self):
        hits = [{'module': 'Mathlib.Test', 'name': 'A', 'type': 'Nat'}, {'module': 'Mathlib.Test', 'name': 'B', 'type': 'Nat'}]
        for count, limit in ((2000, 200), (2, 1)):
            with self.subTest(count=count, limit=limit), patch.object(LO._s, 'fetch', return_value=json.dumps({'hits': hits, 'count': count}).encode()):
                result = LO.loogle_search('Nat', limit=limit)
                self.assertTrue(result['truncated'])
                self.assertFalse(result['pagination_supported'])
                self.assertIn('Narrow the query', result['warning'])

    def test_complete_result_is_not_truncated(self):
        data = {'hits': [{'module': 'Mathlib.Test', 'name': 'A', 'type': 'Nat'}], 'count': 1}
        with patch.object(LO._s, 'fetch', return_value=json.dumps(data).encode()):
            result = LO.loogle_search('Nat')
        self.assertFalse(result['truncated'])


if __name__ == '__main__':
    unittest.main()
