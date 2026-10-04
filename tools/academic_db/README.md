# academic_db: database tools for the agents

An MCP server that lets Omnigent agents read and write the shared academic works database: papers merged from OpenAlex, Semantic Scholar and arXiv, plus claims the team verifies against those papers.

- Database reference for agents and tool designers: [docs/database.md](docs/database.md)
- Schema design and rationale: [docs/schema-design.md](docs/schema-design.md)

## Setup

1. Put the two connection strings in a `.env` file at the **repository root**. It is gitignored; ask the database owner for the values.

   ```bash
   ACADEMIC_DB_URL=postgresql://external_full:...@<db-host>:5432/academic?sslmode=require
   ACADEMIC_DB_READER_URL=postgresql://academic_reader:...@<db-host>:5432/academic?sslmode=require
   ```

   Strongly recommended: `S2_API_KEY` ([free key](https://www.semanticscholar.org/product/api#api-key-form)). Without it, Semantic Scholar rate-limits quickly and the counter-evidence tools lose citation sentences and passage search. Optional: `OPENALEX_MAILTO` (your email, for OpenAlex's faster pool).

2. From the repository root, run `uv sync`. This package is a uv workspace member, so it installs into the root `.venv`.

3. Check it starts: `uv run academic-db-mcp` should wait silently for MCP messages on stdin (Ctrl-C to stop).

## Tools

| Tool | Access | Purpose |
|---|---|---|
| `find_work` | read | Look up a stored paper by DOI, arXiv ID, OpenAlex W-ID, Semantic Scholar ID/CorpusId or URL |
| `search_works` | read | Title search with year, type and open-access filters, most cited first. Compact rows, including `is_retracted` and `preprint_only` |
| `get_work` | read | One paper: abstract, quality signals, each provider's values and authors, claims taken from it, and claims citing it |
| `top_cited` | read | Most cited papers by year, type or journal |
| `works_by_author` | read | One author's papers (author IDs are provider-specific) |
| `review_queue` | read | Data-quality issues: title-only merges, providers disagreeing on the year |
| `run_sql` | read | One read-only SQL statement, 15 s limit, through a login that cannot write |
| `list_claims` | read | Claims with their current verdict and confidence |
| `get_claim` | read | One claim: its source paper and quote, and its full assessment history with cited papers |
| `citing_statements` | read | What later papers say about a paper: citing papers with the sentences that mention it |
| `search_passages` | read | Full-text passages (title, abstract, body) matching a query, e.g. the claim or its negation |
| `import_works` | write | Fetch 1–50 papers from all three providers in one batch, merge and save them |
| `import_work` | write | Same for a single paper, with the full saved record in the result |
| `save_work` | write | Save one provider record the agent already has (e.g. from a search tool) |
| `add_claim` | write | Record a claim with its source paper and quote. Near-duplicates are returned instead of created |
| `assess_claim` | write | Record a verdict with a 0–1 confidence, rationale and supporting or contradicting papers; history is kept |

Read tools use the read-only `academic_reader` login, and write tools use the owner login. Writes are serialized with a database lock, so several agents saving papers at the same time cannot create duplicates.

Imports anchor each paper on the identifier you asked for. Another provider's record is saved only if its title matches; otherwise it's reported as `mismatch`. This guards against provider records that carry another paper's metadata, which happens in OpenAlex.

`citing_statements` and `search_passages` rely on Semantic Scholar. Without an `S2_API_KEY` it rate-limits quickly and returns no citation sentences; `citing_statements` then falls back to OpenAlex's most cited citing works.

## Assigning tools to agents

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

The Verifier can save papers so it can cite counter-evidence it finds without a round trip through the Lead.

Add the server under `tools` in an agent's `config.yaml`, and allow-list that agent's tools. `ACADEMIC_DB_AGENT` is recorded as `created_by` / `assessed_by` on claims and verdicts.

```yaml
# Lead: splits the question, poses claims, decides from verdicts
tools:
  academic_db:
    type: mcp
    command: uv
    args: [run, --quiet, academic-db-mcp]
    env:
      ACADEMIC_DB_AGENT: lead
    tools: [find_work, search_works, get_work, list_claims, get_claim, add_claim]
```

```yaml
# Scout: finds and saves papers for one facet of the question, extracts claims with their source
tools:
  academic_db:
    type: mcp
    command: uv
    args: [run, --quiet, academic-db-mcp]
    env:
      ACADEMIC_DB_AGENT: scout
    tools: [find_work, search_works, get_work, top_cited, works_by_author, search_passages,
            import_works, import_work, save_work, list_claims, get_claim, add_claim]
```

```yaml
# Verifier: checks claims against their source and the wider literature, writes confidence-scored verdicts
tools:
  academic_db:
    type: mcp
    command: uv
    args: [run, --quiet, academic-db-mcp]
    env:
      ACADEMIC_DB_AGENT: verifier
    tools: [find_work, search_works, get_work, top_cited, works_by_author, search_passages,
            citing_statements, review_queue, run_sql, import_works, list_claims, get_claim, assess_claim]
```

`uv run` finds the repository's project from the directory `omnigent run` was launched in, so this works from `demo/workspace` or any other folder inside the repository. The database URLs come from the root `.env`, never from YAML.

## Recommended workflow

Keep paper data out of the agents' context: tools save to the database and return compact rows, and agents pass IDs to each other, not text.

1. **The Lead splits the question into 2–4 facets and starts one Scout per facet.** Split by facet, not by search tool. Providers return overlapping papers, and querying several providers is code's job, not an agent's.
2. **Each Scout searches,** using its literature tools and `search_passages`, shortlists papers and saves them in one `import_works` call. It reads abstracts with `get_work` only for the shortlist. It records claims with `add_claim(text, source_work_id, source_quote)`, reuses near-duplicates it's shown, and returns the `claim_id`s to the Lead.
3. **The Lead picks the claims its conclusion depends on** and sends them to Verifiers in batches, grouped by source paper so each paper is read once. One Verifier is usually enough; add 1–2 above about 10–15 claims.
4. **Each Verifier, per claim:**
   - Skips what code already checked: `get_work`'s `quality` shows retractions, preprint-only status and provider disagreements.
   - Checks the claim is faithful to its source (`get_claim` → source quote, `get_work` → abstract).
   - Looks for counter-evidence: `citing_statements` on the source, then `search_passages` with the claim and with its negation. It saves anything it will cite with `import_works`.
   - Calls `assess_claim` with a confidence, a verdict, and a rationale that says what was searched.
5. **The Lead reads `list_claims`** and decides, or starts another round.

## Development

```bash
uv run --package academic-db-mcp pytest tools/academic_db   # from the repository root
```

- Unit tests use saved provider responses in `tests/fixtures/`.
- Tests marked `db` use the real database through the `.env` logins, and roll back every change.

To apply schema changes, add a numbered file in `migrations/` and run `./migrate.sh`. It records applied migrations in `schema_migrations`. `tests/sql/001_init_test.sql` checks the base schema with `psql`.
