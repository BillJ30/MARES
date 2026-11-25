"""SEC EDGAR filings (8-K current reports, 10-Q, 10-K and foreign-issuer forms).

EDGAR is free, needs no API key and is the primary source for material company
events. The SEC asks automated clients to send a User-Agent with a name and email
and to stay under 10 requests per second.
Docs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

import requests

from ..http import DEFAULT_TIMEOUT, RateLimiter
from ..models import Company, NewsItem

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{document}"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{acc}-index.htm"

EASTERN = ZoneInfo("America/New_York")
SOURCE_NAME = "SEC EDGAR"

# 8-K item codes and what they mean (SEC Form 8-K General Instructions).
ITEM_DESCRIPTIONS = {
    "1.01": "Entry into a Material Definitive Agreement",
    "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership",
    "1.04": "Mine Safety Reporting",
    "1.05": "Material Cybersecurity Incident",
    "2.01": "Completion of Acquisition or Disposition of Assets",
    "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation",
    "2.04": "Triggering Events That Accelerate a Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities",
    "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy a Listing Rule",
    "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders",
    "4.01": "Change in Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements",
    "5.01": "Change in Control of Registrant",
    "5.02": "Departure or Appointment of Directors or Officers",
    "5.03": "Amendments to Articles of Incorporation or Bylaws",
    "5.07": "Shareholder Vote Results",
    "5.08": "Shareholder Director Nominations",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}

ITEM_TAGS = {
    "1.01": "deal",
    "1.02": "deal",
    "1.03": "bankruptcy",
    "1.05": "cybersecurity",
    "2.01": "m-and-a",
    "2.02": "earnings",
    "2.03": "debt",
    "2.04": "debt",
    "2.05": "restructuring",
    "2.06": "restructuring",
    "3.01": "listing",
    "3.02": "equity",
    "4.01": "accounting",
    "4.02": "accounting",
    "5.01": "m-and-a",
    "5.02": "leadership",
    "5.03": "governance",
    "5.07": "governance",
    "5.08": "governance",
    "7.01": "announcement",
    "8.01": "announcement",
}

FORM_LABELS = {
    "10-Q": "quarterly report",
    "10-Q/A": "amended quarterly report",
    "10-K": "annual report",
    "10-K/A": "amended annual report",
    "6-K": "foreign issuer report",
    "20-F": "annual report",
}


# ---------------------------------------------------------------- fetching

class EdgarClient:
    def __init__(self, session: requests.Session):
        self.session = session
        self.limiter = RateLimiter(0.15)  # about 6-7 requests per second, under the SEC's 10/s

    def _get_json(self, url: str):
        self.limiter.wait()
        resp = self.session.get(url, timeout=DEFAULT_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def ticker_map(self) -> dict[str, str]:
        return parse_ticker_map(self._get_json(TICKERS_URL))

    def submissions(self, cik: str) -> dict:
        return self._get_json(SUBMISSIONS_URL.format(cik=cik))


# ---------------------------------------------------------------- parsing

def parse_ticker_map(data: dict) -> dict[str, str]:
    """company_tickers.json -> {'NVDA': '0001045810', ...}"""
    return {str(row["ticker"]).upper(): str(row["cik_str"]).zfill(10) for row in data.values()}


def parse_acceptance(value: str | None, filing_date: str | None) -> datetime:
    """Return the filing's acceptance time in UTC.

    The submissions API writes acceptanceDateTime with a trailing 'Z', but the
    clock time is US Eastern (filings accepted after 17:30 get the next day's
    filingDate, which only lines up if the value is Eastern). So the 'Z' is
    ignored and the time is read as America/New_York.
    """
    if value:
        try:
            naive = datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
            return naive.replace(tzinfo=EASTERN).astimezone(timezone.utc)
        except ValueError:
            pass
    if filing_date:
        day = datetime.strptime(filing_date, "%Y-%m-%d").date()
        return datetime.combine(day, time(0, 0), tzinfo=EASTERN).astimezone(timezone.utc)
    raise ValueError("filing has neither acceptanceDateTime nor filingDate")


def build_title(name: str, form: str, item_codes: list[str]) -> str:
    base_form = form.removesuffix("/A")
    amended = form.endswith("/A")
    if base_form == "8-K":
        meaningful = [ITEM_DESCRIPTIONS.get(c, f"Item {c}") for c in item_codes if c != "9.01"]
        label = "8-K (amended)" if amended else "8-K"
        if meaningful:
            return f"{name} {label}: {'; '.join(meaningful)}"
        return f"{name} files {label}"
    label = FORM_LABELS.get(form) or FORM_LABELS.get(base_form) or "filing"
    if amended and not label.startswith("amended"):
        label = f"amended {label}"
    return f"{name} files {label} ({form})"


def build_tags(form: str, item_codes: list[str]) -> list[str]:
    tags = {ITEM_TAGS[c] for c in item_codes if c in ITEM_TAGS}
    base_form = form.removesuffix("/A")
    if base_form in {"10-Q", "10-K", "20-F"}:
        tags.add("periodic-report")
    tags.add("sec-filing")
    return sorted(tags)


def parse_submissions(
    data: dict,
    company: Company,
    cik: str,
    forms: list[str],
    since: datetime,
) -> list[NewsItem]:
    recent = (data.get("filings") or {}).get("recent") or {}
    accessions = recent.get("accessionNumber") or []
    count = len(accessions)

    def column(name: str) -> list:
        values = recent.get(name) or []
        return values + [""] * (count - len(values))

    form_col = column("form")
    acceptance_col = column("acceptanceDateTime")
    filing_date_col = column("filingDate")
    items_col = column("items")
    doc_col = column("primaryDocument")
    desc_col = column("primaryDocDescription")

    wanted = {f.upper() for f in forms}
    cik_int = int(cik)
    out: list[NewsItem] = []

    for i, acc in enumerate(accessions):
        form = str(form_col[i]).upper()
        if form not in wanted:
            continue
        published = parse_acceptance(acceptance_col[i], filing_date_col[i] or None)
        if published < since:
            continue

        codes = [c.strip() for c in str(items_col[i] or "").split(",") if c.strip()]
        acc_nodash = acc.replace("-", "")
        document = doc_col[i]
        if document:
            url = ARCHIVE_URL.format(cik_int=cik_int, acc_nodash=acc_nodash, document=document)
        else:
            url = INDEX_URL.format(cik_int=cik_int, acc_nodash=acc_nodash, acc=acc)

        if codes:
            summary = "Items: " + "; ".join(f"{c} {ITEM_DESCRIPTIONS.get(c, '')}".strip() for c in codes)
        else:
            summary = desc_col[i] or None

        out.append(
            NewsItem(
                ticker=company.ticker,
                source="sec_edgar",
                source_name=SOURCE_NAME,
                title=build_title(company.name, form, codes),
                url=url,
                published_at=published,
                summary=summary,
                form_type=form,
                sec_items=codes or None,
                tags=build_tags(form, codes),
                external_id=acc,
            )
        )
    return out
