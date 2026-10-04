-- Tests for 001_init (spec section 9). Everything runs in one transaction that is
-- rolled back, and uses IDs that cannot exist in real data, so this is safe to run
-- against the live database:
--   psql "$ACADEMIC_DB_URL" -X -q -f tests/sql/001_init_test.sql
\set ON_ERROR_STOP 1
-- Results go to /dev/null; the ok/FAIL lines are NOTICEs/errors on stderr.
\o /dev/null
BEGIN;

CREATE FUNCTION pg_temp.check(ok boolean, what text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  IF ok IS NOT TRUE THEN
    RAISE EXCEPTION 'FAIL: %', what;
  END IF;
  RAISE NOTICE 'ok: %', what;
END
$$;

CREATE FUNCTION pg_temp.check_error(stmt text, expected_state text, what text) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  failed boolean := false;
BEGIN
  BEGIN
    EXECUTE stmt;
  EXCEPTION WHEN OTHERS THEN
    failed := true;
    IF SQLSTATE <> expected_state THEN
      RAISE EXCEPTION 'FAIL: % (expected %, got %: %)', what, expected_state, SQLSTATE, SQLERRM;
    END IF;
  END;
  IF NOT failed THEN
    RAISE EXCEPTION 'FAIL: % (statement succeeded)', what;
  END IF;
  RAISE NOTICE 'ok: %', what;
END
$$;

-- Seeds
SELECT pg_temp.check(
  (SELECT string_agg(id, ',' ORDER BY priority) FROM providers) = 'openalex,semantic_scholar,arxiv',
  'providers seeded in priority order');

-- Worked example (spec section 8): one paper from three providers
INSERT INTO works (title) VALUES ('placeholder') RETURNING id AS attention \gset

INSERT INTO work_records (work_id, provider, provider_work_id, matched_by, doi, arxiv_id, mag_id, title,
                          publication_year, type, source_id, cited_by_count, is_oa, oa_status, oa_url, raw, fetched_at)
VALUES
  (:attention, 'openalex', 'W-test-9002626778328', 'new', '10.99999/test-2q58a426', 'test-1706.03762', 9002626778328,
   'Attention Is All You Need', 2025, 'preprint', NULL, 26739, true, 'gold', 'https://example.org/repost.pdf', '{}', now() - interval '2 days'),
  (:attention, 'semantic_scholar', 's2-test-204e3073', 'arxiv', NULL, 'test-1706.03762', 9002626778328,
   'Attention is All you Need', 2017, 'conference-paper', 'd9720b90-d60b-48bc-9df8-87a30b9a60dd', 195136, false, NULL, NULL, '{}', now() - interval '1 day'),
  (:attention, 'arxiv', 'test-1706.03762', 'arxiv', NULL, 'test-1706.03762', NULL,
   'Attention Is All You Need', 2017, 'preprint', NULL, NULL, true, 'green', 'https://arxiv.org/pdf/1706.03762', '{}', now());

INSERT INTO work_authors (work_record_id, position, author_id, author_name)
SELECT id, 0, 'A-test-5001226970', 'Ashish Vaswani' FROM work_records WHERE provider_work_id = 'W-test-9002626778328';

SELECT refresh_work(:attention);

SELECT pg_temp.check(title = 'Attention Is All You Need' AND type = 'preprint', 'title and type come from OpenAlex')
FROM works WHERE id = :attention;
SELECT pg_temp.check(publication_year = 2017, 'year is the earliest across records (2017, not 2025)')
FROM works WHERE id = :attention;
SELECT pg_temp.check(doi = '10.99999/test-2q58a426' AND arxiv_id = 'test-1706.03762' AND mag_id = 9002626778328, 'IDs come from the highest-priority record that has them')
FROM works WHERE id = :attention;
SELECT pg_temp.check(title_norm = 'attentionisallyouneed', 'title_norm strips case, spaces and punctuation')
FROM works WHERE id = :attention;
SELECT pg_temp.check(primary_provider = 'openalex' AND cited_by_count = 26739 AND oa_status = 'gold', 'work_overview uses the OpenAlex record')
FROM work_overview WHERE id = :attention;

-- Newest record wins when a provider has two
INSERT INTO work_records (work_id, provider, provider_work_id, matched_by, title, cited_by_count, raw)
VALUES (:attention, 'openalex', 'W-test-9999999999', 'arxiv', 'Attention Is All You Need', 30000, '{}');
SELECT pg_temp.check(cited_by_count = 30000, 'work_overview picks the newest record of the top provider')
FROM work_overview WHERE id = :attention;

-- Without OpenAlex, Semantic Scholar wins over arXiv
INSERT INTO works (title) VALUES ('placeholder') RETURNING id AS s2_only \gset
INSERT INTO work_records (work_id, provider, provider_work_id, matched_by, arxiv_id, title, publication_year, cited_by_count, is_oa, raw)
VALUES
  (:s2_only, 'semantic_scholar', 's2-test-aaaa', 'new', 'test-2101.00001', 'Some Paper', 2021, 12, false, '{}'),
  (:s2_only, 'arxiv', 'test-2101.00001', 'arxiv', 'test-2101.00001', 'Some paper', 2021, NULL, true, '{}');
SELECT refresh_work(:s2_only);
SELECT pg_temp.check(primary_provider = 'semantic_scholar' AND cited_by_count = 12 AND title = 'Some Paper', 'Semantic Scholar wins when there is no OpenAlex record')
FROM work_overview WHERE id = :s2_only;

-- merge_works moves records, deletes the source, recomputes the target
SELECT merge_works(:attention, ARRAY[:s2_only]::bigint[]);
SELECT pg_temp.check(NOT EXISTS (SELECT 1 FROM works WHERE id = :s2_only), 'merged work is deleted');
SELECT pg_temp.check((SELECT count(*) FROM work_records WHERE work_id = :attention) = 6, 'merged records moved to the target');

-- Constraints
SELECT pg_temp.check_error($$INSERT INTO works (title, doi) VALUES ('x', '10.1038/NATURE14539')$$, '23514', 'uppercase DOI rejected');
SELECT pg_temp.check_error($$INSERT INTO works (title, doi) VALUES ('x', 'https://doi.org/10.1038/nature14539')$$, '23514', 'DOI with URL prefix rejected');
SELECT pg_temp.check_error(format($$INSERT INTO work_records (work_id, provider, provider_work_id, matched_by, title, oa_status, raw) VALUES (%s, 'openalex', 'W-test-1', 'new', 'x', 'GOLD', '{}')$$, :attention), '23514', 'uppercase oa_status rejected');
SELECT pg_temp.check_error(format($$INSERT INTO work_records (work_id, provider, provider_work_id, matched_by, title, cited_by_count, raw) VALUES (%s, 'openalex', 'W-test-1', 'new', 'x', -1, '{}')$$, :attention), '23514', 'negative cited_by_count rejected');
SELECT pg_temp.check_error(format($$INSERT INTO work_records (work_id, provider, provider_work_id, matched_by, title, raw) VALUES (%s, 'openalex', 'W-test-1', 'fuzzy', 'x', '{}')$$, :attention), '23514', 'unknown matched_by rejected');
SELECT pg_temp.check_error(format($$INSERT INTO work_records (work_id, provider, provider_work_id, matched_by, title, raw) VALUES (%s, 'openalex', 'W-test-9002626778328', 'new', 'x', '{}')$$, :attention), '23505', 'duplicate (provider, provider_work_id) rejected');
SELECT pg_temp.check_error(format($$INSERT INTO work_records (work_id, provider, provider_work_id, matched_by, title, raw) VALUES (%s, 'crossref', 'X-test-1', 'new', 'x', '{}')$$, :attention), '23503', 'unknown provider rejected');
SELECT pg_temp.check_error($$SELECT refresh_work(-1)$$, 'P0001', 'refresh_work on a missing work raises');

-- Cascade
DELETE FROM works WHERE id = :attention;
SELECT pg_temp.check(NOT EXISTS (SELECT 1 FROM work_records WHERE work_id = :attention), 'deleting a work deletes its records');
SELECT pg_temp.check(NOT EXISTS (SELECT 1 FROM work_authors WHERE author_id = 'A-test-5001226970'), 'deleting a work deletes its authors');

ROLLBACK;
