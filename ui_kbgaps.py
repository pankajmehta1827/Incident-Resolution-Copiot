"""Knowledge-gap agent UI: find runbook gaps, let the agent draft a fix, and let a knowledge
manager review, edit and publish it. Rendered on the Knowledge health page."""
from __future__ import annotations

import streamlit as st

from cockpit import _step_text
from copilot import kbagent
from ui_common import current_model, current_user, current_user_id, resolved_numbers

_KIND = {"missing": ("No runbook", "red"), "incomplete": ("Runbook incomplete", "orange")}


def _run_draft(kb, gap: kbagent.Gap) -> None:
    with st.status(f"Drafting a runbook update for {gap.error_code}…", expanded=True) as status:
        d = kbagent.draft(kb, gap, current_user_id(), resolved_numbers(), current_model(),
                          on_step=lambda s: status.write(_step_text(s)))
        status.update(label=f"Draft failed: {d.error}" if d.error else
                      f"Draft ready · {len(d.trace)} lookups · {d.seconds:.0f} s",
                      state="error" if d.error else "complete", expanded=bool(d.error))
    if not d.error:
        st.toast(f"Draft for {gap.error_code} is waiting for review.", icon=":material/rate_review:")


def _render_gaps(kb, pending_codes: set[str]) -> None:
    gaps = kbagent.find_gaps(kb)
    st.caption("Error codes whose runbook is missing, or whose runbook leaves out what engineers "
               "actually did to fix them (from resolution notes and closure notes).")
    if not gaps:
        st.success("No gaps found: every recurring error has a runbook that matches how it gets fixed.",
                   icon=":material/check_circle:")
        return
    for g in gaps:
        label, color = _KIND[g.kind]
        with st.container(border=True):
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                st.markdown(f"**{g.error_code}** · {g.system}", width="content")
                st.badge(label, color=color)
                st.badge(f"{g.occurrences}×", color="gray")
                st.space("stretch")
                if g.error_code in pending_codes:
                    st.badge("Draft awaiting review", color="blue", icon=":material/hourglass_top:")
                elif st.button("Draft runbook update", icon=":material/edit_note:", key=f"gap_{g.error_code}"):
                    _run_draft(kb, g)
                    st.rerun()
            st.caption(f"{g.description[:160]} · last seen {g.latest}")
            st.caption(f":material/info: {g.detail}")


def _render_draft(d: kbagent.Draft, approver: bool) -> None:
    label, color = _KIND.get(d.kind, ("", "gray"))
    with st.container(border=True):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            st.markdown(f"**{d.title or d.error_code}**", width="content")
            st.badge("New article" if d.change == "new" else "Update", color=color)
            st.space("stretch")
            st.caption(f"Drafted by {d.created_by} · {d.created_at}", width="content")
        if d.summary:
            st.markdown(d.summary)
        key = f"body_{d.id}"
        if approver:
            body = st.text_area("Article (edit before publishing)", d.body_md, height=320, key=key)
        else:
            st.markdown(d.body_md)
            body = d.body_md
        if d.sources:
            st.caption("Sources: " + ", ".join(f"`{s}`" for s in d.sources))
        if d.gaps:
            st.caption(f":material/help: The agent could not find: {d.gaps}")
        with st.expander(f"How the agent drafted it · {len(d.trace)} lookups · {d.seconds:.0f} s"):
            for i, step in enumerate(d.trace, 1):
                st.markdown(f"{i}. {_step_text(step)}")
            if d.dropped:
                st.caption(f"{len(d.dropped)} item(s) removed by the evidence check:")
                for x in d.dropped:
                    st.caption(f"✕ {x['claim'][:160]} ({x['source']})")
        if not approver:
            st.caption(":material/lock: Only a Knowledge Manager or Team Lead can publish or reject this draft.")
            return
        with st.container(horizontal=True, gap="small"):
            if st.button("Approve & publish", type="primary", icon=":material/publish:", key=f"ok_{d.id}"):
                doc_id = kbagent.approve(d, body, current_user_id())
                st.toast(f"Published {doc_id}. The copilot will cite it from now on.", icon=":material/check:")
                st.rerun()
            with st.popover("Reject", icon=":material/block:"):
                reason = st.text_input("Why?", key=f"why_{d.id}", placeholder="e.g. steps already covered elsewhere")
                if st.button("Reject draft", key=f"no_{d.id}", disabled=not reason.strip()):
                    kbagent.reject(d, reason, current_user_id())
                    st.rerun()


def render(kb) -> None:
    st.subheader("Knowledge-gap agent")
    drafts = kbagent.list_drafts()
    pending = [d for d in drafts if d.status == "pending" and not d.error]
    reviewed = [d for d in drafts if d.status != "pending"]
    approver = kbagent.can_approve(current_user_id())
    if not approver:
        st.caption(f"Signed in as {current_user()['role']}: you can request drafts; "
                   "a Knowledge Manager or Team Lead publishes them.")

    tab_gaps, tab_review, tab_done = st.tabs([
        "Gaps", f"Drafts to review ({len(pending)})", f"Reviewed ({len(reviewed)})"])
    with tab_gaps:
        _render_gaps(kb, {d.error_code for d in pending})
    with tab_review:
        if not pending:
            st.caption("No drafts waiting. Pick a gap and ask the agent to draft an update.")
        for d in pending:
            _render_draft(d, approver)
    with tab_done:
        if not reviewed:
            st.caption("Nothing reviewed yet.")
        for d in reviewed:
            icon = ":green[:material/check_circle:]" if d.status == "approved" else ":red[:material/cancel:]"
            line = f"{icon} **{d.error_code}** {d.status} by {d.reviewed_by} · {d.reviewed_at}"
            st.markdown(line + (f" — {d.reason}" if d.reason else ""))
