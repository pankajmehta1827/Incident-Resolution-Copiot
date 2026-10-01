"""A local ServiceNow-style mock: the Incident table, the Table API and a simple list/form UI.

Why: a real ServiceNow developer instance is waitlisted. This mock speaks the same Table API
(/api/now/table/incident, sysparm_query, sysparm_limit/offset/fields/display_value, basic auth,
{"result": ...} responses), uses ServiceNow field names and state/priority codes, and is seeded
from the incident workbook. The copilot connects to it exactly as it would to a real instance,
so switching to real ServiceNow later is a change of URL and credentials in .env.

Run:  python -m uvicorn servicenow_mock.server:app --port 8600
UI:   http://localhost:8600            API: http://localhost:8600/api/now/table/incident
"""
from __future__ import annotations

import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import urlencode

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

HERE = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("MOCK_SN_DB", HERE / "data" / "incident.db"))
SEED_WORKBOOK = Path(os.getenv("MOCK_SN_WORKBOOK",
                               HERE.parent / "data" / "Incident" / "5000_Enterprise_Incidents_Master.xlsx"))
# Local mock defaults. Deployed (MOCK_SN_UI_AUTH=1, set in the Dockerfile) the browser UI also needs
# these credentials, and the server refuses to start while the password is still the default.
API_USER = os.getenv("MOCK_SN_USER", "admin")
API_PASSWORD = os.getenv("MOCK_SN_PASSWORD", "admin")
UI_AUTH = os.getenv("MOCK_SN_UI_AUTH", "0") == "1"

PRIORITY = {1: "1 - Critical", 2: "2 - High", 3: "3 - Moderate", 4: "4 - Low", 5: "5 - Planning"}
STATE = {1: "New", 2: "In Progress", 3: "On Hold", 6: "Resolved", 7: "Closed", 8: "Canceled"}
ESCALATION = {0: "Normal", 1: "Moderate", 2: "High", 3: "Overdue"}
CHOICES = {"priority": PRIORITY, "state": STATE, "escalation": ESCALATION, "impact": PRIORITY, "urgency": PRIORITY}
GROUPS = {"Mainframe Order Processing System": "Mainframe Support",
          "AS400 Warehouse System": "AS400 Support",
          "Java Part Ordering System": "Java Support"}

FIELDS = ["sys_id", "number", "short_description", "description", "priority", "impact", "urgency", "state",
          "escalation", "active", "category", "subcategory", "cmdb_ci", "assignment_group", "assigned_to",
          "caller_id", "opened_at", "resolved_at", "closed_at", "close_code", "close_notes", "u_closing_notes",
          "u_rca", "u_error_code", "u_business_area", "sys_created_on", "sys_updated_on", "sys_updated_by"]
INT_FIELDS = {"priority", "impact", "urgency", "state", "escalation"}
WRITABLE = set(FIELDS) - {"sys_id", "number", "sys_created_on", "sys_updated_on", "sys_updated_by", "active"}


# --- Storage -----------------------------------------------------------------------------

@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as con:
        cols = ", ".join(f"{f} {'INTEGER' if f in INT_FIELDS else 'TEXT'}" for f in FIELDS)
        con.execute(f"CREATE TABLE IF NOT EXISTS incident ({cols}, PRIMARY KEY (sys_id))")
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_number ON incident(number)")
        con.execute("CREATE TABLE IF NOT EXISTS journal (sys_id TEXT, element TEXT, value TEXT, "
                    "created_by TEXT, created_on TEXT)")
        if con.execute("SELECT COUNT(*) FROM incident").fetchone()[0] == 0:
            _seed(con)


def _seed(con: sqlite3.Connection) -> None:
    """Load the incident workbook, mapped onto ServiceNow fields and codes."""
    import pandas as pd

    df = pd.read_excel(SEED_WORKBOOK, sheet_name="Incident Records", dtype=str).fillna("")
    state_of = {"Closed": 7, "Resolved": 6, "In Progress": 2, "Escalated": 2, "New": 1, "On Hold": 3}
    rows = []
    for _, r in df.iterrows():
        prio = int(r["Severity"][1]) if r["Severity"][:1] == "P" and r["Severity"][1:2].isdigit() else 3
        state = state_of.get(r["Status"], 2)
        opened = r["Created Timestamp"][:19]
        resolved = r["Resolved Timestamp"][:19] if state in (6, 7) else ""
        rows.append({
            "sys_id": uuid.uuid5(uuid.NAMESPACE_URL, f"incident/{r['Incident ID']}").hex,
            "number": r["Incident ID"], "short_description": r["Description"],
            "description": f"{r['Description']}\nComponent: {r['Component']}. Business area: {r['Business Area']}.",
            "priority": prio, "impact": min(prio, 3), "urgency": min(prio, 3), "state": state,
            "escalation": 2 if r["Status"] == "Escalated" else 0, "active": "false" if state in (6, 7, 8) else "true",
            "category": r["Category"], "subcategory": r["Component"], "cmdb_ci": r["System"],
            "assignment_group": GROUPS.get(r["System"], "Service Desk"), "assigned_to": r["Assigned To"],
            "caller_id": "Monitoring", "opened_at": opened, "resolved_at": resolved,
            "closed_at": resolved if state == 7 else "",
            "close_code": "Solved (Permanently)" if state in (6, 7) else "",
            "close_notes": r["Resolution Summary"] if state in (6, 7) else "",
            "u_closing_notes": r["Closing Notes"] if state in (6, 7) else "",
            "u_rca": r["RCA (Root Cause Analysis)"] if state in (6, 7) else "",
            "u_error_code": r["Error Code"], "u_business_area": r["Business Area"],
            "sys_created_on": opened, "sys_updated_on": resolved or opened, "sys_updated_by": "import",
        })
    con.executemany(f"INSERT INTO incident ({', '.join(FIELDS)}) VALUES ({', '.join(':' + f for f in FIELDS)})", rows)


# --- Encoded queries (sysparm_query) --------------------------------------------------------

_OPS = [("ISNOTEMPTY", "!= ''"), ("ISEMPTY", "= ''"), ("NOT LIKE", "NOT LIKE"), ("STARTSWITH", "LIKE"),
        ("ENDSWITH", "LIKE"), ("LIKE", "LIKE"), ("NOT IN", "NOT IN"), ("IN", "IN"), ("!=", "!="),
        (">=", ">="), ("<=", "<="), (">", ">"), ("<", "<"), ("=", "=")]


def parse_query(q: str) -> tuple[str, list, str]:
    """Translate a ServiceNow encoded query into SQL. Supports ^ (AND), ^OR, the common operators,
    ORDERBY / ORDERBYDESC. Field names are checked against the table, values are parameterised."""
    if not q:
        return "1=1", [], "number"
    where_groups: list[list[str]] = []
    params: list = []
    order = []
    for raw in q.split("^"):
        if not raw:
            continue
        term, is_or = (raw[2:], True) if raw.startswith("OR") and not raw.startswith("ORDERBY") else (raw, False)
        if term.startswith("ORDERBYDESC"):
            field = term[len("ORDERBYDESC"):]
            if field in FIELDS:
                order.append(f"{field} DESC")
            continue
        if term.startswith("ORDERBY"):
            field = term[len("ORDERBY"):]
            if field in FIELDS:
                order.append(f"{field} ASC")
            continue
        for op, sql_op in _OPS:
            m = re.match(rf"^([a-z_0-9]+){re.escape(op)}(.*)$", term)
            if not m:
                continue
            field, value = m.group(1), m.group(2)
            if field not in FIELDS:
                raise HTTPException(400, f"Invalid field: {field}")
            if op in ("ISEMPTY", "ISNOTEMPTY"):
                clause = f"COALESCE({field}, '') {sql_op}"
            elif op in ("IN", "NOT IN"):
                vals = value.split(",")
                clause = f"{field} {sql_op} ({', '.join('?' * len(vals))})"
                params.extend(vals)
            else:
                if op == "LIKE" or op == "NOT LIKE":
                    value = f"%{value}%"
                elif op == "STARTSWITH":
                    value = f"{value}%"
                elif op == "ENDSWITH":
                    value = f"%{value}"
                clause = f"{field} {sql_op} ?"
                params.append(value)
            if is_or and where_groups:
                where_groups[-1].append(clause)
            else:
                where_groups.append([clause])
            break
        else:
            raise HTTPException(400, f"Unsupported query term: {term}")
    where = " AND ".join("(" + " OR ".join(g) + ")" for g in where_groups) or "1=1"
    return where, params, ", ".join(order) or "number"


# --- Records ------------------------------------------------------------------------------

def _journal(con, sys_id: str, element: str = "work_notes") -> str:
    rows = con.execute("SELECT * FROM journal WHERE sys_id=? AND element=? ORDER BY created_on DESC",
                       (sys_id, element)).fetchall()
    label = "Work notes" if element == "work_notes" else "Additional comments"
    return "\n\n".join(f"{r['created_on']} - {r['created_by']} ({label})\n{r['value']}" for r in rows)


def _render(con, row: sqlite3.Row, fields: list[str] | None, display: str) -> dict:
    out = {}
    for f in (fields or FIELDS + ["work_notes", "comments"]):
        if f in ("work_notes", "comments"):
            out[f] = _journal(con, row["sys_id"], f)
            continue
        if f not in row.keys():
            continue
        value = row[f]
        raw = "" if value is None else str(value)
        if f in CHOICES:
            label = CHOICES[f].get(int(value), raw) if raw else ""
            out[f] = {"true": label, "false": raw, "all": {"display_value": label, "value": raw}}.get(display, raw)
        else:
            out[f] = raw if display != "all" else {"display_value": raw, "value": raw}
    return out


def _apply(con, sys_id: str, body: dict, user: str) -> None:
    current = con.execute("SELECT * FROM incident WHERE sys_id=?", (sys_id,)).fetchone()
    updates = {}
    for k, v in body.items():
        if k in ("work_notes", "comments"):
            if str(v).strip():
                con.execute("INSERT INTO journal VALUES (?,?,?,?,?)", (sys_id, k, str(v).strip(), user, _now()))
        elif k in WRITABLE:
            updates[k] = int(v) if k in INT_FIELDS and str(v).strip() else v
        else:
            raise HTTPException(400, f"Field is not writable: {k}")
    if "state" in updates:
        state = updates["state"]
        updates["active"] = "false" if state in (6, 7, 8) else "true"
        if state in (6, 7) and not (current["resolved_at"] or updates.get("resolved_at")):
            updates["resolved_at"] = _now()
        if state == 7 and not current["closed_at"]:
            updates["closed_at"] = _now()
        if state not in (6, 7, 8):
            updates.setdefault("resolved_at", "")
    updates["sys_updated_on"] = _now()
    updates["sys_updated_by"] = user
    con.execute(f"UPDATE incident SET {', '.join(f'{k}=?' for k in updates)} WHERE sys_id=?",
                [*updates.values(), sys_id])


# --- App ----------------------------------------------------------------------------------

app = FastAPI(title="ServiceNow mock", docs_url="/api/docs", openapi_url="/api/openapi.json")
security = HTTPBasic(auto_error=True)
optional_security = HTTPBasic(auto_error=False)


@app.on_event("startup")
def _startup() -> None:
    if UI_AUTH and API_PASSWORD == "admin":
        raise RuntimeError("Set MOCK_SN_PASSWORD (and MOCK_SN_USER) before deploying the mock: "
                           "the default password 'admin' is only allowed for local use.")
    init_db()


@app.get("/health", include_in_schema=False)
@app.get("/_stcore/health", include_in_schema=False)   # Railway applies the repo's railway.json health check
def health() -> dict:                                  # (the copilot's path) to every service from this repo
    return {"status": "ok"}


def _check(creds: HTTPBasicCredentials | None) -> str:
    ok = creds is not None and secrets.compare_digest(creds.username, API_USER) \
        and secrets.compare_digest(creds.password, API_PASSWORD)
    if not ok:
        raise HTTPException(401, "User Not Authenticated", headers={"WWW-Authenticate": "Basic"})
    return creds.username


def api_user(creds: HTTPBasicCredentials = Depends(security)) -> str:
    return _check(creds)


def ui_user(creds: HTTPBasicCredentials | None = Depends(optional_security)) -> str:
    """Locally the browser UI is open (like being signed in). Deployed (MOCK_SN_UI_AUTH=1) it asks for
    the same credentials as the API. The API always needs basic auth."""
    return _check(creds) if UI_AUTH else "ui.user"


@app.get("/api/now/table/incident")
def list_incidents(request: Request, user: str = Depends(api_user)):
    p = request.query_params
    where, params, order = parse_query(p.get("sysparm_query", ""))
    limit = min(int(p.get("sysparm_limit", 10000)), 10000)
    offset = int(p.get("sysparm_offset", 0))
    fields = [f for f in p.get("sysparm_fields", "").split(",") if f] or None
    display = p.get("sysparm_display_value", "false").lower()
    with db() as con:
        total = con.execute(f"SELECT COUNT(*) FROM incident WHERE {where}", params).fetchone()[0]
        rows = con.execute(f"SELECT * FROM incident WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                           [*params, limit, offset]).fetchall()
        result = [_render(con, r, fields, display) for r in rows]
    return JSONResponse({"result": result}, headers={"X-Total-Count": str(total)})


def _get_row(con, sys_id: str):
    row = con.execute("SELECT * FROM incident WHERE sys_id=?", (sys_id,)).fetchone()
    if not row:
        raise HTTPException(404, {"message": "No Record found", "detail": "Record doesn't exist"})
    return row


@app.get("/api/now/table/incident/{sys_id}")
def get_incident(sys_id: str, request: Request, user: str = Depends(api_user)):
    p = request.query_params
    fields = [f for f in p.get("sysparm_fields", "").split(",") if f] or None
    with db() as con:
        return {"result": _render(con, _get_row(con, sys_id), fields, p.get("sysparm_display_value", "false"))}


@app.patch("/api/now/table/incident/{sys_id}")
@app.put("/api/now/table/incident/{sys_id}")
async def update_incident(sys_id: str, request: Request, user: str = Depends(api_user)):
    body = await request.json()
    with db() as con:
        _get_row(con, sys_id)
        _apply(con, sys_id, body, user)
        return {"result": _render(con, _get_row(con, sys_id), None, "false")}


@app.post("/api/now/table/incident", status_code=201)
async def create_incident(request: Request, user: str = Depends(api_user)):
    body = await request.json()
    with db() as con:
        last = con.execute("SELECT MAX(CAST(SUBSTR(number, 4) AS INTEGER)) FROM incident").fetchone()[0] or 0
        sys_id = uuid.uuid4().hex
        number = f"INC{last + 1:07d}"
        now = _now()
        con.execute("INSERT INTO incident (sys_id, number, state, priority, impact, urgency, escalation, active, "
                    "opened_at, sys_created_on, sys_updated_on, sys_updated_by) "
                    "VALUES (?,?,1,3,2,2,0,'true',?,?,?,?)", (sys_id, number, now, now, now, user))
        _apply(con, sys_id, body, user)
        return {"result": _render(con, _get_row(con, sys_id), None, "false")}


# --- UI: incident list and form ------------------------------------------------------------

_CSS = """
body{margin:0;font-family:'Segoe UI',Arial,sans-serif;font-size:13px;background:#f5f7f8;color:#1e2c32}
.top{background:#293e40;color:#fff;padding:10px 18px;display:flex;align-items:center;gap:14px}
.top b{font-size:16px}.top .sub{color:#b7c9cc}
.bar{background:#fff;border-bottom:1px solid #d9e1e3;padding:10px 18px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.bar input,.bar select{padding:5px 8px;border:1px solid #b9c5c8;border-radius:3px;font-size:13px}
.bar button,.btn{background:#278efc;color:#fff;border:0;border-radius:3px;padding:6px 12px;cursor:pointer;font-size:13px;text-decoration:none}
.btn.grey{background:#e1e6e8;color:#1e2c32}
table{border-collapse:collapse;width:100%;background:#fff}
th{background:#eef2f3;text-align:left;padding:7px 10px;border-bottom:1px solid #d9e1e3;font-weight:600;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid #eef2f3;vertical-align:top}
tr:hover td{background:#f3f8fe} a{color:#0c63b8;text-decoration:none} a:hover{text-decoration:underline}
.wrap{padding:14px 18px}.pager{display:flex;gap:10px;align-items:center;padding:10px 0}
.p1{color:#c0392b;font-weight:600}.p2{color:#d35400;font-weight:600}
.form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px 28px;background:#fff;padding:16px;border:1px solid #d9e1e3}
.form label{display:block;color:#5b6b70;font-size:12px}.form div.v{padding:5px 0;border-bottom:1px solid #eef2f3;min-height:18px}
.full{grid-column:1/-1}.note{background:#fff;border:1px solid #d9e1e3;border-left:4px solid #f5b700;padding:10px;margin:8px 0;white-space:pre-wrap}
.note .h{color:#5b6b70;font-size:12px;margin-bottom:4px}
textarea{width:100%;min-height:70px;padding:6px;border:1px solid #b9c5c8;border-radius:3px;font-family:inherit}
.tag{display:inline-block;padding:1px 6px;border-radius:3px;background:#e8f0fe;color:#0c63b8;font-size:12px}
"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' "
                        f"content='width=device-width,initial-scale=1'><title>{escape(title)}</title>"
                        f"<style>{_CSS}</style></head><body><div class='top'><b>servicenow</b>"
                        f"<span class='sub'>mock instance · Incident</span></div>{body}</body></html>")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/incident_list.do")


@app.get("/incident_list.do", response_class=HTMLResponse, include_in_schema=False)
def ui_list(request: Request, q: str = "", state: str = "active", priority: str = "", group: str = "",
            page: int = 1, _: str = Depends(ui_user)):
    clauses, params = [], []
    if state == "active":
        clauses.append("active='true'")
    elif state.isdigit():
        clauses.append("state=?"), params.append(int(state))
    if priority.isdigit():
        clauses.append("priority=?"), params.append(int(priority))
    if group:
        clauses.append("assignment_group=?"), params.append(group)
    if q:
        clauses.append("(number LIKE ? OR short_description LIKE ? OR u_error_code LIKE ? OR cmdb_ci LIKE ?)")
        params += [f"%{q}%"] * 4
    where = " AND ".join(clauses) or "1=1"
    size = 50
    with db() as con:
        total = con.execute(f"SELECT COUNT(*) FROM incident WHERE {where}", params).fetchone()[0]
        rows = con.execute(f"SELECT * FROM incident WHERE {where} ORDER BY priority, opened_at DESC LIMIT ? OFFSET ?",
                           [*params, size, (page - 1) * size]).fetchall()
        groups = [g[0] for g in con.execute("SELECT DISTINCT assignment_group FROM incident ORDER BY 1")]

    def opt(value, label, current):
        return f"<option value='{escape(str(value))}'{' selected' if str(current) == str(value) else ''}>{escape(label)}</option>"

    states = opt("active", "Active", state) + opt("all", "All", state) + "".join(opt(k, v, state) for k, v in STATE.items())
    prios = opt("", "Any priority", priority) + "".join(opt(k, v, priority) for k, v in PRIORITY.items() if k < 5)
    grps = opt("", "Any group", group) + "".join(opt(g, g, group) for g in groups)
    filt = (f"<form class='bar' method='get'><b>Incidents</b>"
            f"<input name='q' placeholder='Search number, description, error code, CI' value='{escape(q)}' size='38'>"
            f"<select name='state'>{states}</select><select name='priority'>{prios}</select>"
            f"<select name='group'>{grps}</select><button>Search</button>"
            f"<span style='margin-left:auto;color:#5b6b70'>{total:,} records</span></form>")
    head = "".join(f"<th>{h}</th>" for h in ("Number", "Opened", "Short description", "Priority", "State",
                                             "Escalation", "Configuration item", "Assignment group", "Assigned to",
                                             "Updated"))
    body_rows = "".join(
        f"<tr><td><a href='/incident.do?sys_id={r['sys_id']}'>{escape(r['number'])}</a></td>"
        f"<td>{escape(r['opened_at'] or '')}</td><td>{escape(r['short_description'] or '')}</td>"
        f"<td class='p{r['priority']}'>{PRIORITY.get(r['priority'], '')}</td><td>{STATE.get(r['state'], '')}</td>"
        f"<td>{ESCALATION.get(r['escalation'], '')}</td><td>{escape(r['cmdb_ci'] or '')}</td>"
        f"<td>{escape(r['assignment_group'] or '')}</td><td>{escape(r['assigned_to'] or '')}</td>"
        f"<td>{escape(r['sys_updated_on'] or '')}</td></tr>" for r in rows)
    pages = max(1, (total + size - 1) // size)
    base = {"q": q, "state": state, "priority": priority, "group": group}
    nav = (f"<div class='pager'>"
           + (f"<a class='btn grey' href='?{urlencode({**base, 'page': page - 1})}'>‹ Previous</a>" if page > 1 else "")
           + f"<span>Page {page} of {pages}</span>"
           + (f"<a class='btn grey' href='?{urlencode({**base, 'page': page + 1})}'>Next ›</a>" if page < pages else "")
           + "</div>")
    return _page("Incidents", f"{filt}<div class='wrap'><table><tr>{head}</tr>{body_rows}</table>{nav}</div>")


@app.get("/incident.do", response_class=HTMLResponse, include_in_schema=False)
def ui_form(sys_id: str, _: str = Depends(ui_user)):
    with db() as con:
        r = _get_row(con, sys_id)
        notes = con.execute("SELECT * FROM journal WHERE sys_id=? ORDER BY created_on DESC", (sys_id,)).fetchall()

    def field(label, value, full=False):
        return (f"<div class='{'full' if full else ''}'><label>{label}</label>"
                f"<div class='v'>{escape(str(value or ''))}</div></div>")

    fields = "".join([
        field("Number", r["number"]), field("Opened", r["opened_at"]),
        field("Configuration item", r["cmdb_ci"]), field("State", STATE.get(r["state"])),
        field("Category", r["category"]), field("Priority", PRIORITY.get(r["priority"])),
        field("Subcategory (component)", r["subcategory"]), field("Escalation", ESCALATION.get(r["escalation"])),
        field("Error code", r["u_error_code"]), field("Assignment group", r["assignment_group"]),
        field("Business area", r["u_business_area"]), field("Assigned to", r["assigned_to"]),
        field("Short description", r["short_description"], True),
        field("Description", r["description"], True),
        field("Root cause", r["u_rca"], True), field("Close code", r["close_code"]),
        field("Resolved", r["resolved_at"]), field("Close notes", r["close_notes"], True),
        field("Closing notes", r["u_closing_notes"], True),
    ])
    states = "".join(f"<option value='{k}'{' selected' if k == r['state'] else ''}>{v}</option>" for k, v in STATE.items())
    journal = "".join(
        f"<div class='note'><div class='h'>{escape(n['created_on'])} · {escape(n['created_by'])} · "
        f"<span class='tag'>{'Work notes' if n['element'] == 'work_notes' else 'Comments'}</span></div>"
        f"{escape(n['value'])}</div>" for n in notes) or "<p style='color:#5b6b70'>No work notes yet.</p>"
    body = (f"<div class='bar'><a class='btn grey' href='/incident_list.do'>‹ Incidents</a>"
            f"<b>{escape(r['number'])}</b><span>{escape(r['short_description'] or '')}</span>"
            f"<span style='margin-left:auto;color:#5b6b70'>Updated {escape(r['sys_updated_on'] or '')} by "
            f"{escape(r['sys_updated_by'] or '')}</span></div>"
            f"<div class='wrap'><div class='form'>{fields}</div>"
            f"<h3>Activity</h3><form method='post' action='/incident.do?sys_id={sys_id}'>"
            f"<textarea name='work_notes' placeholder='Add a work note'></textarea>"
            f"<div style='display:flex;gap:8px;margin:6px 0 12px'><select name='state'>{states}</select>"
            f"<button class='btn'>Update</button></div></form>{journal}</div>")
    return _page(r["number"], body)


@app.post("/incident.do", include_in_schema=False)
def ui_update(sys_id: str, work_notes: str = Form(""), state: int = Form(...), user: str = Depends(ui_user)):
    with db() as con:
        _get_row(con, sys_id)
        _apply(con, sys_id, {"work_notes": work_notes, "state": state}, user)
    return RedirectResponse(f"/incident.do?sys_id={sys_id}", status_code=303)
