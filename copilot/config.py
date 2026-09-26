"""Tunable settings for the copilot. Values marked (assumption) come from the PRD."""
from __future__ import annotations

import os
from datetime import date, timedelta, timezone

# All times on screen are shown in Indian Standard Time. A fixed offset (IST has no daylight
# saving) needs no tz database, so it is correct on Windows and on UTC cloud servers alike.
# Workbook timestamps have no zone and are taken to be IST already.
IST = timezone(timedelta(hours=5, minutes=30), "IST")
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
KNOWLEDGE_DIR = DATA_DIR / "knowledge"
INCIDENT_WORKBOOK = Path(os.getenv("INCIDENT_WORKBOOK", DATA_DIR / "Incident" / "5000_Enterprise_Incidents_Master.xlsx"))
INCIDENT_SHEET = "Incident Records"
RUNTIME_DIR = DATA_DIR / "runtime"
AUDIT_LOG_FILE = RUNTIME_DIR / "audit_log.jsonl"
WORK_NOTES_FILE = RUNTIME_DIR / "work_notes.jsonl"

DEFAULT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# "Today" for freshness and history windows. Overridable for demos and tests.
TODAY = date.fromisoformat(os.getenv("COPILOT_TODAY", date.today().isoformat()))

HISTORY_WINDOW_DAYS = 730      # resolved incidents from the last 24 months (assumption)
FRESHNESS_WINDOW_DAYS = 365    # sources older than this are flagged stale (assumption)
CONTRADICTION_MIN = 3          # recent resolutions marking an article outdated
PROBLEM_RECORD_MIN = 5         # same error pattern this many times ...
PROBLEM_RECORD_DAYS = 30       # ... within this many days suggests a Problem record

RESOLVED_STATES = {"Closed", "Resolved"}
OPEN_STATES = {"New", "Assigned", "In Progress", "Escalated", "On Hold", "Pending"}

# Confidence thresholds on the best similar-incident score (0..1).
HIGH_CONFIDENCE = 0.40
MIN_CONFIDENCE = 0.20
MIN_DOC_SCORE = 0.08           # a knowledge section must clear this to be used as a source

TOP_INCIDENTS = 3
TOP_SECTIONS = 5

# Second-stage LLM re-ranking of similar incidents: retrieve a wider pool, keep the top TOP_INCIDENTS.
RERANK_ENABLED = os.getenv("COPILOT_RERANK", "1").lower() not in ("0", "false", "off")
RERANK_CANDIDATES = 10

MF = "Mainframe Order Processing System"
AS400 = "AS400 Warehouse System"
JAVA = "Java Part Ordering System"
SYSTEMS = [MF, AS400, JAVA]

# Escalation path per system. The runbooks refer to an external escalation matrix,
# so these are placeholders (assumption) built from the SME roles in the incident data.
ESCALATION = {
    MF: "Mainframe SME (Sarah Jenkins), then the Mainframe escalation matrix",
    AS400: "AS400 Admin (Dave Miller), then the IBM i escalation matrix",
    JAVA: "Java Lead (John Smith) and DevOps (Elena Rostova), then the Java escalation matrix",
}

# Simulated SSO users. Access mirrors ITSM assignment groups and knowledge spaces.
ALL_SPACES = ["RUNBOOK", "AID", "CONFLUENCE"]
USERS = {
    "rajesh.kumar": {"name": "Rajesh Kumar", "role": "L2 Support", "groups": [JAVA, AS400], "spaces": ALL_SPACES},
    "sarah.jenkins": {"name": "Sarah Jenkins", "role": "Mainframe SME", "groups": [MF], "spaces": ALL_SPACES},
    "team.lead": {"name": "Support Team Lead", "role": "Team Lead", "groups": SYSTEMS, "spaces": ALL_SPACES},
    "knowledge.mgr": {"name": "Knowledge Manager", "role": "Knowledge Manager", "groups": SYSTEMS, "spaces": ALL_SPACES},
}

# Resolution SLA per severity, in hours (assumption: the workbook has no SLA targets).
SLA_HOURS = {"P1": 4, "P2": 8, "P3": 24, "P4": 72}
AT_RISK_FRACTION = 0.25        # "breach risk" when less than this share of the SLA window is left
# The SLA clock. The workbook is a snapshot, so by default the clock runs "as of" its newest
# timestamp; set COPILOT_NOW (ISO datetime) to run it against another moment, e.g. real time.
NOW_OVERRIDE = os.getenv("COPILOT_NOW", "")

# How often the screen checks for changes (audit log, workbook, runbooks) and refreshes if any.
AUTO_REFRESH_SECONDS = int(os.getenv("COPILOT_REFRESH_SECONDS", "15"))

CHANGE_PROCEDURE_URL = "https://confluence.example.com/display/CHG/Production+Change+Procedure"
