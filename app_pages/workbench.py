"""Incident cockpit, styled after the "Incident Copilot — Interactive Prototype" design.

Queue (sorted by SLA risk) | incident header with SLA box, Overview / Timeline / Related / Comms |
copilot panel (confidence + why, summary, likely cause + evidence, steps with risk and approval,
actions, sources + feedback, chat with quick questions). Everything is driven by the real
workbook, runbooks and audit log; nothing here executes on a production system.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from copilot import audit, config, engine, ops
from copilot.llm import LLMUnavailable
import ui_cards
from ui_common import (current_model, current_user, current_user_id, data_signature, first_sentence, full_queue,
                       get_kb, resolved_numbers)

ASK_SOURCE = "Agent search"
SEV_COLOR = {"P1": "red", "P2": "orange", "P3": "gray", "P4": "gray"}
RISK_COLOR = {"low": "green", "medium": "orange", "high": "red"}
SHORT = {config.MF: "Mainframe", config.AS400: "AS400", config.JAVA: "Java"}
QUICK_QUESTIONS = {
    "Root cause?": "What is the most likely root cause of this incident, and what evidence supports it?",
    "Rollback plan": "If the recommended fix makes things worse, how is each change step rolled back?",
    "Business impact": "What is the business impact of this incident: which business area and component are affected?",
    "Seen before?": "Has this happened before? How often, and what fixed it?",
}

kb = get_kb()
index = kb.index
user_id, user, model = current_user_id(), current_user(), current_model()
log = audit.entries()
now = ops.snapshot_now(kb.all_incidents)

if flash := st.session_state.pop("flash", None):
    st.toast(flash, icon=":material/task_alt:")

# --- Queue data (resolved incidents are never shown) ---------------------------------
resolved = resolved_numbers(log)
visible = [i for i in full_queue(log) if i["application"] in user["groups"]]
search = st.session_state.get("search", "").strip().lower()
if search:
    visible = [i for i in visible if search in " ".join(
        str(i.get(f, "")) for f in ("number", "error_code", "component", "business_area",
                                    "short_description", "application")).lower()]
asks = [i for i in visible if i.get("source") == ASK_SOURCE]
tickets = [i for i in visible if i.get("source") != ASK_SOURCE]

st.session_state.setdefault("queue_filter", "All")
st.session_state.setdefault("queue_systems", list(user["groups"]))
systems = [s for s in st.session_state.queue_systems if s in user["groups"]] or list(user["groups"])
tickets = [i for i in tickets if i["application"] in systems]
qf = st.session_state.queue_filter
if qf == "P1 only":
    tickets = [i for i in tickets if i.get("priority", "").startswith("P1")]
elif qf == "Breach < 3h":
    tickets = [i for i in tickets if (s := ops.sla_for(i, now)).remaining is not None
               and s.remaining.total_seconds() < 3 * 3600]
groups = ops.build_queue(tickets, now, log)
numbers = [i["number"] for i in asks] + [g.parent["number"] for g in groups]

if "pending_selection" in st.session_state:
    st.session_state.selected_incident = st.session_state.pop("pending_selection")
if st.session_state.get("selected_incident") not in [i["number"] for i in visible]:
    st.session_state.selected_incident = numbers[0] if numbers else None


def _select(number: str) -> None:
    st.session_state.selected_incident = number


def _move(step: int) -> None:
    cur = st.session_state.get("selected_incident")
    if cur in numbers:
        st.session_state.selected_incident = numbers[max(0, min(len(numbers) - 1, numbers.index(cur) + step))]


queue_col, main_col = st.columns([300, 1140], gap="small")

# === Queue =========================================================================
with queue_col, st.container(key="queuepane", height=720, border=False):
    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown("**Queue**", width="content")
        st.space("stretch")
        st.caption(f"{len(groups)} shown · sorted by SLA risk", width="content")
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        st.segmented_control("Queue filter", ["All", "P1 only", "Breach < 3h"], key="queue_filter",
                             label_visibility="collapsed")
        st.space("stretch")
        with st.popover("", icon=":material/tune:", help="Systems", type="tertiary"):
            st.multiselect("Systems", user["groups"], key="queue_systems")
        st.button("", icon=":material/keyboard_arrow_up:", on_click=_move, args=(-1,), shortcut="K",
                  type="tertiary", help="Previous (K)")
        st.button("", icon=":material/keyboard_arrow_down:", on_click=_move, args=(1,), shortcut="J",
                  type="tertiary", help="Next (J)")

    # Each card is HTML (for the prototype's layout) with an invisible full-size button on top.
    with st.container(key="queue", border=False, gap="small"):
        for a in asks:
            with st.container(key=f"card_{a['number']}", gap=None):
                st.html(ui_cards.queue_card(a["number"], "", "", a["short_description"], "", ask=True,
                                            selected=a["number"] == st.session_state.selected_incident))
                st.button(f"Open agent search: {a['short_description'][:60]}", key=f"q_{a['number']}",
                          on_click=_select, args=(a["number"],))
        for g in groups:
            inc = g.parent
            rem = g.sla.remaining.total_seconds() if g.sla.remaining is not None else None
            with st.container(key=f"card_{inc['number']}", gap=None):
                st.html(ui_cards.queue_card(
                    inc["number"], inc["priority"][:2], SHORT.get(inc["application"], ""), inc["short_description"],
                    ui_cards.sla_bar(rem, g.sla.target_h * 3600, deadline_ms=ops.deadline_epoch_ms(
                        g.sla.deadline, kb.all_incidents) if g.sla.deadline else None),
                    repeat=g.size if g.children else 0,
                    selected=inc["number"] == st.session_state.selected_incident))
                st.button(f"Open {inc['number']}", key=f"q_{inc['number']}", on_click=_select, args=(inc["number"],))
        if not numbers:
            st.info("No incidents match the search or filters.")
    st.caption(f"SLA clock started at the workbook export ({now:%d %b %H:%M}) and runs live · J / K to move")

number = st.session_state.selected_incident
if not number:
    st.stop()
incident = next(i for i in visible if i["number"] == number)
is_ask = incident.get("source") == ASK_SOURCE
sla = ops.sla_for(incident, now)
key = (number, user_id, model)
dkey = (number, user_id)
group = next((g for g in groups if g.parent["number"] == number), None)
siblings = [i for i in kb.queue if incident.get("error_code") and i.get("error_code") == incident.get("error_code")
            and i["application"] == incident["application"] and i["number"] != number
            and i["number"] not in resolved]

if key not in st.session_state.results:
    with main_col, st.spinner("Copilot is matching similar incidents and retrieving sources..."):
        st.session_state.results[key] = engine.analyze(incident, user_id, index, model)
rec: engine.Recommendation = st.session_state.results[key]
log = audit.entries()                               # include events logged by the analysis
st.session_state.data_sig = data_signature()        # this session's own writes don't trigger an auto-refresh
state = ops.state_for(incident, log)
best = next((m.incident for m in rec.similar if m.score >= config.MIN_CONFIDENCE), None)
cited_refs = sorted({rec.source(sid).ref for s in rec.steps for sid in s.sources})


# --- Dialogs ---------------------------------------------------------------------------
@st.dialog("Resolve incident", width="large")
def resolve_dialog() -> None:
    applied = [s.text for n, s in enumerate(rec.steps, 1) if n in state.steps_done] or \
        st.session_state.decided.get(dkey, {}).get("applied") or [s.text for s in rec.steps]
    if dkey not in st.session_state.notes:
        with st.spinner("Drafting closure notes..."):
            st.session_state.notes[dkey], by_llm = engine.draft_notes(rec, applied, model)
        audit.log("note_draft", user_id, number, "generated" if by_llm else "template (AI unavailable)")
    draft = st.session_state.notes[dkey]
    st.caption("Closure notes drafted by the copilot from the incident, the steps you ran and the cited sources. "
               "Edit before saving; they feed the knowledge base review.")
    symptom = st.text_area("Symptom", draft["symptom"], height=70)
    cause = st.text_area("Root cause", draft["root_cause"], height=70)
    steps = st.text_area("Steps taken (one per line)", "\n".join(draft["steps_taken"]), height=130)
    verification = st.text_area("Verification", draft["verification"], height=70)
    if st.button("Save notes and resolve", type="primary", icon=":material/task_alt:"):
        note = {"symptom": symptom, "root_cause": cause,
                "steps_taken": [s for s in steps.splitlines() if s.strip()], "verification": verification}
        audit.save_work_note(number, user_id, note)
        audit.log("note_saved", user_id, number, "closure notes saved")
        audit.log("resolve", user_id, number, "resolved by engineer")
        st.session_state.notes.pop(dkey, None)
        st.session_state.flash = f"{number} resolved and removed from the queue"
        st.session_state.selected_incident = None       # move on to the next incident in the queue
        st.rerun()


@st.dialog("Run step", width="medium")
def run_dialog(n: int, step: engine.Step) -> None:
    st.markdown(f"**Step {n}.** {step.text}")
    st.caption("Source: " + " · ".join(rec.source(s).ref for s in step.sources))
    if n in state.steps_approved:
        st.caption(f":material/verified: Approved by {state.steps_approved[n]}")
    st.info("The copilot has no connection to production systems. Run this step yourself, then confirm "
            "it here so it is recorded on the timeline.", icon=":material/visibility:")
    if st.button("Confirm step completed", type="primary", icon=":material/check:"):
        audit.log("step_run", user_id, number, f"step {n} done by engineer",
                  sources=[rec.source(s).ref for s in step.sources],
                  details={"step": n, "text": step.text, "risk": step.risk})
        st.rerun()


@st.dialog("Request approval", width="medium")
def approval_dialog(n: int, step: engine.Step) -> None:
    st.markdown(f"**Step {n}.** {step.text}")
    st.warning(f"**High risk.** {step.risk_reason or 'Production change'}. Follow the "
               f"[change procedure]({config.CHANGE_PROCEDURE_URL}); record who approved it.",
               icon=":material/gpp_maybe:")
    approver = st.text_input("Approver (name)")
    change = st.text_input("Change reference", placeholder="CHG…")
    if st.button("Record approval", type="primary", disabled=not (approver.strip() and change.strip()),
                 icon=":material/verified:"):
        audit.log("step_approved", user_id, number, f"step {n} approved",
                  details={"step": n, "approver": approver.strip(), "change": change.strip(), "text": step.text})
        st.rerun()


@st.dialog("Escalate", width="medium")
def escalate_dialog() -> None:
    st.markdown(f"**Escalation path:** {rec.escalation}")
    reason = st.text_area("Reason", placeholder="What you tried and why it needs escalation")
    if st.button("Escalate", type="primary", disabled=not reason.strip(), icon=":material/north_east:"):
        audit.log("escalate", user_id, number, reason.strip(), details={"to": rec.escalation})
        st.rerun()


def _draft_update(audience: str = "Business") -> None:
    done = [s.text for n, s in enumerate(rec.steps, 1) if n in state.steps_done]
    upd, by_llm = engine.draft_update(rec, audience, state.status, state.owner or "Unassigned", sla.label, done, model)
    st.session_state.updates[dkey] = dict(upd, audience=audience)
    audit.log("update_draft", user_id, number, "generated" if by_llm else "template (AI unavailable)")
    st.session_state.open_comms = number        # switch tabs on the next run, before the tabs render


def _create_problem() -> None:
    pid = ops.new_problem_id(audit.entries())
    audit.log("problem_created", user_id, number, pid, details={
        "error_code": incident.get("error_code"), "system": incident["application"],
        "open_incidents": len(siblings) + 1, "pattern_seen": best.occurrences if best else 0})
    st.toast(f"Problem record {pid} created from {len(siblings) + 1} open incidents", icon=":material/bug_report:")


def _ask(question: str) -> None:
    history = st.session_state.chats.setdefault(dkey, [])
    try:
        reply = engine.ask(rec, question, history, model)
        answer = reply["answer"] + ("  \n:gray[" + " · ".join(rec.source(s).ref for s in reply["sources"]) + "]"
                                    if reply["sources"] else "")
    except LLMUnavailable:
        answer = "The copilot chat is unavailable right now."
    history += [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]


with main_col:
    # === Incident header ===============================================================
    with st.container(key="incheader", border=False):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            if is_ask:
                st.badge("Agent search", icon=":material/search:", color="blue")
                st.badge(incident["application"], color="gray")
            else:
                st.markdown(f"`{number}`", width="content")
                sev = incident["priority"][:2]
                st.badge(incident["priority"], color=SEV_COLOR.get(sev, "gray"))
                st.badge(state.status or "–", color="gray")
                st.badge(f"Owner · {state.owner or 'Unassigned'}", color="gray")
            st.space("stretch")
            if not is_ask:
                if st.button("Acknowledged" if state.acknowledged else "Acknowledge",
                             disabled=state.acknowledged or state.status == "Resolved"):
                    audit.log("acknowledge", user_id, number, "acknowledged; took ownership")
                    st.rerun()
                if st.button("Resolved" if state.status == "Resolved" else "Resolve", type="primary",
                             disabled=state.status == "Resolved"):
                    resolve_dialog()
        with st.container(horizontal=True, vertical_alignment="bottom", gap="medium"):
            with st.container():
                st.markdown(f"#### {incident['short_description']}")
                sub = [SHORT.get(incident["application"], incident["application"]), incident.get("component"),
                       incident.get("business_area"), f"opened {incident.get('opened')}" if incident.get("opened") else None]
                st.caption(" · ".join(x for x in sub if x))
            if not is_ask and sla.remaining is not None:
                # Live timer: counts down to breach, then counts up the time over SLA.
                st.html(ui_cards.sla_box(sla.remaining.total_seconds(), sla.target_h * 3600,
                                         met=state.status == "Resolved",
                                         deadline_ms=ops.deadline_epoch_ms(sla.deadline, kb.all_incidents)),
                        width="content")

    work_col, copilot_col = st.columns([700, 440], gap="small")

    # === Workspace =====================================================================
    with work_col, st.container(height=520, border=False):
        if st.session_state.get("open_comms") == number:
            del st.session_state["open_comms"]
            st.session_state[f"ws_tabs_{number}"] = "Comms"
        overview, timeline_tab, related, comms = st.tabs(["Overview", "Timeline", "Related", "Comms"],
                                                         key=f"ws_tabs_{number}")
        rows = ops.timeline(incident, state.events)

        def dot(who: str) -> str:
            return ":blue[●]" if who == "Copilot" else ":gray[●]" if who == "ITSM" else ":green[●]"

        with overview:
            with st.container(horizontal=True, gap="small"):
                with st.container(border=True):
                    st.caption("Business impact")
                    st.markdown(f"**{incident.get('business_area') or incident['application']}**")
                with st.container(border=True):
                    st.caption("CI path")
                    st.markdown(f"**{SHORT.get(incident['application'], '')} → {incident.get('component') or '–'}**")
                with st.container(border=True):
                    st.caption("Last change")
                    st.markdown("**No change feed connected**")
            code = incident.get("error_code")
            if code:
                with st.container(border=True):
                    trend = pd.DataFrame(ops.daily_counts(kb.all_incidents, incident["application"], code, now))
                    with st.container(horizontal=True):
                        st.markdown(f"**{code} · incidents opened per day**")
                        st.space("stretch")
                        st.caption(f"last 30 days · peak {int(trend['Incidents'].max())}", width="content")
                    st.area_chart(trend, x="Day", y="Incidents", height=130, color="#FF8A4C",
                                  x_label="", y_label="")
            elif incident.get("description") and incident["description"] != incident["short_description"]:
                st.write(incident["description"])
            with st.container(border=True):
                st.markdown("**Latest activity**")
                for t, who, txt in list(reversed(rows))[:3]:
                    st.markdown(f"{dot(who)} `{t[11:16] if len(t) > 11 else t}` {txt}")

        with timeline_tab:
            for t, who, txt in reversed(rows):
                st.markdown(f"{dot(who)} :gray[`{t}` · {who}]  \n{txt}")
            for n in audit.work_notes(number):
                with st.container(border=True):
                    st.caption(f"Closure notes · {n['ts'][:16].replace('T', ' ')} UTC · {n['user']}")
                    st.markdown(f"**Symptom:** {n['note']['symptom']}  \n**Root cause:** {n['note']['root_cause']}")
                    st.markdown("\n".join(f"{i}. {s}" for i, s in enumerate(n["note"]["steps_taken"], 1)))
                    st.markdown(f"**Verification:** {n['note']['verification']}")

        with related:
            problem = ops.problem_for(incident.get("error_code", ""), log) if incident.get("error_code") else ""

            def rel(kind: str, ref: str, text: str) -> None:
                with st.container(border=True, horizontal=True, vertical_alignment="center", gap="small"):
                    st.badge(kind, color="gray", width="content")
                    st.markdown(f"`{ref}`", width="content")
                    st.markdown(text)

            if problem:
                rel("Problem", problem, f"Root-cause record for {incident.get('error_code')}")
            for m in rec.similar:
                if m.score >= config.MIN_CONFIDENCE:
                    i = m.incident
                    rel("Incident", i.number, f"{first_sentence(i.resolution_notes, 110)} · resolved {i.resolved} · seen {i.occurrences}×")
            for ref in sorted({s.ref for s in rec.sources if s.kind != "Incident"}):
                sec = next(s for s in rec.sources if s.ref == ref)
                rel("Knowledge", ref.split("#")[0], sec.title.split(" › ")[-1])
            rel("Change", "–", "Change records not available: no change feed connected")
            if siblings:
                st.markdown(f"**Open incidents with {incident.get('error_code')}** · {len(siblings)}")
                with st.container(horizontal=True, gap="small"):
                    for i in siblings[:8]:
                        st.button(i["number"], key=f"sib_{i['number']}", on_click=_select, args=(i["number"],),
                                  type="tertiary")
                if len(siblings) > 8:
                    st.caption(f"and {len(siblings) - 8} more")

        with comms:
            upd = st.session_state.updates.get(dkey)
            if upd:
                with st.container(border=True):
                    with st.container(horizontal=True):
                        st.markdown("**Stakeholder update · drafted by copilot**")
                        st.space("stretch")
                        st.caption("Review before sending", width="content")
                    audience = st.segmented_control("Audience", ["Business", "Technical", "Management"],
                                                    default=upd.get("audience", "Business"), key=f"aud_{number}")
                    if audience and audience != upd.get("audience"):
                        with st.spinner("Redrafting..."):
                            _draft_update(audience)
                        st.rerun()
                    subject = st.text_input("Subject", upd["subject"], key=f"subj_{number}_{upd.get('audience')}")
                    body = st.text_area("Message", upd["body"], height=180, key=f"body_{number}_{upd.get('audience')}")
                    with st.container(horizontal=True, vertical_alignment="center"):
                        if st.button("Mark as sent", type="primary", icon=":material/send:"):
                            audit.log("update_sent", user_id, number, "sent by engineer",
                                      details={"audience": upd["audience"], "subject": subject, "body": body})
                            st.session_state.updates.pop(dkey, None)
                            st.toast("Update recorded on the timeline", icon=":material/check:")
                            st.rerun()
                        st.caption("Copy it to your comms channel. The copilot drafts; you send.")
            else:
                with st.container(border=True, horizontal=True, vertical_alignment="center"):
                    st.markdown(":gray[No update drafted yet. The copilot writes one from the timeline and cause; "
                                "you review and send.]")
                    if st.button("Draft update", key="draft_empty"):
                        with st.spinner("Drafting..."):
                            _draft_update()
                        st.rerun()
            for e in reversed([e for e in state.events if e["action"] == "update_sent"]):
                with st.container(border=True):
                    st.caption(f"Sent · {e['ts'][:16].replace('T', ' ')} UTC · {e['details'].get('audience')}")
                    st.markdown(f"**{e['details'].get('subject')}**  \n{e['details'].get('body')}")

    # === Copilot panel =================================================================
    with copilot_col, st.container(key="copilot", height=455, border=True):
        conf = {"high": ("High", "green"), "low": ("Low", "red"), "none": ("No", "gray")}[rec.state]
        whykey = f"why_{number}"
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            st.markdown("**:blue[:material/auto_awesome:] Copilot**", width="content")
            st.space("stretch")
            st.badge(f"{conf[0]} confidence · {rec.confidence:.2f}", color=conf[1])
            if rec.similar and rec.state != "none":
                st.toggle("Why?", key=whykey)
            if st.button("", icon=":material/refresh:", type="tertiary", help="Re-run the copilot"):
                st.session_state.results.pop(key, None)
                st.rerun()

        if st.session_state.get(whykey) and best:
            m = rec.similar[0]
            with st.container(border=True):
                st.caption(f"Why it matched {best.number} (seen {best.occurrences}×)")
                marks = {ops.MATCH: ":green[✓]", ops.PARTIAL: ":orange[≈]", ops.DIFFERS: ":red[×]"}
                st.markdown("  \n".join(f"{marks[k]} {t}" for k, t in ops.match_reasons(rec.masked_text, incident, m)))
                if rec.rerank.get("reranked"):
                    st.caption("Order confirmed by a second-pass relevance check.")
        if rec.injection:
            st.warning("Instruction-like text in the ticket was ignored and logged.", icon=":material/gpp_maybe:")
        if rec.error:
            st.error(rec.error, icon=":material/error:")

        if rec.state == "none":
            st.info("**No known fix found.** Nothing in the history or runbooks matches closely enough.",
                    icon=":material/search_off:")
            st.markdown(f"**Escalation path:** {rec.escalation}")
            if not is_ask and st.button("Escalate", icon=":material/north_east:"):
                escalate_dialog()
        else:
            if rec.state == "low":
                st.error("Low confidence: treat this as a lead, not an answer. Escalation is recommended.",
                         icon=":material/help:")

            st.caption("SUMMARY")
            st.markdown(rec.summary or (f"Possible repeat of {best.error_code}: {best.short_description}"
                                        if best else first_sentence(incident["short_description"])))

            if best:
                with st.container(border=True):
                    st.caption("LIKELY CAUSE")
                    st.markdown(f"**{best.rca}**")
                    evidence = [f"Same fix worked {best.occurrences}× · latest {best.number} ({best.resolved})"]
                    if best.error_code and best.error_code.lower() in rec.masked_text.lower():
                        evidence.insert(0, f"Error code {best.error_code} matches")
                    evidence += [f"Runbook {s.ref.split('#')[0]} › {s.title.split(' › ')[-1]}"
                                 for s in rec.sources if s.kind != "Incident" and best.error_code
                                 and best.error_code in s.title][:1]
                    if not best.kb_used:
                        evidence.append(":orange[No runbook entry for this error]")
                    st.markdown("  \n".join(f":blue[›] {e}" for e in evidence))

            if rec.steps:
                done_n = sum(1 for n in range(1, len(rec.steps) + 1) if n in state.steps_done)
                with st.container(border=True):
                    with st.container(horizontal=True):
                        st.caption("RECOMMENDED STEPS")
                        st.space("stretch")
                        st.caption(f"{done_n} of {len(rec.steps)} done", width="content")
                    st.progress(done_n / len(rec.steps))
                    resolved = state.status == "Resolved"
                    for n, step in enumerate(rec.steps, 1):
                        done = n in state.steps_done
                        approved = n in state.steps_approved
                        status = ("Completed" if done else "Approved · ready" if approved
                                  else "Needs approval" if step.high_risk else "Ready")
                        with st.container(border=True, gap="xsmall"):
                            mark = ":green[**✓**]" if done else f"**{n}**"
                            st.markdown(f"{mark} {step.text}")
                            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                                st.badge(f"{step.risk.title()} risk", color=RISK_COLOR[step.risk])
                                st.caption(status, width="content")
                                st.space("stretch")
                                if not is_ask and not resolved and not done:
                                    if step.high_risk and not approved:
                                        if st.button("Request approval", key=f"appr_{number}_{n}", type="tertiary"):
                                            approval_dialog(n, step)
                                    elif st.button("Run", key=f"run_{number}_{n}", type="primary"):
                                        run_dialog(n, step)
                    if rec.dropped:
                        st.caption(f"{len(rec.dropped)} step(s) removed by the groundedness check.")
            for c in rec.conflicts:
                st.warning(c["note"], icon=":material/compare_arrows:")

            if not is_ask:
                with st.container(horizontal=True, gap="small"):
                    if st.button("Draft update", width="stretch"):
                        with st.spinner("Drafting..."):
                            _draft_update()
                        st.rerun()
                    escalated = any(e["action"] == "escalate" for e in state.events)
                    if st.button("Escalated" if escalated else "Escalate", width="stretch",
                                 disabled=escalated or state.status == "Resolved"):
                        escalate_dialog()
                    problem = ops.problem_for(incident.get("error_code", ""), log) if incident.get("error_code") else ""
                    can_problem = bool(incident.get("error_code")) and (len(siblings) + 1 >= config.PROBLEM_RECORD_MIN
                                                                        or (best and best.occurrences >= 10))
                    st.button(f"Problem {problem}" if problem else "Create problem", width="stretch",
                              disabled=bool(problem) or not can_problem, on_click=_create_problem,
                              help=f"{len(siblings) + 1} open incidents with this error code")

        # Sources and feedback
        st.divider()
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            refs = cited_refs or [s.ref for s in rec.sources[:2]]
            st.caption("Sources: " + (" · ".join(r.split("#")[0] if "#" in r else r for r in refs) or "none"))
            st.space("stretch")
            if rec.steps:
                prior = next((e for e in reversed(state.events) if e["action"] in ("accept", "edit", "reject")), None)
                if prior:
                    st.caption({"accept": ":green[Helpful]", "edit": ":orange[Edited]",
                                "reject": ":red[Not helpful]"}[prior["action"]], width="content")
                else:
                    if st.button("Helpful", key=f"up_{number}", type="tertiary", icon=":material/thumb_up:"):
                        audit.log("accept", user_id, number, "helpful", sources=cited_refs,
                                  details={"final_steps": [s.text for s in rec.steps]})
                        st.rerun()
                    with st.popover("Edit", type="tertiary", icon=":material/edit:"):
                        edited = st.text_area("Edited steps", "\n".join(s.text for s in rec.steps), height=150)
                        why = st.text_input("Reason", key=f"edit_reason_{number}")
                        if st.button("Save edit", disabled=not why.strip()):
                            audit.log("edit", user_id, number, why.strip(), sources=cited_refs,
                                      details={"final_steps": edited.splitlines()})
                            st.rerun()
                    with st.popover("Not helpful", type="tertiary", icon=":material/thumb_down:"):
                        why = st.text_input("What was wrong?", key=f"reject_reason_{number}")
                        if st.button("Send feedback", disabled=not why.strip()):
                            audit.log("reject", user_id, number, why.strip(), sources=cited_refs)
                            st.rerun()

        # Low-confidence agent searches get a short cited answer up front
        if is_ask and rec.state == "low" and rec.sources and not st.session_state.chats.get(dkey):
            with st.spinner("Asking the agent..."):
                _ask("Which known issue does this most likely match, and what fixed it before? "
                     "Do not give step-by-step instructions.")

        for msg in st.session_state.chats.get(dkey, []):
            with st.chat_message(msg["role"], avatar=":material/person:" if msg["role"] == "user"
                                 else ":material/auto_awesome:"):
                st.markdown(msg["content"])

    with copilot_col:
        if rec.state != "none" or is_ask:
            with st.container(horizontal=True, gap="xsmall"):
                for label, question in QUICK_QUESTIONS.items():
                    if st.button(label, key=f"chip_{number}_{label}", type="tertiary"):
                        with st.spinner("Thinking..."):
                            _ask(question)
                        st.rerun()
        with st.form(f"ask_{number}", clear_on_submit=True, border=False):
            with st.container(horizontal=True, vertical_alignment="center", gap="small"):
                q = st.text_input("Ask the copilot", placeholder="Ask about this incident…",
                                  label_visibility="collapsed")
                if st.form_submit_button("Ask", type="primary") and q.strip():
                    with st.spinner("Thinking..."):
                        _ask(q.strip())
                    st.rerun()
