"""Knowledge-health report (FR-11), Problem-record suggestions (FR-13) and usage metrics."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from . import audit, config
from .knowledge import Document, HistoricIncident


def knowledge_health(documents: list[Document], history: list[HistoricIncident],
                     log: list[dict] | None = None) -> dict[str, list[dict]]:
    log = log if log is not None else audit.entries()
    recent = config.TODAY - timedelta(days=180)

    stale = [{
        "Article": d.doc_id, "Title": d.title, "Owner": d.owner,
        "Last updated": d.last_updated, "Age (days)": (config.TODAY - d.last_updated).days,
    } for d in documents if (config.TODAY - d.last_updated).days > config.FRESHNESS_WINDOW_DAYS]

    outdated_by: dict[str, list[str]] = defaultdict(list)
    for h in history:
        if h.kb_outcome == "outdated" and h.resolved >= recent:
            outdated_by[h.kb_used].append(h.number)
    for e in log:  # engineer rejections that name an article
        if e["action"] == "reject":
            for ref in e.get("sources", []):
                outdated_by[ref.split("#")[0]].append(f"{e['incident']} (copilot reject)")
    docs = {d.doc_id: d for d in documents}
    contradicted = [{
        "Article": doc_id, "Title": docs[doc_id].title if doc_id in docs else doc_id,
        "Owner": docs[doc_id].owner if doc_id in docs else "",
        "Conflicting incidents": ", ".join(sorted(set(incs))), "Count": len(set(incs)),
    } for doc_id, incs in outdated_by.items() if len(set(incs)) >= config.CONTRADICTION_MIN]

    # Recurring resolved patterns whose error code appears in no runbook or article.
    missing = [{
        "System": h.application, "Error code": h.error_code, "Description": h.short_description,
        "Incidents": h.occurrences, "Latest": h.number, "Ticket resolution": h.resolution_notes,
    } for h in sorted(history, key=lambda h: h.occurrences, reverse=True) if not h.kb_used]

    return {"stale": stale, "contradicted": contradicted, "missing": missing}


def _to_date(value: str) -> date | None:
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def problem_candidates(records: list[dict]) -> list[dict]:
    """(system, error code) patterns seen PROBLEM_RECORD_MIN+ times in PROBLEM_RECORD_DAYS days."""
    since = config.TODAY - timedelta(days=config.PROBLEM_RECORD_DAYS)
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in records:
        d = _to_date(r.get("opened", ""))
        if d and since <= d <= config.TODAY:
            key = r.get("error_code") or r.get("category", "")
            groups[(r["application"], key, r.get("short_description", ""))].append(r["number"])
    out = [{"System": a, "Error code": c, "Description": d, "Incidents (30 days)": len(n),
            "Examples": ", ".join(n[-4:])}
           for (a, c, d), n in groups.items() if len(n) >= config.PROBLEM_RECORD_MIN]
    return sorted(out, key=lambda r: r["Incidents (30 days)"], reverse=True)


def usage_metrics(log: list[dict]) -> dict:
    actions = Counter(e["action"] for e in log)
    decisions = actions["accept"] + actions["edit"] + actions["reject"]
    recs = [e for e in log if e["action"] == "recommendation"]
    latencies = sorted(e["details"].get("latency_s", 0) for e in recs)
    p95 = latencies[int(0.95 * (len(latencies) - 1))] if latencies else None
    return {
        "recommendations": len(recs),
        "high": sum(e["outcome"] == "high" for e in recs),
        "decisions": decisions,
        "acceptance_rate": (actions["accept"] + actions["edit"]) / decisions if decisions else None,
        "rejects": actions["reject"],
        "no_match": actions["no_match"],
        "suspicious": actions["suspicious_input"],
        "p95_latency": p95,
        "actions": dict(actions),
    }
