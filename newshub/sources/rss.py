"""RSS and Atom news feeds (Google News search, Yahoo Finance, company IR feeds).

Only headlines, short summaries and links are stored. Full article text is never
copied, so the dashboard always sends readers to the original publisher.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import feedparser
import requests

from ..config import RssTemplate
from ..http import DEFAULT_TIMEOUT, RateLimiter
from ..models import Company, NewsItem
from ..text import clean_text, strip_html, truncate

SUMMARY_LIMIT = 600


class RssClient:
    def __init__(self, session: requests.Session):
        self.session = session
        self.limiter = RateLimiter(0.5)

    def fetch(self, url: str) -> bytes:
        self.limiter.wait()
        resp = self.session.get(url, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        return resp.content


def default_query(company: Company) -> str:
    return company.news_query or f"{company.name} {company.ticker} stock"


def build_feed_urls(company: Company, templates: list[RssTemplate], lookback_days: int) -> list[tuple[str, str]]:
    """Return (feed name, url) pairs for one company."""
    query = default_query(company)
    if "when:" not in query:
        query = f"{query} when:{max(1, lookback_days)}d"
    urls = [
        (t.name, t.url.format(query=quote_plus(query), ticker=quote_plus(company.ticker)))
        for t in templates
    ]
    urls += [("Company feed", feed) for feed in company.feeds]
    return urls


def _entry_time(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=timezone.utc)


def parse_feed(
    content: bytes,
    company: Company,
    feed_name: str,
    since: datetime,
    now: datetime | None = None,
) -> list[NewsItem]:
    now = now or datetime.now(timezone.utc)
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"could not parse feed: {parsed.get('bozo_exception')}")

    out: list[NewsItem] = []
    for entry in parsed.entries:
        title = clean_text(entry.get("title"))
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue

        publisher = clean_text((entry.get("source") or {}).get("title"))
        # Google News appends " - Publisher" to every headline.
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(f" - {publisher}")].rstrip()

        published = _entry_time(entry) or now
        # Skip old items, and items dated more than a day ahead (broken feed clocks).
        if published < since or published > now + timedelta(days=1):
            continue

        summary = strip_html(entry.get("summary"))
        # Google News descriptions only repeat the headline and publisher.
        if summary and (summary.startswith(title[:40]) or len(summary) < 25):
            summary = ""

        out.append(
            NewsItem(
                ticker=company.ticker,
                source="rss",
                source_name=f"{feed_name}: {publisher}" if publisher else feed_name,
                title=truncate(title, 300),
                url=link,
                published_at=published,
                summary=truncate(summary, SUMMARY_LIMIT) or None,
                tags=["news"],
                external_id=(entry.get("id") or None),
            )
        )
    return out
