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
}

_COMPILED = {tag: re.compile(pattern, re.IGNORECASE) for tag, pattern in RULES.items()}


def tag_headline(title: str) -> list[str]:
    """Return sorted event tags for a headline, always including 'news'."""
    tags = {tag for tag, pattern in _COMPILED.items() if pattern.search(title)}
    tags.add("news")
    return sorted(tags)
