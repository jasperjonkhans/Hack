# Academic Works Schema — Design

- **Date:** 2026-10-03
- **Status:** Implemented in `migrations/001_init.sql`
- **Database:** PostgreSQL

Changes since this design, implemented in `migrations/002_claims.sql` and the `academic-db-mcp` tools:

- Claims, confidence-scored assessments and cited evidence were added (see [database.md](database.md)).
- `refresh_work` and `merge_works` live in the database rather than in loader code.
- Saves take an advisory lock, so parallel writers are safe (this replaces the "one loader at a time" limitation below).
- `migrations/003_provenance.sql` adds per-provider abstracts, the `work_quality` view, and claim provenance (`source_work_id`, `source_quote`).
- `migrations/004_evidence_checks.sql` adds `quote`, `location` and `match_score` to `claim_evidence`, so every citation can show it was checked against the paper's text.
- Imports anchor each paper on the requested identifier and reject other providers' records whose title doesn't match. Matching by ID alone let a corrupted OpenAlex record (BERT, W2896457183) attach an unrelated paper.

## 1. Goal

Store academic works from several metadata providers in one PostgreSQL database. Each real paper is one row, no matter how many providers return it. Each provider's own values (citation counts, open-access status, author and source IDs) are kept side by side, so providers can be compared and nothing is lost when they disagree.

Providers: **OpenAlex** (primary), **Semantic Scholar**, **arXiv**.

## 2. Scope

In scope:

- One SQL migration: tables, constraints, indexes, the `work_overview` view, and seed rows for `providers`.
- The normalization, matching and canonical-value rules in sections 5–7. Every loader must follow them.

Out of scope (separate steps):

- Loader code that calls the provider APIs and applies these rules.
- Author and source metadata (names, ORCID, ISSN). Only IDs are stored, plus author names.
- Abstracts, references, citation graph, fields of study.
- Fuzzy title matching (only exact match after normalization).
- Running several loaders in parallel.

## 3. Fields stored

The requested fields, plus: DOI and title (to match papers across providers), arXiv and MAG IDs (more match keys), and author names (arXiv has no author IDs).

| Field | OpenAlex | Semantic Scholar | arXiv |
|---|---|---|---|
| Provider work ID | `id` | `paperId` | `<id>` |
| DOI | `doi` | `externalIds.DOI` | `<arxiv:doi>` (only once published) |
| arXiv ID | `locations[]` (see §5) | `externalIds.ArXiv` | `<id>` |
| MAG ID | `ids.mag` | `externalIds.MAG` | — |
| Title | `title` | `title` | `<title>` |
| Publication year | `publication_year` | `year` | year of `<published>` (v1 date) |
| Type | `type` | `publicationTypes` (mapped, §5) | always `preprint` |
| Source ID | `primary_location.source.id` | `publicationVenue.id` | — |
| Citations | `cited_by_count` | `citationCount` | — |
| Is OA | `open_access.is_oa` | `isOpenAccess` | always `true` |
| OA status | `open_access.oa_status` | `openAccessPdf.status` | always `green` |
| OA URL | `open_access.oa_url` | `openAccessPdf.url` | `href` of `<link title="pdf">` |
| Author ID | `authorships[].author.id` | `authors[].authorId` | — |
| Author name | `authorships[].author.display_name` | `authors[].name` | `<author><name>` |

Request parameters the loaders need:

- OpenAlex `select`: `id,doi,ids,title,type,publication_year,cited_by_count,open_access,primary_location,locations,authorships`
- Semantic Scholar `fields`: `paperId,externalIds,title,year,publicationTypes,publicationVenue,citationCount,isOpenAccess,openAccessPdf,authors`

## 4. Data model

Four tables and one view. `work_records` is the source of truth: each row is one provider's view of one paper, already normalized. `works` holds canonical values derived from those records (§7).

```
providers 1──* work_records *──1 works
                    │
                    1
                    │
                    * work_authors
```

### 4.1 `providers`

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `text` PK | no | `openalex`, `semantic_scholar`, `arxiv` |
| `priority` | `smallint` UNIQUE | no | Lower number = preferred |

Seed rows: `('openalex', 1)`, `('semantic_scholar', 2)`, `('arxiv', 3)`. Adding a provider or changing the priority order means editing a row, with no schema change.

### 4.2 `works`

Canonical values for one real paper. Loaders don't set these columns directly; they are recomputed from `work_records` (§7).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `bigint` identity PK | no | |
| `doi` | `text` | yes | UNIQUE. CHECK: `doi = lower(doi) AND doi LIKE '10.%'` |
| `arxiv_id` | `text` | yes | UNIQUE. No version suffix |
| `mag_id` | `bigint` | yes | UNIQUE |
| `title` | `text` | no | |
| `title_norm` | `text` | no | Generated, stored: `lower(regexp_replace(title, '[^[:alnum:]]+', '', 'g'))` |
| `publication_year` | `smallint` | yes | |
| `type` | `text` | yes | OpenAlex type names (§5) |
| `created_at` | `timestamptz` | no | Default `now()` |
| `updated_at` | `timestamptz` | no | Default `now()`; set on every recompute |

Indexes: `(title_norm, publication_year)` for the title fallback; `(publication_year)` for filtering.

### 4.3 `work_records`

One row per provider per paper.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `bigint` identity PK | no | |
| `work_id` | `bigint` FK → `works(id)` ON DELETE CASCADE | no | |
| `provider` | `text` FK → `providers(id)` | no | |
| `provider_work_id` | `text` | no | `W2626778328`, S2 `paperId`, `1706.03762` |
| `matched_by` | `text` | no | CHECK in (`new`, `doi`, `arxiv`, `mag`, `title`). Set on first insert; not changed on refresh |
| `doi` | `text` | yes | Same CHECK as `works.doi` |
| `arxiv_id` | `text` | yes | |
| `mag_id` | `bigint` | yes | |
| `title` | `text` | no | Records without a title are skipped by loaders |
| `publication_year` | `smallint` | yes | |
| `type` | `text` | yes | Already mapped to OpenAlex type names |
| `source_id` | `text` | yes | Provider-scoped: `S137773608` (OpenAlex) or venue UUID (S2) |
| `cited_by_count` | `integer` | yes | CHECK `>= 0` |
| `is_oa` | `boolean` | yes | |
| `oa_status` | `text` | yes | CHECK in (`diamond`, `gold`, `green`, `hybrid`, `bronze`, `closed`) |
| `oa_url` | `text` | yes | |
| `raw` | `jsonb` | no | The provider's full payload for this work. For arXiv: `{"entry_xml": "<entry>…</entry>"}` |
| `fetched_at` | `timestamptz` | no | Default `now()` |

Constraints and indexes: UNIQUE `(provider, provider_work_id)`; index on `work_id`; partial indexes on `doi`, `arxiv_id` and `mag_id` (`WHERE … IS NOT NULL`) for matching (§6).

### 4.4 `work_authors`

| Column | Type | Null | Notes |
|---|---|---|---|
| `work_record_id` | `bigint` FK → `work_records(id)` ON DELETE CASCADE | no | |
| `position` | `smallint` | no | 0-based order in the provider's author list |
| `author_id` | `text` | yes | Provider-scoped: `A5001226970` (OpenAlex), `40348417` (S2). `NULL` for arXiv |
| `author_name` | `text` | no | |

PK `(work_record_id, position)`. Partial index on `author_id WHERE author_id IS NOT NULL`. When a record is refreshed, its authors are deleted and re-inserted.

### 4.5 View `work_overview`

One row per work: every `works` column, plus `primary_provider`, `source_id`, `cited_by_count`, `is_oa`, `oa_status` and `oa_url` taken from **one** record: the highest-priority provider's, and the newest one if that provider has several. All OA fields come from the same record, so they never mix providers. Works always have at least one record; the `LEFT JOIN` is only a safety net.

```sql
SELECT w.*, r.provider AS primary_provider,
       r.source_id, r.cited_by_count, r.is_oa, r.oa_status, r.oa_url
FROM works w
LEFT JOIN LATERAL (
  SELECT wr.*
  FROM work_records wr
  JOIN providers p ON p.id = wr.provider
  WHERE wr.work_id = w.id
  ORDER BY p.priority, wr.fetched_at DESC
  LIMIT 1
) r ON true;
```

## 5. Normalization

Loaders turn each provider payload into one `work_records` row (plus its `work_authors` rows) using these rules.

### 5.1 All providers

- **Empty strings become `NULL`.** Semantic Scholar returns `"openAccessPdf": {"url": ""}` when there is no PDF.
- **Title:** trim, and collapse runs of whitespace to one space. arXiv titles contain line breaks.
- **DOI:** strip `https://doi.org/`, `http://doi.org/`, `https://dx.doi.org/` and `doi:` prefixes, trim, lowercase. Semantic Scholar sometimes returns uppercase DOIs.
- **arXiv ID:** strip `http(s)://arxiv.org/abs/` or `/pdf/`, a trailing `.pdf`, and a trailing version (`v\d+`). Old-style IDs keep their archive prefix (`cond-mat/0410550`).
- **A DOI of the form `10.48550/arxiv.<id>`** also gives `arxiv_id = <id>`.
- **MAG ID:** string → `bigint`.
- **OA status:** lowercase. Any value outside the six allowed ones becomes `NULL`, and the loader logs it.

### 5.2 OpenAlex

- Remove the `https://openalex.org/` prefix from work, source and author IDs (`W…`, `S…`, `A…`).
- **arXiv ID:** OpenAlex's `ids` object has no arXiv entry. Take it from the first item in `locations[]` that has either `id = pmh:oai:arXiv.org:<id>` or a `landing_page_url` matching `arxiv.org/abs/<id>`, or from a `10.48550/arxiv.<id>` DOI. For example, `W2626778328` has `pmh:oai:arXiv.org:1706.03762`.
- `type` is stored as returned. OpenAlex's type names are the canonical list: `article`, `preprint`, `review`, `conference-paper`, `book`, `book-chapter`, `dataset`, `editorial`, `letter`, `other`, and so on.

### 5.3 Semantic Scholar

- `provider_work_id = paperId`; `source_id = publicationVenue.id`.
- `is_oa = isOpenAccess`; `oa_status = openAccessPdf.status` (lowercased); `oa_url = openAccessPdf.url`.
- **Type:** first remove spaces from each `publicationTypes` value (the docs show `Journal Article`, the live API returns `JournalArticle`). Then use the first match in this order:

  | S2 value | Stored type |
  |---|---|
  | `Review` | `review` |
  | `Book` | `book` |
  | `BookSection` | `book-chapter` |
  | `Dataset` | `dataset` |
  | `Editorial` | `editorial` |
  | `LettersAndComments` | `letter` |
  | `Conference` | `conference-paper` |
  | `JournalArticle` | `article` |
  | any other value | `other` |
  | null or empty list | `NULL` |

### 5.4 arXiv

- `provider_work_id = arxiv_id`, taken from `<id>`.
- `publication_year` = year of `<published>`.
- `type = 'preprint'`, `is_oa = true`, `oa_status = 'green'`, `oa_url` = `href` of `<link title="pdf">`.
- `source_id`, `cited_by_count` and `mag_id` are `NULL`.
- Authors: `<author><name>` in document order, with `author_id = NULL`.

## 6. Matching

For each normalized record R, in one transaction:

1. **Existing record.** If `(R.provider, R.provider_work_id)` already exists, update that row in place (every column, and replace its authors). Keep its `work_id` and `matched_by`. Then continue to step 2: new IDs may now link it to another work.
2. **Look up by ID.** Collect the works that have any record where `doi = R.doi`, `arxiv_id = R.arxiv_id` or `mag_id = R.mag_id`. Only R's non-null keys are used. If step 1 found a record, add its work.
   - **One work found:** attach R to it. A new record gets `matched_by` set to the first key that matched, checked in the order `doi`, `arxiv`, `mag`.
   - **Several works found:** merge them (step 4) into the one with the lowest `id`, then attach R.
   - **None found:** go to step 3.
3. **Title fallback** (only if R has a `publication_year`). Candidates are works with `title_norm = norm(R.title)` and `publication_year BETWEEN R.year - 1 AND R.year + 1`. Drop any candidate that:
   - already has a record from R's provider (a provider doesn't list the same paper twice), or
   - has a `doi`, `arxiv_id` or `mag_id` that conflicts with R's non-null value for that key.

   If exactly one candidate remains, attach R with `matched_by = 'title'`. Otherwise create a new work and set `matched_by = 'new'`.
4. **Merge** works B… into A: move their `work_records` to A, delete B…, then recompute A.
5. **Recompute** the canonical values of the affected work (§7).

Lookups go through `work_records` rather than `works`, so an ID that only a lower-priority provider reported still matches.

## 7. Canonical values

For a work W, sort its records by provider priority, then by `fetched_at` descending:

| `works` column | Rule |
|---|---|
| `title`, `type` | First record with a non-null value |
| `doi`, `arxiv_id`, `mag_id` | First record with a non-null value |
| `publication_year` | `MIN` across all records (picks the original over later reposts) |
| `updated_at` | `now()` |

The same set of records always gives the same canonical values, regardless of load order.

## 8. Worked example

"Attention Is All You Need", loaded in the order OpenAlex → Semantic Scholar → arXiv. Values are from the live APIs on 2026-10-03.

1. **OpenAlex `W2626778328`.** `doi = 10.65215/2q58a426` (a 2025 repost DOI that OpenAlex attaches to this work), `arxiv_id = 1706.03762` (from `locations`), `mag_id = 2626778328`, year 2025, type `preprint`, 26,739 citations. Nothing matches, so it creates work #1 with `matched_by = 'new'`.
2. **Semantic Scholar `204e3073…`.** No DOI, `arxiv_id = 1706.03762`, `mag_id = 2626778328`, year 2017, 195,136 citations, `is_oa = false`. It matches work #1 by arXiv ID (`matched_by = 'arxiv'`). Recompute: year becomes `MIN(2025, 2017) = 2017`; title, type and DOI stay OpenAlex's.
3. **arXiv `1706.03762`.** Year 2017, OA green. It matches work #1 by arXiv ID.

Result: one work with three records and year 2017. `work_overview` shows OpenAlex's citations (26,739) and OA values.

## 9. Testing

For the migration:

- It applies cleanly to an empty database and seeds three providers.
- Constraints reject: an uppercase or non-`10.` DOI, `oa_status = 'GOLD'`, negative `cited_by_count`, a duplicate `(provider, provider_work_id)`, an unknown provider.
- `title_norm` for "Attention is All you Need" is `attentionisallyouneed`.
- `work_overview` picks the OpenAlex record when one exists, Semantic Scholar when only Semantic Scholar and arXiv exist, and the newest record when a provider has two.
- Deleting a work cascades to its records and their authors.

For the loader step (later), the §8 example is the acceptance test. Fixtures: the real payloads for OpenAlex `W2626778328`, Semantic Scholar `arXiv:1706.03762`, arXiv `1706.03762`, plus OpenAlex `doi:10.1038/nature14539` (a journal paper with no arXiv version).

## 10. Known limitations

- **Provider-scoped author and source IDs.** The same author has different IDs in OpenAlex and Semantic Scholar, and they aren't linked. Linking would need ORCID (authors) or ISSN (sources).
- **MAG is frozen.** MAG IDs only exist for papers indexed up to the end of 2021. Newer papers match by DOI, then arXiv ID, then title.
- **Exact title matching.** A title that differs by more than case, punctuation or spacing (for example, a subtitle present in only one provider) creates a duplicate work.
- **No automatic un-merge.** Records with `matched_by = 'title'` are the ones to review.
- **OpenAlex wins even when it's wrong for a paper.** For "Attention Is All You Need", `work_overview` shows OpenAlex's 26,739 citations and a repost DOI. Semantic Scholar's 195,136 is still in `work_records`.
- **One loader at a time.** Parallel loaders would need row locks or advisory locks around steps 2–5.
- **Semantic Scholar rate limit.** Without an API key it returns 429 quickly. Get a key before bulk loading.
