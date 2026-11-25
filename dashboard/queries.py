"""All database reads for the dashboard. Read-only: nothing here writes.

Every function returns a pandas DataFrame (or a dict for single rows) and is
cached for a couple of minutes, so many visitors do not mean many queries.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from newshub import db  # noqa: E402

CACHE_SECONDS = 120

# Event tags shown in filters and charts. The rarer ones are folded into "Other"
# in charts so the colour palette never needs more than eight slots.
EVENT_TAGS = [
    "earnings", "analyst", "m-and-a", "leadership", "legal", "guidance",
    "product", "restructuring", "capital-return", "deal", "debt", "governance",
    "accounting", "cybersecurity", "bankruptcy", "listing", "equity", "announcement",
    "periodic-report",
]


def database_url() -> str | None:
    """Streamlit Cloud secrets first, then the environment / local .env file."""
    try:
        for key in ("DASHBOARD_DATABASE_URL", "DATABASE_URL"):
            if key in st.secrets:
                return st.secrets[key]
    except Exception:  # noqa: BLE001 - no secrets file when running locally
        pass
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    return os.environ.get("DASHBOARD_DATABASE_URL") or os.environ.get("DATABASE_URL")


def _frame(sql: str, params: dict | tuple | None = None) -> pd.DataFrame:
    url = database_url()
    if not url:
        raise RuntimeError("No database configured: set DATABASE_URL in .env or the app's secrets.")
    conn = db.connect(url)
    try:
        cur = conn.execute(sql, params)
        cols = [c.name for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)
    finally:
        conn.close()


# ------------------------------------------------------------------ overview

@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def overview(hours: int = 24) -> dict:
    df = _frame(
        """
        select count(distinct coalesce(cluster_id, id)) filter (where source <> 'sec_edgar') as stories,
               count(*) filter (where source <> 'sec_edgar')                            as articles,
               count(*) filter (where source = 'sec_edgar')                             as filings,
               count(distinct ticker)                                                   as companies
        from news_items
        where published_at > now() - make_interval(hours => %s)
        """,
        (hours,),
    )
    last = _frame("select max(finished_at) as last_run, max(started_at) as last_start from ingest_runs")
    out = df.iloc[0].to_dict()
    out["last_run"] = last.iloc[0]["last_run"]
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def companies() -> pd.DataFrame:
    return _frame("select ticker, name from companies order by ticker")


# ------------------------------------------------------------------ stories

STORIES_SQL = """
with items as (
    select id, ticker, source, source_name, title, url, published_at, tags, form_type,
           coalesce(cluster_id, id) as story_id
    from news_items
    where published_at > now() - make_interval(hours => %(hours)s)
      and (%(tickers)s::text[] is null or ticker = any(%(tickers)s::text[]))
      and (%(kind)s = 'all'
           or (%(kind)s = 'news' and source <> 'sec_edgar')
           or (%(kind)s = 'filings' and source = 'sec_edgar'))
),
grouped as (
    select story_id, ticker,
           min(published_at)                              as first_seen,
           max(published_at)                              as last_seen,
           count(*)                                       as articles,
           count(distinct source_name)                    as sources,
           (array_agg(title order by published_at))[1]     as headline,
           (array_agg(url order by published_at))[1]       as url,
           (array_agg(form_type order by published_at))[1] as form_type,
           bool_or(source = 'sec_edgar')                  as is_filing
    from items
    group by story_id, ticker
),
tagged as (
    select story_id, ticker, array_agg(distinct t order by t) as tags
    from items cross join lateral unnest(tags) as t
    where t not in ('news', 'sec-filing')
    group by story_id, ticker
)
select g.*, coalesce(t.tags, '{}') as tags
from grouped g
left join tagged t using (story_id, ticker)
where (%(tags)s::text[] is null or t.tags && %(tags)s::text[])
  and (%(q)s = '' or g.headline ilike '%%' || %(q)s || '%%')
order by g.last_seen desc
limit %(limit)s
"""


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def stories(
    hours: int,
    tickers: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
    kind: str = "all",
    q: str = "",
    limit: int = 100,
) -> pd.DataFrame:
    return _frame(
        STORIES_SQL,
        {
            "hours": hours,
            "tickers": list(tickers) or None,
            "tags": list(tags) or None,
            "kind": kind,
            "q": q.strip(),
            "limit": limit,
        },
    )


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def story_articles(story_ids: tuple[int, ...]) -> pd.DataFrame:
    """Every article behind the given stories, in one query."""
    if not story_ids:
        return pd.DataFrame(columns=["story_id", "ticker", "title", "url", "source_name", "published_at"])
    return _frame(
        """
        select coalesce(cluster_id, id) as story_id, ticker, title, url, source_name, published_at
        from news_items
        where coalesce(cluster_id, id) = any(%s)
        order by published_at
        """,
        (list(story_ids),),
    )


# ------------------------------------------------------------------ company

@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def company_filings(ticker: str, days: int = 90) -> pd.DataFrame:
    return _frame(
        """
        select published_at, form_type, sec_items, title, url
        from news_items
        where ticker = %s and source = 'sec_edgar'
          and published_at > now() - make_interval(days => %s)
        order by published_at desc
        """,
        (ticker, days),
    )


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def company_daily_volume(ticker: str, days: int = 30) -> pd.DataFrame:
    df = _frame(
        """
        select date_trunc('day', published_at) as day,
               count(distinct coalesce(cluster_id, id)) as stories,
               count(*) as articles
        from news_items
        where ticker = %s and source <> 'sec_edgar'
          and published_at > now() - make_interval(days => %s)
        group by 1
        order by 1
        """,
        (ticker, days),
    )
    return _fill_days(df, days, ["stories", "articles"])


# ------------------------------------------------------------------ market pulse

@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def most_covered(days: int = 7, limit: int = 15) -> pd.DataFrame:
    return _frame(
        """
        select n.ticker, c.name,
               count(distinct coalesce(n.cluster_id, n.id)) as stories,
               count(*) as articles
        from news_items n
        join companies c using (ticker)
        where n.source <> 'sec_edgar'
          and n.published_at > now() - make_interval(days => %s)
        group by n.ticker, c.name
        order by stories desc, articles desc
        limit %s
        """,
        (days, limit),
    )


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def event_mix(days: int = 7) -> pd.DataFrame:
    """Stories per day per event tag (a story counts once per tag, on the day it first appeared)."""
    return _frame(
        """
        with stories as (
            select coalesce(cluster_id, id) as story_id,
                   min(published_at) as first_seen,
                   array_agg(distinct t) as tags
            from news_items cross join lateral unnest(tags) as t
            where source <> 'sec_edgar'
              and published_at > now() - make_interval(days => %s)
              and t not in ('news', 'sec-filing')
            group by 1
        )
        select date_trunc('day', first_seen) as day, tag, count(*) as stories
        from stories cross join lateral unnest(tags) as tag
        group by 1, 2
        order by 1, 2
        """,
        (days,),
    )


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def duplicate_rate(days: int = 7) -> dict:
    df = _frame(
        """
        select count(*) as articles, count(distinct cluster_id) as stories
        from news_items
        where source <> 'sec_edgar' and cluster_id is not null
          and published_at > now() - make_interval(days => %s)
        """,
        (days,),
    )
    row = df.iloc[0].to_dict()
    row["rate"] = (1 - row["stories"] / row["articles"]) if row["articles"] else 0.0
    return row


@st.cache_data(ttl=CACHE_SECONDS, show_spinner=False)
def recent_filings(days: int = 30, tag: str | None = None) -> pd.DataFrame:
    return _frame(
        """
        select n.published_at, n.ticker, c.name, n.form_type, n.title, n.url
        from news_items n
        join companies c using (ticker)
        where n.source = 'sec_edgar'
          and n.published_at > now() - make_interval(days => %s)
          and (%s::text is null or %s::text = any(n.tags))
        order by n.published_at desc
        """,
        (days, tag, tag),
    )


# ------------------------------------------------------------------ pipeline health

@st.cache_data(ttl=60, show_spinner=False)
def runs(limit: int = 300) -> pd.DataFrame:
    df = _frame(
        "select id, started_at, finished_at, status, stats, errors from ingest_runs order by started_at desc limit %s",
        (limit,),
    )
    if df.empty:
        return df

    def new_items(stats: dict) -> int:
        return sum(int(v.get("items_new", 0)) for v in stats.values() if isinstance(v, dict) and "items_new" in v)

    df["seconds"] = (df["finished_at"] - df["started_at"]).dt.total_seconds().round(0)
    df["new_items"] = df["stats"].apply(new_items)
    df["grouped"] = df["stats"].apply(lambda s: int((s.get("clustering") or {}).get("embedded", 0)))
    df["news_batch"] = df["stats"].apply(lambda s: s.get("news_batch", ""))
    df["problems"] = df["errors"].apply(len)
    return df


# ------------------------------------------------------------------ helpers

def _fill_days(df: pd.DataFrame, days: int, cols: list[str]) -> pd.DataFrame:
    """Add zero rows for days with no news, so charts show gaps honestly."""
    end = pd.Timestamp.now(tz="UTC").floor("D")
    index = pd.date_range(end - pd.Timedelta(days=days - 1), end, freq="D")
    if df.empty:
        return pd.DataFrame({"day": index, **{c: 0 for c in cols}})
    df = df.copy()
    df["day"] = pd.to_datetime(df["day"], utc=True)
    df = df.set_index("day").reindex(index, fill_value=0).rename_axis("day").reset_index()
    return df
