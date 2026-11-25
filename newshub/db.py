"""Postgres access. Works with Supabase, Neon or any Postgres 13+."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from .config import SCHEMA_PATH
from .models import Company, NewsItem

ITEM_COLUMNS = (
    "ticker, url_hash, source, source_name, title, url, summary, "
    "published_at, form_type, sec_items, tags, external_id"
)
ROW_PLACEHOLDER = "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
INSERT_CHUNK = 500  # rows per INSERT statement: one round trip instead of 500


def connect(database_url: str) -> psycopg.Connection:
    # prepare_threshold=None turns off server-side prepared statements, which
    # connection poolers such as Supabase's PgBouncer do not support.
    conn = psycopg.connect(database_url, connect_timeout=15, prepare_threshold=None)
    # Supabase installs extensions such as pgvector in the "extensions" schema.
    conn.execute("set search_path to public, extensions")
    conn.commit()
    return conn


def apply_schema(conn: psycopg.Connection, path: Path = SCHEMA_PATH) -> None:
    conn.execute(path.read_text(encoding="utf-8"))
    conn.commit()


def upsert_companies(conn: psycopg.Connection, companies: list[Company]) -> None:
    with conn.cursor() as cur:
        for c in companies:
            cur.execute(
                """
                insert into companies (ticker, name, cik) values (%s, %s, %s)
                on conflict (ticker) do update
                   set name = excluded.name,
                       cik = coalesce(excluded.cik, companies.cik),
                       updated_at = now()
                """,
                (c.ticker, c.name, c.cik),
            )
    conn.commit()


def known_ciks(conn: psycopg.Connection) -> dict[str, str]:
    rows = conn.execute("select ticker, cik from companies where cik is not null").fetchall()
    return {ticker: cik for ticker, cik in rows}


def save_ciks(conn: psycopg.Connection, ciks: dict[str, str]) -> None:
    with conn.cursor() as cur:
        for ticker, cik in ciks.items():
            cur.execute("update companies set cik = %s, updated_at = now() where ticker = %s", (cik, ticker))
    conn.commit()


def insert_items(conn: psycopg.Connection, items: list[NewsItem]) -> int:
    """Insert new items and return how many were actually new.

    Rows go in batches of INSERT_CHUNK per statement. Duplicates (already saved,
    or repeated within the batch) are skipped by the unique (ticker, url_hash) rule.
    """
    # The same article can appear twice in one fetch; Postgres refuses to touch
    # one row twice in a single INSERT ... ON CONFLICT, so drop repeats first.
    unique: dict[tuple[str, str], NewsItem] = {}
    for it in items:
        unique.setdefault((it.ticker, it.url_hash), it)
    rows = list(unique.values())

    inserted = 0
    with conn.cursor() as cur:
        for start in range(0, len(rows), INSERT_CHUNK):
            chunk = rows[start:start + INSERT_CHUNK]
            params: list = []
            for it in chunk:
                params += [
                    it.ticker, it.url_hash, it.source, it.source_name, it.title, it.url,
                    it.summary, it.published_at, it.form_type, it.sec_items, it.tags, it.external_id,
                ]
            cur.execute(
                f"insert into news_items ({ITEM_COLUMNS}) values "
                + ", ".join([ROW_PLACEHOLDER] * len(chunk))
                + " on conflict (ticker, url_hash) do nothing returning id",
                params,
            )
            inserted += len(cur.fetchall())
    conn.commit()
    return inserted


def prune(conn: psycopg.Connection, retention_days: int) -> int:
    cur = conn.execute(
        "delete from news_items where published_at < now() - make_interval(days => %s)",
        (retention_days,),
    )
    conn.commit()
    return cur.rowcount


def log_run(
    conn: psycopg.Connection,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    stats: dict,
    errors: list[str],
) -> None:
    conn.execute(
        "insert into ingest_runs (started_at, finished_at, status, stats, errors) values (%s, %s, %s, %s, %s)",
        (started_at, finished_at, status, Jsonb(stats), Jsonb(errors)),
    )
    conn.commit()
