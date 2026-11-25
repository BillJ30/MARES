"""Chart helpers: one consistent, quiet style for every chart.

Colours follow a validated, colour-blind-safe palette in a fixed order. Each
event type always keeps the same colour, whatever the filters, so "earnings"
is blue on every chart and every day.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

# Dark theme. Charts sit on the page's dark cards; the palette's dark steps were
# validated against that surface (lightness, colour-blind separation, 3:1 contrast).
INK = "#efefef"
INK_SECONDARY = "#9c9c9c"
GRID = "#262626"
AXIS = "#474747"
PLANE = "#0b0b0b"  # page background: used for the 2px gaps between stacked segments
SERIES_1 = "#338ced"  # single-series charts

# Fixed colour per event type (categorical slots 1-8 of the palette, in order).
EVENT_COLORS = {
    "earnings": "#338ced",
    "analyst": "#e76624",
    "m-and-a": "#00a888",
    "leadership": "#d28e05",
    "legal": "#e24d8b",
    "guidance": "#0aac23",
    "product": "#9887ef",
    "Other": "#eb6263",
}
EVENT_ORDER = list(EVENT_COLORS)


def fold_event(tag: str) -> str:
    return tag if tag in EVENT_COLORS else "Other"


def _style(fig: go.Figure, height: int = 360, legend: bool = False) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=INK_SECONDARY, size=12, family="Inter, system-ui, sans-serif"),
        hoverlabel=dict(bgcolor="#1d1d1d", font=dict(color=INK), bordercolor="#338ced"),
        showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, title_text="", font=dict(color=INK_SECONDARY)),
        barcornerradius=4,
        bargap=0.25,
    )
    fig.update_xaxes(showgrid=False, linecolor=AXIS, tickcolor=AXIS, title_text="")
    fig.update_yaxes(gridcolor=GRID, zeroline=False, linecolor=AXIS, title_text="")
    return fig


def daily_bars(df: pd.DataFrame, value: str, label: str) -> go.Figure:
    """One series over days: a bar per day."""
    fig = go.Figure(
        go.Bar(
            x=df["day"],
            y=df[value],
            marker_color=SERIES_1,
            hovertemplate="%{x|%a %d %b}<br>" + label + ": %{y}<extra></extra>",
        )
    )
    fig.update_xaxes(tickformat="%d %b")
    # Counts are whole numbers: never show 0.2, 0.4... on the axis.
    fig.update_yaxes(tickformat=",d", rangemode="tozero")
    if df[value].max() <= 6:
        fig.update_yaxes(dtick=1)
    return _style(fig, height=260)


def ranked_bars(df: pd.DataFrame, label_col: str, value: str, label: str) -> go.Figure:
    """Horizontal bars, largest at the top."""
    df = df.sort_values(value, ascending=True)
    fig = go.Figure(
        go.Bar(
            x=df[value],
            y=df[label_col],
            orientation="h",
            marker_color=SERIES_1,
            text=df[value],
            textposition="outside",
            textfont=dict(color=INK_SECONDARY),
            cliponaxis=False,
            hovertemplate="%{y}<br>" + label + ": %{x}<extra></extra>",
        )
    )
    # Values are labelled on each bar, so no value axis or grid is needed.
    fig.update_xaxes(showgrid=False, showticklabels=False, showline=False, ticks="")
    fig.update_yaxes(showgrid=False, tickfont=dict(color=INK), ticks="", showline=False)
    return _style(fig, height=max(260, 26 * len(df) + 40))


def stacked_events(df: pd.DataFrame) -> go.Figure:
    """Stories per day, stacked by event type, colours fixed per type."""
    df = df.copy()
    df["event"] = df["tag"].map(fold_event)
    df = df.groupby(["day", "event"], as_index=False)["stories"].sum()
    fig = go.Figure()
    for event in EVENT_ORDER:
        part = df[df["event"] == event]
        if part.empty:
            continue
        fig.add_bar(
            x=part["day"],
            y=part["stories"],
            name=event,
            marker=dict(color=EVENT_COLORS[event], line=dict(color=PLANE, width=2)),
            hovertemplate="%{x|%a %d %b}<br>" + event + ": %{y} stories<extra></extra>",
        )
    # Legend reads in the same order as the stack, bottom segment first.
    fig.update_layout(barmode="stack", legend=dict(traceorder="normal"))
    fig.update_xaxes(tickformat="%d %b")
    fig.update_yaxes(tickformat=",d", rangemode="tozero")
    return _style(fig, height=340, legend=True)


def run_series(df: pd.DataFrame) -> go.Figure:
    """New items saved per pipeline run, over time."""
    df = df.sort_values("started_at")
    fig = go.Figure(
        go.Scatter(
            x=df["started_at"],
            y=df["new_items"],
            mode="lines+markers",
            line=dict(color=SERIES_1, width=2),
            marker=dict(size=8, color=SERIES_1, line=dict(color=PLANE, width=2)),
            hovertemplate="%{x|%d %b %H:%M} UTC<br>new items: %{y}<extra></extra>",
        )
    )
    fig.update_layout(hovermode="x")
    fig.update_yaxes(tickformat=",d", rangemode="tozero")
    return _style(fig, height=260)
