"""Finnhub company news (optional, free key from https://finnhub.io).

The free tier allows 60 calls a minute and is meant for personal use, so this
source is a bonus on top of EDGAR and RSS, not the backbone.
The key is sent in a header, never in the URL, so it cannot leak into logs.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import requests

from ..http import DEFAULT_TIMEOUT, RateLimiter
from ..models import Company, NewsItem
from ..text import clean_text, truncate

COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"


class FinnhubClient:
    def __init__(self, session: requests.Session, api_key: str):
        self.session = session
        self.api_key = api_key
        self.limiter = RateLimiter(1.1)  # stays under 60 calls a minute

    def company_news(self, ticker: str, start: date, end: date) -> list[dict]:
        self.limiter.wait()
        try:
            resp = self.session.get(
                COMPANY_NEWS_URL,
                params={"symbol": ticker, "from": start.isoformat(), "to": end.isoformat()},
                headers={"X-Finnhub-Token": self.api_key},
                timeout=DEFAULT_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Finnhub request failed for {ticker}: {type(exc).__name__}") from None
        if resp.status_code != 200:
            raise RuntimeError(f"Finnhub returned HTTP {resp.status_code} for {ticker}")
        data = resp.json()
        if isinstance(data, dict):
            raise RuntimeError(f"Finnhub error for {ticker}: {data.get('error', 'unexpected response')}")
        return data


def parse_company_news(rows: list[dict], company: Company, since: datetime) -> list[NewsItem]:
    out: list[NewsItem] = []
    for row in rows:
        title = clean_text(row.get("headline"))
        url = (row.get("url") or "").strip()
        stamp = row.get("datetime")
        if not title or not url or not stamp:
            continue
        published = datetime.fromtimestamp(int(stamp), tz=timezone.utc)
        if published < since:
            continue
        publisher = clean_text(row.get("source"))
        summary = clean_text(row.get("summary"))
        out.append(
            NewsItem(
                ticker=company.ticker,
                source="finnhub",
                source_name=f"Finnhub: {publisher}" if publisher else "Finnhub",
                title=truncate(title, 300),
                url=url,
                published_at=published,
                summary=truncate(summary, 600) or None,
                tags=["news"],
                external_id=str(row["id"]) if row.get("id") is not None else None,
            )
        )
    return out
