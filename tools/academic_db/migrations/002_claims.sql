-- 002_claims: claims, the Verifier's confidence assessments, and title search.

-- A statement the agents want to verify. Identical text (ignoring case and
-- surrounding whitespace) is stored once.
CREATE TABLE claims (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  text       text NOT NULL CHECK (btrim(text) <> ''),
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX claims_text_key ON claims (md5(lower(btrim(text))));

COMMENT ON TABLE claims IS 'Statements to verify. Identical text (case-insensitive, trimmed) is stored once.';
COMMENT ON COLUMN claims.created_by IS 'Agent that recorded the claim, e.g. lead or scout.';

-- One verdict on a claim. Re-assessing adds a row; earlier rows are kept.
CREATE TABLE claim_assessments (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  claim_id    bigint NOT NULL REFERENCES claims (id) ON DELETE CASCADE,
  confidence  numeric(4, 3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  verdict     text NOT NULL CHECK (verdict IN ('supported', 'contradicted', 'inconclusive')),
  rationale   text NOT NULL CHECK (btrim(rationale) <> ''),
  assessed_by text NOT NULL,
  assessed_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX claim_assessments_claim_idx ON claim_assessments (claim_id, assessed_at DESC);

COMMENT ON TABLE claim_assessments IS 'Verdicts on claims. Append-only: the newest row per claim is current, older rows are history.';
COMMENT ON COLUMN claim_assessments.confidence IS 'Confidence in the verdict, 0 to 1.';
COMMENT ON COLUMN claim_assessments.assessed_by IS 'Agent that made the assessment, e.g. verifier.';

-- Papers cited by an assessment.
CREATE TABLE claim_evidence (
  assessment_id bigint NOT NULL REFERENCES claim_assessments (id) ON DELETE CASCADE,
  work_id       bigint NOT NULL REFERENCES works (id),
  stance        text NOT NULL CHECK (stance IN ('supports', 'contradicts')),
  note          text,
  PRIMARY KEY (assessment_id, work_id)
);

CREATE INDEX claim_evidence_work_idx ON claim_evidence (work_id);

COMMENT ON TABLE claim_evidence IS 'Papers an assessment cites. A work with evidence cannot be deleted; merge_works moves it.';
COMMENT ON COLUMN claim_evidence.note IS 'Where or how the paper supports/contradicts the claim, e.g. a section or result.';

-- Each claim with its newest assessment (NULLs when not assessed yet).
CREATE VIEW claim_status AS
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
       (SELECT count(*) FROM claim_assessments x WHERE x.claim_id = c.id) AS assessment_count
FROM claims c
LEFT JOIN LATERAL (
  SELECT *
  FROM claim_assessments ca
  WHERE ca.claim_id = c.id
  ORDER BY ca.assessed_at DESC, ca.id DESC
  LIMIT 1
) a ON true;

COMMENT ON VIEW claim_status IS 'One row per claim with its newest assessment; verdict IS NULL means not assessed yet.';

-- Keep evidence when duplicate works are merged.
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

  -- Delete before refreshing so the target can take over their unique IDs.
  DELETE FROM works WHERE id = ANY (p_sources) AND id <> p_target;

  PERFORM refresh_work(p_target);
END
$$;

-- Substring title search (ILIKE '%word%') uses this index.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX works_title_trgm_idx ON works USING gin (title gin_trgm_ops);
