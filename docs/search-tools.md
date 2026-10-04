# Literature search tools

The tools use the standard library plus Omnigent's tool decorator.

## Layout

```
tools/literature/            literature-tools package: provider modules + search_support.py
lab/agents/
  scout/                     config.yaml + tools/python/<tool>.py, one file per tool
  verifier/                  config.yaml + tools/python/<tool>.py
```

Omnigent gives each agent only the tools in its own `tools/python/` folder,
and only dispatches a tool whose function name matches its file name
(`openalex_search.py` defines `openalex_search`). The implementations therefore
live once in the `literature-tools` workspace package, installed into the
project venv by `uv sync`, and each agent has a three-line file per tool that
re-exports it with `search_support.export`. To give an agent another tool, copy
one of those files and rename it. Because the code is imported from the venv, an agent
folder works when run on its own (`omnigent run lab/agents/scout`), as a
sub-agent, or on the deployed server.

| Agent | Tools |
|---|---|
| Scout | `openalex_search`, `openalex_get_paper`, `arxiv_search`, `arxiv_get_paper`, `europepmc_search`, `europepmc_get_paper`, `crossref_search`, `zbmath_search`, `zbmath_get_paper`, `loogle_search`, `read_full_text` |
| Verifier | `crossref_get_paper`, `openalex_get_paper`, `arxiv_get_paper`, `europepmc_get_paper`, `zbmath_get_paper`, `unpaywall_find_full_text`, `loogle_search`, `check_quote`, `read_full_text` |

Do not set `sandbox.container_image` for these agents: Omnigent runs container
tools with networking disabled, which breaks every provider.

Run Omnigent from the project environment (`uv run omnigent …` or
`.venv/bin/omnigent`) so the agents can import `literature-tools` and `pypdf`.

## Configuration

Copy `.env.example` to `.env` at the repository root and fill it in. The
literature tools and `academic_db` both read it; real environment variables
take precedence. `CONTACT_EMAIL` is required for Unpaywall, the database URLs
for saving papers and claims; the API keys are optional. The model login is
configured with `omnigent setup`, not in `.env`.

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

## Checking quotes against full text

`check_quote(paper_id, quote)` checks that a quote (for example a claim's
`source_quote`) really appears in the paper. It takes a DOI, arXiv ID, PMCID,
PMID or OpenAlex ID and reads, in order:

1. Europe PMC full-text XML (open-access PMC papers), with sections.
2. arXiv HTML, with sections.
3. An open-access PDF: arXiv, then OpenAlex and Unpaywall links (Unpaywall
   needs `CONTACT_EMAIL`), at most three tries. Text comes from `pypdf`, so
   locations are page numbers, columns may interleave and scanned PDFs without
   a text layer fail. Links that return a web page instead are skipped.
4. The abstract alone.

`attempts` records why earlier sources were skipped. Matching is deterministic
and ignores case, punctuation, citation markers, footnotes and words split by
PDF extraction ("sup ergravity"); reference lists are excluded.

It returns `verdict` (`exact`, `near_exact` ≥ 0.9, `partial` ≥ 0.6, `not_found`),
`match_score`, `location` (section path and paragraph), `matched_text`,
surrounding `context` and `differences`, the changed words. One altered number
can still score `near_exact`, so always read `differences`. If
`checked_against` is `abstract`, `not_found` only means the quote is not in the
abstract. A match shows the words are in the paper, not that they support the
claim; the Verifier judges that and records it with `assess_claim`.

`read_full_text(paper_id)` returns the section outline; pass `section` (and
`next_offset` as `offset`) to read a section in 8,000-character pages. Parsed
texts are cached under `SEARCH_STATE_DIR/fulltext`, so repeated checks on one
paper fetch it once.

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
- Unpaywall requires `CONTACT_EMAIL` (environment or `.env`).
  Responses are checked for matching DOI, Boolean access status and valid
  location structure. The best location is first. A negative result means
  Unpaywall has no known copy; continue checking other legitimate repositories.
- Loogle validates the result envelope and declarations. `truncated=true`
  means only a subset was returned, either due to the caller's limit or the
  service's cap. Its warning recommends narrowing the query;
  `pagination_supported=false` makes the limitation explicit.

Validate all offline search tests and the additional providers' live behaviour:

```sh
python -m unittest discover -s scripts -p 'test*.py' -v
python scripts/smoke_additional_search_tools.py
python scripts/smoke_full_text.py
```

The live script requires network access and `CONTACT_EMAIL`; it never prints
that value. It checks searches, continuation, lookup, Loogle error/truncation
behaviour and two real Unpaywall DOI lookups.
