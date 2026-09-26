"""Weekly knowledge-health report (FR-11) and Problem-record suggestions (FR-13)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from copilot import audit, config, insights
from ui_common import get_kb

kb = get_kb()
index, docs, excluded = kb.index, kb.documents, kb.excluded
report = insights.knowledge_health(docs, index.history, audit.entries())

st.title("Knowledge health")
st.caption(f"For knowledge managers. Articles older than {config.FRESHNESS_WINDOW_DAYS} days are stale; "
           f"an article is contradicted when {config.CONTRADICTION_MIN}+ recent resolutions or copilot "
           "rejections say it is outdated.")

with st.container(horizontal=True):
    st.metric("Indexed articles", len(docs), border=True)
    st.metric("Stale", len(report["stale"]), border=True)
    st.metric("Contradicted", len(report["contradicted"]), border=True)
    st.metric("Error patterns with no runbook", len(report["missing"]), border=True)
    st.metric("Excluded sources", len(excluded), border=True)

st.subheader("Contradicted by recent resolutions")
if report["contradicted"]:
    st.dataframe(pd.DataFrame(report["contradicted"]), hide_index=True)
else:
    st.caption("None.")

st.subheader("Stale articles")
if report["stale"]:
    st.dataframe(pd.DataFrame(report["stale"]), hide_index=True)
else:
    st.caption("None.")

st.subheader("Recurring errors with no runbook entry")
if report["missing"]:
    st.dataframe(pd.DataFrame(report["missing"]), hide_index=True)
else:
    st.caption("Every recurring error code is covered by a runbook.")

st.subheader("Suggested Problem records")
st.caption(f"The same system and error code {config.PROBLEM_RECORD_MIN}+ times in {config.PROBLEM_RECORD_DAYS} days.")
candidates = insights.problem_candidates(kb.all_incidents)
if candidates:
    st.dataframe(pd.DataFrame(candidates), hide_index=True)
else:
    st.caption("No recurring pattern crosses the threshold.")

with st.expander("Sources excluded at ingestion"):
    st.dataframe(pd.DataFrame([{"Source": e.source, "Reason": e.reason} for e in excluded]), hide_index=True)

md = ["# Knowledge health report", f"Generated {config.TODAY}", ""]
for title, rows in [("Contradicted", report["contradicted"]), ("Stale", report["stale"]),
                    ("No runbook entry", report["missing"])]:
    md.append(f"## {title}")
    md += [("- " + " | ".join(f"{k}: {v}" for k, v in r.items())) for r in rows] or ["- None"]
    md.append("")
st.download_button("Download weekly report", "\n".join(md), file_name=f"knowledge_health_{config.TODAY}.md",
                   icon=":material/download:")
