"""Incident Resolution Copilot: entry point, global bar and page router."""
from __future__ import annotations

import re

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from copilot import audit, config, engine, ops  # noqa: E402  (after load_dotenv so env overrides apply)
import ui_cards  # noqa: E402
from ui_common import data_signature, get_kb, get_models  # noqa: E402

st.set_page_config(page_title="Incident Resolution Copilot", page_icon=":material/support_agent:",
                   layout="wide", initial_sidebar_state="collapsed")

# --- Session state -----------------------------------------------------------
st.session_state.setdefault("user_id", "team.lead")
st.session_state.setdefault("model", config.DEFAULT_MODEL)
st.session_state.setdefault("custom_incidents", [])
st.session_state.setdefault("search", "")
st.session_state.setdefault("results", {})      # (incident, user, model) -> Recommendation
st.session_state.setdefault("chats", {})        # (incident, user) -> list of messages
st.session_state.setdefault("notes", {})        # (incident, user) -> draft notes dict
st.session_state.setdefault("decided", {})      # (incident, user) -> decision
st.session_state.setdefault("updates", {})      # (incident, user) -> drafted stakeholder update
# Baseline for auto-refresh: what the data looked like when this run started.
st.session_state.data_sig = data_signature()

@st.fragment(run_every=config.AUTO_REFRESH_SECONDS)
def auto_refresh() -> None:
    """Checks for changes every few seconds and refreshes the whole screen only when something
    changed (another engineer's action, a new workbook, an edited runbook). Checking is cheap:
    file timestamps only. Refreshing only on change avoids closing dialogs or losing typed text."""
    sig = data_signature()
    if sig != st.session_state.get("data_sig"):
        st.session_state.data_sig = sig
        st.rerun(scope="app")
    st.caption(f":green[:material/sync:] {ops.now_ist():%H:%M:%S} IST", width="content",
               help=f"Auto-refreshes within {config.AUTO_REFRESH_SECONDS} s when incidents, runbooks or "
                    "the activity log change")

# The model is configured in .env (GROQ_MODEL) and deliberately not shown on screen.
models = get_models()
if models and st.session_state.model not in models:
    st.session_state.model = config.DEFAULT_MODEL if config.DEFAULT_MODEL in models else models[0]

# --- Device: phones get the mobile layout ------------------------------------
# Detected once per session from the browser's User-Agent. ?view=mobile or ?view=desktop overrides it,
# and the menu has a switch.
MOBILE_UA = re.compile(r"Mobi|Android|iPhone|iPod|iPad", re.I)
requested = st.query_params.get("view")
if requested in ("mobile", "desktop"):
    st.session_state.view = requested
elif "view" not in st.session_state:
    st.session_state.view = "mobile" if MOBILE_UA.search(st.context.headers.get("User-Agent", "")) else "desktop"
is_mobile = st.session_state.view == "mobile"


def _set_view(view: str) -> None:
    st.session_state.view = view
    st.query_params["view"] = view


desktop_home = st.Page("app_pages/workbench.py", title="Incident cockpit", icon=":material/support_agent:",
                       default=not is_mobile)
mobile_home = st.Page("app_pages/mobile.py", title="Incident cockpit (mobile)", icon=":material/smartphone:",
                      url_path="mobile", default=is_mobile)
home = mobile_home if is_mobile else desktop_home
others = [
    st.Page("app_pages/knowledge_health.py", title="Knowledge health", icon=":material/menu_book:"),
    st.Page("app_pages/audit_log.py", title="Audit log", icon=":material/fact_check:"),
    st.Page("app_pages/evals.py", title="Evals", icon=":material/rule:"),
]
page = st.navigation([desktop_home, mobile_home, *others], position="hidden")
menu_pages = [home, *others]

# Colours and fonts come from .streamlit/config.toml (the prototype's dark theme). This CSS only
# does what theming can't: panel backgrounds per zone and left-aligned queue cards.
st.html("""<style>
.block-container {padding: 2.9rem 1rem 0.5rem 1rem; max-width: 100%;}
.st-key-globalbar {background: #0E1628; border: 1px solid #1E2A44; border-radius: 10px; padding: 4px 14px;}
.st-key-globalbar input {background: #16213A;}
.st-key-queuepane {background: #0E1628; border: 1px solid #1E2A44; border-radius: 10px; padding: 12px 10px;}
.st-key-copilot {background: #101A30; border-color: #22325A !important;}
.st-key-incheader {border-bottom: 1px solid #1E2A44; padding-bottom: 6px;}
</style>""" + ui_cards.CSS)
if is_mobile:
    st.html("""<style>
.block-container {padding: 2.6rem 0.6rem 1rem 0.6rem;}
.st-key-globalbar {padding: 6px 10px;}
.st-key-mheader .sla-box {width: 100%;}
.st-key-queue .qc-title {-webkit-line-clamp: 3;}
</style>""")
# Live SLA timers: a static script (no user data) that ticks every [data-sla-deadline] element each second.
st.html(ui_cards.SCRIPT, unsafe_allow_javascript=True)


def _ask_agent() -> None:
    text = st.session_state.get("search", "").strip()
    if not text:
        return
    user = config.USERS[st.session_state.user_id]
    index = get_kb().index
    app = engine.detect_system(index, text, user["groups"])
    asked = ops.now_ist()
    ask = {"number": f"ASK-{asked:%H%M%S}", "opened": f"{asked:%Y-%m-%d %H:%M}",
           "application": app, "category": "", "error_code": engine.find_error_code(index, text),
           "priority": "Search", "status": "Question", "short_description": text[:120], "description": text,
           "source": "Agent search", "system_detected": True}
    st.session_state.custom_incidents.insert(0, ask)
    st.session_state.pending_selection = ask["number"]
    st.session_state.m_view = "detail"
    st.session_state.search = ""
    st.session_state.goto_cockpit = True
    audit.log("agent_search", st.session_state.user_id, ask["number"], f"system: {app} (detected)")


short = {config.MF: "MF", config.AS400: "AS400", config.JAVA: "Java"}
kb = get_kb()
user = config.USERS[st.session_state.user_id]
status = ":green[●]" if models else ":orange[●]"


def _menu() -> None:
    """Pages, role and layout switch. On mobile this holds everything that doesn't fit the bar."""
    for p in menu_pages:
        st.page_link(p, label=p.title, icon=p.icon)
    if is_mobile:
        st.divider()
        st.selectbox("Signed in as", options=list(config.USERS), key="user_id",
                     format_func=lambda u: f"{config.USERS[u]['name']} · {config.USERS[u]['role']}")
        st.caption(f"{status} {len(kb.documents)} runbooks · {sum(h.occurrences for h in kb.index.history):,} "
                   f"incidents indexed · {' · '.join(short.get(g, g) for g in user['groups'])}")
    st.divider()
    other = "desktop" if is_mobile else "mobile"
    st.button(f"Switch to {other} layout", icon=":material/desktop_windows:" if is_mobile else ":material/smartphone:",
              on_click=_set_view, args=(other,), type="tertiary")


if is_mobile:
    with st.container(key="globalbar", gap="small"):
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            st.markdown("**:blue[:material/bolt:] Incident Copilot**", width="content")
            st.space("stretch")
            auto_refresh()
            with st.popover("", icon=":material/menu:", help="Menu", type="tertiary"):
                _menu()
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            st.text_input("Search", key="search", placeholder="Search or ask the agent…",
                          label_visibility="collapsed", icon=":material/search:")
            st.button("", icon=":material/auto_awesome:", on_click=_ask_agent, type="primary",
                      help="Ask the agent")
else:
    with st.container(key="globalbar", horizontal=True, vertical_alignment="center", gap="small"):
        st.markdown("**:blue[:material/bolt:] Incident Copilot**", width="content")
        st.text_input("Search", key="search", placeholder="Search incidents, CIs, error codes… or ask the agent",
                      label_visibility="collapsed", icon=":material/search:", width=380)
        st.button("Ask agent", icon=":material/auto_awesome:", on_click=_ask_agent, type="primary",
                  help="Describe a problem in your own words; the agent finds the known fix.")
        with st.popover("", icon=":material/apps:", help="Pages", type="tertiary"):
            _menu()
        st.space("stretch")
        st.caption(f"{status} {len(kb.documents)} runbooks · "
                   f"{sum(h.occurrences for h in kb.index.history):,} incidents indexed", width="content",
                   help=f"Freshness window {config.FRESHNESS_WINDOW_DAYS} days")
        st.caption(" · ".join(short.get(g, g) for g in user["groups"]), width="content")
        auto_refresh()
        st.selectbox("Signed in as", options=list(config.USERS), key="user_id", label_visibility="collapsed",
                     format_func=lambda u: config.USERS[u]["name"], width=170)

if not models:
    st.warning("AI recommendations are unavailable. The copilot shows similar incidents only.",
               icon=":material/cloud_off:")

# Send each device to its own cockpit (also after an agent search from another page).
if (st.session_state.pop("goto_cockpit", False) or page in (desktop_home, mobile_home)) and page is not home:
    st.switch_page(home)
page.run()
