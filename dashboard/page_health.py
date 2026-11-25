import pandas as pd
import streamlit as st

import charts
import queries
import ui

ui.page_head(
    "Behind the scenes",
    "Pipeline health",
    "The collector runs on GitHub Actions every 15 minutes: it fetches SEC filings and news, "
    "saves what is new, groups duplicates, and logs every run here.",
)

try:
    runs = queries.runs(300)
except Exception as exc:  # noqa: BLE001
    ui.database_error(exc)
    st.stop()

if runs.empty:
    st.info("No runs logged yet.")
    st.stop()

last = runs.iloc[0]
now = pd.Timestamp.now(tz="UTC")
day = runs[runs["started_at"] > now - pd.Timedelta(hours=24)]
week = runs[runs["started_at"] > now - pd.Timedelta(days=7)]
healthy = (week["status"] != "failed").mean() if not week.empty else 0

ui.kpis(
    [
        (f"Last run, {ui.ago(last['finished_at'])}", ui.STATUS_LABELS.get(last["status"], last["status"])),
        ("Runs, last 24 h (max 96)", f"{len(day)}"),
        ("Runs without failure, 7 days", f"{healthy:.0%}"),
        ("Typical run time", f"{int(week['seconds'].median()) if not week.empty else 0} s"),
    ]
)

ui.section("New items saved per run")
st.plotly_chart(charts.run_series(runs.head(200)), width="stretch", config={"displayModeBar": False})

ui.section("Recent runs")
table = runs.head(50).assign(status=runs["status"].map(ui.STATUS_LABELS))
st.dataframe(
    table[["started_at", "status", "seconds", "new_items", "grouped", "news_batch", "problems"]],
    hide_index=True,
    width="stretch",
    column_config={
        "started_at": st.column_config.DatetimeColumn("Started (UTC)", format="D MMM, HH:mm"),
        "status": "Status",
        "seconds": st.column_config.NumberColumn("Seconds", format="%d"),
        "new_items": "New items",
        "grouped": "Grouped",
        "news_batch": "News batch",
        "problems": "Problems",
    },
)

errors = runs.head(50)
errors = errors[errors["problems"] > 0]
if not errors.empty:
    with st.expander(f"Problems in recent runs ({len(errors)} runs)"):
        for _, r in errors.iterrows():
            st.markdown(f"**{ui.utc(r['started_at'])}**")
            for e in r["errors"]:
                st.markdown(f"- `{e}`")
