"""PII / secret masking and prompt-injection detection.

Applied at ingestion (knowledge and history), to incoming ticket text, and again
to every model output before display.
"""
from __future__ import annotations

import re

_SECRET_PATTERNS = [
    # JDBC / URL style connection strings with inline credentials
    (re.compile(r"jdbc:[a-z0-9]+:[^\s]*?[^\s/]+/[^\s@]+@[^\s]+", re.I), "[MASKED-CONNECTION-STRING]"),
    (re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@]+@[^\s]+", re.I), "[MASKED-CONNECTION-STRING]"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|token)\b\s*[:=]\s*\S+"), r"\1=[MASKED-SECRET]"),
    (re.compile(r"\bgsk_[A-Za-z0-9]{20,}\b"), "[MASKED-SECRET]"),
    (re.compile(r"\b(?:sk|pk|ghp|xox[bp])[-_][A-Za-z0-9_-]{16,}\b"), "[MASKED-SECRET]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[MASKED-SECRET]"),
]

_PII_PATTERNS = [
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[MASKED-EMAIL]"),
    (re.compile(r"(?<!\w)\+?\d[\d\s-]{8,}\d\b"), "[MASKED-PHONE]"),
    (re.compile(r"\b(?:Customer|Mr\.?|Mrs\.?|Ms\.?)\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"), "Customer [MASKED-NAME]"),
]

_INJECTION = re.compile(
    r"(ignore (all |any )?(previous|prior|above) (instructions|rules)|disregard (the )?(system|previous)"
    r"|you are now|act as (an? )?|reveal (the )?(system prompt|password|secret)"
    r"|print (the )?(database )?(password|secret|credentials?)|system prompt|jailbreak)",
    re.I,
)


def mask(text: str) -> tuple[str, list[str]]:
    """Return masked text and the kinds of data that were masked."""
    found: list[str] = []
    for pattern, repl in _SECRET_PATTERNS:
        text, n = pattern.subn(repl, text)
        if n:
            found.append("secret")
    for pattern, repl in _PII_PATTERNS:
        text, n = pattern.subn(repl, text)
        if n:
            found.append("pii")
    return text, sorted(set(found))


def contains_secret(text: str) -> bool:
    return any(p.search(text) for p, _ in _SECRET_PATTERNS)


def find_injection(text: str) -> list[str]:
    return [m.group(0) for m in _INJECTION.finditer(text)]


def neutralise_injection(text: str) -> str:
    """Replace instruction-like fragments so they never reach the model as commands."""
    return _INJECTION.sub("[REMOVED: instruction-like text]", text)
