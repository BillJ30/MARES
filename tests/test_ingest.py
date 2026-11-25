import os

import pytest
from conftest import NOW, FakeEmbedder, FakeRss

from newshub import db
from newshub.config import load_settings
from newshub.ingest import RUN_INTERVAL_SECONDS, Clients, news_batch, run
from newshub.models import Company


def test_news_rotation_covers_every_company_once_per_cycle():
    companies = [Company(ticker=f"T{i}", name=f"Co {i}") for i in range(100)]
    seen = []
    for k in range(4):  # four consecutive 15-minute runs
        moment = NOW.fromtimestamp(NOW.timestamp() + k * RUN_INTERVAL_SECONDS, tz=NOW.tzinfo)
        batch, number, total = news_batch(companies, 25, moment)
        assert len(batch) == 25 and total == 4
        seen += [c.ticker for c in batch]
    assert sorted(seen) == sorted(c.ticker for c in companies)


def test_news_rotation_handles_uneven_lists_and_off_switch():
    companies = [Company(ticker=f"T{i}", name=f"Co {i}") for i in range(30)]
    sizes = set()
    for k in range(2):
        moment = NOW.fromtimestamp(NOW.timestamp() + k * RUN_INTERVAL_SECONDS, tz=NOW.tzinfo)
        sizes.add(len(news_batch(companies, 25, moment)[0]))
    assert sizes == {25, 5}
    assert len(news_batch(companies, 0, NOW)[0]) == 30


def test_real_watchlist_loads():
    settings = load_settings()
    tickers = [c.ticker for c in settings.companies]
    assert len(tickers) == 98
    assert "ON" in tickers and "TRUE" not in tickers
    assert settings.news_batch_size == 25
    assert "40-F" in settings.sec_forms


def test_unquoted_yaml_boolean_ticker_is_rejected(tmp_path):
    bad = tmp_path / "watchlist.yaml"
    bad.write_text("companies:\n  - { ticker: ON, name: ON Semiconductor }\n")
    with pytest.raises(ValueError, match="quotes"):
        load_settings(bad)


def test_dry_run_collects_every_source(settings, fake_clients):
    result = run(settings, dry_run=True, clients=fake_clients, now=NOW)
    assert result["status"] == "ok"
    stats = result["stats"]
    assert stats["edgar"]["items_fetched"] == 3      # 10-Q + two 8-Ks
    assert stats["rss"]["items_fetched"] == 2
    assert stats["finnhub"]["items_fetched"] == 1
    assert fake_clients.edgar.ticker_map_calls == 1  # CIK looked up once


def test_one_broken_source_gives_partial_not_failed(settings, fake_clients):
    fake_clients.rss = FakeRss(fail=True)
    result = run(settings, dry_run=True, clients=fake_clients, now=NOW)
    assert result["status"] == "partial"
    assert any("network down" in e for e in result["errors"])
    assert result["stats"]["edgar"]["items_fetched"] == 3


def test_everything_broken_is_failed(settings):
    clients = Clients(edgar=None, rss=FakeRss(fail=True), finnhub=None)
    result = run(settings, dry_run=True, clients=clients, now=NOW)
    assert result["status"] == "failed"
    assert any("SEC_USER_AGENT" in e for e in result["errors"])


# ---------------------------------------------------------------- real Postgres

DB_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not DB_URL, reason="set TEST_DATABASE_URL to run database tests")


@pytest.fixture
def clean_db():
    conn = db.connect(DB_URL)
    conn.execute("drop table if exists news_items, ingest_runs, companies cascade")
    conn.commit()
    yield conn
    conn.close()


@needs_db
def test_end_to_end_insert_and_dedupe(settings, fake_clients, clean_db):
    settings.database_url = DB_URL
    settings.retention_days = 3650  # keep fixture rows despite their fixed dates

    first = run(settings, clients=fake_clients, now=NOW, embedder=FakeEmbedder())
    assert first["status"] == "ok"
    assert first["stats"]["edgar"]["items_new"] == 3
    assert first["stats"]["rss"]["items_new"] == 2
    assert first["stats"]["finnhub"]["items_new"] == 1
    assert first["clustering"]["embedded"] == 6       # every new row gets an embedding

    # Same data again: nothing new, and the CIK now comes from the database.
    fake_clients.edgar.ticker_map_calls = 0
    second = run(settings, clients=fake_clients, now=NOW, embedder=FakeEmbedder())
    assert sum(s["items_new"] for s in second["stats"].values()) == 0
    assert second["clustering"]["embedded"] == 0      # nothing new to embed
    assert fake_clients.edgar.ticker_map_calls == 0

    conn = clean_db
    assert conn.execute("select count(*) from news_items").fetchone()[0] == 6
    assert conn.execute("select cik from companies where ticker = 'NVDA'").fetchone()[0] == "0001045810"
    runs = conn.execute("select status, stats->'edgar'->>'items_new' from ingest_runs order by id").fetchall()
    assert runs == [("ok", "3"), ("ok", "0")]
    row = conn.execute(
        "select sec_items, tags from news_items where external_id = '0001045810-26-000199'"
    ).fetchone()
    assert row == (["5.02", "9.01"], ["leadership", "sec-filing"])


@needs_db
def test_prune_removes_only_old_rows(settings, clean_db):
    from datetime import datetime, timedelta, timezone

    from newshub.models import NewsItem

    conn = clean_db
    db.apply_schema(conn)
    db.upsert_companies(conn, settings.companies)
    now = datetime.now(timezone.utc)

    def item(url, age_days):
        return NewsItem(ticker="NVDA", source="rss", source_name="Test", title=url, url=url,
                        published_at=now - timedelta(days=age_days))

    assert db.insert_items(conn, [item("https://a.test/old", 40), item("https://a.test/new", 2)]) == 2
    assert db.prune(conn, retention_days=30) == 1
    assert conn.execute("select url from news_items").fetchall() == [("https://a.test/new",)]


@needs_db
def test_bulk_insert_counts_only_new_rows_and_handles_repeats(settings, clean_db):
    from datetime import datetime, timedelta, timezone

    from newshub.models import NewsItem

    conn = clean_db
    db.apply_schema(conn)
    db.upsert_companies(conn, settings.companies)
    now = datetime.now(timezone.utc)
    items = [NewsItem("NVDA", "rss", "T", f"t{i}", f"https://b.test/{i}", now - timedelta(minutes=i)) for i in range(1200)]
    repeated = items + items[:10]                      # same article twice in one fetch
    assert db.insert_items(conn, repeated) == 1200     # 3 chunks of up to 500 rows
    assert db.insert_items(conn, items) == 0           # second time: nothing new
    assert conn.execute("select count(*) from news_items").fetchone()[0] == 1200
