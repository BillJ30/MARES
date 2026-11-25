-- Market News Hub schema. Safe to run repeatedly: every statement is idempotent.
-- The ingest job applies this file at the start of each run.

create table if not exists companies (
    ticker      text primary key,
    name        text not null,
    cik         text,                         -- SEC company number, 10 digits, zero padded
    updated_at  timestamptz not null default now()
);

create table if not exists news_items (
    id            bigserial primary key,
    ticker        text not null references companies (ticker) on delete cascade,
    url_hash      text not null,               -- sha256 of the normalised URL
    source        text not null check (source in ('sec_edgar', 'rss', 'finnhub')),
    source_name   text not null,               -- e.g. 'SEC EDGAR', 'Google News: Reuters'
    title         text not null,
    url           text not null,
    summary       text,
    published_at  timestamptz not null,
    fetched_at    timestamptz not null default now(),
    form_type     text,                        -- SEC form, e.g. '8-K' (EDGAR rows only)
    sec_items     text[],                      -- 8-K item codes, e.g. {'2.02','9.01'}
    tags          text[] not null default '{}',
    external_id   text,                        -- accession number, Finnhub id, etc.
    cluster_id    bigint,                      -- filled by the duplicate-grouping step (step 2)
    constraint news_items_ticker_url_unique unique (ticker, url_hash)
);

create index if not exists news_items_ticker_published_idx
    on news_items (ticker, published_at desc);
create index if not exists news_items_published_idx
    on news_items (published_at desc);

create table if not exists ingest_runs (
    id           bigserial primary key,
    started_at   timestamptz not null,
    finished_at  timestamptz not null,
    status       text not null check (status in ('ok', 'partial', 'failed')),
    stats        jsonb not null default '{}'::jsonb,
    errors       jsonb not null default '[]'::jsonb
);

-- On Supabase, tables are also exposed through its public REST API.
-- Turning on row level security with no policies blocks that API completely,
-- while this job and the dashboard (which connect directly as the table owner)
-- keep working.
alter table companies   enable row level security;
alter table news_items  enable row level security;
alter table ingest_runs enable row level security;
