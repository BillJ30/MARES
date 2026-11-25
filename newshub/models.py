from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .text import url_hash


@dataclass
class Company:
    ticker: str
    name: str
    cik: str | None = None
    news_query: str | None = None
    feeds: list[str] = field(default_factory=list)


@dataclass
class NewsItem:
    """One headline, filing or press release, already normalised for storage."""

    ticker: str
    source: str            # 'sec_edgar' | 'rss' | 'finnhub'
    source_name: str
    title: str
    url: str
    published_at: datetime  # always timezone aware, UTC
    summary: str | None = None
    form_type: str | None = None
    sec_items: list[str] | None = None
    tags: list[str] = field(default_factory=list)
    external_id: str | None = None

    @property
    def url_hash(self) -> str:
        return url_hash(self.url)
