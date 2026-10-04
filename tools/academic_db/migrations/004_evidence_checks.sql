-- 004_evidence_checks: record how each cited passage was checked against the paper's text.

ALTER TABLE claim_evidence
  ADD COLUMN quote text,
  ADD COLUMN location text,
  ADD COLUMN match_score numeric(4, 3) CHECK (match_score BETWEEN 0 AND 1);

COMMENT ON COLUMN claim_evidence.quote IS 'Exact passage from the paper that supports or contradicts the claim.';
COMMENT ON COLUMN claim_evidence.location IS 'Where the passage is in the paper, e.g. a section, page or character offset from check_quote.';
COMMENT ON COLUMN claim_evidence.match_score IS 'How closely the quote matches the paper''s full text (0-1), e.g. from check_quote. NULL = not checked.';

-- Merging works keeps the check results of moved evidence.
CREATE OR REPLACE FUNCTION merge_works(p_target bigint, p_sources bigint[]) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE work_records SET work_id = p_target
  WHERE work_id = ANY (p_sources) AND work_id <> p_target;

  INSERT INTO claim_evidence (assessment_id, work_id, stance, note, quote, location, match_score)
  SELECT assessment_id, p_target, stance, note, quote, location, match_score
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
