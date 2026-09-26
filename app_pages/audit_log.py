"""Audit trail of every copilot action (FR-9) and usage metrics for team leads."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from copilot import audit, insights, ops

# Technical fields (which AI model ran, token counts, raw provider errors) stay in the
# log file for engineering but are not shown on screen.
_HIDDEN = {"model", "prompt_tokens", "completion_tokens", "error", "reason"}


def _public(d):
    if isinstance(d, dict):
        return {k: _public(v) for k, v in d.items() if k not in _HIDDEN}
    return d


st.title("Audit log")

entries = audit.entries()
ok, message = audit.verify()
if ok:
    st.success(f"Hash chain verified: {message} ({len(entries)} entries)", icon=":material/verified:")
else:
    st.error(f"Kill-switch condition: audit log altered or has gaps. {message}", icon=":material/dangerous:")

m = insights.usage_metrics(entries)
with st.container(horizontal=True):
    st.metric("Recommendations", m["recommendations"], border=True)
    st.metric("High confidence", m["high"], border=True)
    st.metric("Engineer decisions", m["decisions"], border=True)
    st.metric("Acceptance rate", f"{m['acceptance_rate']:.0%}" if m["acceptance_rate"] is not None else "–",
              help="Accepted or edited / all decisions. PRD target above 70%; kill switch below 30%.", border=True)
    st.metric("P95 latency", f"{m['p95_latency']:.1f}s" if m["p95_latency"] is not None else "–",
              help="PRD target under 8 s.", border=True)
    st.metric("Suspicious inputs", m["suspicious"], border=True)

if not entries:
    st.info("No copilot actions yet. Open an incident in the workbench.")
    st.stop()

df = pd.DataFrame(entries)
actions = sorted(df["action"].unique())
chosen = st.pills("Actions", actions, selection_mode="multi", default=actions)
view = df[df["action"].isin(chosen)].sort_values("seq", ascending=False).copy()
view["sources"] = view["sources"].apply(lambda s: ", ".join(s))
view["ts"] = view["ts"].apply(ops.fmt_ist)
view["details"] = view["details"].apply(lambda d: json.dumps(_public(d), ensure_ascii=False) if d else "")
st.dataframe(
    view[["seq", "ts", "user", "incident", "action", "outcome", "sources", "details", "hash"]],
    hide_index=True,
    column_config={"hash": st.column_config.TextColumn("hash", width="small")},
)
st.download_button("Export log (JSONL)", "\n".join(json.dumps(e, ensure_ascii=False) for e in entries),
                   file_name="copilot_audit_log.jsonl", icon=":material/download:")
