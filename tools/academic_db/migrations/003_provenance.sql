-- 003_provenance: abstracts, per-work quality signals, and where each claim came from.

ALTER TABLE work_records ADD COLUMN abstract text;

COMMENT ON COLUMN work_records.abstract IS 'Abstract as this provider gives it (OpenAlex inverted index rebuilt to text, arXiv summary, Semantic Scholar abstract).';

-- Signals code can establish without reading the paper, so verifiers do not spend effort on them.
CREATE VIEW work_quality AS
SELECT r.work_id,
       array_agg(r.provider ORDER BY p.priority) AS providers,
       bool_or((r.raw ->> 'is_retracted')::boolean) FILTER (WHERE r.provider = 'openalex') AS is_retracted,
       bool_and(r.type = 'preprint') AS preprint_only,
       max(r.publication_year) - min(r.publication_year) AS year_spread,
       bool_or(r.matched_by = 'title') AS title_matched
FROM work_records r
JOIN providers p ON p.id = r.provider
GROUP BY r.work_id;

COMMENT ON VIEW work_quality IS 'Per-work signals: providers, retraction (OpenAlex; NULL = unknown), preprint-only, year disagreement, title-only merge.';

-- Provenance: the paper (and passage) a claim was taken from.
ALTER TABLE claims
  ADD COLUMN source_work_id bigint REFERENCES works (id),
  ADD COLUMN source_quote text;

CREATE INDEX claims_source_work_idx ON claims (source_work_id) WHERE source_work_id IS NOT NULL;
-- Near-duplicate detection when several scouts record the same claim in different words.
CREATE INDEX claims_text_trgm_idx ON claims USING gin (text gin_trgm_ops);

COMMENT ON COLUMN claims.source_work_id IS 'Paper the claim was taken from; the verifier checks the claim is faithful to it.';
COMMENT ON COLUMN claims.source_quote IS 'Passage in the source paper that states the claim.';

CREATE OR REPLACE VIEW claim_status AS
SELECT c.id,
       c.text,
       c.created_by,
       c.created_at,
       a.id          AS assessment_id,
       a.confidence,
       a.verdict,
       a.rationale,
       a.assessed_by,
       a.assessed_at,
       (SELECT count(*) FROM claim_assessments x WHERE x.claim_id = c.id) AS assessment_count,
       c.source_work_id,
       c.source_quote
FROM claims c
LEFT JOIN LATERAL (
  SELECT *
  FROM claim_assessments ca
  WHERE ca.claim_id = c.id
  ORDER BY ca.assessed_at DESC, ca.id DESC
  LIMIT 1
) a ON true;

-- Merging works now also moves claim sources.
CREATE OR REPLACE FUNCTION merge_works(p_target bigint, p_sources bigint[]) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE work_records SET work_id = p_target
  WHERE work_id = ANY (p_sources) AND work_id <> p_target;

  INSERT INTO claim_evidence (assessment_id, work_id, stance, note)
  SELECT assessment_id, p_target, stance, note
  FROM claim_evidence
  WHERE work_id = ANY (p_sources) AND work_id <> p_target
  ON CONFLICT (assessment_id, work_id) DO NOTHING;

  DELETE FROM claim_evidence WHERE work_id = ANY (p_sources) AND work_id <> p_target;

  UPDATE claims SET source_work_id = p_target
  WHERE source_work_id = ANY (p_sources) AND source_work_id <> p_target;

  -- Delete before refreshing so the target can take over their unique IDs.
  DELETE FROM works WHERE id = ANY (p_sources) AND id <> p_target;

  PERFORM refresh_work(p_target);
END
$$;
