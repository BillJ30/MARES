import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from newshub.config import RssTemplate, Settings  # noqa: E402
from newshub.ingest import Clients  # noqa: E402
from newshub.models import Company  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str):
    return json.loads(fixture_bytes(name))


@pytest.fixture
def nvda() -> Company:
    return Company(ticker="NVDA", name="Nvidia")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        companies=[Company(ticker="NVDA", name="Nvidia")],
        rss_templates=[RssTemplate(name="Google News", url="https://news.google.com/rss/search?q={query}")],
        lookback_days=3,
        sec_user_agent="Test Runner test@example.com",
    )


class FakeEdgar:
    def __init__(self):
        self.ticker_map_calls = 0

    def ticker_map(self):
        self.ticker_map_calls += 1
        from newshub.sources.edgar import parse_ticker_map

        return parse_ticker_map(fixture_json("company_tickers.json"))

    def submissions(self, cik):
        assert cik == "0001045810"
        return fixture_json("sec_submissions_nvda.json")


class FakeRss:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.urls: list[str] = []

    def fetch(self, url):
        self.urls.append(url)
        if self.fail:
            raise ConnectionError("network down")
        return fixture_bytes("google_news_nvda.xml")


class FakeFinnhub:
    def company_news(self, ticker, start: date, end: date):
        return fixture_json("finnhub_nvda.json")


@pytest.fixture
def fake_clients() -> Clients:
    return Clients(edgar=FakeEdgar(), rss=FakeRss(), finnhub=FakeFinnhub())
