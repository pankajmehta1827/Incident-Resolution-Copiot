"""Offline retrieval evals against the PRD launch gates."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from copilot import evals
from ui_common import get_kb

index = get_kb().index

st.title("Evals")
st.caption("Retrieval-level evals that run offline, on the labelled set in data/eval_set.json. "
           "Groundedness, hallucination rate and usefulness need SME review of live recommendations.")


@st.cache_data(show_spinner="Running evals...")
def _run() -> dict:
    return evals.run(index)


r = _run()


def _tile(label: str, value: float | None, target: float, help_text: str) -> None:
    passed = value is not None and value >= target
    st.metric(label, f"{value:.0%}" if value is not None else "–", border=True, help=help_text,
              delta=f"{'meets' if passed else 'below'} {target:.0%} gate", delta_color="normal" if passed else "inverse")


with st.container(horizontal=True):
    _tile("Repeat detection accuracy", r["detection_accuracy"], 0.90, "PRD: at least 90% on labelled incidents.")
    _tile("Top-3 relevance", r["top3_relevance"], 0.85, "PRD: at least 85% of repeats have the correct fix in the top 3.")
    _tile("No-match honesty", r["no_match_honesty"], 0.90, "PRD: at least 90% of new incidents get 'no known fix'.")
    st.metric("Access-control leaks", r["access_leaks"], border=True, help=f"Across {r['access_checks']} checks. PRD: zero.",
              delta="meets zero gate" if r["access_leaks"] == 0 else "kill switch", delta_color="off" if r["access_leaks"] == 0 else "inverse")

st.warning(f"The sample set has only {len(r['rows'])} cases. The PRD gates call for 300 labelled incidents, "
           "200 known repeats and 50 genuinely new incidents from your own data.", icon=":material/info:")
st.dataframe(pd.DataFrame(r["rows"]), hide_index=True)
