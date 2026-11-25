"""Group duplicate stories: many outlets reporting the same event become one story.

How it works, for every item that has no embedding yet (oldest first):
  1. Embed its headline (embeddings.py).
  2. Ask Postgres (pgvector) for the most similar earlier news item about the
     same company, published within `window_hours` of it.
  3. If that item is similar enough (cosine similarity >= `threshold`), join
     its cluster. Otherwise start a new cluster, using the item's own id.

SEC filings get embeddings too (useful for search later) but always stay in a
cluster of their own: a formal filing title rarely reads like a news headline.

Re-run with a different threshold to tune it:
    python -m newshub.cluster --recluster-days 3 --threshold 0.8
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import psycopg

from . import db
from .config import load_settings
from .embeddings import Embedder, FastEmbedder, to_pgvector

log = logging.getLogger("newshub.cluster")

PENDING = """
select id, ticker, source, title, published_at
from news_items
where embedding is null
order by published_at asc, id asc
limit %s
"""

NEAREST = """
select cluster_id, 1 - (embedding <=> %s::vector) as similarity
from news_items
where ticker = %s
  and id <> %s
  and source <> 'sec_edgar'
  and embedding is not null
  and cluster_id is not null
  and published_at between %s - make_interval(hours => %s) and %s + make_interval(hours => %s)
order by embedding <=> %s::vector
limit 1
"""


def assign_clusters(
    conn: psycopg.Connection,
    embedder: Embedder,
    threshold: float = 0.78,
    window_hours: int = 48,
    limit: int = 5000,
) -> dict:
    """Embed and cluster items that have no embedding yet. Returns counts."""
    started = time.monotonic()
    rows = conn.execute(PENDING, (limit,)).fetchall()
    stats = {"embedded": 0, "joined_existing": 0, "new_clusters": 0, "seconds": 0.0}
    if not rows:
        return stats

    vectors = embedder.embed([title for _, _, _, title, _ in rows])
    with conn.cursor() as cur:
        for (item_id, ticker, source, _title, published), vector in zip(rows, vectors):
            vec = to_pgvector(vector)
            cluster_id = item_id
            if source != "sec_edgar":
                cur.execute(NEAREST, (vec, ticker, item_id, published, window_hours, published, window_hours, vec))
                best = cur.fetchone()
                if best is not None and best[1] >= threshold:
                    cluster_id = best[0]
            cur.execute(
                "update news_items set embedding = %s::vector, cluster_id = %s where id = %s",
                (vec, cluster_id, item_id),
            )
            stats["embedded"] += 1
            if cluster_id == item_id:
                stats["new_clusters"] += 1
            else:
                stats["joined_existing"] += 1
    conn.commit()
    stats["seconds"] = round(time.monotonic() - started, 1)
    return stats


def recluster(conn: psycopg.Connection, days: int, threshold: float, window_hours: int) -> dict:
    """Redo the grouping for recent news, keeping the embeddings (no model needed)."""
    started = time.monotonic()
    rows = conn.execute(
        """
        select id, ticker, source, published_at, embedding::text
        from news_items
        where embedding is not null and published_at > now() - make_interval(days => %s)
        order by published_at asc, id asc
        """,
        (days,),
    ).fetchall()
    ids = [r[0] for r in rows]
    stats = {"items": len(rows), "joined_existing": 0, "new_clusters": 0}
    with conn.cursor() as cur:
        cur.execute("update news_items set cluster_id = null where id = any(%s)", (ids,))
        for item_id, ticker, source, published, vec in rows:
            cluster_id = item_id
            if source != "sec_edgar":
                cur.execute(NEAREST, (vec, ticker, item_id, published, window_hours, published, window_hours, vec))
                best = cur.fetchone()
                if best is not None and best[1] >= threshold:
                    cluster_id = best[0]
            cur.execute("update news_items set cluster_id = %s where id = %s", (cluster_id, item_id))
            stats["new_clusters" if cluster_id == item_id else "joined_existing"] += 1
    conn.commit()
    stats["seconds"] = round(time.monotonic() - started, 1)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Embed headlines and group duplicate stories.")
    parser.add_argument("--threshold", type=float, help="similarity needed to join a story (0 to 1)")
    parser.add_argument("--window-hours", type=int, help="how far apart in time duplicates can be")
    parser.add_argument("--recluster-days", type=int, help="redo grouping for the last N days with the current settings")
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
            stats = assign_clusters(conn, FastEmbedder(), threshold, window)
    finally:
        conn.close()
    print(f"threshold {threshold}, window {window}h: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
