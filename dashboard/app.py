"""MARES dashboard.

Run locally:   streamlit run dashboard/app.py
"""

import streamlit as st

import queries
import ui

st.set_page_config(page_title="MARES · Nasdaq-100 news and SEC filings", page_icon=":material/newspaper:", layout="wide")
ui.inject_theme()

pages = st.navigation(
    [
        st.Page("page_latest.py", title="Front page", icon=":material/newspaper:", default=True),
        st.Page("page_company.py", title="Companies", icon=":material/domain:"),
        st.Page("page_pulse.py", title="Market pulse", icon=":material/insights:"),
        st.Page("page_health.py", title="Pipeline health", icon=":material/monitor_heart:"),
    ]
)

with st.sidebar:
    st.markdown('<div class="side-brand">MARES</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="side-note">SEC filings and news for 98 Nasdaq-100 companies, collected every '
        "10 minutes. Articles about the same event are grouped, so each story appears once with "
        "every source kept.</div>",
        unsafe_allow_html=True,
    )
    try:
        last = queries.overview(24)["last_run"]
        st.markdown(f'<div class="side-note"><span class="live">Updated {ui.esc(ui.ago(last))}</span></div>', unsafe_allow_html=True)
    except Exception:  # noqa: BLE001 - the sidebar should never break a page
        pass
    st.markdown(
        '<div class="side-note"><a href="https://github.com/BillJ30/MARES" target="_blank">Source code on GitHub</a><br>'
        "Not financial advice. Headlines link to their original publishers; filings come from SEC EDGAR.</div>",
        unsafe_allow_html=True,
    )

pages.run()
