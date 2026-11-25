"""Group duplicate stories: many outlets reporting the same event become one story.

How it works, for items that have no embedding yet (oldest first):
  1. Embed their headlines in one batch (embeddings.py).
  2. Load, in ONE query, the already-grouped news for the same companies around
     the same time, with their embeddings.
  3. For each new item, find the most similar of those (cosine similarity, done
     with numpy in memory). If it is similar enough (>= `threshold`) and was
     published within `window_hours`, join its story; otherwise start a new one
     using the item's own id. The new item then becomes a candidate for the
     items after it.
  4. Save in chunks, committing as it goes, so a stopped run keeps its progress.

Doing the comparison in memory instead of one database query per headline
matters: the scheduled job runs in the US and the database is in Ireland, so
every round trip costs about 0.1 seconds.

SEC filings get embeddings too (useful for search later) but always stay in a
story of their own: a formal filing title rarely reads like a news headline.

Re-run with a different threshold to tune it (no model needed):
    python -m newshub.cluster --recluster-days 3 --threshold 0.8
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import defaultdict
from datetime import timedelta

import numpy as np
import psycopg

from . import db
from .config import load_settings
from .embeddings import Embedder, FastEmbedder, to_pgvector

log = logging.getLogger("newshub.cluster")

SAVE_CHUNK = 250  # rows written per commit

PENDING = """
select id, ticker, source, title, published_at
from news_items
where embedding is null
order by published_at asc, id asc
limit %s
"""

CANDIDATES = """
select id, ticker, cluster_id, published_at, embedding::text
from news_items
where ticker = any(%s)
  and source <> 'sec_edgar'
  and embedding is not null
  and cluster_id is not null
  and published_at between %s and %s
"""


def parse_vector(text: str) -> np.ndarray:
    """pgvector text '[0.1,0.2,...]' -> numpy array."""
    return np.array(text.strip("[]").split(","), dtype=np.float32)


class Pool:
    """Already-grouped news items, per company, that new items can join."""

    def __init__(self):
        self.items: dict[str, list[tuple]] = defaultdict(list)  # ticker -> [(published, cluster_id, vector)]

    def add(self, ticker, published, cluster_id, vector: np.ndarray) -> None:
        self.items[ticker].append((published, cluster_id, vector))

    def best_match(self, ticker, published, vector: np.ndarray, window: timedelta):
        best_cluster, best_sim = None, -1.0
        for other_published, cluster_id, other in self.items.get(ticker, ()):
            if abs(other_published - published) > window:
                continue
            sim = float(np.dot(vector, other))  # vectors have length 1, so dot = cosine similarity
            if sim > best_sim:
                best_cluster, best_sim = cluster_id, sim
        return best_cluster, best_sim


def _group(rows, vectors, pool: Pool, threshold: float, window: timedelta) -> list[tuple[int, int]]:
    """Decide a cluster for each row (rows oldest first). Returns [(item_id, cluster_id)]."""
    decisions = []
    for (item_id, ticker, source, published), vector in zip(rows, vectors):
        cluster_id = item_id
        if source != "sec_edgar":
            match, sim = pool.best_match(ticker, published, vector, window)
            if match is not None and sim >= threshold:
                cluster_id = match
            pool.add(ticker, published, cluster_id, vector)
        decisions.append((item_id, cluster_id))
    return decisions


def _load_pool(conn, rows, window: timedelta) -> Pool:
    pool = Pool()
    if not rows:
        return pool
    tickers = sorted({r[1] for r in rows})
    start = min(r[3] for r in rows) - window
    end = max(r[3] for r in rows) + window
    for _id, ticker, cluster_id, published, vec in conn.execute(CANDIDATES, (tickers, start, end)):
        pool.add(ticker, published, cluster_id, parse_vector(vec))
    return pool


def assign_clusters(
    conn: psycopg.Connection,
    embedder: Embedder,
    threshold: float = 0.78,
    window_hours: int = 48,
    limit: int = 3000,
) -> dict:
    """Embed and group items that have no embedding yet. Returns counts.

    `limit` caps one run; anything left over is picked up by the next run.
    """
    started = time.monotonic()
    pending = conn.execute(PENDING, (limit,)).fetchall()
    stats = {"embedded": 0, "joined_existing": 0, "new_clusters": 0, "seconds": 0.0}
    if not pending:
        return stats

    window = timedelta(hours=window_hours)
    vectors = [np.asarray(v, dtype=np.float32) for v in embedder.embed([r[3] for r in pending])]
    rows = [(item_id, ticker, source, published) for item_id, ticker, source, _title, published in pending]

    pool = _load_pool(conn, rows, window)
    decisions = _group(rows, vectors, pool, threshold, window)

    with conn.cursor() as cur:
        for start in range(0, len(decisions), SAVE_CHUNK):
            chunk = range(start, min(start + SAVE_CHUNK, len(decisions)))
            cur.executemany(
                "update news_items set embedding = %s::vector, cluster_id = %s where id = %s",
                [(to_pgvector(vectors[i].tolist()), decisions[i][1], decisions[i][0]) for i in chunk],
            )
            conn.commit()

    stats["embedded"] = len(decisions)
    stats["new_clusters"] = sum(1 for item_id, cid in decisions if item_id == cid)
    stats["joined_existing"] = stats["embedded"] - stats["new_clusters"]
    stats["seconds"] = round(time.monotonic() - started, 1)
    return stats


def recluster(conn: psycopg.Connection, days: int, threshold: float, window_hours: int) -> dict:
    """Redo the grouping for recent news, keeping the embeddings (no model needed)."""
    started = time.monotonic()
    found = conn.execute(
        """
        select id, ticker, source, published_at, embedding::text
        from news_items
        where embedding is not null and published_at > now() - make_interval(days => %s)
        order by published_at asc, id asc
        """,
        (days,),
    ).fetchall()
    rows = [(i, t, s, p) for i, t, s, p, _ in found]
    vectors = [parse_vector(v) for *_, v in found]

    # Items older than the reclustered period keep their groups and can still be joined.
    window = timedelta(hours=window_hours)
    pool = Pool()
    if rows:
        older = conn.execute(
            CANDIDATES + " and published_at <= now() - make_interval(days => %s)",
            (sorted({r[1] for r in rows}), rows[0][3] - window, rows[0][3], days),
        ).fetchall()
        for _id, ticker, cluster_id, published, vec in older:
            pool.add(ticker, published, cluster_id, parse_vector(vec))

    decisions = _group(rows, vectors, pool, threshold, window)
    with conn.cursor() as cur:
        for start in range(0, len(decisions), SAVE_CHUNK):
            cur.executemany(
                "update news_items set cluster_id = %s where id = %s",
                [(cid, item_id) for item_id, cid in decisions[start:start + SAVE_CHUNK]],
            )
        conn.commit()

    new = sum(1 for item_id, cid in decisions if item_id == cid)
    return {
        "items": len(decisions),
        "new_clusters": new,
        "joined_existing": len(decisions) - new,
        "seconds": round(time.monotonic() - started, 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Embed headlines and group duplicate stories.")
    parser.add_argument("--threshold", type=float, help="similarity needed to join a story (0 to 1)")
    parser.add_argument("--window-hours", type=int, help="how far apart in time duplicates can be")
    parser.add_argument("--recluster-days", type=int, help="redo grouping for the last N days with the current settings")
    parser.add_argument("--limit", type=int, default=20000, help="most headlines to embed in this run")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    settings = load_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set.")
    threshold = args.threshold if args.threshold is not None else settings.cluster_threshold
    window = args.window_hours or settings.cluster_window_hours

    conn = db.connect(settings.database_url)
    try:
        db.apply_schema(conn)
        if args.recluster_days:
            stats = recluster(conn, args.recluster_days, threshold, window)
        else:
            stats = assign_clusters(conn, FastEmbedder(), threshold, window, limit=args.limit)
    finally:
        conn.close()
    print(f"threshold {threshold}, window {window}h: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
