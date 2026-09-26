"""Groq client wrapper. All calls return JSON so outputs can be validated."""
from __future__ import annotations

import json
import os

from groq import Groq

_EXCLUDED = ("whisper", "tts", "orpheus", "guard", "safeguard", "allam")


class LLMUnavailable(RuntimeError):
    pass


def _client() -> Groq:
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key or key.startswith("your_"):   # unset, or still the .env.example placeholder
        raise LLMUnavailable("GROQ_API_KEY is not set")
    return Groq(api_key=key, timeout=30, max_retries=1)


def list_models() -> list[str]:
    data = _client().models.list().data
    return sorted(m.id for m in data if getattr(m, "active", True) and not any(x in m.id for x in _EXCLUDED))


def complete_json(model: str, system: str, user: str, max_tokens: int = 2000) -> tuple[dict, dict]:
    """Return (parsed JSON, usage dict). Raises LLMUnavailable on any failure."""
    try:
        resp = _client().chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.1,
            max_completion_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
    except LLMUnavailable:
        raise
    except Exception as exc:  # network, auth, rate limit, model retired
        raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc
    content = resp.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMUnavailable("Model returned invalid JSON") from exc
    usage = {"prompt_tokens": getattr(resp.usage, "prompt_tokens", 0),
             "completion_tokens": getattr(resp.usage, "completion_tokens", 0)}
    return parsed, usage
