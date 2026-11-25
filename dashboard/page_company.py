import streamlit as st

import charts
import queries
import ui

try:
    company_list = queries.companies()
except Exception as exc:  # noqa: BLE001
    ui.database_error(exc)
    st.stop()

names = dict(zip(company_list["ticker"], company_list["name"]))
tickers = list(names)
default = tickers.index("NVDA") if "NVDA" in tickers else 0

top_l, top_r = st.columns([3, 1.3])
with top_r:
    ticker = st.selectbox("Company", tickers, index=default, format_func=lambda t: f"{t} · {names[t]}")
with top_l:
    ui.page_head("Company file", f"{names[ticker]} ({ticker})", "Every story and SEC filing for one company.")

try:
    volume = queries.company_daily_volume(ticker, 30)
    week = queries.stories(168, (ticker,), (), "news", "", limit=30)
    filings = queries.company_filings(ticker, 90)
    articles = queries.story_articles(tuple(int(s) for s in week.loc[week["articles"] > 1, "story_id"]))
except Exception as exc:  # noqa: BLE001
    ui.database_error(exc)
    st.stop()

ui.kpis(
    [
        ("Stories, last 7 days", f"{len(week):,}"),
        ("Articles, last 7 days", f"{int(week['articles'].sum()) if not week.empty else 0:,}"),
        ("SEC filings, last 90 days", f"{len(filings):,}"),
    ]
)

ui.section("Stories per day, last 30 days")
st.plotly_chart(charts.daily_bars(volume, "stories", "stories"), width="stretch", config={"displayModeBar": False})
ui.table_view(volume.assign(day=volume["day"].dt.strftime("%Y-%m-%d")))

left, right = st.columns([2, 1], gap="large")
with left:
    ui.section("Latest stories")
    if week.empty:
        st.info("No news stories in the last 7 days.")
    for _, story in week.head(15).iterrows():
        ui.story_card(story, articles[articles["story_id"] == story["story_id"]])
with right:
    ui.section("SEC filings")
    if filings.empty:
        st.info("No filings in the last 90 days.")
    for _, f in filings.iterrows():
        ui.filing_item(f)
