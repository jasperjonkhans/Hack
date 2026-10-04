# Academic Works Database — Agent Reference

Reference for an agent that reads from and writes to the academic works database, and for the people designing its tools and skills. Agents normally use the [MCP tools](#agent-tools) rather than SQL. The full design rationale is in [schema-design.md](schema-design.md). The schema itself is in [`migrations/`](../migrations/).

## What this database is

A PostgreSQL 18 database of academic papers merged from three metadata providers:

| Provider | Priority | Gives us | Missing |
|---|---|---|---|
| `openalex` | 1 (primary) | IDs, citations, OA status, source, author IDs | Sometimes attaches a repost's DOI or year |
| `semantic_scholar` | 2 | IDs, citations, OA status, venue, author IDs | Many DOIs; OA status is often `false` for arXiv papers |
| `arxiv` | 3 | Preprint metadata, PDF link | Citations, source, author IDs |

Each real paper is **one row in `works`**. Each provider's view of that paper is **one row in `work_records`**. When providers disagree, both values are kept, and the canonical value follows fixed rules (see [How canonical values are chosen](#how-canonical-values-are-chosen)).

The database also stores **claims** that the agents verify, with the Verifier's confidence-scored verdicts and the papers each verdict cites.

## Connecting

| | |
|---|---|
| Host | `<db-host>:5432`, database `academic` |
| TLS | Required (`sslmode=require`). The certificate is self-signed, so don't verify it: in Node `pg` use `ssl: { rejectUnauthorized: false }`, in Python `psycopg` use `sslmode=require` |
| Write login | `external_full`: full rights on the database; its sessions run as the owner `academic`. Repository-root `.env`: `ACADEMIC_DB_URL` |
| Read login | `academic_reader`: SELECT only, read-only transactions, 15 s query limit. Repository-root `.env`: `ACADEMIC_DB_READER_URL` |
| Limits | `external_full` 30 and `academic_reader` 20 concurrent connections. Server: 100 |

## Data model

```
providers (id, priority)
    │
    │ 1..*
    ▼
work_records ──*..1──► works            ◄── work_overview (view: works + top record's values)
    │                    ▲
    │ 1..*               │ *..1
    ▼                    │
work_authors       claim_evidence ──*..1──► claim_assessments ──*..1──► claims
                                                                          ▲
                                            claim_status (view: claims + newest assessment)
```

- **`works`** holds canonical values: what the paper *is* (title, year, type, shared IDs).
- **`work_records`** holds what each provider *says* (its own citations, OA status, source, raw payload).
- **`work_authors`** holds authors as each provider lists them.
- **`work_overview`** is the convenience view: one row per work, plus citations, OA and source from the preferred provider.
- **`claims`**, **`claim_assessments`** and **`claim_evidence`** hold statements to verify, every verdict on them (history kept), and the papers each verdict cites.

### `works`: one row per real paper

| Column | Type | Meaning |
|---|---|---|
| `id` | bigint PK | Internal ID |
| `doi` | text, unique | Lowercase, no URL prefix: `10.1038/nature14539` |
| `arxiv_id` | text, unique | No version: `1706.03762`, `cond-mat/0410550` |
| `mag_id` | bigint, unique | Microsoft Academic ID. Only for papers indexed up to 2021 |
| `title` | text | From the highest-priority provider |
| `title_norm` | text, generated | `title` lowercased with non-alphanumerics removed: `attentionisallyouneed` |
| `publication_year` | smallint | **Earliest** year across providers |
| `type` | text | OpenAlex type names: `article`, `preprint`, `review`, `conference-paper`, `book`, `book-chapter`, `dataset`, `editorial`, `letter`, `other`, … |
| `created_at`, `updated_at` | timestamptz | `updated_at` changes whenever canonical values are recomputed |

### `work_records`: one row per provider per paper

| Column | Type | Meaning |
|---|---|---|
| `id` | bigint PK | |
| `work_id` | bigint → `works.id` | The paper this record belongs to |
| `provider` | text → `providers.id` | `openalex`, `semantic_scholar`, `arxiv` |
| `provider_work_id` | text | The provider's own ID (see [Identifier formats](#identifier-formats)). Unique per provider |
| `matched_by` | text | How the record was linked when first inserted: `new`, `doi`, `arxiv`, `mag`, `title` |
| `doi`, `arxiv_id`, `mag_id` | | The IDs **this provider** reported |
| `title`, `publication_year`, `type` | | This provider's values (type already mapped to OpenAlex names) |
| `source_id` | text | Provider-scoped journal/venue ID. `NULL` for arXiv |
| `cited_by_count` | integer | This provider's citation count. `NULL` for arXiv |
| `is_oa` | boolean | Open access according to this provider |
| `oa_status` | text | `diamond`, `gold`, `green`, `hybrid`, `bronze`, `closed` |
| `oa_url` | text | Best free URL (PDF or landing page) |
| `abstract` | text | This provider's abstract (OpenAlex's inverted index rebuilt to text, arXiv summary, Semantic Scholar abstract) |
| `raw` | jsonb | Full provider payload. arXiv: `{"entry_xml": "<entry>…</entry>"}` |
| `fetched_at` | timestamptz | When this provider was last queried |

### `work_authors`

| Column | Type | Meaning |
|---|---|---|
| `work_record_id` | bigint → `work_records.id` | |
| `position` | smallint | 0-based author order |
| `author_id` | text | **Provider-scoped**: OpenAlex `A5001226970`, Semantic Scholar `40348417`. `NULL` for arXiv |
| `author_name` | text | As the provider writes it |

### `providers`

`id` and `priority` (lower = preferred). Currently `openalex=1`, `semantic_scholar=2`, `arxiv=3`.

### `work_overview` (view)

Every `works` column, plus `primary_provider`, `source_id`, `cited_by_count`, `is_oa`, `oa_status` and `oa_url`, all taken from **one** record: the highest-priority provider's, and the newest if that provider has several. **This is the default table to read from.**

### `claims`

| Column | Type | Meaning |
|---|---|---|
| `id` | bigint PK | |
| `text` | text | The statement. Identical text (ignoring case and surrounding whitespace) is stored once |
| `created_by` | text | Agent that recorded it (`lead`, `scout`, …) |
| `created_at` | timestamptz | |
| `source_work_id` | bigint → `works.id` | Paper the claim was taken from; the Verifier checks the claim is faithful to it |
| `source_quote` | text | The passage in that paper that states the claim |

### `claim_assessments`: one verdict on a claim, append-only

| Column | Type | Meaning |
|---|---|---|
| `id` | bigint PK | |
| `claim_id` | bigint → `claims.id` | |
| `confidence` | numeric(4,3) | 0 to 1 |
| `verdict` | text | `supported`, `contradicted`, `inconclusive` |
| `rationale` | text | Why, including strength and limitations of the evidence |
| `assessed_by` | text | Agent that made it (`verifier`) |
| `assessed_at` | timestamptz | The newest row per claim is the current verdict |

### `claim_evidence`

| Column | Type | Meaning |
|---|---|---|
| `assessment_id` | bigint → `claim_assessments.id` | |
| `work_id` | bigint → `works.id` | The cited paper. A work with evidence can't be deleted; `merge_works` moves its evidence |
| `stance` | text | `supports` or `contradicts` |
| `note` | text | Where or how, e.g. "Table 2: BLEU 28.4" |

### `claim_status` (view)

One row per claim with its newest assessment: `id`, `text`, `created_by`, `created_at`, `assessment_id`, `confidence`, `verdict`, `rationale`, `assessed_by`, `assessed_at`, `assessment_count`, `source_work_id`, `source_quote`. `verdict IS NULL` means it hasn't been assessed yet.

### `work_quality` (view)

What code can establish about a paper without reading it, so Verifiers don't spend effort on it: `work_id`, `providers` (in priority order), `is_retracted` (from OpenAlex; NULL = unknown), `preprint_only`, `year_spread` (largest year disagreement between providers), `title_matched` (linked only by title).

### Functions

| Function | Use |
|---|---|
| `refresh_work(work_id)` | Recompute a work's canonical columns from its records. Call it after any insert, update or move of `work_records`. Raises if the work has no records |
| `merge_works(target_id, source_ids bigint[])` | Move all records, claim evidence and claim sources of the source works into the target, delete the sources, then refresh the target |

## Identifier formats

Normalize user input to these forms before querying. For example, `https://doi.org/10.1038/Nature14539` → `10.1038/nature14539`, and `arXiv:1706.03762v5` → `1706.03762`.

| Identifier | Format | Example | Where it lives |
|---|---|---|---|
| DOI | lowercase `10.…` | `10.1038/nature14539` | `works.doi`, `work_records.doi` |
| arXiv ID | `YYMM.NNNNN` or `archive/NNNNNNN`, no version | `1706.03762` | `works.arxiv_id`, `work_records.arxiv_id` |
| MAG ID | integer | `2626778328` | `works.mag_id`, `work_records.mag_id` |
| OpenAlex work | `W` + digits | `W2626778328` | `work_records.provider_work_id` where `provider='openalex'` |
| Semantic Scholar paper | 40 hex chars | `204e3073870fae3d05bcbc2f6a8e263d9b72e776` | `work_records.provider_work_id` where `provider='semantic_scholar'` |
| arXiv record | same as arXiv ID | `1706.03762` | `work_records.provider_work_id` where `provider='arxiv'` |
| OpenAlex author | `A` + digits | `A5001226970` | `work_authors.author_id` |
| Semantic Scholar author | digits | `40348417` | `work_authors.author_id` |
| OpenAlex source | `S` + digits | `S137773608` | `work_records.source_id` |
| Semantic Scholar venue | UUID | `d9720b90-d60b-48bc-9df8-87a30b9a60dd` | `work_records.source_id` |

## Reading: query recipes

**Find a paper by any identifier**

```sql
-- DOI, arXiv or MAG ID: search the records, which also catches IDs that only a lower-priority provider reported
SELECT o.* FROM work_overview o
WHERE o.id IN (SELECT work_id FROM work_records WHERE doi = '10.1038/nature14539');

-- Provider ID (OpenAlex W…, Semantic Scholar paperId, arXiv ID)
SELECT o.* FROM work_overview o
JOIN work_records r ON r.work_id = o.id
WHERE r.provider = 'openalex' AND r.provider_work_id = 'W2626778328';
```

**Find a paper by title**

```sql
-- Exact title, ignoring case and punctuation (uses an index)
SELECT * FROM work_overview
WHERE title_norm = lower(regexp_replace('Attention is all you need!', '[^[:alnum:]]+', '', 'g'));

-- Keyword search (sequential scan, so it gets slow on large tables)
SELECT id, title, publication_year, cited_by_count FROM work_overview
WHERE title ILIKE '%transformer%' ORDER BY cited_by_count DESC NULLS LAST LIMIT 20;
```

**Most cited papers in a year**

```sql
SELECT id, title, cited_by_count, primary_provider FROM work_overview
WHERE publication_year = 2017 AND type = 'article'
ORDER BY cited_by_count DESC NULLS LAST LIMIT 10;
```

**Full detail for one paper: every provider's view plus authors**

```sql
SELECT r.provider, r.provider_work_id, r.cited_by_count, r.is_oa, r.oa_status, r.oa_url, r.matched_by,
       (SELECT string_agg(a.author_name, ', ' ORDER BY a.position) FROM work_authors a WHERE a.work_record_id = r.id) AS authors
FROM work_records r JOIN providers p ON p.id = r.provider
WHERE r.work_id = 42
ORDER BY p.priority, r.fetched_at DESC;
```

**Compare citation counts across providers**

```sql
SELECT w.id, w.title,
       max(r.cited_by_count) FILTER (WHERE r.provider = 'openalex')         AS openalex,
       max(r.cited_by_count) FILTER (WHERE r.provider = 'semantic_scholar') AS semantic_scholar
FROM works w JOIN work_records r ON r.work_id = w.id
GROUP BY w.id, w.title
HAVING count(DISTINCT r.provider) > 1;
```

**Open-access share by year**

```sql
SELECT publication_year, count(*) AS works,
       round(100.0 * count(*) FILTER (WHERE is_oa) / count(*), 1) AS pct_oa
FROM work_overview GROUP BY publication_year ORDER BY publication_year;
```

**Papers by an author** (always filter by provider, since author IDs are provider-scoped)

```sql
SELECT DISTINCT o.id, o.title, o.publication_year, o.cited_by_count
FROM work_authors a
JOIN work_records r ON r.id = a.work_record_id AND r.provider = 'openalex'
JOIN work_overview o ON o.id = r.work_id
WHERE a.author_id = 'A5001226970'
ORDER BY o.publication_year DESC;
```

**Papers in a journal or venue**

```sql
SELECT o.id, o.title, o.publication_year FROM work_records r
JOIN work_overview o ON o.id = r.work_id
WHERE r.provider = 'openalex' AND r.source_id = 'S137773608';
```

**Data-quality checks**

```sql
-- Records linked by title only: the ones most likely to be wrong merges
SELECT r.work_id, r.provider, r.title, w.title AS canonical_title
FROM work_records r JOIN works w ON w.id = r.work_id WHERE r.matched_by = 'title';

-- Providers that disagree on the year by more than one
SELECT work_id, array_agg(provider || ':' || publication_year) FROM work_records
GROUP BY work_id HAVING max(publication_year) - min(publication_year) > 1;

-- Which providers cover each paper
SELECT array_agg(provider ORDER BY provider) AS providers, count(*) FROM (
  SELECT DISTINCT work_id, provider FROM work_records) t
GROUP BY work_id;
```

## Writing: the ingestion contract

The `import_work` and `save_work` tools implement this contract (`src/academic_db/normalize.py` and `ingest.py`); use them rather than writing SQL. This section is the reference for anyone writing their own loader.

Never write the canonical columns of `works` by hand. Write `work_records`, then call `refresh_work`. Process each provider payload in **one transaction**, as follows.

### 1. Normalize the payload into a record

| Rule | Detail |
|---|---|
| Empty strings | Become `NULL` (Semantic Scholar returns `openAccessPdf.url: ""`) |
| Title | Trim, and collapse runs of whitespace (arXiv titles contain line breaks). Skip records with no title |
| DOI | Strip `https://doi.org/`, `http://doi.org/`, `https://dx.doi.org/` and `doi:`, then lowercase |
| arXiv ID | Strip the URL prefix, `.pdf` and the `vN` version. A DOI `10.48550/arxiv.<id>` also gives the arXiv ID |
| MAG ID | String → integer |
| OA status | Lowercase. Anything other than the six allowed values becomes `NULL` |
| OpenAlex | Strip `https://openalex.org/` from work, source and author IDs. Take the arXiv ID from `locations[]`: an `id` of `pmh:oai:arXiv.org:<id>` or a landing page `arxiv.org/abs/<id>` |
| Semantic Scholar | Remove spaces from `publicationTypes`, then map using the first match in this order: `Review`→`review`, `Book`→`book`, `BookSection`→`book-chapter`, `Dataset`→`dataset`, `Editorial`→`editorial`, `LettersAndComments`→`letter`, `Conference`→`conference-paper`, `JournalArticle`→`article`; any other value → `other` |
| arXiv | `type='preprint'`, `is_oa=true`, `oa_status='green'`, `oa_url` = PDF link, year taken from `<published>`, `author_id = NULL` |

### 2. Find the work it belongs to

```sql
-- a) Already stored?
SELECT id, work_id FROM work_records WHERE provider = $provider AND provider_work_id = $provider_work_id;

-- b) Shared IDs (only the non-null ones)
SELECT DISTINCT work_id FROM work_records
WHERE doi = $doi OR arxiv_id = $arxiv_id OR mag_id = $mag_id;

-- c) Title fallback, only if (b) found nothing and the record has a year
SELECT w.id FROM works w
WHERE w.title_norm = lower(regexp_replace($title, '[^[:alnum:]]+', '', 'g'))
  AND w.publication_year BETWEEN $year - 1 AND $year + 1
  AND NOT EXISTS (SELECT 1 FROM work_records r WHERE r.work_id = w.id AND r.provider = $provider)
  AND (w.doi      IS NULL OR $doi      IS NULL OR w.doi      = $doi)
  AND (w.arxiv_id IS NULL OR $arxiv_id IS NULL OR w.arxiv_id = $arxiv_id)
  AND (w.mag_id   IS NULL OR $mag_id   IS NULL OR w.mag_id   = $mag_id);
```

| Result | Action | `matched_by` (new records only) |
|---|---|---|
| (b) finds 1 work, or (a) found one | Use it | first key that matched: `doi`, then `arxiv`, then `mag` |
| (b) finds several works | `SELECT merge_works(lowest_id, ARRAY[other_ids])` and use the lowest ID | as above |
| (c) finds exactly 1 work | Use it | `title` |
| otherwise | `INSERT INTO works (title) VALUES ($title) RETURNING id` | `new` |

### 3. Upsert the record, replace its authors, refresh

```sql
INSERT INTO work_records (work_id, provider, provider_work_id, matched_by, doi, arxiv_id, mag_id, title,
                          publication_year, type, source_id, cited_by_count, is_oa, oa_status, oa_url, abstract,
                          raw, fetched_at)
VALUES (...)
ON CONFLICT (provider, provider_work_id) DO UPDATE SET
  doi = EXCLUDED.doi, arxiv_id = EXCLUDED.arxiv_id, mag_id = EXCLUDED.mag_id, title = EXCLUDED.title,
  publication_year = EXCLUDED.publication_year, type = EXCLUDED.type, source_id = EXCLUDED.source_id,
  cited_by_count = EXCLUDED.cited_by_count, is_oa = EXCLUDED.is_oa, oa_status = EXCLUDED.oa_status,
  oa_url = EXCLUDED.oa_url, abstract = EXCLUDED.abstract, raw = EXCLUDED.raw, fetched_at = now()
  -- work_id and matched_by deliberately not updated
RETURNING id;

DELETE FROM work_authors WHERE work_record_id = $record_id;
INSERT INTO work_authors (work_record_id, position, author_id, author_name) VALUES (...), (...);

SELECT refresh_work($work_id);
```

Start the transaction with `SELECT pg_advisory_xact_lock(hashtext('academic_db.ingest'))`. This serializes all saves, so two writers matching the same paper at once can't create duplicate works.

## How canonical values are chosen

`refresh_work` sorts a work's records by provider priority, then newest first:

- `title`, `type`, `doi`, `arxiv_id`, `mag_id`: the first non-null value.
- `publication_year`: the **minimum** across records, so the original wins over later reposts.

The same records always produce the same result, whatever order they were loaded in.

## Gotchas

- **Author and source IDs aren't shared across providers.** The same person has an OpenAlex ID and a separate Semantic Scholar ID, with no link between them. Always pair `author_id` and `source_id` with the record's `provider`.
- **OpenAlex isn't always right.** For "Attention Is All You Need", OpenAlex reports a 2025 repost DOI and 26,739 citations, while Semantic Scholar has 195,136. `work_overview` shows the OpenAlex numbers; use the provider comparison recipe when numbers look off.
- **`cited_by_count` is `NULL` for arXiv-only works**, not 0. Sort with `NULLS LAST`.
- **Providers disagree on OA.** Semantic Scholar often says `is_oa=false` for papers that are on arXiv. `work_overview` uses the top provider's answer.
- **Title matching is exact after normalization.** A subtitle present in only one provider means no match and possibly a duplicate work.
- **No journal names, topics or citation links as columns yet.** They exist in `raw` for some providers. Abstracts are stored per provider; `get_work` prefers arXiv's, then Semantic Scholar's, then OpenAlex's, because OpenAlex's rebuilt abstracts are sometimes front matter such as author lists.
- **Imports reject provider records that describe another paper.** Each import is anchored on the requested identifier; a provider record whose title doesn't match is reported as `mismatch` and not saved.
- **A verdict must cite papers that are already stored.** Import them first. `supported` needs at least one `supports` paper and `contradicted` at least one `contradicts` paper; `inconclusive` may cite none.
- **Re-assessing a claim adds a verdict; it never edits one.** The newest is current, and `get_claim` shows the whole history.

## Agent tools

The `academic-db-mcp` server exposes these tools; see the [README](../README.md) for what each one does, the YAML for each agent, and the recommended Scout/Verifier workflow.

| Tool | Lead | Scout | Verifier |
|---|:-:|:-:|:-:|
| `find_work`, `search_works`, `get_work` | ✓ | ✓ | ✓ |
| `top_cited`, `works_by_author` | | ✓ | ✓ |
| `list_claims`, `get_claim` | ✓ | ✓ | ✓ |
| `search_passages` | | ✓ | ✓ |
| `citing_statements`, `review_queue`, `run_sql` | | | ✓ |
| `import_works`, `import_work`, `save_work` | | ✓ | `import_works` |
| `add_claim` | ✓ | ✓ | |
| `assess_claim` | | | ✓ |

Read tools connect as `academic_reader`, so they can't change data even through `run_sql`.
