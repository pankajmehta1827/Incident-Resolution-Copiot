"""Incident cockpit (desktop), styled after the "Incident Copilot — Interactive Prototype" design.

Queue (sorted by SLA risk) | incident header with SLA box, Overview / Timeline / Related / Comms |
copilot panel. The pieces live in cockpit.py and are shared with the mobile layout.
"""
from __future__ import annotations

import streamlit as st

import cockpit
from copilot import audit
from ui_common import current_model, current_user, current_user_id, get_kb

kb = get_kb()
user_id, user, model = current_user_id(), current_user(), current_model()

if flash := st.session_state.pop("flash", None):
    st.toast(flash, icon=":material/task_alt:")

qv = cockpit.load_queue(kb, user, audit.entries())

queue_col, main_col = st.columns([300, 1140], gap="small")

# === Queue =========================================================================
with queue_col, st.container(key="queuepane", height=720, border=False):
    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown("**Queue**", width="content")
        st.space("stretch")
        st.caption(f"{len(qv.groups)} shown · sorted by SLA risk", width="content")
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        st.segmented_control("Queue filter", ["All", "P1 only", "Breach < 3h"], key="queue_filter",
                             label_visibility="collapsed")
        st.space("stretch")
        with st.popover("", icon=":material/tune:", help="Systems", type="tertiary"):
            st.multiselect("Systems", user["groups"], key="queue_systems")
        st.button("", icon=":material/keyboard_arrow_up:", on_click=cockpit.move, args=(qv.numbers, -1),
                  shortcut="K", type="tertiary", help="Previous (K)")
        st.button("", icon=":material/keyboard_arrow_down:", on_click=cockpit.move, args=(qv.numbers, 1),
                  shortcut="J", type="tertiary", help="Next (J)")
    with st.container(key="queue", border=False, gap="small"):
        cockpit.render_queue_cards(qv, kb)
    st.caption(f"SLA clock started at the workbook export ({qv.now:%d %b %H:%M} IST) and runs live · J / K to move")

c = cockpit.open_incident(kb, qv, user_id, model, spinner_slot=main_col)
if c is None:
    st.stop()

with main_col:
    # === Incident header ===============================================================
    with st.container(key="incheader", border=False):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            cockpit.render_header_badges(c)
            st.space("stretch")
            cockpit.render_header_actions(c)
        with st.container(horizontal=True, vertical_alignment="bottom", gap="medium"):
            with st.container():
                cockpit.render_title(c)
            cockpit.render_sla_box(c)

    work_col, copilot_col = st.columns([700, 440], gap="small")
    with work_col, st.container(height=520, border=False):
        cockpit.render_workspace(c)
    with copilot_col:
        with st.container(key="copilot", height=455, border=True):
            cockpit.render_copilot(c)
        cockpit.render_chat_input(c)
