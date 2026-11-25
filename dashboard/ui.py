"""Display helpers shared by the pages: theme, masthead, story cards, KPI cards."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

STATUS_LABELS = {
    "ok": "✅ OK",
    "partial": "⚠️ Partial",
    "failed": "❌ Failed",
}

CSS = (Path(__file__).parent / "style.css").read_text(encoding="utf-8")


def inject_theme() -> None:
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


def esc(value) -> str:
    return html.escape(str(value), quote=True)


# ------------------------------------------------------------ time

def ago(when) -> str:
    """'12 min ago', '3 h ago', '2 days ago'."""
    if when is None or pd.isna(when):
        return "never"
    when = pd.Timestamp(when)
    if when.tzinfo is None:
        when = when.tz_localize("UTC")
    seconds = (datetime.now(timezone.utc) - when.to_pydatetime()).total_seconds()
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    days = int(seconds // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"


def utc(when) -> str:
    return pd.Timestamp(when).strftime("%d %b %Y, %H:%M UTC")


# ------------------------------------------------------------ page furniture

def masthead(updated) -> None:
    today = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    st.markdown(
        f"""
        <div class="masthead">
          <div class="kicker">Nasdaq-100 · SEC filings · Market news</div>
          <div class="title">MARES</div>
          <div class="dateline">
            <span>{today}</span>
            <span>98 companies · every story once, every source kept</span>
            <span class="live">Updated {esc(ago(updated))}</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def page_head(kicker: str, title: str, sub: str = "") -> None:
    st.markdown(
        f"""<div class="page-head"><div class="kicker">{esc(kicker)}</div>
        <div class="title">{esc(title)}</div><div class="sub">{esc(sub)}</div></div>""",
        unsafe_allow_html=True,
    )


def ticker_tape(df: pd.DataFrame) -> None:
    """Scrolling strip of the most covered companies today. Pauses on hover."""
    if df.empty:
        return
    items = "".join(
        f'<span class="tape-item"><b>{esc(r.ticker)}</b>{esc(r["name"])} · '
        f'<span class="n">{int(r.stories)} stor{"y" if int(r.stories) == 1 else "ies"}</span></span>'
        for _, r in df.iterrows()
    )
    # The list is repeated once so the loop scrolls without a gap.
    st.markdown(f'<div class="tape"><div class="tape-track">{items}{items}</div></div>', unsafe_allow_html=True)


def kpis(values: list[tuple[str, str]]) -> None:
    cls = "kpis three" if len(values) == 3 else "kpis"
    cards = "".join(f'<div class="kpi"><div class="v">{esc(v)}</div><div class="l">{esc(l)}</div></div>' for l, v in values)
    st.markdown(f'<div class="{cls}">{cards}</div>', unsafe_allow_html=True)


def section(label: str) -> None:
    st.markdown(f'<div class="section-label">{esc(label)}</div>', unsafe_allow_html=True)


def footnote(text: str) -> None:
    st.markdown(f'<div class="footnote">{esc(text)}</div>', unsafe_allow_html=True)


# ------------------------------------------------------------ stories

def _chips(story: pd.Series) -> str:
    out = [f'<span class="chip ticker">{esc(story["ticker"])}</span>']
    if story["is_filing"]:
        out.append(f'<span class="chip filing">SEC {esc(story["form_type"])}</span>')
    out += [f'<span class="chip">{esc(t)}</span>' for t in story["tags"]]
    return '<div class="chips">' + "".join(out) + "</div>"


def _meta(story: pd.Series) -> str:
    parts = [f"First seen <b>{esc(ago(story['first_seen']))}</b>"]
    if not story["is_filing"]:
        n, s = int(story["articles"]), int(story["sources"])
        parts.append(f"<b>{n}</b> article{'s' if n != 1 else ''} · <b>{s}</b> source{'s' if s != 1 else ''}")
    else:
        parts.append("SEC EDGAR")
    return '<div class="meta">' + " · ".join(parts) + "</div>"


def story_card(story: pd.Series, articles: pd.DataFrame, variant: str = "", rank: int | None = None) -> None:
    """One story. variant: '' (normal), 'lead' (front-page lead) or 'compact'."""
    rank_html = f'<span class="rank">{rank}</span>' if rank else ""
    sources_html = ""
    if variant == "lead" and not articles.empty:
        names = sorted({n.split(": ", 1)[-1] for n in articles["source_name"]})
        sources_html = f'<div class="sources">Reported by {esc(", ".join(names[:8]))}{" and others" if len(names) > 8 else ""}</div>'
    st.markdown(
        f"""<div class="story {variant}">{rank_html}{_chips(story) if variant != 'compact' else ''}
        <a class="headline" href="{esc(story['url'])}" target="_blank" rel="noopener">{esc(story['headline'])}</a>
        {sources_html}{_meta(story)}</div>""",
        unsafe_allow_html=True,
    )
    if variant != "compact" and not story["is_filing"] and int(story["articles"]) > 1 and not articles.empty:
        with st.expander(f"All {int(story['articles'])} articles"):
            for _, a in articles.iterrows():
                src = a["source_name"].split(": ", 1)[-1]
                st.markdown(f"- [{a['title']}]({a['url']}) · {src} · {ago(a['published_at'])}")


def filing_item(f: pd.Series) -> None:
    what = f["title"].split(": ", 1)[-1] if ": " in f["title"] else f["title"]
    st.markdown(
        f"""<div class="filing-item"><div class="what"><span class="chip filing">{esc(f['form_type'])}</span> {esc(what)}</div>
        <div class="when">Filed {esc(utc(f['published_at']))} · <a href="{esc(f['url'])}" target="_blank" rel="noopener">Open the filing</a></div></div>""",
        unsafe_allow_html=True,
    )


def table_view(df: pd.DataFrame, label: str = "View the data as a table") -> None:
    """Every chart also has a table view, for screen readers and exact numbers."""
    with st.expander(label):
        st.dataframe(df, hide_index=True, width="stretch")


def database_error(exc: Exception) -> None:
    st.error(
        "Could not reach the database. If you are running this locally, check DATABASE_URL in your .env file. "
        f"Details: {type(exc).__name__}"
    )
