"""Tag news headlines with event types using keyword rules.

Rules, not a model, on purpose: they are instant, free, and every tag can be
explained ("tagged 'analyst' because the headline says 'downgrade'"). The tag
names match the ones SEC filings get from their 8-K item codes, so a dashboard
filter like "leadership" shows both the filing and the news about it.
"""

from __future__ import annotations

import re

RULES: dict[str, str] = {
    "earnings": r"\b(earnings|quarterly results|q[1-4] (results|revenue|profit|sales)|revenue|eps|profit|"
                r"beats? (estimates|expectations)|miss(es)? (estimates|expectations)|tops? (estimates|forecasts))\b",
    "guidance": r"\b(guidance|outlook|forecasts?|raises? (its )?(full-year|annual)|cuts? (its )?(full-year|annual))\b",
    "m-and-a": r"\b(acquir\w*|acquisition|merger|merge|buyout|takeover|to buy|bid for|spin-?off|divest\w*)\b",
    "leadership": r"\b(ceo|cfo|coo|chief executive|chief financial|chairman|resign\w*|steps? down|"
                  r"appoint\w*|succession|successor)\b",
    "legal": r"\b(lawsuit|sues?|sued|antitrust|probe|investigation|charged|fined?|settle\w*|"
             r"regulators?|ftc|doj|european commission)\b",
    "analyst": r"\b(upgrades?|upgraded|downgrades?|downgraded|price target|overweight|underweight|"
               r"outperform|underperform|initiates? coverage)\b",
    "restructuring": r"\b(layoffs?|job cuts|cuts? [\d,]+ jobs|restructur\w*|lay off|laying off)\b",
    "capital-return": r"\b(dividend|buyback|share repurchase|repurchase program)\b",
    "product": r"\b(launch\w*|unveil\w*|rolls? out|new chip|new model)\b",
    # Auto-generated fund-holding reports ("X Acquires 69,083 Shares of Broadcom",
    # "NVDA Stock Purchased by Y Advisors"). Real but low value: the dashboard hides
    # them by default so they do not crowd out actual news.
    "holdings": r"\b(acquires|buys|sells|purchases|trims)\s+[\d,]+\s+shares\s+of\b"
                r"|\b(shares|stock|stake|position|holdings)\s+(bought|sold|purchased|acquired|trimmed|raised|lowered|increased|decreased|reduced|boosted|lifted|cut)\s+by\b"
                r"|\b(acquires|buys|sells|purchases|trims|boosts|lifts|reduces|increases|decreases|raises|lowers|grows|cuts)\s+(its\s+)?(stake|position|holdings)\s+in\b"
                r"|\bhas\s+\$[\d.,]+\s*(million|billion|thousand)?\s+(stake|position|holdings)\s+in\b"
                r"|\b(takes|initiates|opens|establishes)\s+(a\s+)?(new\s+)?(stake|position)\s+in\b"
                r"|\bmakes\s+new\s+\$?[\d.,]+\s*(million|billion|thousand)?\s+investment\s+in\b",
}

_COMPILED = {tag: re.compile(pattern, re.IGNORECASE) for tag, pattern in RULES.items()}


def tag_headline(title: str) -> list[str]:
    """Return sorted event tags for a headline, always including 'news'."""
    tags = {tag for tag, pattern in _COMPILED.items() if pattern.search(title)}
    tags.add("news")
    return sorted(tags)


def retag(days: int = 60) -> int:
    """Re-apply the current rules to news already saved (after the rules change)."""
    from . import db  # imported here: tagging itself needs no database
    from .config import load_settings

    settings = load_settings()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not set.")
    conn = db.connect(settings.database_url)
    try:
        rows = conn.execute(
            "select id, title, tags from news_items where source <> 'sec_edgar' "
            "and published_at > now() - make_interval(days => %s)",
            (days,),
        ).fetchall()
        changes = [(tag_headline(title), item_id) for item_id, title, tags in rows if tag_headline(title) != sorted(tags)]
        with conn.cursor() as cur:
            cur.executemany("update news_items set tags = %s where id = %s", changes)
        conn.commit()
    finally:
        conn.close()
    return len(changes)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Re-apply event tags to saved news headlines.")
    parser.add_argument("--retag-days", type=int, default=60, help="how many days of news to re-tag")
    args = parser.parse_args()
    print(f"Updated tags on {retag(args.retag_days)} headlines.")
