"""Smoke test: every dashboard page renders against a real database without errors."""

import os
from pathlib import Path

import pytest
from conftest import NOW, FakeEmbedder

from newshub import db
from newshub.ingest import run

DB_URL = os.environ.get("TEST_DATABASE_URL")
streamlit_testing = pytest.importorskip("streamlit.testing.v1", reason="dashboard requirements not installed")
pytestmark = pytest.mark.skipif(not DB_URL, reason="set TEST_DATABASE_URL to run database tests")

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard"
PAGES = ["app.py", "page_latest.py", "page_company.py", "page_pulse.py", "page_health.py"]


@pytest.fixture(scope="module", autouse=True)
def filled_database():
    conn = db.connect(DB_URL)
    conn.execute("drop table if exists news_items, ingest_runs, companies cascade")
    conn.commit()
    conn.close()
    from conftest import FakeEdgar, FakeFinnhub, FakeRss  # noqa: PLC0415
    from newshub.config import RssTemplate, Settings  # noqa: PLC0415
    from newshub.ingest import Clients  # noqa: PLC0415
    from newshub.models import Company  # noqa: PLC0415

    settings = Settings(
        companies=[Company("NVDA", "Nvidia")],
        rss_templates=[RssTemplate("Google News", "https://news.google.com/rss/search?q={query}")],
        sec_user_agent="Test test@example.com",
        database_url=DB_URL,
        retention_days=3650,
    )
    run(settings, clients=Clients(FakeEdgar(), FakeRss(), FakeFinnhub()), now=NOW, embedder=FakeEmbedder())
    os.environ["DATABASE_URL"] = DB_URL
    yield


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_errors(page):
    at = streamlit_testing.AppTest.from_file(str(DASHBOARD / page), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert not at.error, [e.value for e in at.error]
