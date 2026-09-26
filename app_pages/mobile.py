"""Incident cockpit (mobile): one column, thumb-friendly, answer first.

List view: queue cards sorted by SLA risk; tap one to open it.
Detail view: back to queue, incident header with live SLA timer, Acknowledge / Resolve,
the copilot (cause and steps first), then Overview / Timeline / Related / Comms and the chat.
Same data, actions and guardrails as desktop; the pieces come from cockpit.py.
"""
from __future__ import annotations

import streamlit as st

import cockpit
from copilot import audit
from ui_common import current_model, current_user, current_user_id, get_kb

kb = get_kb()
user_id, user, model = current_user_id(), current_user(), current_model()
st.session_state.setdefault("m_view", "list")

if flash := st.session_state.pop("flash", None):
    st.toast(flash, icon=":material/task_alt:")

qv = cockpit.load_queue(kb, user, audit.entries())


def _back() -> None:
    st.session_state.m_view = "list"


# === List view =====================================================================
if st.session_state.m_view != "detail" or not st.session_state.get("selected_incident"):
    st.session_state.m_view = "list"
    p1 = sum(i.get("priority", "").startswith("P1") for i in qv.tickets)
    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown(f"**Queue · {len(qv.groups)}**", width="content")
        st.space("stretch")
        st.badge(f"P1 · {p1}", color="red")
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        st.segmented_control("Queue filter", ["All", "P1 only", "Breach < 3h"], key="queue_filter",
                             label_visibility="collapsed")
        st.space("stretch")
        with st.popover("", icon=":material/tune:", help="Systems", type="tertiary"):
            st.multiselect("Systems", user["groups"], key="queue_systems")
    with st.container(key="queue", border=False, gap="small"):
        cockpit.render_queue_cards(qv, kb, detail_on_tap=True)
    st.caption(f"SLA clock from the workbook export ({qv.now:%d %b %H:%M} IST), running live")
    st.stop()

# === Detail view ===================================================================
st.button("Queue", icon=":material/arrow_back:", type="tertiary", on_click=_back, key="m_back")
c = cockpit.open_incident(kb, qv, user_id, model)
if c is None:
    st.session_state.m_view = "list"
    st.rerun()

with st.container(key="mheader", border=False, gap="small"):
    with st.container(horizontal=True, gap="xsmall"):
        cockpit.render_header_badges(c)
    cockpit.render_title(c)
    cockpit.render_sla_box(c, stretch=True)
    with st.container(horizontal=True, gap="small"):
        cockpit.render_header_actions(c, stretch=True)

with st.container(key="copilot", border=True):
    cockpit.render_copilot(c, compact=True)
cockpit.render_chat_input(c)

st.divider()
cockpit.render_workspace(c, compact=True)
