# MARES - Market News Hub

One place to see the latest filings, press releases and news for 98 companies from the Nasdaq-100.
Instead of searching company by company, a scheduled job collects everything every 15 minutes,
removes duplicates and stores it in Postgres, ready for the dashboard.

The whole project runs on free tiers: GitHub Actions, Supabase and public data sources.

> Not financial advice. This project only collects and organises public information.

## How it works

```mermaid
flowchart LR
    subgraph Sources
        A[SEC EDGAR<br/>8-K, 10-Q, 10-K]
        B[RSS feeds<br/>Google News, Yahoo Finance,<br/>company IR feeds]
        C[Finnhub<br/>company news, optional]
    end
    subgraph "GitHub Actions (every 15 min)"
        D[newshub.ingest<br/>fetch, normalise, tag]
    end
    E[(Supabase Postgres)]
    F[Streamlit dashboard<br/>coming in step 3]
    A --> D
    B --> D
    C --> D
    D --> E --> F
```

Each run:

1. Looks up each company's SEC number (CIK) once and caches it in the database.
2. Pulls recent filings from EDGAR and turns 8-K item codes into readable titles and tags
   (for example item 5.02 becomes "Departure or Appointment of Directors or Officers", tagged `leadership`).
3. Reads the RSS feeds and, if a key is set, Finnhub company news. News feeds rotate through the
   watchlist in batches (25 companies per run by default), so every company gets fresh news about
   once an hour while SEC filings for every company are still checked every 15 minutes.
   This keeps each run short and stays polite to the free feeds.
4. Normalises URLs (drops tracking parameters) and inserts only rows it has not seen before.
5. **Groups duplicate stories.** Each headline is turned into an embedding (384 numbers that capture
   its meaning) with the open-source `all-MiniLM-L6-v2` model, run locally through fastembed: no API, no cost.
   pgvector then finds the most similar earlier headline about the same company within 48 hours; if the
   similarity is at least 0.78, the new item joins that story. Nine outlets reporting the same Nvidia news
   become one story with nine sources.
6. Tags news headlines with event types (earnings, guidance, leadership, m-and-a, legal, analyst,
   restructuring, capital-return, product) using transparent keyword rules, matching the tags SEC filings get.
7. Deletes rows older than the retention window so the free database never fills up.
8. Writes a row to `ingest_runs` with counts and any errors, so failures are visible.

If one source or one company fails, the run logs it and carries on with the rest.

## Data sources

| Source | Cost | Key needed | What it gives |
| --- | --- | --- | --- |
| [SEC EDGAR](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | Free | No (name and email in User-Agent) | Official filings: earnings, leadership changes, deals, annual and quarterly reports |
| Google News RSS | Free | No | Headlines from many publishers, per company |
| Yahoo Finance RSS | Free | No | Company headlines |
| [Finnhub](https://finnhub.io) | Free tier | Yes, optional | Company news (free tier is for personal use) |

Only headlines, short summaries and links are stored. Full articles are never copied, so readers always go to the original publisher.

## Setup (about 15 minutes)

### 1. Create the database

1. Sign up at [supabase.com](https://supabase.com) and create a new project (free plan).
2. Click **Connect** at the top of the project and copy the **Session pooler** connection string.
   Use the pooler string, not the direct one: the direct connection is IPv6 only and GitHub's runners cannot reach it.
3. Replace `[YOUR-PASSWORD]` in the string with your database password.

You do not need to create any tables. The job applies `db/schema.sql` on every run.

### 2. Push the code to GitHub

```bash
git init
git add .
git commit -m "Ingestion pipeline: EDGAR, RSS and Finnhub into Postgres"
git branch -M main
git remote add origin https://github.com/<your-username>/market-news-hub.git
git push -u origin main
```

Make the repository **public** so GitHub Actions minutes are free.

### 3. Add the secrets

In the repository go to **Settings > Secrets and variables > Actions > New repository secret** and add:

| Name | Value |
| --- | --- |
| `DATABASE_URL` | the Session pooler string from step 1 |
| `SEC_USER_AGENT` | your name and email, e.g. `Jane Doe jane@example.com` |
| `FINNHUB_API_KEY` | optional, from finnhub.io |

### 4. Run it

Go to the **Actions** tab, pick **Ingest news** and click **Run workflow**.
After a minute, open the Supabase **Table Editor** and look at `news_items` and `ingest_runs`.
From then on it runs every 15 minutes by itself.

## Running locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then fill in your values

python -m newshub.ingest --dry-run           # fetch and print, no database needed
python -m newshub.ingest                     # fetch and store
python -m newshub.ingest --sources edgar -v  # one source, with logging
```

## Changing the watchlist

The default list is the Nasdaq-100. The index is reshuffled every December, so review the list once a year.
`news_batch_size` in the same file controls the news rotation (`0` = every company on every run), and
`python -m newshub.ingest --all-news` forces a full news refresh once.

Edit `config/watchlist.yaml`. Each company needs a ticker and a display name; everything else is optional:

```yaml
companies:
  - ticker: NVDA
    name: Nvidia
    news_query: "Nvidia OR NVDA"            # custom Google News search
    feeds: ["https://example.com/ir/rss"]   # extra feeds, e.g. investor relations
```

## Database

| Table | Contents |
| --- | --- |
| `companies` | ticker, display name, SEC CIK |
| `news_items` | one row per headline or filing per company: title, link, source, time (UTC), SEC form and items, tags |
| `ingest_runs` | one row per run: status (`ok`, `partial`, `failed`), counts per source, errors |
| `story_clusters` (view) | one row per story: headline, first and last seen, number of articles and sources |

`news_items` also holds each headline's `embedding` (pgvector, 384 dimensions) and its `cluster_id`
(the id of the first report of that story).

### Tuning the duplicate grouping

The similarity threshold is a judgement call: too low merges different stories, too high leaves duplicates apart.
Look at the groups, then try another value on the last few days without re-running the model:

```bash
python -m newshub.cluster --recluster-days 3 --threshold 0.82
```

and set the value you prefer as `cluster_threshold` in `config/watchlist.yaml`.

Row level security is switched on with no policies, which blocks Supabase's public REST API.
The job and the dashboard connect directly as the table owner and are not affected.

## Tests

```bash
pytest -q                                                      # parser and pipeline tests
TEST_DATABASE_URL=postgresql://... pytest -q                   # plus end-to-end tests on a real Postgres
```

The **Tests** workflow runs the full suite, including a Postgres 16 service with pgvector, on every push.

## Things to know about the free tiers

- GitHub may start scheduled runs a few minutes late when it is busy, so "every 15 minutes" is approximate.
- GitHub pauses scheduled workflows in public repos after 60 days with no commits. A small commit restarts them.
- Supabase pauses free projects after a week without activity. The 15-minute job keeps it active.
- The SEC allows up to 10 requests a second. The job stays well under that.

## Roadmap

- [x] Step 1: ingestion from EDGAR, RSS and Finnhub into Postgres on a schedule
- [x] Step 2: group duplicate stories across sources (sentence embeddings + pgvector) and tag event types
- [ ] Step 3: Streamlit dashboard with company pages, filters and a watchlist
- [ ] Step 4: short summaries, tone labels (FinBERT) and Telegram alerts for important filings
- [ ] Step 5: public launch

## License

MIT
