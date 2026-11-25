from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .models import Company

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WATCHLIST = ROOT / "config" / "watchlist.yaml"
SCHEMA_PATH = ROOT / "db" / "schema.sql"


@dataclass
class RssTemplate:
    name: str
    url: str


@dataclass
class Settings:
    companies: list[Company]
    rss_templates: list[RssTemplate]
    lookback_days: int = 3
    retention_days: int = 90
    news_batch_size: int = 0  # companies per run for news feeds; 0 = all every run
    sec_forms: list[str] = field(default_factory=lambda: ["8-K", "8-K/A", "10-Q", "10-K", "6-K", "20-F"])
    database_url: str | None = None
    sec_user_agent: str | None = None
    finnhub_api_key: str | None = None


def load_settings(path: Path | str = DEFAULT_WATCHLIST) -> Settings:
    load_dotenv(ROOT / ".env")
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    opts = raw.get("settings") or {}

    companies: list[Company] = []
    seen: set[str] = set()
    for entry in raw.get("companies") or []:
        raw_ticker = entry["ticker"]
        if not isinstance(raw_ticker, str):
            # YAML reads bare ON / OFF / YES / NO as true/false, and some tickers as numbers.
            raise ValueError(
                f"Ticker {raw_ticker!r} (company {entry.get('name')!r}) was not read as text. "
                f"Put it in quotes in the watchlist, e.g. ticker: \"ON\""
            )
        ticker = raw_ticker.strip().upper()
        if ticker in seen:
            raise ValueError(f"{ticker} appears twice in the watchlist")
        seen.add(ticker)
        cik = entry.get("cik")
        companies.append(
            Company(
                ticker=ticker,
                name=str(entry.get("name") or ticker).strip(),
                cik=str(cik).zfill(10) if cik else None,
                news_query=entry.get("news_query"),
                feeds=list(entry.get("feeds") or []),
            )
        )
    if not companies:
        raise ValueError("The watchlist has no companies")

    templates = [RssTemplate(name=t["name"], url=t["url"]) for t in raw.get("rss_templates") or []]

    settings = Settings(
        companies=companies,
        rss_templates=templates,
        lookback_days=int(opts.get("lookback_days", 3)),
        retention_days=int(opts.get("retention_days", 90)),
        news_batch_size=int(opts.get("news_batch_size", 0)),
        database_url=_env("DATABASE_URL"),
        sec_user_agent=_env("SEC_USER_AGENT"),
        finnhub_api_key=_env("FINNHUB_API_KEY"),
    )
    if opts.get("sec_forms"):
        settings.sec_forms = [str(f).upper() for f in opts["sec_forms"]]
    return settings


def _env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None
