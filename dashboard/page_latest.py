import streamlit as st

import queries
import ui

try:
    o = queries.overview(24)
    company_list = queries.companies()
    tape = queries.most_covered(1, 20)
except Exception as exc:  # noqa: BLE001
    ui.database_error(exc)
    st.stop()

ui.masthead(o["last_run"])
ui.ticker_tape(tape)
ui.kpis(
    [
        ("Stories, last 24 h", f"{int(o['stories']):,}"),
        ("Articles, last 24 h", f"{int(o['articles']):,}"),
        ("SEC filings, last 24 h", f"{int(o['filings']):,}"),
        ("Companies in the news", f"{int(o['companies']):,}"),
    ]
)

WINDOWS = {"6 hours": 6, "24 hours": 24, "3 days": 72, "7 days": 168}
KINDS = {"News and filings": "all", "News only": "news", "SEC filings only": "filings"}
names = dict(zip(company_list["ticker"], company_list["name"]))

f1, f2, f3, f4, f5 = st.columns([1.35, 2, 1.8, 1.65, 1.8])
window = f1.selectbox("Time window", list(WINDOWS), index=1)
tickers = f2.multiselect("Companies", list(names), format_func=lambda t: f"{t} · {names[t]}", placeholder="All companies")
tags = f3.multiselect("Event types", queries.EVENT_TAGS, placeholder="All event types")
kind = f4.selectbox("Show", list(KINDS))
query = f5.text_input("Search headlines", placeholder="guidance, CEO, antitrust...")

try:
    df = queries.stories(WINDOWS[window], tuple(tickers), tuple(tags), KINDS[kind], query, limit=100)
    multi = tuple(int(s) for s in df.loc[df["articles"] > 1, "story_id"])
    articles = queries.story_articles(multi)
except Exception as exc:  # noqa: BLE001
    ui.database_error(exc)
    st.stop()

if df.empty:
    st.info("No stories match these filters. Try a longer time window or fewer filters.")
    st.stop()


def arts(story):
    return articles[articles["story_id"] == story["story_id"]]


# Front page: the most covered story leads, the next five sit beside it,
# and everything else follows newest first.
by_coverage = df.sort_values(["articles", "last_seen"], ascending=False)
lead = by_coverage.iloc[0]
runners = by_coverage.iloc[1:6]
used = {lead["story_id"], *runners["story_id"]}
rest = df[~df["story_id"].isin(used)]
just_in, rest = rest.head(2), rest.iloc[2:]

left, right = st.columns([2, 1], gap="large")
with left:
    ui.section("Lead story")
    ui.story_card(lead, arts(lead), variant="lead")
    if not just_in.empty:
        ui.section("Just in")
        cols = st.columns(2, gap="small")
        for i, (_, s) in enumerate(just_in.iterrows()):
            with cols[i]:
                ui.story_card(s, arts(s))
with right:
    ui.section("Most covered")
    for i, (_, s) in enumerate(runners.iterrows(), start=2):
        ui.story_card(s, arts(s), variant="compact", rank=i)

if not rest.empty:
    ui.section(f"Latest · {len(rest)} more stories")
    cols = st.columns(2, gap="medium")
    for i, (_, s) in enumerate(rest.iterrows()):
        with cols[i % 2]:
            ui.story_card(s, arts(s))

ui.footnote("Coverage counts are distinct publishers reporting the same event within 48 hours. Not financial advice.")
