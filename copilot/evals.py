"""Retrieval-level evals from the PRD that can run offline, without model calls.

Covers repeat-incident detection, top-3 relevance, no-match honesty and access
control. Groundedness, hallucination and usefulness need SME review of live
recommendations and are tracked through the audit log instead.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config
from .retrieval import KnowledgeIndex

EVAL_FILE = config.DATA_DIR / "eval_set.json"
ALL_GROUPS = config.SYSTEMS


def run(index: KnowledgeIndex, path: Path = EVAL_FILE) -> dict:
    cases = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for c in cases:
        matches = index.similar_incidents(c["text"], c["application"], ALL_GROUPS)
        best = matches[0].score if matches else 0.0
        predicted_repeat = best >= config.MIN_CONFIDENCE
        top3 = [m.incident for m in matches if m.score >= config.MIN_CONFIDENCE]
        rows.append({
            "Case": c["id"], "Text": c["text"], "Labelled repeat": c["repeat"],
            "Predicted repeat": predicted_repeat, "Best score": round(best, 2),
            "Top 3": ", ".join(f"{i.error_code} ({i.number})" for i in top3),
            "Relevant in top 3": bool({i.error_code for i in top3} & set(c["expected_codes"])) if c["repeat"] else None,
        })
    repeats = [r for r in rows if r["Labelled repeat"]]
    new = [r for r in rows if not r["Labelled repeat"]]

    # Access control: a user without a group/space must never receive its content.
    leaks = 0
    checks = 0
    for uid, u in config.USERS.items():
        for c in cases:
            for m in index.similar_incidents(c["text"], c["application"], u["groups"]):
                checks += 1
                leaks += m.incident.application not in u["groups"]
            for s in index.relevant_sections(c["text"], c["application"], u["spaces"]):
                checks += 1
                leaks += s.section.space not in u["spaces"]
    restricted_indexed = sum(s.space == "SECOPS" for s in index.sections)

    return {
        "rows": rows,
        "detection_accuracy": sum(r["Labelled repeat"] == r["Predicted repeat"] for r in rows) / len(rows),
        "top3_relevance": sum(bool(r["Relevant in top 3"]) for r in repeats) / len(repeats) if repeats else None,
        "no_match_honesty": sum(not r["Predicted repeat"] for r in new) / len(new) if new else None,
        "access_leaks": leaks + restricted_indexed,
        "access_checks": checks,
    }
