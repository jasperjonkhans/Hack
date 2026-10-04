"""Offline coverage for the Verifier's full-text tools."""
import importlib.util
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from test_search_tools import load

FT = load('full_text')

JATS = b'''<article><front><article-meta><abstract><p>We study gene flow in fungi.</p></abstract></article-meta></front>
<body>
<sec><title>Introduction</title>
<p>The fungus is common in tropical regions <xref ref-type="bibr" rid="r1">[1]</xref>, <xref ref-type="bibr" rid="r2">[2]</xref>.</p>
<p>Infections were rare before 1999.</p></sec>
<sec><title>Results</title>
<sec><title>Growth</title><p>Strain A grew 40% faster than strain B at 37 degrees.</p>
<fig><caption><p>Growth curves for both strains.</p></caption></fig></sec></sec>
<sec><title>References</title><p>The fungus is common in tropical regions, as cited.</p></sec>
</body></article>'''

LATEXML = b'''<html><body>
<header class="arxiv-html-header"><p class="ltx_p">Banner text, not the paper.</p></header>
<article class="ltx_document ltx_authors_1line">
<div class="ltx_authors"><p class="ltx_p">Ada Lovelace, Example University</p></div>
<div class="ltx_abstract"><h6 class="ltx_title ltx_title_abstract">Abstract</h6>
<p class="ltx_p">We introduce a sparse attention method for long documents.</p></div>
<section class="ltx_section"><h2 class="ltx_title ltx_title_section">1 Introduction</h2>
<div class="ltx_para"><p class="ltx_p">Attention cost grows quadratically <cite class="ltx_cite">[<a href="#b1">3</a>]</cite> with length
<math alttext="n^{2}"><mi>n</mi></math> in practice.</p></div>
<div class="ltx_para"><p class="ltx_p">Our method reduces memory use by half<span class="ltx_note"><span class="ltx_note_content">A footnote.</span></span>.</p></div>
<section class="ltx_subsection"><h3 class="ltx_title ltx_title_subsection">1.1 Setup</h3>
<div class="ltx_para"><p class="ltx_p">We train for 100,000 steps on eight GPUs.</p></div></section></section>
<section class="ltx_bibliography"><h2 class="ltx_title">References</h2><p class="ltx_p">Attention cost grows quadratically, cited.</p></section>
</article></body></html>'''


FILLER = 'Background sentence about strains and growth conditions in the laboratory.'


def make_pdf(pages):
    """A minimal text PDF: one list of lines per page."""
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               f'<< /Type /Pages /Kids [{" ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))}] /Count {len(pages)} >>'.encode(),
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    for i, lines in enumerate(pages):
        ops = 'BT /F1 10 Tf 14 TL 50 750 Td ' + ' '.join(f'({line}) Tj T*' for line in lines) + ' ET'
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>'.encode())
        objects.append(f'<< /Length {len(ops)} >>\nstream\n{ops}\nendstream'.encode())
    out, offsets = b'%PDF-1.4\n', []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f'{number} 0 obj\n'.encode() + body + b'\nendobj\n'
    xref = len(out)
    out += f'xref\n0 {len(objects) + 1}\n0000000000 65535 f \n'.encode() + b''.join(f'{o:010d} 00000 n \n'.encode() for o in offsets)
    return out + f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode()


PDF = make_pdf([
    ['A study of fungal strains'] + [FILLER] * 6,
    [FILLER] * 4 + ['Strain A grew 40% faster than strain B and the', 'effect was driven by sup ergravity-like stress.'],
    [FILLER, 'References', 'Strain A grew 90% faster in an unrelated cited paper.'],
])


class FullTextTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict(os.environ, SEARCH_STATE_DIR=directory.name)
        env.start()
        self.addCleanup(env.stop)

    def epmc(self, provider, url, **_):
        if 'search?' in url:
            return json.dumps({'resultList': {'result': [{'pmcid': 'PMC1', 'isOpenAccess': 'Y'}]}}).encode()
        return JATS

    def arxiv(self, provider, url, **_):
        return LATEXML

    def test_jats_sections_drop_citations_and_back_matter(self):
        with patch.object(FT._s, 'fetch', side_effect=self.epmc):
            outline = FT.read_full_text('PMC1')
            intro = FT.read_full_text('PMC1', 1)
        self.assertEqual(outline['text_source'], 'europepmc_xml')
        self.assertEqual([s['heading'] for s in outline['outline']], ['Abstract', 'Introduction', 'Results > Growth'])
        self.assertIn('tropical regions.', intro['text'])
        self.assertNotIn('[1]', intro['text'])

    def test_latexml_sections_skip_authors_notes_citations_and_bibliography(self):
        with patch.object(FT._s, 'fetch', side_effect=self.arxiv):
            outline = FT.read_full_text('2401.00001')
            intro = FT.read_full_text('2401.00001', 1)
        self.assertEqual([s['heading'] for s in outline['outline']], ['Abstract', '1 Introduction', '1 Introduction > 1.1 Setup'])
        self.assertIn('grows quadratically with length n^{2} in practice.', intro['text'])
        self.assertIn('by half.', intro['text'])
        self.assertNotIn('footnote', intro['text'])
        self.assertNotIn('Lovelace', json.dumps(outline))

    def test_exact_quote_is_located_ignoring_case_and_punctuation(self):
        with patch.object(FT._s, 'fetch', side_effect=self.epmc):
            result = FT.check_quote('PMC1', 'the fungus is common in tropical regions')
        self.assertEqual(result['verdict'], 'exact')
        self.assertEqual(result['location'], {'section': 'Introduction', 'paragraph': 1})
        self.assertEqual(result['checked_against'], 'full_text')
        self.assertEqual(result['differences'], [])

    def test_changed_number_is_reported_as_a_difference(self):
        with patch.object(FT._s, 'fetch', side_effect=self.epmc):
            result = FT.check_quote('PMC1', 'Strain A grew 60% faster than strain B at 37 degrees')
        self.assertEqual(result['verdict'], 'near_exact')
        self.assertEqual(result['differences'], [{'quote': '60', 'paper': '40'}])
        self.assertEqual(result['location']['section'], 'Results > Growth')

    def test_absent_quote_is_not_found(self):
        with patch.object(FT._s, 'fetch', side_effect=self.arxiv):
            result = FT.check_quote('2401.00001', 'convolutional networks outperform every transformer baseline')
        self.assertEqual(result['verdict'], 'not_found')

    def test_parsed_text_is_cached(self):
        with patch.object(FT._s, 'fetch', side_effect=self.arxiv) as fetch:
            FT.check_quote('2401.00001', 'We train for 100,000 steps')
            FT.check_quote('2401.00001', 'reduces memory use by half')
        self.assertEqual(fetch.call_count, 1)

    def test_falls_back_to_abstract_when_no_open_full_text(self):
        hit = json.dumps({'resultList': {'result': [{'pmcid': 'PMC9', 'isOpenAccess': 'N'}]}}).encode()
        paper = {'paper': {'id': 'W1', 'doi': '10.1234/x', 'identifiers': {}, 'full_text_links': [],
                           'abstract': 'Deep learning allows models with many layers to learn representations.'}}
        closed = {'source': 'unpaywall', 'locations': []}
        with patch.object(FT._s, 'fetch', return_value=hit), patch.object(FT._openalex, 'openalex_get_paper', return_value=paper), \
                patch.object(FT._unpaywall, 'unpaywall_find_full_text', return_value=closed):
            result = FT.check_quote('10.1234/x', 'deep learning allows models with many layers')
        self.assertEqual(result['checked_against'], 'abstract')
        self.assertEqual(result['verdict'], 'exact')
        self.assertEqual(result['attempts'][0], {'source': 'europepmc', 'result': 'not_open_access'})
        self.assertNotIn('_abstract', result['identifiers'])

    def pdf_fallback(self, provider, url, **_):
        if 'arxiv.org/html/' in url:
            return b'<html><body><p>No HTML for this paper.</p></body></html>'
        return PDF

    @unittest.skipUnless(importlib.util.find_spec('pypdf'), 'pypdf not installed')
    def test_pdf_fallback_locates_quote_by_page_and_drops_references(self):
        with patch.object(FT._s, 'fetch', side_effect=self.pdf_fallback):
            result = FT.check_quote('2401.00001', 'Strain A grew 40% faster than strain B and the effect was driven')
            outline = FT.read_full_text('2401.00001')
        self.assertEqual((result['text_source'], result['verdict']), ('pdf', 'exact'))
        self.assertEqual(result['location']['section'], 'Page 2')
        self.assertEqual(result['text_url'], 'https://arxiv.org/pdf/2401.00001')
        self.assertEqual(result['attempts'], [{'source': 'arxiv_html', 'result': 'not_found'}])
        self.assertNotIn('90%', json.dumps(FT.read_full_text('2401.00001', 2)))
        self.assertEqual(len(outline['outline']), 3)

    @unittest.skipUnless(importlib.util.find_spec('pypdf'), 'pypdf not installed')
    def test_words_split_by_pdf_extraction_still_match_exactly(self):
        with patch.object(FT._s, 'fetch', side_effect=self.pdf_fallback):
            result = FT.check_quote('2401.00001', 'the effect was driven by supergravity-like stress')
        self.assertEqual((result['verdict'], result['differences']), ('exact', []))

    def test_web_page_instead_of_pdf_falls_back_to_abstract(self):
        paper = {'paper': {'abstract': 'We introduce a sparse attention method for long documents.'}}
        page = b'<html><body>Please verify you are human.</body></html>'
        with patch.object(FT._s, 'fetch', return_value=page), patch.object(FT._arxiv, 'arxiv_get_paper', return_value=paper):
            result = FT.check_quote('2401.00001', 'a sparse attention method for long documents')
        self.assertEqual(result['checked_against'], 'abstract')
        if importlib.util.find_spec('pypdf'):
            self.assertIn({'source': 'pdf', 'url': 'https://arxiv.org/pdf/2401.00001', 'result': 'not_pdf'}, result['attempts'])

    def test_missing_pypdf_is_reported_without_downloading(self):
        paper = {'paper': {'abstract': 'We introduce a sparse attention method for long documents.'}}
        html = b'<html><body><p>No HTML.</p></body></html>'
        with patch.object(FT._s, 'fetch', return_value=html) as fetch, patch.object(FT._arxiv, 'arxiv_get_paper', return_value=paper), \
                patch.object(FT.importlib.util, 'find_spec', return_value=None):
            result = FT.check_quote('2401.00001', 'a sparse attention method for long documents')
        self.assertIn({'source': 'pdf', 'result': 'pypdf_not_installed'}, result['attempts'])
        self.assertEqual(fetch.call_count, 1)

    def test_invalid_inputs_are_rejected_before_fetching(self):
        with patch.object(FT._s, 'fetch') as fetch:
            short = FT.check_quote('2401.00001', 'too short')
            bad_id = FT.check_quote('not an id', 'a long enough quote here')
            bad_section = FT.read_full_text('2401.00001', section=-1)
        for result in (short, bad_id, bad_section):
            self.assertEqual(result['error']['code'], 'invalid_arguments')
        fetch.assert_not_called()

    def test_section_out_of_range_points_to_outline(self):
        with patch.object(FT._s, 'fetch', side_effect=self.arxiv):
            result = FT.read_full_text('2401.00001', section=50)
        self.assertIn('outline', result['error']['message'])


if __name__ == '__main__':
    unittest.main()
