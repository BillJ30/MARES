import os
from datetime import datetime, timedelta, timezone

import pytest
from conftest import FakeEmbedder

from newshub import cluster, db
from newshub.models import Company, NewsItem

DB_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="set TEST_DATABASE_URL to run database tests")

T0 = datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc)


def item(ticker, title, hours=0.0, source="rss"):
    return NewsItem(
        ticker=ticker, source=source, source_name="Test", title=title,
        url=f"https://example.test/{ticker}/{abs(hash((title, hours)))}",
        published_at=T0 + timedelta(hours=hours),
    )


@pytest.fixture
def conn():
    c = db.connect(DB_URL)
    c.execute("drop table if exists news_items, ingest_runs, companies cascade")
    c.commit()
    db.apply_schema(c)
    db.upsert_companies(c, [Company("NVDA", "Nvidia"), Company("AAPL", "Apple")])
    yield c
    c.close()


def clusters(conn):
    rows = conn.execute("select title, cluster_id, id from news_items order by id").fetchall()
    return {title: cid for title, cid, _ in rows}


def test_duplicates_join_one_story_and_others_stay_apart(conn):
    a = "Nvidia beats earnings estimates as AI demand surges"
    b = "Nvidia beats earnings estimates on AI demand"          # same story, other outlet
    c = "Nvidia unveils new gaming chip"                         # different story
    d = "Nvidia beats earnings estimates as AI demand surges"    # same words, 5 days later
    e = "Apple beats earnings estimates as AI demand surges"     # other company
    f = "Nvidia 8-K: Results of Operations and Financial Condition"
    db.insert_items(conn, [
        item("NVDA", a, 0), item("NVDA", b, 2), item("NVDA", c, 3),
        item("NVDA", d, 120), item("AAPL", e, 1), item("NVDA", f, 0.5, source="sec_edgar"),
    ])
    stats = cluster.assign_clusters(conn, FakeEmbedder(), threshold=0.78, window_hours=48)
    assert stats["embedded"] == 6
    assert stats["joined_existing"] == 1

    rows = conn.execute("select title, published_at, cluster_id, id from news_items order by published_at").fetchall()
    by_time = {(t, p): (cid, i) for t, p, cid, i in rows}
    cid_a, id_a = by_time[(a, T0)]
    cid_b, _ = by_time[(b, T0 + timedelta(hours=2))]
    cid_d, id_d = by_time[(d, T0 + timedelta(hours=120))]
    assert cid_a == id_a            # first report starts the story
    assert cid_b == cid_a           # duplicate joins it
    assert cid_d == id_d            # outside the 48-hour window: a new story
    assert len({cid for cid, _ in by_time.values()}) == 5

    story = conn.execute(
        "select articles, sources, headline from story_clusters where cluster_id = %s", (cid_a,)
    ).fetchone()
    assert story == (2, 1, a)       # headline = earliest report


def test_nothing_to_do_does_not_load_the_model(conn):
    embedder = FakeEmbedder()
    assert cluster.assign_clusters(conn, embedder)["embedded"] == 0
    assert embedder.calls == 0


def test_recluster_with_stricter_threshold_splits_stories(conn):
    a = "Nvidia beats earnings estimates as AI demand surges"
    b = "Nvidia beats earnings estimates on AI demand"
    now = datetime.now(timezone.utc)
    db.insert_items(conn, [
        NewsItem("NVDA", "rss", "Test", a, "https://example.test/1", now - timedelta(hours=3)),
        NewsItem("NVDA", "rss", "Test", b, "https://example.test/2", now - timedelta(hours=2)),
    ])
    cluster.assign_clusters(conn, FakeEmbedder(), threshold=0.78)
    assert len(set(clusters(conn).values())) == 1

    stats = cluster.recluster(conn, days=1, threshold=0.9, window_hours=48)
    assert stats == {**stats, "items": 2, "new_clusters": 2, "joined_existing": 0}
    assert len(set(clusters(conn).values())) == 2
