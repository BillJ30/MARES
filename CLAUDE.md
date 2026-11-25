# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Python ingestion job that collects SEC EDGAR filings, RSS headlines (Google News, Yahoo Finance, company IR feeds) and optional Finnhub news for a watchlist of US stocks, deduplicates them and stores them in Postgres (Supabase). It runs every 15 minutes via GitHub Actions (`.github/workflows/ingest.yml`). Everything is designed to stay on free tiers. Only headlines, short summaries and links are stored, never full articles.

Roadmap (README): step 1 (ingestion) is done; later steps add cross-source story clustering (`news_items.cluster_id` is reserved for this), a Streamlit dashboard, summaries/FinBERT tone, and Telegram alerts.

## Commands

```bash
pip install -r requirements.txt
cp .env.example .env                          # DATABASE_URL, SEC_USER_AGENT, FINNHUB_API_KEY (optional)

python -m newshub.ingest --dry-run            # fetch and print, no database needed
python -m newshub.ingest                      # fetch and store
python -m newshub.ingest --sources edgar -v   # one source (edgar,rss,finnhub), with logging
python -m newshub.ingest --lookback-days 7 --config path/to/watchlist.yaml

pytest -q                                     # parser + pipeline tests (DB tests skipped)
TEST_DATABASE_URL=postgresql://... pytest -q  # also run end-to-end tests against real Postgres
pytest tests/test_parsers.py::test_name -q    # single test
```

CI (`tests.yml`) runs the full suite on Python 3.12 against a Postgres 16 service. Exit code is 1 only when the run status is `failed`.

## Architecture

- **`newshub/ingest.py`** is the orchestrator. `run()` loads settings, applies `db/schema.sql` (idempotent, applied on every run — there are no migrations; schema changes must stay `if not exists`-safe), upserts companies, then runs `_run_edgar` / `_run_rss` / `_run_finnhub`, inserts items, prunes rows older than `retention_days`, and writes an `ingest_runs` row.
- **Fault isolation is a core rule**: every per-company/per-feed fetch is wrapped in `try/except` and reported through `Tracker` (`ok`/`fail`/`error`). One failure must never stop the run. `Tracker.status` yields `ok`, `partial` (any failure or error) or `failed` (zero successful requests).
- **Sources** (`newshub/sources/`) each split into a thin client class doing HTTP (`EdgarClient`, `RssClient`, `FinnhubClient`) and pure `parse_*` functions that turn raw JSON/XML into `NewsItem`s. Tests exercise the parsers with fixtures in `tests/fixtures/` and swap the clients via the `Clients` dataclass (`FakeEdgar`, `FakeRss`, `FakeFinnhub` in `tests/conftest.py`). Keep that split when adding a source; a new source also needs adding to `ALL_SOURCES`, `Clients`/`build_clients`, and the `news_items.source` check constraint.
- **EDGAR**: CIKs are resolved once via the SEC ticker map and cached in `companies.cik` (or set with `cik:` in the watchlist). 8-K item codes map to readable titles and tags through `ITEM_DESCRIPTIONS` / `ITEM_TAGS` in `edgar.py`. Requests need `SEC_USER_AGENT` (name + email); without it EDGAR is skipped with an error. Stay under the SEC's 10 req/s (`http.RateLimiter`).
- **Dedup**: uniqueness is `(ticker, url_hash)` where `url_hash` is sha256 of `text.normalize_url` (lowercased host, tracking params like `utm_*` stripped, no fragment/trailing slash). Inserts use `on conflict do nothing returning id` to count new rows.
- **`newshub/db.py`** uses psycopg 3 with `prepare_threshold=None` because Supabase's PgBouncer pooler doesn't support server-side prepared statements. Use the Supabase *Session pooler* URL (direct connection is IPv6-only and unreachable from GitHub runners).
- **Config**: `config/watchlist.yaml` holds companies (ticker, name, optional `cik`, `news_query`, `feeds`), `settings` (lookback/retention days, `sec_forms`) and `rss_templates` (with `{query}`/`{ticker}` placeholders). Secrets come only from env / `.env` (`newshub/config.py`).
- All `published_at` timestamps are timezone-aware UTC.
- Row level security is enabled with no policies on purpose, to block Supabase's public REST API; the job connects as table owner.
