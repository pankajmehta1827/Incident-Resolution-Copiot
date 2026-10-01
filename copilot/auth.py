"""Sign-in for the copilot.

Accounts are the users in config.USERS. Each one signs in with a password supplied through an
environment variable named COPILOT_PASSWORD_<USER ID in capitals, "." -> "_">, e.g.
COPILOT_PASSWORD_TEAM_LEAD for "team.lead". A user without that variable cannot sign in.

Sign-in is required unless COPILOT_AUTH=off (meant for local development only). With sign-in
required and no passwords configured, the app stays locked: deploying without setting any
password must never leave it open.

This is a prototype-grade login. For production, use single sign-on (Streamlit's st.login with
Microsoft Entra ID or Google) so passwords never live in the app's settings.
"""
from __future__ import annotations

import hmac
import os
import time

from . import config

MAX_FAILURES = 5          # wrong passwords in a row before a short lockout
LOCKOUT_SECONDS = 60


def required() -> bool:
    return os.getenv("COPILOT_AUTH", "on").strip().lower() not in ("off", "0", "false", "no")


def env_name(user_id: str) -> str:
    return "COPILOT_PASSWORD_" + user_id.upper().replace(".", "_").replace("-", "_")


def accounts() -> dict[str, str]:
    """user_id -> password, for every configured user that has a password set."""
    out = {}
    for uid in config.USERS:
        pw = os.getenv(env_name(uid), "")
        if pw.strip():
            out[uid] = pw
    return out


def verify(username: str, password: str) -> str | None:
    """Return the user id if the credentials are right, else None. Constant-time comparison,
    and the same work is done whether or not the user exists."""
    uid = username.strip().lower()
    expected = accounts().get(uid, "")
    ok = hmac.compare_digest(password.encode(), (expected or "\0invalid\0").encode())
    return uid if ok and expected else None


def locked_for(state: dict) -> int:
    """Seconds left on a lockout after too many failures in this session (0 if not locked)."""
    until = state.get("auth_locked_until", 0)
    return max(0, int(until - time.time()))


def record_failure(state: dict) -> None:
    state["auth_failures"] = state.get("auth_failures", 0) + 1
    if state["auth_failures"] >= MAX_FAILURES:
        state["auth_locked_until"] = time.time() + LOCKOUT_SECONDS
        state["auth_failures"] = 0
