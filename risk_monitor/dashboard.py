from __future__ import annotations

import json
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from risk_monitor.store import connect, list_reports, get_report

st.set_page_config(page_title="Codebase risk monitor", layout="wide")
st.title("Codebase risk monitor")


@st.cache_data(ttl=30)
def load_reports(limit: int = 500):
    with connect() as conn:
        reports = list_reports(conn, limit=limit)
    rows = []
    for r in reports:
        rows.append({
            "sha": r.sha[:8],
            "full_sha": r.sha,
            "score": r.risk_score,
            "band": r.risk_band.value,
            "action": r.recommended_action,
            "summary": r.summary,
            "created_at": r.created_at,
            "skipped": r.skipped_deep_analysis,
        })
    return pd.DataFrame(rows)


df = load_reports()

if df.empty:
    st.info("No reports yet. Run `risk-monitor scan <repo>` first.")
    st.stop()

# --- top metrics
col1, col2, col3, col4 = st.columns(4)
col1.metric("Commits scored", len(df))
col2.metric("High/Critical", int(((df["band"] == "high") | (df["band"] == "critical")).sum()))
col3.metric("Avg score", f"{df['score'].mean():.1f}")
col4.metric("Skipped (triage)", int(df["skipped"].sum()))

# --- band filter
st.sidebar.header("Filters")
bands = st.sidebar.multiselect("Risk band", ["critical", "high", "medium", "low"], default=["critical", "high", "medium", "low"])
min_score = st.sidebar.slider("Min score", 0, 100, 0)
filtered = df[df["band"].isin(bands) & (df["score"] >= min_score)]

# --- distribution chart
st.subheader("Score distribution")
chart = (
    alt.Chart(filtered)
    .mark_bar()
    .encode(
        x=alt.X("score:Q", bin=alt.Bin(maxbins=20), title="Risk score"),
        y=alt.Y("count():Q", title="Commits"),
        color=alt.Color(
            "band:N",
            scale=alt.Scale(
                domain=["low", "medium", "high", "critical"],
                range=["#16a34a", "#eab308", "#ef4444", "#7f1d1d"],
            ),
        ),
    )
    .properties(height=240)
)
st.altair_chart(chart, use_container_width=True)

# --- ranked feed
st.subheader("Ranked commits")
view = filtered.sort_values("score", ascending=False)[
    ["sha", "score", "band", "action", "summary", "created_at"]
]
st.dataframe(view, use_container_width=True, hide_index=True)

# --- drill down
st.subheader("Drill down")
sha = st.text_input("Enter SHA (full or prefix) to inspect", "")
if sha:
    with connect() as conn:
        row = conn.execute(
            "SELECT sha FROM reports WHERE sha LIKE ? LIMIT 1", (sha + "%",)
        ).fetchone()
        if not row:
            st.warning("No matching commit.")
        else:
            report = get_report(conn, row["sha"])
    if sha and report:
        st.markdown(f"### {report.sha[:12]}")
        st.markdown(
            f"**Score:** `{report.risk_score}` &nbsp; **Band:** `{report.risk_band.value}` "
            f"&nbsp; **Action:** `{report.recommended_action}`"
        )
        st.markdown(f"**Summary:** {report.summary}")

        if report.skipped_deep_analysis:
            st.info(f"Deep analysis skipped: {report.skipped_reason}")
            st.json(report.triage_signals.model_dump())
        else:
            for d in report.dimensions:
                with st.expander(f"{d.dimension.value} — score {d.score}/10 (conf {d.confidence:.2f})"):
                    st.write(d.rationale)
                    if d.evidence:
                        st.markdown("**Evidence:**")
                        for e in d.evidence:
                            loc = f"{e.file}:{e.line}" if e.line else e.file
                            st.markdown(f"- `{loc}`" + (f" — {e.snippet}" if e.snippet else ""))

            st.markdown("**Triage signals**")
            st.json(report.triage_signals.model_dump())
