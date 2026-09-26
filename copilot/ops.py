"""Operational view of the queue: SLA clock, breach-risk ordering, repeat grouping,
and incident state/timeline derived from the audit log.

The audit log is the single source of truth for what engineers did in the copilot
(acknowledge, run step, escalate, resolve, updates), so state survives restarts and
every change on screen is traceable.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import audit, config

SEV_RANK = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
STATE_ACTIONS = {"acknowledge": "Acknowledged", "escalate": "Escalated", "resolve": "Resolved",
                 "reopen": "In Progress"}


_CLOCK_STARTED = datetime.now()


def clock_offset(all_incidents: list[dict]) -> timedelta:
    """SLA clock minus wall clock. The clock starts at the workbook's newest timestamp (or
    COPILOT_NOW) when the app starts, then runs in real time, so timers tick without every
    ticket being months overdue."""
    if config.NOW_OVERRIDE:
        base = datetime.fromisoformat(config.NOW_OVERRIDE)
    else:
        stamps = [s for s in (_parse(i.get("opened", "")) for i in all_incidents) if s]
        base = max(stamps) if stamps else _CLOCK_STARTED
    return base - _CLOCK_STARTED


def snapshot_now(all_incidents: list[dict]) -> datetime:
    return datetime.now() + clock_offset(all_incidents)


def deadline_epoch_ms(deadline: datetime, all_incidents: list[dict]) -> float:
    """Wall-clock epoch (ms) at which an SLA-clock deadline falls: what the browser counts down to."""
    return (deadline - clock_offset(all_incidents)).timestamp() * 1000


def _parse(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)[:16])
    except ValueError:
        return None


@dataclass
class Sla:
    target_h: float
    remaining: timedelta | None      # negative when breached; None when no SLA applies
    deadline: datetime | None = None

    @property
    def breached(self) -> bool:
        return self.remaining is not None and self.remaining.total_seconds() < 0

    @property
    def at_risk(self) -> bool:
        return (self.remaining is not None and not self.breached
                and self.remaining.total_seconds() < self.target_h * 3600 * config.AT_RISK_FRACTION)

    @property
    def tier(self) -> int:           # 0 breached, 1 at risk, 2 on track, 3 no SLA
        return 3 if self.remaining is None else 0 if self.breached else 1 if self.at_risk else 2

    @property
    def label(self) -> str:
        if self.remaining is None:
            return "No SLA"
        secs = abs(self.remaining.total_seconds())
        text = _duration(secs)
        return f"Breached {text} ago" if self.breached else f"{text} to breach"

    @property
    def pct_used(self) -> int:
        if self.remaining is None or not self.target_h:
            return 0
        used = 1 - self.remaining.total_seconds() / (self.target_h * 3600)
        return int(max(0, min(1, used)) * 100)

    @property
    def short(self) -> str:
        if self.remaining is None:
            return "–"
        return f"SLA breached {_duration(abs(self.remaining.total_seconds()))}" if self.breached \
            else f"SLA {_duration(self.remaining.total_seconds())} left"


def _duration(secs: float) -> str:
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins} min"
    hours, mins = divmod(mins, 60)
    if hours < 48:
        return f"{hours} h {mins} min" if mins and hours < 10 else f"{hours} h"
    return f"{hours // 24} d"


def sla_for(incident: dict, now: datetime) -> Sla:
    target = config.SLA_HOURS.get(incident.get("priority", "")[:2])
    opened = _parse(incident.get("opened", ""))
    if target is None or opened is None:
        return Sla(0, None)
    deadline = opened + timedelta(hours=target)
    return Sla(target, deadline - now, deadline)


# --- State from the audit log ------------------------------------------------------

@dataclass
class IncidentState:
    status: str
    owner: str
    acknowledged: bool = False
    steps_done: set[int] = field(default_factory=set)
    steps_approved: dict[int, str] = field(default_factory=dict)   # step -> "approver (change)"
    problem: str = ""
    events: list[dict] = field(default_factory=list)


def state_for(incident: dict, log: list[dict]) -> IncidentState:
    st = IncidentState(status=incident.get("status", ""), owner=incident.get("assigned_to", ""))
    for e in log:
        if e["incident"] != incident["number"]:
            continue
        st.events.append(e)
        if e["action"] in STATE_ACTIONS:
            st.status = STATE_ACTIONS[e["action"]]
        if e["action"] == "acknowledge":
            st.acknowledged = True
            st.owner = config.USERS.get(e["user"], {}).get("name", e["user"])
        if e["action"] == "step_run":
            st.steps_done.add(int(e["details"].get("step", 0)))
        if e["action"] == "step_approved":
            d = e["details"]
            st.steps_approved[int(d.get("step", 0))] = f"{d.get('approver', '')} ({d.get('change', '')})"
    return st


def problem_for(error_code: str, log: list[dict]) -> str:
    for e in reversed(log):
        if e["action"] == "problem_created" and e["details"].get("error_code") == error_code:
            return e["outcome"]
    return ""


def new_problem_id(log: list[dict]) -> str:
    return f"PRB{sum(e['action'] == 'problem_created' for e in log) + 1:07d}"


# --- Queue ---------------------------------------------------------------------------

@dataclass
class QueueGroup:
    key: str
    parent: dict
    children: list[dict]
    sla: Sla

    @property
    def size(self) -> int:
        return 1 + len(self.children)


def build_queue(incidents: list[dict], now: datetime, log: list[dict]) -> list[QueueGroup]:
    """Sort by breach risk then severity, and collapse repeats (same system + error code) under one parent."""
    resolved = {e["incident"] for e in log if e["action"] == "resolve"} - \
               {e["incident"] for e in log if e["action"] == "reopen"}

    def sort_key(i: dict):
        s = sla_for(i, now)
        urgency = abs(s.remaining.total_seconds()) if s.remaining is not None else 0
        return (s.tier, SEV_RANK.get(i.get("priority", "")[:2], 9), urgency)

    live = sorted((i for i in incidents if i["number"] not in resolved), key=sort_key)
    groups: dict[str, QueueGroup] = {}
    for i in live:
        code = i.get("error_code") or ""
        key = f"{i['application']}|{code}" if code else i["number"]
        if key in groups:
            groups[key].children.append(i)
        else:
            groups[key] = QueueGroup(key, i, [], sla_for(i, now))
    return list(groups.values())


# --- Why did it match? ---------------------------------------------------------------

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{3,}")
_STOP = {"with", "from", "that", "this", "when", "into", "after", "while", "system", "error", "issue",
         "failed", "failure", "incident", "during", "between", "calling"}


MATCH, PARTIAL, DIFFERS = "match", "partial", "differs"


def match_reasons(query: str, incident: dict, match) -> list[tuple[str, str]]:
    """(kind, text) reasons a past pattern matched, and what differs. kind is match / partial / differs."""
    i = match.incident
    reasons: list[tuple[str, str]] = []
    code = incident.get("error_code") or ""
    if i.error_code and i.error_code.lower() in query.lower():
        reasons.append((MATCH, f"Same error code **{i.error_code}**"))
    elif code and i.error_code:
        reasons.append((DIFFERS, f"Different error code: {code} here, {i.error_code} in the past fix"))
    else:
        reasons.append((DIFFERS, "No shared error code"))

    for label, mine, theirs in (("component (CI)", incident.get("component"), i.component),
                                ("business area", incident.get("business_area"), i.business_area)):
        if mine and theirs:
            if mine.lower() == theirs.lower():
                reasons.append((MATCH, f"Same {label} **{theirs}**"))
            else:
                reasons.append((PARTIAL, f"Different {label}: {mine} here, {theirs} before"))

    q = Counter(t.lower() for t in _TOKEN.findall(query))
    shared = [t for t in dict.fromkeys(t.lower() for t in _TOKEN.findall(i.search_text))
              if t in q and t not in _STOP]
    if shared:
        kind = MATCH if match.score >= config.HIGH_CONFIDENCE else PARTIAL
        reasons.append((kind, f"Text similarity {match.score:.2f} · shared terms "
                              + ", ".join(f"`{t}`" for t in shared[:5])))
    else:
        reasons.append((DIFFERS, f"Text similarity only {match.score:.2f}"))
    if i.occurrences > 1:
        reasons.append((MATCH, f"Same fix worked **{i.occurrences}×** ({i.first_seen} to {i.resolved})"))
    return reasons


# --- Timeline --------------------------------------------------------------------------

_EVENT_TEXT = {
    "match": lambda e: f"Copilot matched: {e['outcome']}" + (f" · {', '.join(e['sources'][:2])}" if e["sources"] else ""),
    "recommendation": lambda e: f"Copilot recommendation ({e['outcome']} confidence, {e['details'].get('steps', 0)} steps)",
    "no_match": lambda e: "Copilot: no known fix found",
    "acknowledge": lambda e: "Acknowledged",
    "step_run": lambda e: f"Step {e['details'].get('step')} done by engineer",
    "step_approved": lambda e: f"Step {e['details'].get('step')} approved by {e['details'].get('approver')} "
                               f"({e['details'].get('change')})",
    "problem_created": lambda e: f"Problem record {e['outcome']} created",
    "escalate": lambda e: f"Escalated to {e['details'].get('to', '')}: {e['outcome']}",
    "resolve": lambda e: "Resolved",
    "reopen": lambda e: "Reopened",
    "update_sent": lambda e: f"Update sent ({e['details'].get('audience', '')}): {e['details'].get('subject', '')}",
    "accept": lambda e: "Recommendation accepted",
    "edit": lambda e: f"Recommendation edited: {e['outcome']}",
    "reject": lambda e: f"Recommendation rejected: {e['outcome']}",
    "note_saved": lambda e: "Resolution notes saved",
    "suspicious_input": lambda e: "Instruction-like text in ticket ignored",
    "agent_search": lambda e: "Agent search",
}


def event_label(e: dict) -> str:
    fn = _EVENT_TEXT.get(e["action"])
    return fn(e) if fn else ""


def timeline(incident: dict, events: list[dict]) -> list[tuple[str, str, str]]:
    """(time, actor, text) rows: the ticket's own timestamps plus every logged copilot/engineer event."""
    rows = []
    if incident.get("opened"):
        rows.append((incident["opened"], "ITSM", "Opened"))
    seen_copilot: set[str] = set()
    for e in events:
        text = event_label(e)
        is_copilot = e["action"] in ("match", "recommendation", "no_match", "suspicious_input")
        if is_copilot and text in seen_copilot:
            continue                    # re-running the copilot on the same result adds no new information
        if is_copilot:
            seen_copilot.add(text)
        if text:
            actor = "Copilot" if e["action"] in ("match", "recommendation", "no_match", "suspicious_input") \
                else config.USERS.get(e["user"], {}).get("name", e["user"])
            rows.append((e["ts"].replace("T", " ")[:16] + " UTC", actor, text))
    if incident.get("status") in ("Escalated",) and not any(e["action"] == "escalate" for e in events):
        rows.insert(1, ("time not recorded", "ITSM", "Escalated in the ITSM tool"))
    return rows


# --- Trend of this error pattern (real data, replaces the prototype's monitoring sparkline) ---

def daily_counts(all_incidents: list[dict], system: str, error_code: str, now: datetime,
                 days: int = 30) -> list[dict]:
    """Incidents opened per day with this system + error code over the last `days` days."""
    start = (now - timedelta(days=days - 1)).date()
    counts = Counter()
    for i in all_incidents:
        if i["application"] == system and i.get("error_code") == error_code:
            d = _parse(i.get("opened", ""))
            if d and start <= d.date() <= now.date():
                counts[d.date()] += 1
    return [{"Day": start + timedelta(days=k), "Incidents": counts.get(start + timedelta(days=k), 0)}
            for k in range(days)]
