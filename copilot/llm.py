"""Groq client wrapper. All calls return JSON so outputs can be validated."""
from __future__ import annotations

import json
import os

import groq
from groq import Groq

_EXCLUDED = ("whisper", "tts", "orpheus", "guard", "safeguard", "allam")


class LLMUnavailable(RuntimeError):
    """The AI call failed. `reason` is a plain-language cause that is safe to show on screen
    (no model or provider names); the full technical message stays in str(exc) for the audit log."""

    def __init__(self, message: str, reason: str = "the AI service is not configured", rate_limited: bool = False):
        super().__init__(message)
        self.reason = reason
        self.rate_limited = rate_limited


def _reason(exc: Exception) -> str:
    text = str(exc).lower()
    if isinstance(exc, groq.RateLimitError):
        if "per day" in text or "tpd" in text or "rpd" in text:
            return "the daily AI usage limit has been reached; it resets within 24 hours"
        return "the AI usage limit was reached; try again in a minute"
    if isinstance(exc, groq.AuthenticationError):
        return "the AI key was rejected; check the key in the app settings"
    if isinstance(exc, (groq.PermissionDeniedError, groq.NotFoundError)) or "model" in text and (
            "decommissioned" in text or "does not exist" in text or "blocked" in text):
        return "the configured AI model is not available to this key"
    if isinstance(exc, groq.APITimeoutError):
        return "the AI service took too long to answer"
    if isinstance(exc, groq.APIConnectionError):
        return "the AI service could not be reached"
    if isinstance(exc, groq.BadRequestError):
        if "json" in text:
            return "the AI answered in an unusable format; try again"
        return "the AI service rejected the request"
    if isinstance(exc, groq.InternalServerError):
        return "the AI service had an internal error; try again shortly"
    return "an unexpected AI error occurred"


def _client() -> Groq:
    key = os.getenv("GROQ_API_KEY", "").strip()
    if not key or key.startswith("your_"):   # unset, or still the .env.example placeholder
        raise LLMUnavailable("GROQ_API_KEY is not set", "no AI key is configured")
    return Groq(api_key=key, timeout=30, max_retries=1)


def list_models() -> list[str]:
    data = _client().models.list().data
    return sorted(m.id for m in data if getattr(m, "active", True) and not any(x in m.id for x in _EXCLUDED))


# When the main model hits its usage limit, retry once on this one. Each model has its own
# allowance, so a smaller model keeps the copilot working when the main one is used up.
FALLBACK_MODEL = os.getenv("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b")


def complete_json(model: str, system: str, user: str, max_tokens: int = 2000) -> tuple[dict, dict]:
    """Return (parsed JSON, usage dict). Raises LLMUnavailable on any failure.
    Falls back to FALLBACK_MODEL once if the main model is rate-limited."""
    try:
        return _complete_json(model, system, user, max_tokens)
    except LLMUnavailable as exc:
        if not (exc.rate_limited and FALLBACK_MODEL and FALLBACK_MODEL != model):
            raise
        try:
            out, usage = _complete_json(FALLBACK_MODEL, system, user, max_tokens)
        except LLMUnavailable:
            raise exc from None          # report the original limit, not the fallback's error
        return out, dict(usage, fallback=True)


def chat_with_tools(model: str, messages: list[dict], tools: list[dict] | None, max_tokens: int = 1500):
    """One step of a tool-calling conversation. Returns (assistant message, usage dict).
    Falls back to FALLBACK_MODEL once if the main model is rate-limited, like complete_json."""
    def call(m: str):
        kwargs = {"model": m, "messages": messages, "temperature": 0.1, "max_completion_tokens": max_tokens}
        if tools:
            kwargs.update(tools=tools, tool_choice="auto")
        try:
            resp = _client().chat.completions.create(**kwargs)
        except LLMUnavailable:
            raise
        except Exception as exc:
            raise LLMUnavailable(f"{type(exc).__name__}: {exc}", _reason(exc),
                                 rate_limited=isinstance(exc, groq.RateLimitError)) from exc
        usage = {"prompt_tokens": getattr(resp.usage, "prompt_tokens", 0),
                 "completion_tokens": getattr(resp.usage, "completion_tokens", 0)}
        return resp.choices[0].message, usage

    try:
        return call(model)
    except LLMUnavailable as exc:
        if not (exc.rate_limited and FALLBACK_MODEL and FALLBACK_MODEL != model):
            raise
        try:
            msg, usage = call(FALLBACK_MODEL)
        except LLMUnavailable:
            raise exc from None
        return msg, dict(usage, fallback=True)


def _complete_json(model: str, system: str, user: str, max_tokens: int) -> tuple[dict, dict]:
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
        raise LLMUnavailable(f"{type(exc).__name__}: {exc}", _reason(exc),
                             rate_limited=isinstance(exc, groq.RateLimitError)) from exc
    content = resp.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMUnavailable("Model returned invalid JSON", "the AI answered in an unusable format; try again") from exc
    usage = {"prompt_tokens": getattr(resp.usage, "prompt_tokens", 0),
             "completion_tokens": getattr(resp.usage, "completion_tokens", 0)}
    return parsed, usage
