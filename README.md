# Incident Resolution Copilot

A working prototype of the Incident Resolution Copilot AI PRD. When a support engineer opens an incident, the
copilot checks whether it is a repeat of a known problem. It retrieves the relevant AID sections,
Confluence runbooks and similar resolved incidents, then shows a step-by-step fix with a citation
on every step. The engineer reviews it and decides. The copilot is read-only and never executes anything.

Stack: Streamlit, Groq (`openai/gpt-oss-120b` by default), and hybrid TF-IDF retrieval (scikit-learn).

## Run

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env          # then set GROQ_API_KEY
.venv\Scripts\streamlit run streamlit_app.py
```

If no key is set, or Groq is unreachable, the app still works. It shows similar incidents only (the
PRD's low-confidence view) and uses a notes template instead of a generated draft.

## ServiceNow (mock or real)

`servicenow_mock/` is a local stand-in for a ServiceNow instance while a real developer instance
is unavailable. It provides the same **Table API** (`/api/now/table/incident` with `sysparm_query`,
`sysparm_limit` / `offset` / `fields` / `display_value`, GET / POST / PATCH, basic auth,
`{"result": ...}` responses), ServiceNow field names and state / priority codes, and a
ServiceNow-style incident list and form. It is seeded from the incident workbook into
`servicenow_mock/data/incident.db`. Delete that file to reset it.

```bash
.venv\Scripts\pip install -r servicenow_mock\requirements.txt
.venv\Scripts\python -m uvicorn servicenow_mock.server:app --port 8600
```

- **Incident list:** http://localhost:8600 (search, filter by state, priority and group; open an
  incident to see its fields and work notes, add notes, change state).
- **API:** http://localhost:8600/api/now/table/incident, with the user `admin` and password
  `admin` (local mock only; change them with `MOCK_SN_USER` / `MOCK_SN_PASSWORD`). Interactive
  API docs are at `/api/docs`.

To make the copilot use ServiceNow instead of the workbook, add to `.env`:

```
INCIDENT_SOURCE=servicenow
SERVICENOW_URL=http://localhost:8600
SERVICENOW_USER=admin
SERVICENOW_PASSWORD=admin
```

The copilot then reads the queue and history from the Table API, and writes engineer actions back
to the incident:

| Copilot action | ServiceNow update |
|---|---|
| Acknowledge | state In Progress, assigned to the engineer, work note |
| Run step / Request approval | work note with the step (and approver and change reference) |
| Escalate | escalation High, work note with the reason |
| Mark update as sent | work note with the stakeholder update |
| Resolve | state Resolved, close code, close notes, root cause, work note |

Changes made directly in ServiceNow, such as an incident resolved there, reach the copilot on the
next auto-refresh. If ServiceNow can't be reached, the copilot keeps working, warns, and logs
`itsm_sync_failed` on the timeline. For a real instance, change only the URL and credentials. The
custom fields `u_error_code`, `u_business_area`, `u_rca` and `u_closing_notes` would need adding
to the Incident table, or mapping to existing fields, in `copilot/servicenow.py`.

## Deploy on Railway

The repo includes a `Dockerfile` (Python 3.12) and a `railway.json` (start command and a health
check on `/_stcore/health`), so Railway builds and runs it with no extra setup.

1. In Railway, choose **New Project → Deploy from GitHub repo** and pick this repository.
2. Under **Variables**, add `GROQ_API_KEY` (and optionally `GROQ_MODEL`, `GROQ_FALLBACK_MODEL`,
   `COPILOT_RERANK`).
3. Under **Volumes**, add a volume mounted at `/data`, then add the variable
   `COPILOT_RUNTIME_DIR=/data`. This keeps the audit log and work notes through redeploys and
   restarts. Without it they reset on every deploy.
4. Under **Settings → Networking**, click **Generate Domain** to get a public `*.up.railway.app` URL.

**ServiceNow mock as a second service.** The same image runs the mock when the service has the
variable `APP_ROLE=servicenow-mock` (see `start.sh`), so no separate Dockerfile setting is needed.

- **Mock service** (create it from the same repo): set `APP_ROLE=servicenow-mock`,
  `MOCK_SN_USER`, `MOCK_SN_PASSWORD` (not `admin`; the mock refuses to start with it) and
  `PORT=8600`. Add a volume at `/data`, and generate a public domain on port 8600. Its screens
  and API then ask for that username and password.
- **Copilot service:** set `INCIDENT_SOURCE=servicenow`,
  `SERVICENOW_URL=https://<mock-domain>.up.railway.app` (no port), and the same
  `SERVICENOW_USER` and `SERVICENOW_PASSWORD`.

Every push to `main` redeploys automatically. The same image runs anywhere Docker runs:

```bash
docker build -t incident-copilot .
docker run -p 8501:8501 --env-file .env incident-copilot
```

## Pages

| Page | PRD coverage |
|---|---|
| **Incident cockpit** | The four-zone layout from *Incident Copilot - Target Layout*. **Global bar:** search across incidents, CIs and error codes, *Ask agent* for free-text questions, page menu, system scope, role. **Smart queue:** sorted by SLA breach risk then severity, repeats (same system and error code) grouped under one parent with a *Create problem* prompt, filters in one menu, J / K to move. **Header:** ID, severity, status, SLA timer, owner, *Acknowledge* / *Resolve* (resolve drafts notes to edit). **Workspace tabs:** Overview (impact, CI path, last change, recent timeline), Timeline (every ITSM, copilot and engineer event), Related (similar incidents, open siblings, problem record, runbooks), Comms (copilot-drafted stakeholder update; the engineer sends it). **Copilot panel:** status and score with *why?*, summary, likely cause with evidence, steps with approval flags, actions (*Run step*, *Draft update*, *Escalate*), sources, accept / edit / reject, follow-up chat. |
| **Knowledge health** | FR-11 weekly report (stale, contradicted, missing articles) and FR-13 Problem-record suggestions |
| **Audit log** | FR-9 hash-chained log with tamper and gap detection (kill-switch check), acceptance rate, P95 latency |
| **Evals** | Offline launch gates: repeat detection, top-3 relevance, no-match honesty, access-control leaks |

## How the guardrails are implemented

The pipeline lives in [copilot/engine.py](copilot/engine.py) and follows the PRD decision flow.

- **Hallucinated fix.** Every step must cite a valid source id. Any command in backticks must appear
  token for token in a cited source, otherwise the step is dropped and listed as removed. If no steps
  survive, the view falls back to low confidence.
- **Outdated fix.** Every source shows its last-updated date, and sources older than 365 days are
  flagged. When 3 or more recent resolutions mark an article outdated, it is shown as a conflict, the
  recent fix is preferred, and the article owner is notified (logged).
- **Risky action.** High-risk steps are labelled by keyword rules plus the model's own flag. Each one
  links to the change procedure and must be acknowledged before Accept is enabled.
- **Secrets and PII.** Masked at ingestion, on incoming ticket text, and again on every model output
  ([copilot/masking.py](copilot/masking.py)). Pages that contain secrets are excluded until cleaned.
- **Restricted content.** Retrieval is filtered by the user's assignment groups and Confluence spaces.
  Restricted spaces, drafts, personal spaces and security incidents are not indexed.
- **Prompt injection.** Instruction-like text is removed before it reaches the model. The ticket is
  passed as data inside `<incident_data>` tags, and each attempt is logged as `suspicious_input`.


**Mobile layout**

When a support engineer opens the app on a phone, it switches to a mobile layout automatically. The
phone is detected from the browser's User-Agent. `?view=mobile` or `?view=desktop` in the URL
forces a layout, and the menu has a *Switch to … layout* button.

- **Queue:** full-width cards with live SLA timers, P1 count and filter chips. Tap a card to open
  the incident.
- **Incident:** a *Queue* back button, the header, a full-width SLA timer, and *Acknowledge* /
  *Resolve*. The copilot comes first (cause, steps with *Run* / *Request approval*, *Draft update*,
  *Escalate*, *Create problem*, feedback), then quick questions and chat, then Overview / Timeline /
  Related / Comms.
- **Top bar:** brand, sync time and a menu (pages, role, layout switch), plus search and *Ask the
  agent*.

The mobile layout uses the same URL, data, actions and guardrails as the desktop, because both
layouts are built from `cockpit.py` (`app_pages/workbench.py` for desktop, `app_pages/mobile.py`
for mobile). It works in any phone browser, with nothing to install.

**Cockpit notes**

- **Visual design.** The look follows the Design artifact *Incident Copilot - Interactive Prototype*
  (a copy is in `design/`): dark theme, IBM Plex Sans and JetBrains Mono (`.streamlit/config.toml`),
  SLA bars on queue cards, an SLA box in the header, and a copilot panel with Why? (✓ / ≈ / ×),
  evidence bullets, per-step risk (Low / Medium / High) with *Run* or *Request approval*, and chat
  with quick questions.
- **Where it differs from the prototype.**
  - The trend chart shows this error code's incidents per day from the workbook, because there
    are no monitoring metrics.
  - *Request approval* records a named approver and change reference instead of auto-approving.
  - *Run* records that the engineer ran the step; it never executes anything.
  - Chat answers are retrieved from the cited sources rather than canned.

- **SLA clock.** Targets are an assumption, because the workbook has none: P1 4 h, P2 8 h, P3 24 h,
  P4 72 h (`SLA_HOURS` in `copilot/config.py`). The workbook is a snapshot, so the clock starts at
  its newest timestamp (or `COPILOT_NOW`) when the app starts, then runs in real time.
- **Resolved incidents leave the screen.** Once an incident is resolved (in the copilot, or with
  status *Resolved* in the workbook), it drops out of the queue, related lists and search, and the
  cockpit moves to the next incident.
- **Auto-refresh.** Every 15 s (`COPILOT_REFRESH_SECONDS`) the app checks the timestamps of the
  workbook, runbooks, audit log and work notes. It refreshes the whole screen only if something
  changed, for example another engineer resolving an incident or a new workbook export, so idle
  screens don't flicker and typed text isn't lost. A new workbook or runbook is re-indexed
  automatically. The green sync time in the top bar shows the last check.
- **Live timers.** Queue cards and the header SLA box tick every second in the browser, counting down
  to breach and then counting up the time over SLA. The server sends each incident's deadline once,
  so every timer for an incident agrees (`ui_cards.py`).
- **Run step never executes anything.** Per the PRD, the copilot has no connection to production
  systems. The step opens a preview, asks for a named approver and change reference on high-risk
  steps, and records the step as done by the engineer.
- **State comes from the audit log.** Acknowledged, escalated, resolved, steps done and problem
  records are all derived from it, so they survive restarts and every change is traceable.
- **Not available yet.** The Last change tile and change records need a change feed, which isn't
  connected.

## Data sources

**Incident workbook** ([data/Incident/5000_Enterprise_Incidents_Master.xlsx](data/Incident/5000_Enterprise_Incidents_Master.xlsx), sheet *Incident Records*)

- The 4,484 Closed or Resolved rows are the incident history. They contain only 15 distinct
  error patterns, so they are grouped by pattern (system, error code, description, root cause and
  resolution). The copilot shows "seen 355 times, latest INC…" instead of three copies of one ticket.
- The 516 In Progress or Escalated rows are the work queue, sorted most severe and most recent first.
- `Assigned To` is never indexed, because the PRD excludes engineer identity.
- To point at another workbook with the same columns, set `INCIDENT_WORKBOOK`.

**Runbooks** (`data/knowledge/**/*.docx`, plus any `.md` with front matter)

- Word documents are split into citable sections by heading and by `ERR-…` error-code
  paragraphs, with tables kept in their section. The system is inferred from the file name
  (Mainframe, AS400 or Java).
- A document's last-updated date comes from the Word file properties.
- Each history pattern is linked to the runbook section that mentions its error code. Patterns with
  no runbook appear on the Knowledge health page (currently `ERR-JAVA-429`, 339 incidents), and the
  workbench warns when a recommendation rests on past tickets alone.

**Users and settings.** Users are simulated SSO identities in [copilot/config.py](copilot/config.py):
Rajesh (Java and AS400), Sarah (Mainframe), a team lead and a knowledge manager. That file also
holds the thresholds, freshness window and per-system escalation paths. The runbooks point to an
escalation matrix that isn't included, so the escalation paths are placeholders. Runtime state
(audit log, work notes) is written to `data/runtime/`.

**Old demo data.** The earlier synthetic demo data (Oracle and legacy runbooks) is kept in
`data/_archive_synthetic/` and is not loaded.

## Not production-ready yet

- TF-IDF stands in for embeddings. `retrieval._Index` is the only class to swap out.
- ServiceNow, Confluence and SSO are file-based stand-ins, not API integrations.
- The eval set ([data/eval_set.json](data/eval_set.json)) has 20 hand-written paraphrases (15 repeats
  and 5 new). The PRD gates need 300 labelled incidents from real tickets.
