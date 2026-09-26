"""Append-only, hash-chained audit log (FR-9) and work-note store.

Every entry carries the hash of the previous entry, so an edited, deleted or
reordered line breaks the chain and `verify()` reports where. That is the
"audit log altered or has gaps" kill-switch check from the PRD.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config

_LOCK = threading.Lock()
_GENESIS = "0" * 64


def _hash(entry: dict) -> str:
    body = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def log(action: str, user: str, incident: str, outcome: str = "",
        sources: list[str] | None = None, details: dict | None = None,
        path: Path = config.AUDIT_LOG_FILE) -> dict:
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        entries = _read(path)
        entry = {
            "seq": len(entries) + 1,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "user": user,
            "incident": incident,
            "action": action,
            "outcome": outcome,
            "sources": sources or [],
            "details": details or {},
            "prev_hash": entries[-1]["hash"] if entries else _GENESIS,
        }
        entry["hash"] = _hash(entry)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry


def entries(path: Path = config.AUDIT_LOG_FILE) -> list[dict]:
    return _read(path)


def verify(path: Path = config.AUDIT_LOG_FILE) -> tuple[bool, str]:
    prev = _GENESIS
    for i, e in enumerate(_read(path), start=1):
        if e.get("seq") != i:
            return False, f"Gap or reorder at entry {i} (found seq {e.get('seq')})"
        if e.get("prev_hash") != prev:
            return False, f"Chain broken at entry {i}"
        if _hash(e) != e.get("hash"):
            return False, f"Entry {i} was modified after it was written"
        prev = e["hash"]
    return True, "Chain intact"


def save_work_note(incident: str, user: str, note: dict, path: Path = config.WORK_NOTES_FILE) -> None:
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "incident": incident, "user": user, "note": note}, ensure_ascii=False) + "\n")


def work_notes(incident: str | None = None, path: Path = config.WORK_NOTES_FILE) -> list[dict]:
    rows = _read(path)
    return [r for r in rows if incident is None or r["incident"] == incident]
