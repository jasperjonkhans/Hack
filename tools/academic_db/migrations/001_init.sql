-- 001_init: academic works merged from several metadata providers.
-- Design: docs/schema-design.md

-- Providers and their priority (lower = preferred when values disagree).
CREATE TABLE providers (
  id       text PRIMARY KEY,
  priority smallint NOT NULL UNIQUE
);

COMMENT ON TABLE providers IS 'Metadata providers. priority decides which provider wins for canonical values and in work_overview (lower = preferred).';

INSERT INTO providers (id, priority) VALUES
  ('openalex', 1),
  ('semantic_scholar', 2),
  ('arxiv', 3);

-- One row per real paper. Canonical values are derived from work_records by refresh_work().
CREATE TABLE works (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  doi              text UNIQUE CHECK (doi = lower(doi) AND doi LIKE '10.%'),
  arxiv_id         text UNIQUE,
  mag_id           bigint UNIQUE,
  title            text NOT NULL,
  title_norm       text GENERATED ALWAYS AS (lower(regexp_replace(title, '[^[:alnum:]]+', '', 'g'))) STORED NOT NULL,
  publication_year smallint,
  type             text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX works_title_norm_year_idx ON works (title_norm, publication_year);
CREATE INDEX works_publication_year_idx ON works (publication_year);

COMMENT ON TABLE works IS 'One row per real paper, merged across providers. Do not write canonical columns directly: insert/update work_records, then call refresh_work(id).';
COMMENT ON COLUMN works.doi IS 'Lowercase DOI without URL prefix, e.g. 10.1038/nature14539. From the highest-priority record that has one.';
COMMENT ON COLUMN works.arxiv_id IS 'arXiv ID without version, e.g. 1706.03762 or cond-mat/0410550.';
COMMENT ON COLUMN works.mag_id IS 'Microsoft Academic Graph ID. Only exists for papers indexed up to 2021.';
COMMENT ON COLUMN works.title IS 'Title from the highest-priority record.';
COMMENT ON COLUMN works.title_norm IS 'Generated: title lowercased with all non-alphanumerics removed. Used for exact title matching.';
COMMENT ON COLUMN works.publication_year IS 'Earliest year across all records (prefers the original over later reposts).';
COMMENT ON COLUMN works.type IS 'OpenAlex type vocabulary: article, preprint, review, conference-paper, book, book-chapter, dataset, editorial, letter, other, ...';

-- One row per provider per paper: that provider's values, already normalized.
CREATE TABLE work_records (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  work_id          bigint NOT NULL REFERENCES works (id) ON DELETE CASCADE,
  provider         text NOT NULL REFERENCES providers (id),
  provider_work_id text NOT NULL,
  matched_by       text NOT NULL CHECK (matched_by IN ('new', 'doi', 'arxiv', 'mag', 'title')),
  doi              text CHECK (doi = lower(doi) AND doi LIKE '10.%'),
  arxiv_id         text,
  mag_id           bigint,
  title            text NOT NULL,
  publication_year smallint,
  type             text,
  source_id        text,
  cited_by_count   integer CHECK (cited_by_count >= 0),
  is_oa            boolean,
  oa_status        text CHECK (oa_status IN ('diamond', 'gold', 'green', 'hybrid', 'bronze', 'closed')),
  oa_url           text,
  raw              jsonb NOT NULL,
  fetched_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, provider_work_id)
);

CREATE INDEX work_records_work_id_idx ON work_records (work_id);
CREATE INDEX work_records_doi_idx ON work_records (doi) WHERE doi IS NOT NULL;
CREATE INDEX work_records_arxiv_id_idx ON work_records (arxiv_id) WHERE arxiv_id IS NOT NULL;
CREATE INDEX work_records_mag_id_idx ON work_records (mag_id) WHERE mag_id IS NOT NULL;

COMMENT ON TABLE work_records IS 'One provider''s normalized view of one paper. Source of truth for works.';
COMMENT ON COLUMN work_records.provider_work_id IS 'The provider''s own ID: OpenAlex W2626778328, Semantic Scholar paperId (40-char hex), arXiv 1706.03762.';
COMMENT ON COLUMN work_records.matched_by IS 'How the record was linked to its work when first inserted: new (created the work), doi, arxiv, mag, or title (review these).';
COMMENT ON COLUMN work_records.source_id IS 'Provider-scoped journal/venue ID: OpenAlex S137773608 or Semantic Scholar venue UUID. NULL for arXiv.';
COMMENT ON COLUMN work_records.cited_by_count IS 'Citation count according to this provider. NULL for arXiv (no citation data).';
COMMENT ON COLUMN work_records.oa_status IS 'Lowercase: diamond, gold, green, hybrid, bronze, closed.';
COMMENT ON COLUMN work_records.raw IS 'Full provider payload for this work. arXiv: {"entry_xml": "<entry>...</entry>"}.';

-- Authors as listed by each provider. Author IDs are provider-scoped.
CREATE TABLE work_authors (
  work_record_id bigint NOT NULL REFERENCES work_records (id) ON DELETE CASCADE,
  position       smallint NOT NULL CHECK (position >= 0),
  author_id      text,
  author_name    text NOT NULL,
  PRIMARY KEY (work_record_id, position)
);

CREATE INDEX work_authors_author_id_idx ON work_authors (author_id) WHERE author_id IS NOT NULL;

COMMENT ON TABLE work_authors IS 'Authors in the order a provider lists them. Replace all rows of a record when it is refreshed.';
COMMENT ON COLUMN work_authors.position IS '0-based position in the provider''s author list.';
COMMENT ON COLUMN work_authors.author_id IS 'Provider-scoped: OpenAlex A5001226970 or Semantic Scholar 40348417. NULL for arXiv. Join work_records to know the provider.';

-- One row per work with the highest-priority (then newest) record's provider-specific values.
CREATE VIEW work_overview AS
SELECT w.*,
       r.provider AS primary_provider,
       r.source_id,
       r.cited_by_count,
       r.is_oa,
       r.oa_status,
       r.oa_url
FROM works w
LEFT JOIN LATERAL (
  SELECT wr.*
  FROM work_records wr
  JOIN providers p ON p.id = wr.provider
  WHERE wr.work_id = w.id
  ORDER BY p.priority, wr.fetched_at DESC
  LIMIT 1
) r ON true;

COMMENT ON VIEW work_overview IS 'One row per work: canonical columns plus source, citations and OA taken together from the single highest-priority record (OpenAlex first).';

-- Recompute a work's canonical values from its records (spec section 7).
CREATE FUNCTION refresh_work(p_work_id bigint) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE works w
  SET title            = c.title,
      type             = c.type,
      doi              = c.doi,
      arxiv_id         = c.arxiv_id,
      mag_id           = c.mag_id,
      publication_year = c.publication_year,
      updated_at       = now()
  FROM (
    SELECT (array_agg(r.title    ORDER BY p.priority, r.fetched_at DESC) FILTER (WHERE r.title    IS NOT NULL))[1] AS title,
           (array_agg(r.type     ORDER BY p.priority, r.fetched_at DESC) FILTER (WHERE r.type     IS NOT NULL))[1] AS type,
           (array_agg(r.doi      ORDER BY p.priority, r.fetched_at DESC) FILTER (WHERE r.doi      IS NOT NULL))[1] AS doi,
           (array_agg(r.arxiv_id ORDER BY p.priority, r.fetched_at DESC) FILTER (WHERE r.arxiv_id IS NOT NULL))[1] AS arxiv_id,
           (array_agg(r.mag_id   ORDER BY p.priority, r.fetched_at DESC) FILTER (WHERE r.mag_id   IS NOT NULL))[1] AS mag_id,
           min(r.publication_year) AS publication_year
    FROM work_records r
    JOIN providers p ON p.id = r.provider
    WHERE r.work_id = p_work_id
  ) c
  WHERE w.id = p_work_id
    AND c.title IS NOT NULL;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'work % does not exist or has no records', p_work_id;
  END IF;
END
$$;

COMMENT ON FUNCTION refresh_work(bigint) IS 'Recompute works canonical columns from work_records. Call after inserting, updating or moving records.';

-- Merge works into p_target: move their records, delete them, recompute the target (spec section 6.4).
CREATE FUNCTION merge_works(p_target bigint, p_sources bigint[]) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE work_records SET work_id = p_target
  WHERE work_id = ANY (p_sources) AND work_id <> p_target;

  -- Delete before refreshing so the target can take over their unique IDs.
  DELETE FROM works WHERE id = ANY (p_sources) AND id <> p_target;

  PERFORM refresh_work(p_target);
END
$$;

COMMENT ON FUNCTION merge_works(bigint, bigint[]) IS 'Move all records of p_sources into p_target, delete the source works, then refresh the target.';
