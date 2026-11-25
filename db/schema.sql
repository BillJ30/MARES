-- Market News Hub schema. Safe to run repeatedly: every statement is idempotent.
-- The ingest job applies this file at the start of each run.

-- pgvector stores the headline embeddings used to group duplicate stories
-- (and, later, to search the news by meaning). Supabase keeps extensions in
-- their own schema; the app adds it to the search path when it connects.
create schema if not exists extensions;
create extension if not exists vector with schema extensions;

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

-- Step 2: one 384-number embedding per headline (all-MiniLM-L6-v2).
alter table news_items add column if not exists embedding vector(384);

create table if not exists ingest_runs (
    id           bigserial primary key,
    started_at   timestamptz not null,
    finished_at  timestamptz not null,
    status       text not null check (status in ('ok', 'partial', 'failed')),
    stats        jsonb not null default '{}'::jsonb,
    errors       jsonb not null default '[]'::jsonb
);

-- One row per story: duplicates of the same news grouped under cluster_id.
-- security_invoker keeps the view behind the same row level security as its table.
create or replace view story_clusters with (security_invoker = true) as
select
    cluster_id,
    ticker,
    min(published_at)                          as first_seen,
    max(published_at)                          as last_seen,
    count(*)                                   as articles,
    count(distinct source_name)                as sources,
    (array_agg(title order by published_at))[1] as headline,
    (array_agg(url   order by published_at))[1] as url,
    array_agg(distinct source_name)            as source_names
from news_items
where cluster_id is not null
group by cluster_id, ticker;

-- On Supabase, tables are also exposed through its public REST API.
-- Turning on row level security with no policies blocks that API completely,
-- while this job and the dashboard (which connect directly as the table owner)
-- keep working.
alter table companies   enable row level security;
alter table news_items  enable row level security;
alter table ingest_runs enable row level security;
