"""Cached resources and small helpers shared by the Streamlit pages."""
from __future__ import annotations

from dataclasses import dataclass

import streamlit as st

from copilot import audit, config, knowledge, llm
from copilot.retrieval import KnowledgeIndex


@dataclass
class KnowledgeBase:
    index: KnowledgeIndex
    documents: list
    queue: list[dict]           # open incidents from the workbook
    all_incidents: list[dict]   # every row, for recurrence analysis
    excluded: list


def _stamp(paths) -> tuple:
    out = []
    for p in paths:
        try:
            s = p.stat()
            out.append((str(p), s.st_mtime_ns, s.st_size))
        except OSError:
            out.append((str(p), 0, 0))
    return tuple(out)


def sources_version() -> tuple:
    """Changes whenever the incident workbook or any knowledge document changes."""
    docs = sorted(p for p in config.KNOWLEDGE_DIR.rglob("*") if p.suffix.lower() in (".docx", ".md"))
    return _stamp([config.INCIDENT_WORKBOOK, *docs])


def data_signature() -> tuple:
    """Everything that can change what the screen shows: sources plus the audit log and work notes."""
    return sources_version() + _stamp([config.AUDIT_LOG_FILE, config.WORK_NOTES_FILE])


@st.cache_resource(show_spinner="Indexing runbooks and incidents...", max_entries=1)
def _load_kb(version: tuple) -> KnowledgeBase:
    docs, doc_excl = knowledge.load_documents()
    history, queue, all_rows, inc_excl = knowledge.load_incidents(documents=docs)
    return KnowledgeBase(KnowledgeIndex(docs, history), docs, queue, all_rows, doc_excl + inc_excl)


def get_kb() -> KnowledgeBase:
    """The indexed knowledge base, rebuilt automatically when the workbook or documents change."""
    return _load_kb(sources_version())


@st.cache_data(ttl=3600, show_spinner=False)
def get_models() -> list[str]:
    try:
        return llm.list_models()
    except Exception:
        return []


def current_user_id() -> str:
    return st.session_state.get("user_id", next(iter(config.USERS)))


def current_user() -> dict:
    return config.USERS[current_user_id()]


def current_model() -> str:
    return st.session_state.get("model", config.DEFAULT_MODEL)


def resolved_numbers(log: list[dict] | None = None) -> set[str]:
    """Incidents resolved in the copilot (and not reopened since)."""
    log = audit.entries() if log is None else log
    state: dict[str, bool] = {}
    for e in log:
        if e["action"] == "resolve":
            state[e["incident"]] = True
        elif e["action"] == "reopen":
            state[e["incident"]] = False
    return {n for n, r in state.items() if r}


def full_queue(log: list[dict] | None = None) -> list[dict]:
    """Open work: agent searches plus workbook incidents that are not resolved."""
    done = resolved_numbers(log)
    return [i for i in st.session_state.get("custom_incidents", []) + get_kb().queue
            if i["number"] not in done and i.get("status") != "Resolved"]


def first_sentence(text: str, limit: int = 180) -> str:
    s = text.split(". ")[0].strip()
    return (s[: limit - 1] + "…") if len(s) > limit else s
