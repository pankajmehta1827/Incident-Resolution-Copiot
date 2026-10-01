"""ServiceNow Table API connector: read incidents, write engineer actions back.

Used when INCIDENT_SOURCE=servicenow. Works the same against the local mock
(servicenow_mock/) and a real instance: only SERVICENOW_URL / _USER / _PASSWORD change.
The copilot never executes anything on production systems; it only records what the
engineer did (state, assignee, work notes, close notes) on the incident, as the ITSM
screen would.
"""
from __future__ import annotations

import httpx

from . import config

FIELDS = ("sys_id,number,short_description,priority,state,escalation,category,subcategory,cmdb_ci,"
          "assignment_group,assigned_to,opened_at,resolved_at,close_notes,u_closing_notes,u_rca,"
          "u_error_code,u_business_area,sys_updated_on")
PAGE = 1000
SEVERITY = {1: "P1 - Critical", 2: "P2 - High", 3: "P3 - Medium", 4: "P4 - Low", 5: "P4 - Low"}


class ServiceNowError(RuntimeError):
    pass


def enabled() -> bool:
    return config.INCIDENT_SOURCE == "servicenow"


def _client() -> httpx.Client:
    settings = {"SERVICENOW_URL": config.SERVICENOW_URL, "SERVICENOW_USER": config.SERVICENOW_USER,
                "SERVICENOW_PASSWORD": config.SERVICENOW_PASSWORD}
    missing = [name for name, value in settings.items() if not value.strip()]
    if missing:   # name the exact variables, so a deployment can be fixed without guessing
        raise ServiceNowError(f"These settings are empty or missing: {', '.join(missing)}. "
                              f"Set them as plain values (not references) and redeploy.")
    return httpx.Client(base_url=config.SERVICENOW_URL.rstrip("/"), timeout=30,
                        auth=(config.SERVICENOW_USER, config.SERVICENOW_PASSWORD),
                        headers={"Accept": "application/json", "Content-Type": "application/json"})


def _get(client: httpx.Client, params: dict) -> tuple[list[dict], int]:
    r = client.get("/api/now/table/incident", params=params)
    if r.status_code != 200:
        raise ServiceNowError(f"ServiceNow returned {r.status_code}: {r.text[:200]}")
    return r.json()["result"], int(r.headers.get("X-Total-Count", "0") or 0)


def fetch_incidents() -> list[dict]:
    """Every incident, paged, as raw field values."""
    out: list[dict] = []
    with _client() as c:
        offset = 0
        while True:
            rows, total = _get(c, {"sysparm_fields": FIELDS, "sysparm_limit": PAGE, "sysparm_offset": offset,
                                   "sysparm_query": "ORDERBYnumber", "sysparm_exclude_reference_link": "true"})
            out += rows
            offset += PAGE
            if not rows or offset >= total:
                return out


def latest_update() -> str:
    """Newest sys_updated_on: a cheap 'has anything changed?' check for auto-refresh."""
    try:
        with _client() as c:
            rows, total = _get(c, {"sysparm_fields": "sys_updated_on", "sysparm_limit": 1,
                                   "sysparm_query": "ORDERBYDESCsys_updated_on"})
        return f"{total}|{rows[0]['sys_updated_on'] if rows else ''}"
    except (httpx.HTTPError, ServiceNowError):
        return "unreachable"


def to_workbook_rows(records: list[dict]) -> list[dict]:
    """Map ServiceNow incidents onto the workbook's columns, so the rest of the copilot is unchanged."""
    rows = []
    for r in records:
        state = int(r.get("state") or 0)
        if state == 8:                                    # Canceled: neither open work nor a usable fix
            continue
        status = {7: "Closed", 6: "Resolved", 1: "New", 3: "On Hold"}.get(state)
        if status is None:
            status = "Escalated" if int(r.get("escalation") or 0) >= 2 else "In Progress"
        rows.append({
            "Incident ID": r["number"], "System": r.get("cmdb_ci", ""),
            "Business Area": r.get("u_business_area", ""), "Component": r.get("subcategory", ""),
            "Error Code": r.get("u_error_code", ""), "Description": r.get("short_description", ""),
            "Severity": SEVERITY.get(int(r.get("priority") or 3), "P3 - Medium"),
            "Category": r.get("category", ""), "RCA (Root Cause Analysis)": r.get("u_rca", ""),
            "Resolution Summary": r.get("close_notes", ""), "Closing Notes": r.get("u_closing_notes", ""),
            "Status": status, "Assigned To": r.get("assigned_to", ""),
            "Created Timestamp": r.get("opened_at", ""), "Resolved Timestamp": r.get("resolved_at", ""),
        })
    return rows


def update(number: str, body: dict) -> None:
    """PATCH one incident by number (looks up its sys_id first, as a real integration would)."""
    with _client() as c:
        rows, _ = _get(c, {"sysparm_query": f"number={number}", "sysparm_fields": "sys_id", "sysparm_limit": 1})
        if not rows:
            raise ServiceNowError(f"{number} not found in ServiceNow")
        r = c.patch(f"/api/now/table/incident/{rows[0]['sys_id']}", json=body)
        if r.status_code not in (200, 201):
            raise ServiceNowError(f"ServiceNow returned {r.status_code}: {r.text[:200]}")
