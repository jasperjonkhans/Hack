# Literature search tools

The tools use the standard library plus Omnigent's tool decorator.

## Layout

```
lab/
  lib/                       provider modules + search_support.py (shared code)
  agents/
    scout/tools/python/      literature_search.py: re-exports the Scout's tools
    verifier/tools/python/   source_lookup.py: re-exports the Verifier's tools
```

Omnigent gives each agent only the tools in its own `tools/python/` folder,
and every file there must define at least one `@tool`. The implementations
therefore live once in `lab/lib/`, and each agent's file re-exports the subset
it needs with `search_support.export`. To give an agent another tool, add one
`export` line to its file. `lab/lib/` must stay inside the bundle root, because
Omnigent ships the whole bundle and the agent files find it relative to
themselves.

| Agent | Tools |
|---|---|
| Scout | `openalex_search`, `openalex_get_paper`, `arxiv_search`, `arxiv_get_paper`, `europepmc_search`, `europepmc_get_paper`, `crossref_search`, `zbmath_search`, `zbmath_get_paper`, `loogle_search` |
| Verifier | `crossref_get_paper`, `openalex_get_paper`, `arxiv_get_paper`, `europepmc_get_paper`, `zbmath_get_paper`, `unpaywall_find_full_text`, `loogle_search` |

Do not set `sandbox.container_image` for these agents: Omnigent runs container
tools with networking disabled, which breaks every provider.

## Searching and continuing

Search tools default to `detail="compact"`: identifiers, title, first three
authors, year, venue, type, citation count, `is_preprint`/`is_retracted` and a
200-character `snippet`. That is about 60% smaller than `detail="full"` (roughly
1.8k vs 4.5k tokens for 10 OpenAlex results). Shortlist from compact results,
then read full abstracts with the `*_get_paper` tools or `detail="full"`. The
detail level can change between pages without invalidating the cursor.

Search returns `results`, `total_matches`, `returned_count`, `has_more`,
`next_cursor`, `effective_query` and `retrieved_at`. Pass `next_cursor` back as
`cursor`, keeping every other argument unchanged. Page sizes are 1–100; start
with 10 because complete abstracts can be long. Cursors are bound to the query,
provider, filters, sort and page size. They do not freeze an upstream index,
so pages and providers can return the same work; the academic database (#11)
merges duplicates when papers are saved.

OpenAlex and Europe PMC default to `search_scope="title_abstract"` for every
sort. Use `all` for broader discovery or `title` for targeted searches. Sorting
never changes the scope. OpenAlex accepts its native Boolean syntax directly.
For arXiv and Europe PMC, set `query_mode="advanced"` and `search_scope="all"`
to use native query syntax. For example:

```python
arxiv_search('cat:math.NT AND abs:"prime gaps"', query_mode="advanced")
europepmc_search('TITLE:"malaria vaccine" AND AUTH:Smith',
                 query_mode="advanced", search_scope="all")
```

In plain arXiv queries, each word or quoted phrase is required. Boolean operators
in plain mode are rejected with guidance rather than rewritten as search terms.
`from_year` refers to publication year for OpenAlex/Europe PMC and submission
year for arXiv. arXiv searches are limited to 30,000 results upstream; narrow the
query if the tool reports that boundary.

## Paper records and evidence

Search and lookup return full available abstracts, all supplied author names,
provider IDs, normalised identifiers, access links, citation metadata and
retrieval provenance. An absent abstract is `null`; `abstract_truncated` is false.
The tools supply available full-text links; they do not download or read PDFs.

`doi` is now a lower-case bare DOI; use `doi_url` for a clickable URL. Provider
`id` values remain suitable for the matching `*_get_paper` tool. OpenAlex lookup
also accepts a DOI; Europe PMC accepts a bare PMID or source-qualified ID;
arXiv accepts modern/legacy IDs and optional versions.

`is_retracted` and `is_preprint` may be null when the source does not establish
status. False is not a guarantee of scientific reliability or peer review.
arXiv's preprint flag describes the repository version, which can also have a
published DOI. Unknown citation counts remain null rather than becoming zero.

Records carry `identifiers` (DOI, PMID, PMCID, versionless arXiv ID, provider
ID) so the academic database can merge the same work found by several providers.
A merged work is one piece of evidence, not several.

## Failures and request limits

Failures return `error` with `code`, `message` and `retryable`, optionally
`http_status` and `retry_after_seconds`. Treat errors as failed searches, never
as evidence that no literature exists. Invalid input, authentication failures,
provider error feeds and malformed responses are distinguishable from a valid
zero-result response. Authentication errors no longer silently fall back to
keyless OpenAlex access.

Temporary HTTP/network failures get bounded retries. `Retry-After` is respected;
long cooldowns are returned to the caller instead of sleeping through them.
Daily quota exhaustion is reported separately and is not immediately retried.
OpenAlex credentials use an Authorization header and are omitted from results.

SQLite coordinates requests across threads and subprocesses on one host,
including a three-second gap after each arXiv request. State lives under
`~/.cache/discovery-lab/search`; override with `SEARCH_STATE_DIR` if needed.
Workers must share this directory. Separate hosts need a deployment-level
shared limiter; these scripts do not coordinate across machines.

## Validation

From the repository root, using Python with `omnigent_client` installed:

```sh
python -m unittest discover -s scripts -p 'test_search_tools.py' -v
python scripts/smoke_search_tools.py
```

Offline tests cover failure handling, query scope, pagination, identifiers,
each agent's tool set as Omnigent loads it, and cross-process spacing.
Live checks cover six searches, filters, non-overlapping pages and detail lookups,
plus known-paper retrieval from each provider. This small benchmark catches
regressions; it does not establish exhaustive recall or evidence quality.

## Additional providers

The bundle also includes Crossref search/lookup, zbMATH search/lookup, Unpaywall
access lookup and Loogle declaration search.

- Crossref's former `author` argument is now `author_query`. This is a fuzzy
  search hint, not an author filter. Calls using it return a warning to verify
  author identity and topic relevance. Live Crossref pages can overlap.
- Pages now report `fetched_count` and `skipped_count`. Crossref and zbMATH may
  omit untitled records, but those records still count towards pagination.
  An empty returned page with `has_more=true` should be followed using its
  cursor. `total_matches` is the upstream count, including skipped records.
- zbMATH records carry both the zbMATH document ID and the Zbl number in
  `identifiers`.
- Unpaywall requires `CONTACT_EMAIL`, available from the shell environment.
  Responses are checked for matching DOI, Boolean access status and valid
  location structure. The best location is first. A negative result means
  Unpaywall has no known copy; continue checking other legitimate repositories.
- Loogle validates the result envelope and declarations. `truncated=true`
  means only a subset was returned, either due to the caller's limit or the
  service's cap. Its warning recommends narrowing the query;
  `pagination_supported=false` makes the limitation explicit.

Validate all offline search tests and the additional providers' live behaviour:

```sh
python -m unittest discover -s scripts -p 'test*search_tools.py' -v
python scripts/smoke_additional_search_tools.py
```

The live script requires network access and `CONTACT_EMAIL`; it never prints
that value. It checks searches, continuation, lookup, Loogle error/truncation
behaviour and two real Unpaywall DOI lookups.
