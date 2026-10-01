"""Sign-in screen and sign-out for the Streamlit app (see copilot/auth.py for the rules)."""
from __future__ import annotations

import time

import streamlit as st

from copilot import audit, auth, config


def signed_in_user() -> str | None:
    return st.session_state.get("auth_user")


def sign_out() -> None:
    uid = st.session_state.pop("auth_user", None)
    if uid:
        audit.log("sign_out", uid, "-", "signed out")
    # Forget this person's working state so the next person on the device starts clean.
    for k in ("results", "chats", "notes", "decided", "updates", "custom_incidents", "selected_incident"):
        st.session_state.pop(k, None)


def require_sign_in() -> None:
    """Show the sign-in screen and stop the run until someone signs in. No-op when sign-in is off."""
    if not auth.required():
        return
    uid = signed_in_user()
    if uid in config.USERS:
        st.session_state.user_id = uid           # identity comes from the sign-in, not a picker
        return

    _, middle, _ = st.columns([1, 1.2, 1])
    with middle:
        st.space("large")
        with st.container(border=True):
            st.markdown("### :blue[:material/bolt:] Incident Copilot")
            st.caption("Sign in with your support account.")
            if not auth.accounts():
                st.warning("No sign-in accounts are configured yet. An administrator needs to set a "
                           "COPILOT_PASSWORD_… variable for each user.", icon=":material/lock:")
            wait = auth.locked_for(st.session_state)
            if wait:
                st.error(f"Too many failed attempts. Try again in {wait} seconds.", icon=":material/lock_clock:")
            with st.form("sign_in", border=False):
                username = st.text_input("Username", placeholder="e.g. team.lead", autocomplete="username")
                password = st.text_input("Password", type="password", autocomplete="current-password")
                submitted = st.form_submit_button("Sign in", type="primary", width="stretch", disabled=bool(wait))
            if submitted and not wait:
                user = auth.verify(username, password)
                if user:
                    st.session_state.auth_user = user
                    st.session_state.user_id = user
                    st.session_state.pop("auth_failures", None)
                    audit.log("sign_in", user, "-", "signed in")
                    st.rerun()
                auth.record_failure(st.session_state)
                audit.log("sign_in_failed", (username.strip().lower() or "?")[:40], "-", "wrong username or password")
                time.sleep(1)                           # slow down guessing
                if auth.locked_for(st.session_state):
                    st.rerun()                          # show the lockout (and disable the button) right away
                st.error("Wrong username or password.", icon=":material/error:")
            st.caption("Access is logged. Contact your team lead if you need an account.")
    st.stop()
