"""Run one ingestion pass: fetch every source for every company, store what is new.

Usage:
    python -m newshub.ingest                 # normal run, writes to DATABASE_URL
    python -m newshub.ingest --dry-run       # fetch and print, no database needed
    python -m newshub.ingest --sources edgar --lookback-days 7
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import cluster, db
from .config import DEFAULT_WATCHLIST, Settings, load_settings
from .embeddings import Embedder, FastEmbedder
from .http import make_session
from .models import NewsItem
from .sources import edgar, finnhub, rss

ALL_SOURCES = ("edgar", "rss", "finnhub")
RUN_INTERVAL_SECONDS = 10 * 60  # matches the cron schedule in .github/workflows/ingest.yml
log = logging.getLogger("newshub")


def news_batch(companies: list, batch_size: int, now: datetime) -> tuple[list, int, int]:
    """Pick which companies get their news feeds checked on this run.

    SEC filings are checked for every company on every run. News feeds cost two
    requests per company, so with a large watchlist they rotate: the list is cut
    into batches of `batch_size` and each 15-minute slot takes the next batch.
    With 100 companies and batches of 25, every company's news is refreshed
    about once an hour. Returns (companies, batch number, number of batches).
    """
    if batch_size <= 0 or batch_size >= len(companies):
        return companies, 1, 1
    batches = math.ceil(len(companies) / batch_size)
    slot = int(now.timestamp() // RUN_INTERVAL_SECONDS)
    index = slot % batches
    return companies[index * batch_size:(index + 1) * batch_size], index + 1, batches


@dataclass
class Clients:
    edgar: object | None = None
    rss: object | None = None
    finnhub: object | None = None


def build_clients(settings: Settings, sources: tuple[str, ...]) -> Clients:
    clients = Clients()
    if "edgar" in sources and settings.sec_user_agent:
        clients.edgar = edgar.EdgarClient(make_session(settings.sec_user_agent))
    if "rss" in sources:
        clients.rss = rss.RssClient(make_session())
    if "finnhub" in sources and settings.finnhub_api_key:
        clients.finnhub = finnhub.FinnhubClient(make_session(), settings.finnhub_api_key)
    return clients


class Tracker:
    """Counts requests and items per source and keeps a list of readable errors."""

    def __init__(self, sources: tuple[str, ...]):
        self.stats = {s: {"requests_ok": 0, "requests_failed": 0, "items_fetched": 0, "items_new": 0} for s in sources}
        self.errors: list[str] = []
        self.items: dict[str, list[NewsItem]] = {s: [] for s in sources}

    def ok(self, source: str, items: list[NewsItem]) -> None:
        self.stats[source]["requests_ok"] += 1
        self.stats[source]["items_fetched"] += len(items)
        self.items[source].extend(items)

    def fail(self, source: str, where: str, exc: Exception) -> None:
        self.stats[source]["requests_failed"] += 1
        self.error(f"{source} {where}: {type(exc).__name__}: {exc}")

    def error(self, message: str) -> None:
        message = message[:300]
        log.warning(message)
        self.errors.append(message)

    @property
    def status(self) -> str:
        ok = sum(s["requests_ok"] for s in self.stats.values())
        failed = sum(s["requests_failed"] for s in self.stats.values())
        if ok == 0:
            return "failed"
        return "partial" if failed or self.errors else "ok"


def run(
    settings: Settings,
    sources: tuple[str, ...] = ALL_SOURCES,
    dry_run: bool = False,
    clients: Clients | None = None,
    now: datetime | None = None,
    embedder: Embedder | None = None,
    skip_clustering: bool = False,
) -> dict:
    started = now or datetime.now(timezone.utc)
    since = started - timedelta(days=settings.lookback_days)
    clients = clients or build_clients(settings, sources)
    track = Tracker(sources)
    companies = settings.companies

    conn = None
    if not dry_run:
        if not settings.database_url:
            raise SystemExit("DATABASE_URL is not set. Use --dry-run to test without a database.")
        conn = db.connect(settings.database_url)
        db.apply_schema(conn)
        db.upsert_companies(conn, companies)

    try:
        if "edgar" in sources:
            _run_edgar(settings, clients, track, conn, since)
        news_companies, batch_no, batches = news_batch(companies, settings.news_batch_size, started)
        if "rss" in sources:
            _run_rss(settings, news_companies, clients, track, since, started)
        if "finnhub" in sources:
            _run_finnhub(news_companies, clients, track, since, started)

        clustering: dict = {}
        if conn is not None:
            for source, items in track.items.items():
                track.stats[source]["items_new"] = db.insert_items(conn, items)
            pruned = db.prune(conn, settings.retention_days)
            if not skip_clustering:
                try:
                    clustering = cluster.assign_clusters(
                        conn,
                        embedder or FastEmbedder(),
                        settings.cluster_threshold,
                        settings.cluster_window_hours,
                    )
                except Exception as exc:  # noqa: BLE001 - news is saved even if grouping fails
                    conn.rollback()
                    track.error(f"clustering: {type(exc).__name__}: {exc}")
            finished = datetime.now(timezone.utc)
            extra = {"pruned": pruned, "news_batch": f"{batch_no}/{batches}", "clustering": clustering}
            db.log_run(conn, started, finished, track.status, {**track.stats, **extra}, track.errors)
    finally:
        if conn is not None:
            conn.close()

    return {
        "status": track.status,
        "stats": track.stats,
        "errors": track.errors,
        "items": track.items,
        "news_batch": (batch_no, batches, [c.ticker for c in news_companies]),
        "clustering": clustering,
    }


def _run_edgar(settings: Settings, clients: Clients, track: Tracker, conn, since: datetime) -> None:
    if clients.edgar is None:
        track.error("edgar skipped: SEC_USER_AGENT is not set (the SEC requires a name and email)")
        return

    ciks = db.known_ciks(conn) if conn is not None else {}
    ciks.update({c.ticker: c.cik for c in settings.companies if c.cik})

    missing = [c.ticker for c in settings.companies if c.ticker not in ciks]
    if missing:
        try:
            ticker_map = clients.edgar.ticker_map()
            found = {t: ticker_map[t] for t in missing if t in ticker_map}
            ciks.update(found)
            if conn is not None and found:
                db.save_ciks(conn, found)
            for t in missing:
                if t not in found:
                    track.error(f"edgar: no SEC company number found for {t}; add cik: to the watchlist")
        except Exception as exc:  # noqa: BLE001 - one failure should not stop the run
            track.fail("edgar", "ticker lookup", exc)

    for company in settings.companies:
        cik = ciks.get(company.ticker)
        if not cik:
            continue
        try:
            data = clients.edgar.submissions(cik)
            track.ok("edgar", edgar.parse_submissions(data, company, cik, settings.sec_forms, since))
        except Exception as exc:  # noqa: BLE001
            track.fail("edgar", company.ticker, exc)


def _run_rss(settings: Settings, companies: list, clients: Clients, track: Tracker, since: datetime, now: datetime) -> None:
    for company in companies:
        for feed_name, url in rss.build_feed_urls(company, settings.rss_templates, settings.lookback_days):
            try:
                content = clients.rss.fetch(url)
                track.ok("rss", rss.parse_feed(content, company, feed_name, since, now=now))
            except Exception as exc:  # noqa: BLE001
                track.fail("rss", f"{company.ticker} ({feed_name})", exc)


def _run_finnhub(companies: list, clients: Clients, track: Tracker, since: datetime, now: datetime) -> None:
    if clients.finnhub is None:
        log.info("finnhub skipped: FINNHUB_API_KEY is not set")
        return
    for company in companies:
        try:
            rows = clients.finnhub.company_news(company.ticker, since.date(), now.date())
            track.ok("finnhub", finnhub.parse_company_news(rows, company, since))
        except Exception as exc:  # noqa: BLE001
            track.fail("finnhub", company.ticker, exc)


def _print_summary(result: dict, dry_run: bool) -> None:
    print(f"\nStatus: {result['status']}")
    batch_no, batches, tickers = result["news_batch"]
    if batches > 1:
        print(f"News batch {batch_no} of {batches}: {', '.join(tickers)}")
    for source, s in result["stats"].items():
        new = "" if dry_run else f", {s['items_new']} new"
        print(
            f"  {source:8} {s['requests_ok']} ok / {s['requests_failed']} failed requests, "
            f"{s['items_fetched']} items{new}"
        )
    c = result.get("clustering") or {}
    if c.get("embedded"):
        print(
            f"  grouping {c['embedded']} embedded: {c['joined_existing']} joined an existing story, "
            f"{c['new_clusters']} new stories ({c['seconds']}s)"
        )
    if result["errors"]:
        print(f"\n{len(result['errors'])} problem(s):")
        for e in result["errors"][:20]:
            print(f"  - {e}")
    if dry_run:
        everything = [i for items in result["items"].values() for i in items]
        everything.sort(key=lambda i: i.published_at, reverse=True)
        print("\nNewest items:")
        for item in everything[:15]:
            print(f"  {item.published_at:%Y-%m-%d %H:%M} UTC  [{item.ticker}] {item.title}  ({item.source_name})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch filings and news for the watchlist.")
    parser.add_argument("--dry-run", action="store_true", help="fetch and print, do not touch the database")
    parser.add_argument("--sources", default=",".join(ALL_SOURCES), help="comma list: edgar,rss,finnhub")
    parser.add_argument("--lookback-days", type=int, help="override lookback_days from the watchlist")
    parser.add_argument("--all-news", action="store_true", help="check news for every company this run (no rotation)")
    parser.add_argument("--skip-clustering", action="store_true", help="save news but do not embed or group it")
    parser.add_argument("--config", default=str(DEFAULT_WATCHLIST), help="path to watchlist.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )
    sources = tuple(s.strip() for s in args.sources.split(",") if s.strip())
    unknown = set(sources) - set(ALL_SOURCES)
    if unknown:
        parser.error(f"unknown source(s): {', '.join(sorted(unknown))}")

    settings = load_settings(args.config)
    if args.lookback_days:
        settings.lookback_days = args.lookback_days
    if args.all_news:
        settings.news_batch_size = 0

    result = run(settings, sources=sources, dry_run=args.dry_run, skip_clustering=args.skip_clustering)
    _print_summary(result, args.dry_run)
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
