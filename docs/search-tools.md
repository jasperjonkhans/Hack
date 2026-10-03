# Research search tools

The original seven researcher tools are: `openalex_search`, `europepmc_search`,
`arxiv_search`, their three corresponding `*_get_paper` tools, and
`deduplicate_papers`. They use the standard library plus Omnigent's tool decorator.
Keep `search_support.py` alongside the provider scripts when deploying the bundle.

## Searching and continuing

Search returns `results`, `total_matches`, `returned_count`, `has_more`,
`next_cursor`, `effective_query` and `retrieved_at`. Pass `next_cursor` back as
`cursor`, keeping every other argument unchanged. Page sizes are 1–100; start
with 10 because complete abstracts can be long. Cursors are bound to the query,
provider, filters, sort and page size. They do not freeze an upstream index;
deduplicate across pages and searches as well as across providers.

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

Call `deduplicate_papers` on combined results before assigning evidence IDs.
It groups by shared DOI, PMID, PMCID, arXiv ID without its version suffix, or
provider ID. It retains every source/version record in each group. Similar
titles alone do not establish identity; preprints with separate identifiers
may still need manual linking. A merged group is one work, not several pieces
of independent evidence. Citation counts from different sources are not summed.

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
version-preserving deduplication, Omnigent discovery and cross-process spacing.
Live checks cover six searches, filters, non-overlapping pages and detail lookups,
plus known-paper retrieval from each provider. This small benchmark catches
regressions; it does not establish exhaustive recall or evidence quality.

## Additional providers

The bundle also includes Crossref search/lookup, zbMATH search/lookup, Unpaywall
access lookup and Loogle declaration search.

- Crossref's former `author` argument is now `author_query`. This is a fuzzy
  search hint, not an author filter. Calls using it return a warning to verify
  author identity and topic relevance. Live Crossref pages can overlap; combine
  them with `deduplicate_papers` before counting evidence. Update existing callers and restart
  searches using old cursors after migrating this argument.
- Pages now report `fetched_count` and `skipped_count`. Crossref and zbMATH may
  omit untitled records, but those records still count towards pagination.
  An empty returned page with `has_more=true` should be followed using its
  cursor. `total_matches` is the upstream count, including skipped records.
- Deduplication recognises zbMATH IDs, Zbl numbers and each provider's own ID,
  preserving all representations. Provider IDs are namespaced to prevent
  unrelated providers' numeric IDs from colliding.
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
