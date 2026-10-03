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

   Optional: `S2_API_KEY` (Semantic Scholar rate-limits anonymous use quickly) and `OPENALEX_MAILTO` (your email, for OpenAlex's faster pool).

2. From the repository root, run `uv sync`. This package is a uv workspace member, so it installs into the root `.venv`.

3. Check it starts: `uv run academic-db-mcp` should wait silently for MCP messages on stdin (Ctrl-C to stop).

## Tools

| Tool | Access | Purpose |
|---|---|---|
| `find_work` | read | Look up a stored paper by DOI, arXiv ID, OpenAlex W-ID, Semantic Scholar ID or URL |
| `search_works` | read | Title search with year, type and open-access filters, most cited first |
| `get_work` | read | One paper: each provider's values and authors side by side, plus claims citing it |
| `top_cited` | read | Most cited papers by year, type or journal |
| `works_by_author` | read | One author's papers (author IDs are provider-specific) |
| `review_queue` | read | Data-quality issues: title-only merges, providers disagreeing on the year |
| `run_sql` | read | One read-only SQL statement, 15 s limit, through a login that cannot write |
| `list_claims` | read | Claims with their current verdict and confidence |
| `get_claim` | read | One claim's full assessment history and cited papers |
| `import_work` | write | Fetch a paper from all three providers by identifier, merge and save it |
| `save_work` | write | Save one provider record the agent already has (e.g. from a search tool) |
| `add_claim` | write | Record a claim to verify (duplicates return the existing claim) |
| `assess_claim` | write | Record a verdict with a 0–1 confidence, rationale and supporting or contradicting papers; history is kept |

Read tools use the read-only `academic_reader` login, and write tools use the owner login. Writes are serialized with a database lock, so several agents saving papers at the same time cannot create duplicates.

## Assigning tools to agents

Add the server under `tools` in an agent's `config.yaml`, and allow-list that agent's tools. `ACADEMIC_DB_AGENT` is recorded as `created_by` / `assessed_by` on claims and verdicts.

```yaml
# Lead: follows the evidence, poses claims
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
# Scout: finds and saves papers, extracts claims
tools:
  academic_db:
    type: mcp
    command: uv
    args: [run, --quiet, academic-db-mcp]
    env:
      ACADEMIC_DB_AGENT: scout
    tools: [find_work, search_works, get_work, top_cited, works_by_author,
            import_work, save_work, list_claims, get_claim, add_claim]
```

```yaml
# Verifier: checks claims against the papers, writes confidence-scored verdicts
tools:
  academic_db:
    type: mcp
    command: uv
    args: [run, --quiet, academic-db-mcp]
    env:
      ACADEMIC_DB_AGENT: verifier
    tools: [find_work, search_works, get_work, top_cited, works_by_author,
            review_queue, run_sql, list_claims, get_claim, assess_claim]
```

`uv run` finds the repository's project from the directory `omnigent run` was launched in, so this works from `demo/workspace` or any other folder inside the repository. The database URLs come from the root `.env`, never from YAML.

## Typical flow

1. The Lead or Scout records a claim with `add_claim`.
2. The Scout finds papers (`search_works`, or a literature search tool) and saves them with `import_work` / `save_work`.
3. The Verifier reads them (`get_work`), then calls `assess_claim` with a verdict, a confidence and the `work_id`s that support or contradict the claim.
4. The Lead reads `list_claims` / `get_claim` to decide what to do next.

## Development

```bash
uv run --package academic-db-mcp pytest tools/academic_db   # from the repository root
```

- Unit tests use saved provider responses in `tests/fixtures/`.
- Tests marked `db` use the real database through the `.env` logins, and roll back every change.

To apply schema changes, add a numbered file in `migrations/` and run `./migrate.sh`. It records applied migrations in `schema_migrations`. `tests/sql/001_init_test.sql` checks the base schema with `psql`.
