from datetime import date, datetime, timedelta, timezone

from conftest import NOW, fixture_bytes, fixture_json

from newshub.config import RssTemplate
from newshub.models import Company
from newshub.sources import edgar, finnhub, rss
from newshub.text import normalize_url, url_hash

SINCE = NOW - timedelta(days=3)


# ---------------------------------------------------------------- text

def test_normalize_url_drops_tracking_and_fragment():
    a = "https://WWW.Example.com/story/?utm_source=x&id=7#comments"
    b = "https://www.example.com/story?id=7"
    assert normalize_url(a) == b
    assert url_hash(a) == url_hash(b)


def test_normalize_url_keeps_real_parameters():
    assert url_hash("https://x.com/a?id=1") != url_hash("https://x.com/a?id=2")


# ---------------------------------------------------------------- edgar

def test_ticker_map_pads_cik():
    mapping = edgar.parse_ticker_map(fixture_json("company_tickers.json"))
    assert mapping["NVDA"] == "0001045810"
    assert mapping["AAPL"] == "0000320193"


def test_acceptance_time_is_read_as_us_eastern():
    # 16:21 Eastern in November (EST, UTC-5) is 21:21 UTC.
    got = edgar.parse_acceptance("2026-11-20T16:21:06.000Z", "2026-11-20")
    assert got == datetime(2026, 11, 20, 21, 21, 6, tzinfo=timezone.utc)
    # Summer time (EDT, UTC-4).
    got = edgar.parse_acceptance("2026-07-01T09:00:00.000Z", "2026-07-01")
    assert got == datetime(2026, 7, 1, 13, 0, tzinfo=timezone.utc)


def test_acceptance_falls_back_to_filing_date():
    got = edgar.parse_acceptance("", "2026-11-20")
    assert got == datetime(2026, 11, 20, 5, 0, tzinfo=timezone.utc)


def test_build_title_for_8k_skips_exhibits_item():
    assert edgar.build_title("Nvidia", "8-K", ["5.02", "9.01"]) == (
        "Nvidia 8-K: Departure or Appointment of Directors or Officers"
    )
    assert edgar.build_title("Nvidia", "8-K", ["9.01"]) == "Nvidia files 8-K"
    assert edgar.build_title("Nvidia", "10-Q", []) == "Nvidia files quarterly report (10-Q)"
    assert edgar.build_title("Nvidia", "10-K/A", []) == "Nvidia files amended annual report (10-K/A)"


def test_parse_submissions_filters_forms_and_dates(nvda):
    items = edgar.parse_submissions(
        fixture_json("sec_submissions_nvda.json"), nvda, "0001045810", ["8-K", "10-Q", "10-K"], SINCE
    )
    # Form 4 is not in the list and the October 8-K is outside the window.
    assert [i.form_type for i in items] == ["10-Q", "8-K", "8-K"]

    leadership = items[1]
    assert leadership.sec_items == ["5.02", "9.01"]
    assert "leadership" in leadership.tags and "sec-filing" in leadership.tags
    assert leadership.url == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000199/nvda-20261119.htm"
    )
    assert leadership.external_id == "0001045810-26-000199"

    earnings = items[2]
    assert earnings.tags == ["earnings", "sec-filing"]
    assert earnings.published_at.tzinfo is not None


# ---------------------------------------------------------------- rss

def test_build_feed_urls_encodes_query_and_adds_window():
    company = Company(ticker="NVDA", name="Nvidia", feeds=["https://ir.example.com/rss"])
    templates = [RssTemplate("Google News", "https://news.google.com/rss/search?q={query}&x={ticker}")]
    urls = rss.build_feed_urls(company, templates, lookback_days=3)
    assert urls[0] == ("Google News", "https://news.google.com/rss/search?q=Nvidia+NVDA+stock+when%3A3d&x=NVDA")
    assert urls[1] == ("Company feed", "https://ir.example.com/rss")


def test_parse_google_news_feed(nvda):
    items = rss.parse_feed(fixture_bytes("google_news_nvda.xml"), nvda, "Google News", SINCE, now=NOW)
    assert len(items) == 2  # the 9 November story is too old
    first = items[0]
    assert first.title == "Nvidia names new chief operating officer in leadership reshuffle"
    assert first.source_name == "Google News: Example Wire"
    assert first.summary is None  # description only repeated the headline
    assert first.published_at == datetime(2026, 11, 19, 22, 40, tzinfo=timezone.utc)


def test_parse_feed_rejects_garbage(nvda):
    try:
        rss.parse_feed(b"<html>not a feed", nvda, "X", SINCE, now=NOW)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


# ---------------------------------------------------------------- finnhub

def test_parse_finnhub(nvda):
    items = finnhub.parse_company_news(fixture_json("finnhub_nvda.json"), nvda, SINCE)
    assert len(items) == 1
    assert items[0].source_name == "Finnhub: Sample Markets"
    assert items[0].external_id == "9000001"


class _Resp:
    status_code = 200

    def json(self):
        return []


class _Session:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Resp()


def test_finnhub_key_goes_in_header_not_url():
    session = _Session()
    client = finnhub.FinnhubClient(session, api_key="SECRET123")
    client.limiter.min_interval = 0
    client.company_news("NVDA", date(2026, 11, 18), date(2026, 11, 21))
    url, kwargs = session.calls[0]
    assert "SECRET123" not in url
    assert "SECRET123" not in str(kwargs["params"])
    assert kwargs["headers"]["X-Finnhub-Token"] == "SECRET123"
