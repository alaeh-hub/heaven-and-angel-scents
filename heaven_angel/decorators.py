import hashlib
import secrets
import time
from functools import wraps

from flask import abort, flash, redirect, request, session, url_for


from heaven_angel.db import query

# Pages reachable while must_change_password is set, so the user can clear it.
_PASSWORD_CHANGE_EXEMPT_ENDPOINTS = {"auth.change_password", "auth.logout"}


def password_fingerprint(password_hash):
    """Short, non-reversible tag for the current password hash. Stored in the
    session at login; once the password changes (self-service or HQ reset)
    the tag no longer matches, so every other signed-in session for that
    account is rejected on its next request."""
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:16]


def _current_account_or_none():
    """Re-fetch the signed-in user's live status from the database.

    Session data (role, is_active, ...) is only ever set at login time.
    Without this, an admin deactivating someone — or a forced password
    reset — wouldn't take effect until that user's cookie happened to
    expire or they logged out on their own. This makes both take
    effect on the very next request instead. branch_id/branch_name are
    included here (and re-synced onto the session below) for the same
    reason: if a Branch account's branch_id is ever reassigned, an
    already-signed-in session should immediately start reading/writing
    the new branch's data instead of the stale one from login time.
    """
    user_id = session.get("user_id")
    if user_id is None:
        return None
    return query(
        """SELECT u.user_id, u.role, u.is_active, u.must_change_password,
                  u.password_hash, u.branch_id, b.branch_name
           FROM users u LEFT JOIN branches b ON u.branch_id = b.branch_id
           WHERE u.user_id = %s""",
        (user_id,), fetchone=True,
    )


def live_account_or_none():
    """The signed-in account if its session is still valid right now, else
    None — same active / password-fingerprint checks as _require_session(),
    for JSON endpoints that must answer 401 instead of redirecting."""
    account = _current_account_or_none()
    if account is None or not account["is_active"]:
        return None
    if session.get("pwv") != password_fingerprint(account["password_hash"]):
        return None
    return account


# Signed-out session ids (in memory, one worker); stops a copied cookie after logout.
_revoked_sids = {}
_REVOKE_TTL = 60 * 60 * 24 * 2


def new_session_id():
    """A random id for a new login session."""
    return secrets.token_urlsafe(12)


def revoke_current_session():
    """Remember the current session id as signed out."""
    sid = session.get("sid")
    if sid:
        now = time.time()
        for k in [k for k, exp in _revoked_sids.items() if exp < now]:
            del _revoked_sids[k]
        _revoked_sids[sid] = now + _REVOKE_TTL


def session_is_revoked():
    """True if this session id was signed out."""
    sid = session.get("sid")
    return bool(sid) and sid in _revoked_sids


def _require_session(required_role=None):
    """Shared gate used by all three decorators below.

    Returns a Flask redirect/abort response if access should be
    denied, or None if the request should proceed.
    """
    if "user_id" not in session:
        flash("Please sign in to continue.", "warning")
        return redirect(url_for("auth.login"))

    if session_is_revoked():
        session.clear()
        flash("Please sign in to continue.", "warning")
        return redirect(url_for("auth.login"))

    account = _current_account_or_none()
    if account is None or not account["is_active"]:
        session.clear()
        flash("This account is no longer active. Please contact HQ.", "error")
        return redirect(url_for("auth.login"))

    if session.get("pwv") != password_fingerprint(account["password_hash"]):
        session.clear()
        flash("Your password changed, so please sign in again.", "warning")
        return redirect(url_for("auth.login"))

    # A Branch account without a branch is refused (a null branch_id would mean HQ).
    if account["role"] == "Branch" and account["branch_id"] is None:
        session.clear()
        flash("This account isn't assigned to a branch. Please contact HQ.", "error")
        return redirect(url_for("auth.login"))

    # Keep the session's branch in sync with the database on every request.
    session["branch_id"] = account["branch_id"]
    session["branch_name"] = account["branch_name"]

    if required_role is not None and account["role"] != required_role:
        abort(403)

    if account["must_change_password"] and request.endpoint not in _PASSWORD_CHANGE_EXEMPT_ENDPOINTS:
        flash("Please set a new password before continuing.", "warning")
        return redirect(url_for("auth.change_password"))

    return None


def login_required(view):
    """Require a signed-in user."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        deny = _require_session()
        if deny is not None:
            return deny
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    """Require an Admin user."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        deny = _require_session(required_role="Admin")
        if deny is not None:
            return deny
        return view(*args, **kwargs)
    return wrapped


def branch_required(view):
    """Require a Branch user."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        deny = _require_session(required_role="Branch")
        if deny is not None:
            return deny
        return view(*args, **kwargs)
    return wrapped
