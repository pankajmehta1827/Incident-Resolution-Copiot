"""Loading and ingestion rules for the knowledge sources.

Sources:
- Runbooks / AIDs as Word documents (.docx) or markdown with front matter (.md)
  under data/knowledge. Each is split into citable sections by heading and by
  error-code paragraphs ("ERR-..."), with tables kept in their section.
- The incident workbook (data/Incident/*.xlsx). Resolved rows become history,
  grouped into recurring patterns; open rows become the work queue.

Ingestion applies the PRD's exclusions: drafts, personal spaces, restricted
content, pages containing secrets, unresolved or no-fix incidents, anything
outside the history window, and engineer identity (never indexed).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config
from .masking import contains_secret, mask


@dataclass
class Section:
    section_id: str          # e.g. "RB-AS400#err-as400-001-cpf5149-record-lock"
    doc_id: str
    doc_title: str
    heading: str
    text: str
    doc_type: str            # "Runbook", "AID" or "Confluence"
    application: str
    space: str
    owner: str
    last_updated: date

    @property
    def label(self) -> str:
        return f"{self.doc_id} › {self.heading}"


@dataclass
class Document:
    doc_id: str
    title: str
    doc_type: str
    application: str
    space: str
    owner: str
    last_updated: date
    source_file: str = ""
    sections: list[Section] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(f"{s.heading}\n{s.text}" for s in self.sections)


@dataclass
class Exclusion:
    source: str
    reason: str


@dataclass
class HistoricIncident:
    """One recurring resolved pattern; `number` is its most recent occurrence."""
    number: str
    opened: date
    resolved: date
    application: str
    category: str
    priority: str
    short_description: str
    description: str
    resolution_notes: str
    close_code: str
    error_code: str = ""
    component: str = ""
    business_area: str = ""
    rca: str = ""
    occurrences: int = 1
    first_seen: date | None = None
    examples: list[str] = field(default_factory=list)
    kb_used: str = ""
    kb_outcome: str = ""

    @property
    def search_text(self) -> str:
        # Root cause is included because engineers often describe the cause they suspect.
        return (f"{self.error_code}. {self.component}. {self.business_area}. "
                f"{self.short_description}. {self.description}. {self.rca}")


# --- Documents -------------------------------------------------------------------

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


_ERR_LINE = re.compile(r"^(ERR-[A-Z0-9-]+)\b")
_APP_BY_KEYWORD = [("mainframe", config.MF), ("as400", config.AS400), ("java", config.JAVA)]


def _application_for(name: str) -> str:
    low = name.lower()
    return next((app for key, app in _APP_BY_KEYWORD if key in low), "")


def _doc_id_for(app: str, fallback: str) -> str:
    return {config.MF: "RB-MF", config.AS400: "RB-AS400", config.JAVA: "RB-JAVA"}.get(app, _slug(fallback).upper())


def _docx_blocks(path: Path) -> tuple[list[tuple[str, str]], datetime | None]:
    """Yield (kind, text) in body order: kind is 'h' (heading), 'e' (error code line) or 'p'."""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    d = docx.Document(str(path))
    blocks: list[tuple[str, str]] = []
    for child in d.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, d)
            text = p.text.strip()
            if not text:
                continue
            style = (p.style.name if p.style is not None else "").lower()
            if style.startswith("heading") or style == "title":
                blocks.append(("t" if style == "title" else "h", text))
            elif _ERR_LINE.match(text) and len(text) < 90:
                blocks.append(("e", text))
            else:
                blocks.append(("p", text))
        elif tag == "tbl":
            rows = [[c.text.strip().replace("\n", " / ") for c in r.cells] for r in Table(child, d).rows]
            if not rows:
                continue
            header, body = rows[0], rows[1:]
            for r in body or [header]:
                blocks.append(("p", " | ".join(f"{h}: {v}" for h, v in zip(header, r)) if body else " | ".join(r)))
    modified = d.core_properties.modified
    return blocks, modified


def _load_docx(path: Path, doc_type: str, space: str) -> Document:
    blocks, modified = _docx_blocks(path)
    title = next((t for k, t in blocks if k == "t"), path.stem)
    app = _application_for(path.stem) or _application_for(title)
    last = (modified.date() if modified else datetime.fromtimestamp(path.stat().st_mtime).date())
    doc = Document(doc_id=_doc_id_for(app, path.stem), title=title, doc_type=doc_type, application=app,
                   space=space, owner="Not named in document", last_updated=last, source_file=path.name)

    parents: list[str] = []          # heading trail for context
    heading, lines = "", []

    def flush() -> None:
        body = "\n".join(lines).strip()
        if heading and body:
            text, _ = mask(body)
            doc.sections.append(Section(
                section_id=f"{doc.doc_id}#{_slug(heading)}", doc_id=doc.doc_id, doc_title=doc.title,
                heading=heading, text=text, doc_type=doc_type, application=app, space=space,
                owner=doc.owner, last_updated=last))

    for kind, text in blocks:
        if kind == "t":
            continue
        if kind in ("h", "e"):
            flush()
            lines = []
            if kind == "h":
                parents = [text]
                heading = text
            else:
                heading = text
                # keep the category heading ("A. System Bug") as context for the model
                lines = [f"({parents[-1]})"] if parents else []
        else:
            lines.append(text)
    flush()

    # Error-code sections that only exist inside tables (e.g. the Java runbook) get their own
    # section too, so each code is individually citable.
    for sec in list(doc.sections):
        for line in sec.text.splitlines():
            m = re.search(r"Error Code: (ERR-[A-Z0-9-]+)", line)
            if m and not any(s.heading.startswith(m.group(1)) for s in doc.sections):
                related = [s.text for s in doc.sections if m.group(1) in s.heading or
                           (m.group(1) in s.text and s is not sec)]
                doc.sections.append(Section(
                    section_id=f"{doc.doc_id}#{_slug(m.group(1))}", doc_id=doc.doc_id, doc_title=doc.title,
                    heading=f"{m.group(1)} ({sec.heading})", text="\n".join([line] + related),
                    doc_type=doc_type, application=app, space=space, owner=doc.owner, last_updated=last))
    return doc


def _parse_front_matter(raw: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if not m:
        return {}, raw
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, m.group(2)


def _load_markdown(path: Path, excluded: list[Exclusion]) -> Document | None:
    raw = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    meta, body = _parse_front_matter(raw)
    doc_id = meta.get("doc_id", path.stem)
    space = meta.get("space", "CONFLUENCE")
    if meta.get("status", "").lower() == "draft" or space.startswith("~"):
        excluded.append(Exclusion(doc_id, "Unapproved draft or personal space"))
        return None
    if meta.get("restricted", "").lower() == "true":
        excluded.append(Exclusion(doc_id, f"Restricted space {space}: not indexed in v1"))
        return None
    doc = Document(doc_id=doc_id, title=meta.get("title", path.stem), doc_type=meta.get("type", "Confluence"),
                   application=meta.get("application", ""), space=space, owner=meta.get("owner", "Unassigned"),
                   last_updated=date.fromisoformat(meta.get("last_updated", "1970-01-01")), source_file=path.name)
    for chunk in re.split(r"(?m)^## ", body):
        chunk = chunk.strip()
        if not chunk or "\n" not in chunk:
            continue
        heading, text = chunk.split("\n", 1)
        doc.sections.append(Section(
            section_id=f"{doc_id}#{_slug(heading)}", doc_id=doc_id, doc_title=doc.title, heading=heading.strip(),
            text=mask(text.strip())[0], doc_type=doc.doc_type, application=doc.application, space=space,
            owner=doc.owner, last_updated=doc.last_updated))
    return doc


def knowledge_files(knowledge_dir: Path = config.KNOWLEDGE_DIR) -> list[Path]:
    """Bundled runbooks plus articles approved from knowledge-gap drafts (on the persistent volume)."""
    roots = [knowledge_dir] + ([config.APPROVED_KB_DIR] if knowledge_dir == config.KNOWLEDGE_DIR else [])
    return sorted(p for root in roots if root.exists() for p in root.rglob("*")
                  if p.suffix.lower() in (".docx", ".md") and not p.name.startswith("~$"))


def load_documents(knowledge_dir: Path = config.KNOWLEDGE_DIR) -> tuple[list[Document], list[Exclusion]]:
    docs: list[Document] = []
    excluded: list[Exclusion] = []
    for path in knowledge_files(knowledge_dir):
        if path.name.startswith("~$") or path.suffix.lower() not in (".docx", ".md"):
            continue  # Word lock files, images, etc.
        folder = path.parent.name.lower()
        if path.suffix.lower() == ".md":
            doc = _load_markdown(path, excluded)
        else:
            doc_type = "Runbook" if "runbook" in path.stem.lower() else "AID" if folder == "aid" else "Confluence"
            try:
                doc = _load_docx(path, doc_type, "RUNBOOK" if doc_type == "Runbook" else folder.upper())
            except Exception as exc:  # corrupt or password-protected file
                excluded.append(Exclusion(path.name, f"Could not read document: {type(exc).__name__}"))
                continue
        if doc is None:
            continue
        if contains_secret(doc.text):
            excluded.append(Exclusion(doc.doc_id, "Contains a secret or credential: excluded until cleaned"))
            continue
        if not doc.sections:
            excluded.append(Exclusion(doc.doc_id, "No readable sections"))
            continue
        docs.append(doc)
    return docs, excluded


# --- Incidents -------------------------------------------------------------------

def _read_workbook(path: Path = config.INCIDENT_WORKBOOK):
    """Incidents as a DataFrame with the workbook's columns, from the workbook or from ServiceNow."""
    import pandas as pd

    from . import servicenow

    if servicenow.enabled():
        df = pd.DataFrame(servicenow.to_workbook_rows(servicenow.fetch_incidents()), dtype=str).fillna("")
    else:
        df = pd.read_excel(path, sheet_name=config.INCIDENT_SHEET, dtype=str).fillna("")
    df.columns = [c.strip() for c in df.columns]
    for col in ("Created Timestamp", "Resolved Timestamp"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


def _incident_dict(r) -> dict:
    return {
        "number": r["Incident ID"],
        "opened": r["Created Timestamp"].strftime("%Y-%m-%d %H:%M") if not _isnat(r["Created Timestamp"]) else "",
        "application": r["System"],
        "category": r["Category"],
        "error_code": r["Error Code"],
        "component": r["Component"],
        "business_area": r["Business Area"],
        "priority": r["Severity"],
        "status": r["Status"],
        "assigned_to": r.get("Assigned To", ""),   # shown as owner; never indexed or used for matching
        "short_description": r["Description"],
        "description": r["Description"],
        "source": "ServiceNow" if config.INCIDENT_SOURCE == "servicenow" else "Incident workbook",
    }


def _isnat(v) -> bool:
    import pandas as pd
    return v is None or pd.isna(v)


def load_incidents(path: Path = config.INCIDENT_WORKBOOK, documents: list[Document] | None = None
                   ) -> tuple[list[HistoricIncident], list[dict], list[dict], list[Exclusion]]:
    """Return (history patterns, open queue, all incidents as dicts, exclusions)."""
    df = _read_workbook(path)
    cutoff = config.TODAY - timedelta(days=config.HISTORY_WINDOW_DAYS)
    excluded: list[Exclusion] = []

    all_records = [_incident_dict(r) for _, r in df.iterrows()]

    open_df = df[df["Status"].isin(config.OPEN_STATES)]
    sev_rank = {"P1": 0, "P2": 1, "P3": 2, "P4": 3}
    queue = [_incident_dict(r) for _, r in open_df.iterrows()]
    queue.sort(key=lambda i: (sev_rank.get(i["priority"][:2], 9), -_ts(i["opened"])))

    hist = df[df["Status"].isin(config.RESOLVED_STATES)]
    no_fix = hist["Resolution Summary"].str.strip().eq("") | hist["Resolution Summary"].str.lower().str.startswith("no fix")
    if no_fix.any():
        excluded.append(Exclusion(f"{int(no_fix.sum())} incidents", "Closed with no fix recorded"))
    hist = hist[~no_fix & hist["Resolved Timestamp"].notna()]
    old = hist["Resolved Timestamp"].dt.date < cutoff
    if old.any():
        excluded.append(Exclusion(f"{int(old.sum())} incidents", "Outside the 24-month history window"))
    hist = hist[~old]
    if len(open_df):
        excluded.append(Exclusion(f"{len(open_df)} incidents", "Still open (In Progress / Escalated): in the queue, not used as fixes"))

    docs = documents or []
    keys = ["System", "Error Code", "Description", "RCA (Root Cause Analysis)", "Resolution Summary", "Closing Notes"]
    patterns: list[HistoricIncident] = []
    for key, g in hist.groupby(keys, sort=False):
        system, code, desc, rca, res, notes = key
        g = g.sort_values("Resolved Timestamp")
        latest = g.iloc[-1]
        kb = next((d.doc_id for d in docs if code and code in d.text), "")
        patterns.append(HistoricIncident(
            number=latest["Incident ID"], opened=latest["Created Timestamp"].date(),
            resolved=latest["Resolved Timestamp"].date(), application=system, category=latest["Category"],
            priority=latest["Severity"], short_description=mask(desc)[0],
            description=f"{mask(desc)[0]} Component: {latest['Component']}. Business area: {latest['Business Area']}.",
            rca=mask(rca)[0], resolution_notes=mask(f"{res} {notes}".strip())[0], close_code=latest["Status"],
            error_code=code, component=latest["Component"], business_area=latest["Business Area"],
            occurrences=len(g), first_seen=g.iloc[0]["Resolved Timestamp"].date(),
            examples=list(g["Incident ID"].iloc[-5:][::-1]), kb_used=kb,
        ))
    patterns.sort(key=lambda p: p.occurrences, reverse=True)
    return patterns, queue, all_records, excluded


def _ts(opened: str) -> float:
    try:
        return datetime.fromisoformat(opened).timestamp()
    except ValueError:
        return 0.0
