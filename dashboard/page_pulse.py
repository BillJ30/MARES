import streamlit as st

import charts
import queries
import ui

ui.page_head("Analysis", "Market pulse", "What the Nasdaq-100 news flow looks like: who is in the news, and what kind of news it is.")

DAYS = {"Last 7 days": 7, "Last 14 days": 14, "Last 30 days": 30}
days = DAYS[st.segmented_control("Period", list(DAYS), default="Last 7 days") or "Last 7 days"]

try:
    dup = queries.duplicate_rate(days)
    top = queries.most_covered(days, 15)
    mix = queries.event_mix(days)
except Exception as exc:  # noqa: BLE001
    ui.database_error(exc)
    st.stop()

ui.kpis(
    [
        ("Articles collected", f"{int(dup['articles']):,}"),
        ("Distinct stories", f"{int(dup['stories']):,}"),
        ("Duplicate coverage, grouped away", f"{dup['rate']:.0%}"),
    ]
)

left, right = st.columns(2, gap="large")
with left:
    ui.section("Most covered companies")
    st.caption("Distinct stories per company. Duplicates count once.")
    if top.empty:
        st.info("No news in this period yet.")
    else:
        labels = top.assign(label=top["ticker"] + "  " + top["name"])
        st.plotly_chart(charts.ranked_bars(labels, "label", "stories", "stories"), width="stretch", config={"displayModeBar": False})
        ui.table_view(top)
with right:
    ui.section("What kind of news")
    st.caption("Stories per day by event type, from headline keywords. A story can have more than one type.")
    if mix.empty:
        st.info("No tagged stories in this period yet.")
    else:
        st.plotly_chart(charts.stacked_events(mix), width="stretch", config={"displayModeBar": False})
        ui.table_view(mix.assign(day=mix["day"].dt.strftime("%Y-%m-%d")))

ui.section("Recent SEC filings")
FILING_TYPES = {
    "Leadership changes (8-K item 5.02)": "leadership",
    "Earnings releases (8-K item 2.02)": "earnings",
    "Deals and M&A": "m-and-a",
    "All filings": None,
}
choice = st.selectbox("Filing type", list(FILING_TYPES))
try:
    filings = queries.recent_filings(30, FILING_TYPES[choice])
except Exception as exc:  # noqa: BLE001
    ui.database_error(exc)
    st.stop()
if filings.empty:
    st.info("None in the last 30 days.")
else:
    st.dataframe(
        filings[["published_at", "ticker", "name", "form_type", "title", "url"]],
        hide_index=True,
        width="stretch",
        column_config={
            "published_at": st.column_config.DatetimeColumn("Filed (UTC)", format="D MMM YYYY, HH:mm"),
            "ticker": "Ticker",
            "name": "Company",
            "form_type": "Form",
            "title": "What it is",
            "url": st.column_config.LinkColumn("Document", display_text="Open"),
        },
    )
