"""Knowledge-gap agent: find what the runbooks are missing and draft the fix for review.

1. find_gaps(): no AI. Recurring resolved errors with no runbook entry ("missing"), and runbook
   entries that leave out much of what engineers actually did ("incomplete").
2. draft(): a read-only tool-calling agent reads the past resolutions, root causes, copilot
   closure notes and the current runbook for one error code, then drafts a runbook section in the
   runbook's own format. Every step must cite a source it read; uncited steps and commands that
   appear in no source are removed.
3. Drafts are saved as pending. A Knowledge Manager or Team Lead edits, then approves (the article
   is written to APPROVED_KB_DIR and indexed on the next refresh) or rejects with a reason.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime

from . import audit, config
from .agent import _grounded_check, _parse_final, _summary, _Tools
from .engine import _NUMBER, _value_grounded
from .llm import LLMUnavailable, chat_with_tools
from .masking import mask

MAX_TOOL_CALLS = 5
_STOP = {"with", "from", "that", "this", "were", "have", "been", "into", "after", "before", "their", "there",
         "which", "using", "used", "successfully", "service", "system", "issue", "error", "incident", "resolved",
         "operations", "across", "again", "team", "user", "users", "back", "then", "also", "once"}


# --- 1. Gap detection ------------------------------------------------------------------

@dataclass
class Gap:
    error_code: str
    system: str
    kind: str                # "missing" or "incomplete"
    occurrences: int
    latest: str
    description: str
    detail: str
    runbook_doc: str = ""


_GENERIC = {"perform", "provid", "issu", "execut", "updat", "guid", "educat", "add", "ensur", "check", "handl",
             "verifi", "confirm", "complet", "restor", "resum", "process", "manag", "applic", "connect",
             "successful", "notifi", "answer", "pend", "messag", "problem", "request", "work", "time", "step"}


def _stem(word: str) -> str:
    w = word.lower().strip(".,;:()[]'\"")
    for suffix in ("ing", "ed", "es", "s", "e"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            return w[: -len(suffix)]
    return w


def _terms(text: str) -> list[str]:
    """Distinctive word stems: no stop words, no generic verbs, punctuation stripped."""
    stems = (_stem(w) for w in re.findall(r"[A-Za-z][A-Za-z0-9_/.-]{3,}", text))
    return [s for s in dict.fromkeys(stems) if s not in _STOP and s not in _GENERIC and len(s) >= 4]


def closure_notes_by_code(kb) -> dict[str, list[dict]]:
    code_of = {i["number"]: i.get("error_code") for i in kb.all_incidents}
    out: dict[str, list[dict]] = {}
    for n in audit.work_notes():
        code = code_of.get(n["incident"])
        if code:
            out.setdefault(code, []).append(n)
    return out


def find_gaps(kb) -> list[Gap]:
    notes = closure_notes_by_code(kb)
    gaps: list[Gap] = []
    for h in kb.index.history:
        if not h.error_code:
            continue
        practice = h.resolution_notes + " " + " ".join(
            " ".join(n["note"].get("steps_taken", [])) for n in notes.get(h.error_code, []))
        if not h.kb_used:
            gaps.append(Gap(h.error_code, h.application, "missing", h.occurrences, h.number, h.short_description,
                            f"No runbook entry. Engineers fixed it {h.occurrences} times without one."))
            continue
        doc = kb.index.documents.get(h.kb_used)
        runbook = " ".join(f"{s.heading} {s.text}" for s in (doc.sections if doc else [])
                           if h.error_code in s.heading or h.error_code in s.text)
        covered = set(_terms(runbook))
        terms = _terms(practice)
        missing = [t for t in terms if t not in covered and not any(c.startswith(t) or t.startswith(c)
                                                                    for c in covered if len(c) >= 5)]
        if terms and len(missing) >= 4 and len(missing) / len(terms) >= 0.45:
            gaps.append(Gap(h.error_code, h.application, "incomplete", h.occurrences, h.number,
                            h.short_description,
                            f"Runbook omits what engineers did: {', '.join(missing[:6])}", runbook_doc=h.kb_used))
    return sorted(gaps, key=lambda g: (g.kind != "missing", -g.occurrences))


# --- 2. Drafting agent ------------------------------------------------------------------

SYSTEM_PROMPT = f"""You are the knowledge-gap agent for an application support team.
Draft ONE runbook section for a recurring error, from how engineers actually fixed it.

How to work:
1. get_pattern for the error code: root cause, the fix that worked, closing notes, how often.
2. get_closure_notes for the error code: notes engineers saved when resolving with the copilot.
3. If the runbook already has a section for this code, search_runbooks / read_runbook_section to read it,
   and keep its correct steps (cite the section id) while adding what is missing.
4. Conclude. At most {MAX_TOOL_CALLS} tool calls.

Rules:
- Use only facts the tools returned. Never invent commands, settings, hosts, thresholds or values.
- Every step and the root cause must cite exactly one source id a tool returned (an incident number,
  a NOTE-... id, or a runbook section id).
- Write commands only if they appear in a tool result; otherwise describe the action in words.
- Steps are imperative, in order: diagnosis, fix, then verification. 3 to 7 steps.

Reply with JSON only:
{{"title": "ERR-CODE: short title",
  "change": "new|update",
  "summary": "one sentence: what this adds or changes compared with the current runbook",
  "rca": {{"text": "...", "source": "..."}},
  "steps": [{{"text": "...", "source": "..."}}],
  "verification": {{"text": "...", "source": "..."}},
  "gaps": "anything the sources did not cover"}}"""

TOOLS = [
    {"type": "function", "function": {
        "name": "get_pattern",
        "description": "The resolved incident pattern for an error code: root cause, the fix that worked, "
                       "closing notes, how often it recurred and example incident numbers.",
        "parameters": {"type": "object", "properties": {"error_code": {"type": "string"}},
                       "required": ["error_code"]}}},
    {"type": "function", "function": {
        "name": "get_closure_notes",
        "description": "Closure notes engineers saved in the copilot when resolving incidents with this error code.",
        "parameters": {"type": "object", "properties": {"error_code": {"type": "string"}},
                       "required": ["error_code"]}}},
    {"type": "function", "function": {
        "name": "search_runbooks",
        "description": "Search runbook sections. Returns section ids, titles and excerpts.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}, "system": {"type": "string"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_runbook_section",
        "description": "Read one runbook section in full by its id.",
        "parameters": {"type": "object", "properties": {"section_id": {"type": "string"}},
                       "required": ["section_id"]}}},
    {"type": "function", "function": {
        "name": "list_open_incidents",
        "description": "Count OPEN incidents with this error code (to judge urgency).",
        "parameters": {"type": "object", "properties": {"error_code": {"type": "string"}}}}},
]


class _KbTools(_Tools):
    def __init__(self, kb, gap: Gap, user: dict, resolved: set[str]):
        super().__init__(kb, {"number": "-", "application": gap.system}, user, resolved)
        self.notes = closure_notes_by_code(kb)

    def get_pattern(self, error_code: str) -> dict:
        code = error_code.strip().upper()
        pats = [h for h in self.kb.index.history if h.error_code.upper() == code and h.application in self.user["groups"]]
        out = []
        for h in pats:
            self.seen.update([h.number, *h.examples])
            out.append({"number": h.number, "system": h.application, "symptom": h.short_description,
                        "root_cause": h.rca, "fix_and_closing_notes": h.resolution_notes,
                        "seen_times": h.occurrences, "first_seen": str(h.first_seen), "last_resolved": str(h.resolved),
                        "component": h.component, "business_area": h.business_area, "examples": h.examples,
                        "current_runbook": h.kb_used or "none"})
        return {"patterns": out} if out else {"error": f"No resolved pattern for {code}"}

    def get_closure_notes(self, error_code: str) -> dict:
        out = []
        for n in self.notes.get(error_code.strip().upper(), [])[-5:]:
            nid = f"NOTE-{n['incident']}"
            self.seen.add(nid)
            out.append({"id": nid, "saved": n["ts"][:16], "symptom": n["note"].get("symptom", ""),
                        "root_cause": n["note"].get("root_cause", ""), "steps_taken": n["note"].get("steps_taken", []),
                        "verification": n["note"].get("verification", "")})
        return {"notes": out, "count": len(out)}


@dataclass
class Draft:
    id: str
    error_code: str
    system: str
    kind: str
    title: str = ""
    change: str = "new"
    summary: str = ""
    body_md: str = ""
    sources: list[str] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    gaps: str = ""
    created_by: str = ""
    created_at: str = ""
    status: str = "pending"          # pending | approved | rejected
    reviewed_by: str = ""
    reviewed_at: str = ""
    reason: str = ""
    error: str = ""
    seconds: float = 0.0


def _now() -> str:
    return datetime.now(config.IST).strftime("%Y-%m-%d %H:%M")


def _body(title: str, system: str, rca: dict, steps: list[dict], verification: dict) -> str:
    lines = [f"## {title}", f"System: {system}", "",
             f"Root Cause Analysis (RCA): {rca.get('text', '')} (source: {rca.get('source', '')})", "",
             "Resolution Procedure:"]
    lines += [f"{n}. {s['text']} (source: {s['source']})" for n, s in enumerate(steps, 1)]
    if verification.get("text"):
        lines += ["", f"Verification: {verification['text']} (source: {verification.get('source', '')})"]
    return "\n".join(lines)


def draft(kb, gap: Gap, user_id: str, resolved: set[str], model: str = config.DEFAULT_MODEL,
          on_step=None) -> Draft:
    t0 = time.perf_counter()
    user = config.USERS[user_id]
    d = Draft(id=uuid.uuid4().hex[:10], error_code=gap.error_code, system=gap.system, kind=gap.kind,
              created_by=user_id, created_at=_now())
    tools = _KbTools(kb, gap, user, resolved)
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Error code: {gap.error_code}\nSystem: {gap.system}\nGap: {gap.kind} - "
                                            f"{gap.detail}\nCurrent runbook document: {gap.runbook_doc or 'none'}\n"
                                            "Draft the runbook section."}]
    calls_made, final = 0, ""
    try:
        while True:
            budget = MAX_TOOL_CALLS - calls_made
            msg, _ = chat_with_tools(model, messages, TOOLS if budget > 0 else None, max_tokens=1800)
            calls = getattr(msg, "tool_calls", None) or []
            if not calls or budget <= 0:
                final = msg.content or ""
                break
            messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
                {"id": c.id, "type": "function", "function": {"name": c.function.name,
                                                              "arguments": c.function.arguments or "{}"}} for c in calls]})
            for c in calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                if calls_made >= MAX_TOOL_CALLS:
                    result = {"error": "Tool budget used up; conclude now."}
                else:
                    result = tools.run(c.function.name, args)
                    calls_made += 1
                    step = {"tool": c.function.name, "args": args,
                            "summary": _kb_summary(c.function.name, result)}
                    d.trace.append(step)
                    if on_step:
                        on_step(step)
                messages.append({"role": "tool", "tool_call_id": c.id,
                                 "content": json.dumps(result, ensure_ascii=False)[:6000]})
            if calls_made >= MAX_TOOL_CALLS:
                messages.append({"role": "user", "content": "Tool budget used up. Reply now with the final JSON only."})
    except LLMUnavailable as exc:
        d.error = f"The knowledge-gap agent is unavailable right now: {exc.reason}."
        d.seconds = time.perf_counter() - t0
        return d

    out = _parse_final(final)
    corpus = "\n".join([f"{gap.error_code} {gap.system}", *tools.corpus])

    def keep(item, label: str) -> dict | None:
        if not isinstance(item, dict) or not str(item.get("text", "")).strip():
            return None
        it = {"text": mask(str(item["text"]))[0].strip(), "source": str(item.get("source", "")).strip()}
        if it["source"] not in tools.seen:
            d.dropped.append({"claim": it["text"], "source": f"{label}: cites {it['source'] or 'nothing'}, not read"})
            return None
        if not _grounded_check(it["text"], corpus):
            d.dropped.append({"claim": it["text"], "source": f"{label}: command not found in any source"})
            return None
        # Values (limits, timeouts, durations, percentages) must come from a source too.
        for num, pct in _NUMBER.findall(re.sub(r"`[^`]+`", " ", it["text"])):
            if (float(num) >= 10 or pct) and not _value_grounded(num, corpus):
                d.dropped.append({"claim": it["text"], "source": f"{label}: value {num}{pct.strip()} not in any source"})
                return None
        return it

    steps = [s for s in (keep(x, "step") for x in (out.get("steps") or [])) if s]
    rca = keep(out.get("rca"), "root cause") or {}
    verification = keep(out.get("verification"), "verification") or {}
    d.title = mask(str(out.get("title") or f"{gap.error_code}: {gap.description}"))[0][:120]
    if gap.error_code not in d.title:
        d.title = f"{gap.error_code}: {d.title}"
    d.change = "update" if str(out.get("change", "")).lower() == "update" else "new"
    d.summary = mask(str(out.get("summary", "")))[0]
    d.gaps = mask(str(out.get("gaps", "")))[0]
    d.sources = sorted({x["source"] for x in [*steps, rca, verification] if x})
    if not steps:
        d.error = "The agent could not draft any step backed by a source it read."
    d.body_md = _body(d.title, gap.system, rca, steps, verification)
    d.seconds = time.perf_counter() - t0
    save_draft(d)
    audit.log("kb_draft_created", user_id, gap.error_code, f"{d.change} · {len(steps)} steps",
              sources=d.sources, details={"draft": d.id, "dropped": len(d.dropped), "tools": [s["tool"] for s in d.trace]})
    return d


def _kb_summary(name: str, result: dict) -> str:
    if "error" in result:
        return result["error"]
    if name == "get_pattern":
        p = result["patterns"][0]
        return f"{p['number']}: seen {p['seen_times']}×, runbook {p['current_runbook']}"
    if name == "get_closure_notes":
        return f"{result['count']} closure note(s)"
    return _summary(name, result)


# --- 3. Draft storage, review and publishing ------------------------------------------------

def save_draft(d: Draft) -> None:
    config.KB_DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    (config.KB_DRAFTS_DIR / f"{d.id}.json").write_text(json.dumps(asdict(d), ensure_ascii=False, indent=1),
                                                       encoding="utf-8")


def list_drafts() -> list[Draft]:
    if not config.KB_DRAFTS_DIR.exists():
        return []
    out = []
    for p in config.KB_DRAFTS_DIR.glob("*.json"):
        try:
            out.append(Draft(**json.loads(p.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, TypeError):
            continue
    return sorted(out, key=lambda d: d.created_at, reverse=True)


def can_approve(user_id: str) -> bool:
    return config.USERS.get(user_id, {}).get("role") in config.KB_APPROVER_ROLES


def approve(d: Draft, body_md: str, user_id: str) -> str:
    """Publish the (possibly edited) draft as a runbook article. Returns the article's doc id."""
    if not can_approve(user_id):
        raise PermissionError("Only a Knowledge Manager or Team Lead can approve runbook changes.")
    doc_id = f"KB-{d.error_code}"
    owner = config.USERS[user_id]["name"]
    front = "\n".join(["---", f"doc_id: {doc_id}", f"title: {d.title}", "type: Runbook",
                       f"application: {d.system}", "space: CONFLUENCE", f"owner: {owner}",
                       f"last_updated: {config.TODAY.isoformat()}", "status: approved",
                       f"origin: knowledge-gap agent draft {d.id}, approved by {owner}", "---", ""])
    body = mask(body_md.strip())[0]
    if not body.startswith("## "):
        body = f"## {d.title}\n{body}"
    config.APPROVED_KB_DIR.mkdir(parents=True, exist_ok=True)
    (config.APPROVED_KB_DIR / f"{doc_id}.md").write_text(front + body + "\n", encoding="utf-8")
    d.status, d.reviewed_by, d.reviewed_at, d.body_md = "approved", user_id, _now(), body
    save_draft(d)
    audit.log("kb_draft_approved", user_id, d.error_code, f"published {doc_id}", sources=d.sources,
              details={"draft": d.id})
    return doc_id


def reject(d: Draft, reason: str, user_id: str) -> None:
    if not can_approve(user_id):
        raise PermissionError("Only a Knowledge Manager or Team Lead can reject runbook changes.")
    d.status, d.reviewed_by, d.reviewed_at, d.reason = "rejected", user_id, _now(), reason.strip()
    save_draft(d)
    audit.log("kb_draft_rejected", user_id, d.error_code, reason.strip()[:200], details={"draft": d.id})
