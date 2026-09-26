"""Shared cockpit building blocks used by the desktop and mobile layouts.

Queue building, the open-incident context, dialogs, actions and the render functions for the
incident header, workspace tabs and copilot panel live here, so both layouts behave identically
and only arrange the pieces differently.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import streamlit as st

from copilot import audit, config, engine, ops
from copilot.llm import LLMUnavailable
import ui_cards
from ui_common import data_signature, first_sentence, full_queue, resolved_numbers

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


# --- Queue --------------------------------------------------------------------------------

@dataclass
class QueueView:
    visible: list[dict]           # everything this user may open (tickets + agent searches)
    asks: list[dict]
    tickets: list[dict]           # after filters
    groups: list[ops.QueueGroup]
    numbers: list[str]            # navigation order
    resolved: set[str]
    now: datetime


def load_queue(kb, user: dict, log: list[dict]) -> QueueView:
    now = ops.snapshot_now(kb.all_incidents)
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
    return QueueView(visible, asks, tickets, groups, numbers, resolved, now)


def select(number: str, detail: bool = False) -> None:
    st.session_state.selected_incident = number
    if detail:
        st.session_state.m_view = "detail"


def move(numbers: list[str], step: int) -> None:
    cur = st.session_state.get("selected_incident")
    if cur in numbers:
        st.session_state.selected_incident = numbers[max(0, min(len(numbers) - 1, numbers.index(cur) + step))]


def render_queue_cards(qv: QueueView, kb, detail_on_tap: bool = False) -> None:
    """Queue cards: HTML for the layout, an invisible full-size button on top for the tap/click."""
    sel = st.session_state.get("selected_incident")
    for a in qv.asks:
        with st.container(key=f"card_{a['number']}", gap=None):
            st.html(ui_cards.queue_card(a["number"], "", "", a["short_description"], "", ask=True,
                                        selected=a["number"] == sel and not detail_on_tap))
            st.button(f"Open agent search: {a['short_description'][:60]}", key=f"q_{a['number']}",
                      on_click=select, args=(a["number"], detail_on_tap))
    for g in qv.groups:
        inc = g.parent
        rem = g.sla.remaining.total_seconds() if g.sla.remaining is not None else None
        with st.container(key=f"card_{inc['number']}", gap=None):
            st.html(ui_cards.queue_card(
                inc["number"], inc["priority"][:2], SHORT.get(inc["application"], ""), inc["short_description"],
                ui_cards.sla_bar(rem, g.sla.target_h * 3600, deadline_ms=ops.deadline_epoch_ms(
                    g.sla.deadline, kb.all_incidents) if g.sla.deadline else None),
                repeat=g.size if g.children else 0,
                selected=inc["number"] == sel and not detail_on_tap))
            st.button(f"Open {inc['number']}", key=f"q_{inc['number']}", on_click=select,
                      args=(inc["number"], detail_on_tap))
    if not qv.numbers:
        st.info("No incidents match the search or filters.")


# --- Open incident ------------------------------------------------------------------------

@dataclass
class Ctx:
    kb: object
    user_id: str
    model: str
    number: str
    incident: dict
    is_ask: bool
    sla: ops.Sla
    siblings: list[dict]
    rec: engine.Recommendation
    state: ops.IncidentState
    log: list[dict]
    now: datetime
    best: object = None
    cited_refs: list[str] = field(default_factory=list)

    @property
    def key(self):
        return (self.number, self.user_id, self.model)

    @property
    def dkey(self):
        return (self.number, self.user_id)


def open_incident(kb, qv: QueueView, user_id: str, model: str, spinner_slot=None) -> Ctx | None:
    number = st.session_state.get("selected_incident")
    if not number:
        return None
    incident = next(i for i in qv.visible if i["number"] == number)
    siblings = [i for i in kb.queue if incident.get("error_code") and i.get("error_code") == incident.get("error_code")
                and i["application"] == incident["application"] and i["number"] != number
                and i["number"] not in qv.resolved]
    key = (number, user_id, model)
    if key not in st.session_state.results:
        with (spinner_slot or st.container()), st.spinner("Copilot is matching similar incidents and retrieving sources..."):
            st.session_state.results[key] = engine.analyze(incident, user_id, kb.index, model)
    rec = st.session_state.results[key]
    log = audit.entries()                            # include events logged by the analysis
    st.session_state.data_sig = data_signature()     # this session's own writes don't trigger an auto-refresh
    return Ctx(kb=kb, user_id=user_id, model=model, number=number, incident=incident,
               is_ask=incident.get("source") == ASK_SOURCE, sla=ops.sla_for(incident, qv.now),
               siblings=siblings, rec=rec, state=ops.state_for(incident, log), log=log, now=qv.now,
               best=next((m.incident for m in rec.similar if m.score >= config.MIN_CONFIDENCE), None),
               cited_refs=sorted({rec.source(sid).ref for s in rec.steps for sid in s.sources}))


# --- Dialogs ------------------------------------------------------------------------------

@st.dialog("Resolve incident", width="large")
def resolve_dialog(c: Ctx) -> None:
    rec, state = c.rec, c.state
    applied = [s.text for n, s in enumerate(rec.steps, 1) if n in state.steps_done] or \
        st.session_state.decided.get(c.dkey, {}).get("applied") or [s.text for s in rec.steps]
    if c.dkey not in st.session_state.notes:
        with st.spinner("Drafting closure notes..."):
            st.session_state.notes[c.dkey], by_llm = engine.draft_notes(rec, applied, c.model)
        audit.log("note_draft", c.user_id, c.number, "generated" if by_llm else "template (AI unavailable)")
    draft = st.session_state.notes[c.dkey]
    st.caption("Closure notes drafted by the copilot from the incident, the steps you ran and the cited sources. "
               "Edit before saving; they feed the knowledge base review.")
    symptom = st.text_area("Symptom", draft["symptom"], height=70)
    cause = st.text_area("Root cause", draft["root_cause"], height=70)
    steps = st.text_area("Steps taken (one per line)", "\n".join(draft["steps_taken"]), height=130)
    verification = st.text_area("Verification", draft["verification"], height=70)
    if st.button("Save notes and resolve", type="primary", icon=":material/task_alt:"):
        note = {"symptom": symptom, "root_cause": cause,
                "steps_taken": [s for s in steps.splitlines() if s.strip()], "verification": verification}
        audit.save_work_note(c.number, c.user_id, note)
        audit.log("note_saved", c.user_id, c.number, "closure notes saved")
        audit.log("resolve", c.user_id, c.number, "resolved by engineer")
        st.session_state.notes.pop(c.dkey, None)
        st.session_state.flash = f"{c.number} resolved and removed from the queue"
        st.session_state.selected_incident = None       # move on to the next incident in the queue
        st.session_state.m_view = "list"
        st.rerun()


@st.dialog("Run step", width="medium")
def run_dialog(c: Ctx, n: int, step: engine.Step) -> None:
    st.markdown(f"**Step {n}.** {step.text}")
    st.caption("Source: " + " · ".join(c.rec.source(s).ref for s in step.sources))
    if n in c.state.steps_approved:
        st.caption(f":material/verified: Approved by {c.state.steps_approved[n]}")
    st.info("The copilot has no connection to production systems. Run this step yourself, then confirm "
            "it here so it is recorded on the timeline.", icon=":material/visibility:")
    if st.button("Confirm step completed", type="primary", icon=":material/check:"):
        audit.log("step_run", c.user_id, c.number, f"step {n} done by engineer",
                  sources=[c.rec.source(s).ref for s in step.sources],
                  details={"step": n, "text": step.text, "risk": step.risk})
        st.rerun()


@st.dialog("Request approval", width="medium")
def approval_dialog(c: Ctx, n: int, step: engine.Step) -> None:
    st.markdown(f"**Step {n}.** {step.text}")
    st.warning(f"**High risk.** {step.risk_reason or 'Production change'}. Follow the "
               f"[change procedure]({config.CHANGE_PROCEDURE_URL}); record who approved it.",
               icon=":material/gpp_maybe:")
    approver = st.text_input("Approver (name)")
    change = st.text_input("Change reference", placeholder="CHG…")
    if st.button("Record approval", type="primary", disabled=not (approver.strip() and change.strip()),
                 icon=":material/verified:"):
        audit.log("step_approved", c.user_id, c.number, f"step {n} approved",
                  details={"step": n, "approver": approver.strip(), "change": change.strip(), "text": step.text})
        st.rerun()


@st.dialog("Escalate", width="medium")
def escalate_dialog(c: Ctx) -> None:
    st.markdown(f"**Escalation path:** {c.rec.escalation}")
    reason = st.text_area("Reason", placeholder="What you tried and why it needs escalation")
    if st.button("Escalate", type="primary", disabled=not reason.strip(), icon=":material/north_east:"):
        audit.log("escalate", c.user_id, c.number, reason.strip(), details={"to": c.rec.escalation})
        st.rerun()


# --- Actions ------------------------------------------------------------------------------

def draft_update(c: Ctx, audience: str = "Business") -> None:
    done = [s.text for n, s in enumerate(c.rec.steps, 1) if n in c.state.steps_done]
    upd, by_llm = engine.draft_update(c.rec, audience, c.state.status, c.state.owner or "Unassigned",
                                      c.sla.label, done, c.model)
    st.session_state.updates[c.dkey] = dict(upd, audience=audience)
    audit.log("update_draft", c.user_id, c.number, "generated" if by_llm else "template (AI unavailable)")
    st.session_state.open_comms = c.number        # switch tabs on the next run, before the tabs render


def remove_ask(user_id: str, ask_number: str) -> None:
    st.session_state.custom_incidents = [i for i in st.session_state.custom_incidents
                                         if i["number"] != ask_number]
    audit.log("agent_search_removed", user_id, ask_number, "removed from queue by engineer")
    st.session_state.flash = "Question removed from the queue"
    st.session_state.selected_incident = None
    st.session_state.m_view = "list"


def create_problem(c: Ctx) -> None:
    pid = ops.new_problem_id(audit.entries())
    audit.log("problem_created", c.user_id, c.number, pid, details={
        "error_code": c.incident.get("error_code"), "system": c.incident["application"],
        "open_incidents": len(c.siblings) + 1, "pattern_seen": c.best.occurrences if c.best else 0})
    st.toast(f"Problem record {pid} created from {len(c.siblings) + 1} open incidents", icon=":material/bug_report:")


def ask(c: Ctx, question: str) -> None:
    history = st.session_state.chats.setdefault(c.dkey, [])
    try:
        reply = engine.ask(c.rec, question, history, c.model)
        answer = reply["answer"] + ("  \n:gray[" + " · ".join(c.rec.source(s).ref for s in reply["sources"]) + "]"
                                    if reply["sources"] else "")
    except LLMUnavailable as exc:
        answer = f"The copilot chat is unavailable right now: {exc.reason}."
    history += [{"role": "user", "content": question}, {"role": "assistant", "content": answer}]


# --- Render: incident header --------------------------------------------------------------

def render_header_badges(c: Ctx) -> None:
    if c.is_ask:
        st.badge("Agent search", icon=":material/search:", color="blue")
        st.badge(c.incident["application"], color="gray")
    else:
        st.markdown(f"`{c.number}`", width="content")
        sev = c.incident["priority"][:2]
        st.badge(c.incident["priority"], color=SEV_COLOR.get(sev, "gray"))
        st.badge(c.state.status or "–", color="gray")
        st.badge(f"Owner · {c.state.owner or 'Unassigned'}", color="gray")


def render_header_actions(c: Ctx, stretch: bool = False) -> None:
    width = "stretch" if stretch else "content"
    if c.is_ask:
        st.button("Remove from queue", icon=":material/close:", on_click=remove_ask, args=(c.user_id, c.number),
                  help="Remove this agent search from the queue (it is not a ticket)", width=width)
        return
    if st.button("Acknowledged" if c.state.acknowledged else "Acknowledge", width=width,
                 disabled=c.state.acknowledged or c.state.status == "Resolved"):
        audit.log("acknowledge", c.user_id, c.number, "acknowledged; took ownership")
        st.rerun()
    if st.button("Resolved" if c.state.status == "Resolved" else "Resolve", type="primary", width=width,
                 disabled=c.state.status == "Resolved"):
        resolve_dialog(c)


def render_title(c: Ctx) -> None:
    st.markdown(f"#### {c.incident['short_description']}")
    sub = [SHORT.get(c.incident["application"], c.incident["application"]), c.incident.get("component"),
           c.incident.get("business_area"),
           f"{'asked' if c.is_ask else 'opened'} {c.incident.get('opened')} IST" if c.incident.get("opened") else None]
    st.caption(" · ".join(x for x in sub if x))


def render_sla_box(c: Ctx, stretch: bool = False) -> None:
    if not c.is_ask and c.sla.remaining is not None:
        # Live timer: counts down to breach, then counts up the time over SLA.
        st.html(ui_cards.sla_box(c.sla.remaining.total_seconds(), c.sla.target_h * 3600,
                                 met=c.state.status == "Resolved",
                                 deadline_ms=ops.deadline_epoch_ms(c.sla.deadline, c.kb.all_incidents)),
                width="stretch" if stretch else "content")


# --- Render: workspace tabs ---------------------------------------------------------------

def _dot(who: str) -> str:
    return ":blue[●]" if who == "Copilot" else ":gray[●]" if who == "ITSM" else ":green[●]"


def render_workspace(c: Ctx, compact: bool = False) -> None:
    number, incident, rec, state = c.number, c.incident, c.rec, c.state
    if st.session_state.get("open_comms") == number:
        del st.session_state["open_comms"]
        st.session_state[f"ws_tabs_{number}"] = "Comms"
    overview, timeline_tab, related, comms = st.tabs(["Overview", "Timeline", "Related", "Comms"],
                                                     key=f"ws_tabs_{number}")
    rows = ops.timeline(incident, state.events)

    with overview:
        with st.container(horizontal=not compact, gap="small"):
            for label, value in (("Business impact", incident.get("business_area") or incident["application"]),
                                 ("CI path", f"{SHORT.get(incident['application'], '')} → {incident.get('component') or '–'}"),
                                 ("Last change", "No change feed connected")):
                with st.container(border=True):
                    st.caption(label)
                    st.markdown(f"**{value}**")
        code = incident.get("error_code")
        if code:
            with st.container(border=True):
                trend = pd.DataFrame(ops.daily_counts(c.kb.all_incidents, incident["application"], code, c.now))
                with st.container(horizontal=True):
                    st.markdown(f"**{code} · incidents opened per day**")
                    st.space("stretch")
                    st.caption(f"last 30 days · peak {int(trend['Incidents'].max())}", width="content")
                st.area_chart(trend, x="Day", y="Incidents", height=130, color="#FF8A4C", x_label="", y_label="")
        elif incident.get("description") and incident["description"] != incident["short_description"]:
            st.write(incident["description"])
        with st.container(border=True):
            st.markdown("**Latest activity**")
            for t, who, txt in list(reversed(rows))[:3]:
                st.markdown(f"{_dot(who)} `{t[11:] if t[:4].isdigit() else t}` {txt}")

    with timeline_tab:
        for t, who, txt in reversed(rows):
            st.markdown(f"{_dot(who)} :gray[`{t}` · {who}]  \n{txt}")
        for n in audit.work_notes(number):
            with st.container(border=True):
                st.caption(f"Closure notes · {ops.fmt_ist(n['ts'])} · {n['user']}")
                st.markdown(f"**Symptom:** {n['note']['symptom']}  \n**Root cause:** {n['note']['root_cause']}")
                st.markdown("\n".join(f"{i}. {s}" for i, s in enumerate(n["note"]["steps_taken"], 1)))
                st.markdown(f"**Verification:** {n['note']['verification']}")

    with related:
        problem = ops.problem_for(incident.get("error_code", ""), c.log) if incident.get("error_code") else ""

        def rel(kind: str, ref: str, text: str) -> None:
            with st.container(border=True, horizontal=not compact, vertical_alignment="center", gap="small"):
                st.badge(kind, color="gray", width="content")
                st.markdown(f"`{ref}`", width="content")
                st.markdown(text)

        if problem:
            rel("Problem", problem, f"Root-cause record for {incident.get('error_code')}")
        for m in rec.similar:
            if m.score >= config.MIN_CONFIDENCE:
                i = m.incident
                rel("Incident", i.number, f"{first_sentence(i.resolution_notes, 110)} · resolved {i.resolved} · "
                                          f"seen {i.occurrences}×")
        for ref in sorted({s.ref for s in rec.sources if s.kind != "Incident"}):
            sec = next(s for s in rec.sources if s.ref == ref)
            rel("Knowledge", ref.split("#")[0], sec.title.split(" › ")[-1])
        rel("Change", "–", "Change records not available: no change feed connected")
        if c.siblings:
            st.markdown(f"**Open incidents with {incident.get('error_code')}** · {len(c.siblings)}")
            with st.container(horizontal=True, gap="small"):
                for i in c.siblings[:8]:
                    st.button(i["number"], key=f"sib_{i['number']}", on_click=select, args=(i["number"],),
                              type="tertiary")
            if len(c.siblings) > 8:
                st.caption(f"and {len(c.siblings) - 8} more")

    with comms:
        upd = st.session_state.updates.get(c.dkey)
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
                        draft_update(c, audience)
                    st.rerun()
                subject = st.text_input("Subject", upd["subject"], key=f"subj_{number}_{upd.get('audience')}")
                body = st.text_area("Message", upd["body"], height=180, key=f"body_{number}_{upd.get('audience')}")
                if st.button("Mark as sent", type="primary", icon=":material/send:"):
                    audit.log("update_sent", c.user_id, number, "sent by engineer",
                              details={"audience": upd["audience"], "subject": subject, "body": body})
                    st.session_state.updates.pop(c.dkey, None)
                    st.toast("Update recorded on the timeline", icon=":material/check:")
                    st.rerun()
                st.caption("Copy it to your comms channel. The copilot drafts; you send.")
        else:
            with st.container(border=True, horizontal=not compact, vertical_alignment="center"):
                st.markdown(":gray[No update drafted yet. The copilot writes one from the timeline and cause; "
                            "you review and send.]")
                if st.button("Draft update", key="draft_empty"):
                    with st.spinner("Drafting..."):
                        draft_update(c)
                    st.rerun()
        for e in reversed([e for e in state.events if e["action"] == "update_sent"]):
            with st.container(border=True):
                st.caption(f"Sent · {ops.fmt_ist(e['ts'])} · {e['details'].get('audience')}")
                st.markdown(f"**{e['details'].get('subject')}**  \n{e['details'].get('body')}")


# --- Render: copilot panel ----------------------------------------------------------------

def render_copilot(c: Ctx, compact: bool = False) -> None:
    """Status + why, summary, likely cause, steps with risk/approval, actions, sources + feedback, chat log."""
    number, incident, rec, state, best = c.number, c.incident, c.rec, c.state, c.best
    conf = {"high": ("High confidence", "green"), "low": ("Low confidence", "red"),
            "none": ("No confidence", "gray")}[rec.state]
    if getattr(rec, "ai_failed", False):
        conf = ("Strong match · AI steps unavailable", "orange")   # only the AI step generation failed
    whykey = f"why_{number}"
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        st.markdown("**:blue[:material/auto_awesome:] Copilot**", width="content")
        st.space("stretch")
        st.badge(f"{conf[0]} · {rec.confidence:.2f}", color=conf[1])
        if rec.similar and rec.state != "none":
            st.toggle("Why?", key=whykey)
        if st.button("", icon=":material/refresh:", type="tertiary", help="Re-run the copilot", key=f"rerun_{number}"):
            st.session_state.results.pop(c.key, None)
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
        if c.is_ask:
            # An off-topic or unknown question has nothing to work on: offer to clear it.
            st.caption("This question doesn't match any known issue for your systems.")
            st.button("Remove this question from the queue", icon=":material/close:", type="primary",
                      key="remove_ask_nomatch", on_click=remove_ask, args=(c.user_id, number), width="stretch")
        elif st.button("Escalate", icon=":material/north_east:", key="escalate_nomatch"):
            escalate_dialog(c)
    else:
        if rec.state == "low" and not getattr(rec, "ai_failed", False):
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
                            if not c.is_ask and not resolved and not done:
                                if step.high_risk and not approved:
                                    if st.button("Request approval", key=f"appr_{number}_{n}", type="tertiary"):
                                        approval_dialog(c, n, step)
                                elif st.button("Run", key=f"run_{number}_{n}", type="primary"):
                                    run_dialog(c, n, step)
                if rec.dropped:
                    st.caption(f"{len(rec.dropped)} step(s) removed by the groundedness check.")
        for cf in rec.conflicts:
            st.warning(cf["note"], icon=":material/compare_arrows:")

        if not c.is_ask:
            with st.container(horizontal=True, gap="small"):
                if st.button("Draft update", width="stretch", key=f"draft_{number}"):
                    with st.spinner("Drafting..."):
                        draft_update(c)
                    st.rerun()
                escalated = any(e["action"] == "escalate" for e in state.events)
                if st.button("Escalated" if escalated else "Escalate", width="stretch", key=f"esc_{number}",
                             disabled=escalated or state.status == "Resolved"):
                    escalate_dialog(c)
                problem = ops.problem_for(incident.get("error_code", ""), c.log) if incident.get("error_code") else ""
                can_problem = bool(incident.get("error_code")) and (len(c.siblings) + 1 >= config.PROBLEM_RECORD_MIN
                                                                    or (best and best.occurrences >= 10))
                st.button(f"Problem {problem}" if problem else "Create problem", width="stretch",
                          key=f"prb_{number}", disabled=bool(problem) or not can_problem, on_click=create_problem,
                          args=(c,), help=f"{len(c.siblings) + 1} open incidents with this error code")

    # Sources and feedback
    st.divider()
    with st.container(horizontal=True, vertical_alignment="center", gap="small"):
        refs = c.cited_refs or [s.ref for s in rec.sources[:2]]
        st.caption("Sources: " + (" · ".join(r.split("#")[0] if "#" in r else r for r in refs) or "none"))
        st.space("stretch")
        if rec.steps:
            prior = next((e for e in reversed(state.events) if e["action"] in ("accept", "edit", "reject")), None)
            if prior:
                st.caption({"accept": ":green[Helpful]", "edit": ":orange[Edited]",
                            "reject": ":red[Not helpful]"}[prior["action"]], width="content")
            else:
                if st.button("Helpful", key=f"up_{number}", type="tertiary", icon=":material/thumb_up:"):
                    audit.log("accept", c.user_id, number, "helpful", sources=c.cited_refs,
                              details={"final_steps": [s.text for s in rec.steps]})
                    st.rerun()
                with st.popover("Edit", type="tertiary", icon=":material/edit:"):
                    edited = st.text_area("Edited steps", "\n".join(s.text for s in rec.steps), height=150)
                    why = st.text_input("Reason", key=f"edit_reason_{number}")
                    if st.button("Save edit", disabled=not why.strip()):
                        audit.log("edit", c.user_id, number, why.strip(), sources=c.cited_refs,
                                  details={"final_steps": edited.splitlines()})
                        st.rerun()
                with st.popover("Not helpful", type="tertiary", icon=":material/thumb_down:"):
                    why = st.text_input("What was wrong?", key=f"reject_reason_{number}")
                    if st.button("Send feedback", disabled=not why.strip()):
                        audit.log("reject", c.user_id, number, why.strip(), sources=c.cited_refs)
                        st.rerun()

    # Low-confidence agent searches get a short cited answer up front
    if c.is_ask and rec.state == "low" and rec.sources and not st.session_state.chats.get(c.dkey):
        with st.spinner("Asking the agent..."):
            ask(c, "Which known issue does this most likely match, and what fixed it before? "
                   "Do not give step-by-step instructions.")

    for msg in st.session_state.chats.get(c.dkey, []):
        with st.chat_message(msg["role"], avatar=":material/person:" if msg["role"] == "user"
                             else ":material/auto_awesome:"):
            st.markdown(msg["content"])


def render_chat_input(c: Ctx) -> None:
    """Quick-question chips and the ask box."""
    if c.rec.state != "none" or c.is_ask:
        with st.container(horizontal=True, gap="xsmall"):
            for label, question in QUICK_QUESTIONS.items():
                if st.button(label, key=f"chip_{c.number}_{label}", type="tertiary"):
                    with st.spinner("Thinking..."):
                        ask(c, question)
                    st.rerun()
    with st.form(f"ask_{c.number}", clear_on_submit=True, border=False):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            q = st.text_input("Ask the copilot", placeholder="Ask about this incident…", label_visibility="collapsed")
            if st.form_submit_button("Ask", type="primary") and q.strip():
                with st.spinner("Thinking..."):
                    ask(c, q.strip())
                st.rerun()
