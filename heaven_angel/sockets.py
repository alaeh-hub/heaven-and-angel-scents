"""Realtime push over Socket.IO.

Every signed-in browser tab opens one Socket.IO connection and joins a
room based on its Flask session — "admin" for HQ Admin users, or
"branch:<id>" for that branch's own staff. Route handlers that write
data call notify_admin() / notify_branch() / notify_admin_and_branch()
afterwards so every other open tab that cares can quietly re-fetch its
own page in the background instead of the user having to hit refresh.

The payload is intentionally tiny — {"scopes": [...]}, e.g.
{"scopes": ["requests", "inventory"]}. This file only ever broadcasts
*that* something in a given scope changed, never any actual data; the
frontend (main.js) maps the current page's URL to the scope(s) it
cares about and decides locally whether to re-fetch itself. That keeps
this module decoupled from which pages exist and what they show.

Scopes in use (kept intentionally coarse):
  requests       stock_requests row inserted/updated
  inventory      branch_inventory row changed (stock_qty or branch_price;
                 this also covers the HQ warehouse, which is just
                 branch_id=1's row)
  movement_logs  a stock_movement_logs row was inserted
  production     a production_logs row was inserted
  sales          a sales row was inserted
  products       the product catalog changed
  branches       a branch was added
  users          a login account was added/toggled/reset
"""
from flask import request, session
from flask_socketio import join_room

from heaven_angel.decorators import (
    _current_account_or_none, password_fingerprint, session_is_revoked,
)
from heaven_angel.extensions import socketio


# Open sockets by user and by login session, so a logout, deactivation or
# password reset can cut them off instead of waiting for the tab to close.
_sockets_by_user = {}
_sockets_by_session = {}


def _drop(table, key, socket_sid):
    """Remove a socket from a tracking group."""
    group = table.get(key)
    if group:
        group.discard(socket_sid)
        if not group:
            table.pop(key, None)


def _kick(table, key):
    """Disconnect every socket in a tracking group."""
    for socket_sid in list(table.get(key, ())):
        try:
            socketio.server.disconnect(socket_sid, namespace="/")
        except Exception:
            pass
    table.pop(key, None)


def disconnect_user(user_id):
    """Close every live socket of this account (deactivated / password reset)."""
    _kick(_sockets_by_user, user_id)


def disconnect_session(session_sid):
    """Close the sockets of one login session (logout)."""
    if session_sid:
        _kick(_sockets_by_session, session_sid)


@socketio.on("disconnect")
def handle_disconnect(*_args):
    """Forget a socket when it disconnects."""
    for table in (_sockets_by_user, _sockets_by_session):
        for key in [k for k, group in table.items() if request.sid in group]:
            _drop(table, key, request.sid)


@socketio.on("connect")
def handle_connect():
    """Gate room membership behind the same session Flask already trusts.

    Returning False refuses the connection outright. Without this, any
    visitor who merely has a tab open (including on the public login
    screen, which never signs in) could open a raw Socket.IO connection
    and ask to join the "admin" room themselves.
    """
    if "user_id" not in session or session_is_revoked():
        return False
    # Same live account checks as the HTTP routes; stale cookies must not keep receiving pushes.
    account = _current_account_or_none()
    if (account is None or not account["is_active"]
            or session.get("pwv") != password_fingerprint(account["password_hash"])):
        return False
    _sockets_by_user.setdefault(session["user_id"], set()).add(request.sid)
    if session.get("sid"):
        _sockets_by_session.setdefault(session["sid"], set()).add(request.sid)
    if account["role"] == "Admin":
        join_room("admin")
    elif account["branch_id"]:
        join_room(f"branch:{account['branch_id']}")
    else:
        return False


def _emit(scopes, room=None):
    """Tell clients which data scopes changed."""
    if isinstance(scopes, str):
        scopes = [scopes]
    socketio.emit("data_changed", {"scopes": scopes}, room=room)


def notify_admin(scopes):
    """Push to every connected HQ Admin tab."""
    _emit(scopes, room="admin")


def notify_branch(branch_id, scopes):
    """Push to every connected tab signed in as staff of this branch."""
    if branch_id:
        _emit(scopes, room=f"branch:{branch_id}")


def notify_admin_and_branch(branch_id, scopes):
    """The common case: HQ sees everything, and the one branch this
    change belongs to should see it too (e.g. a request they made just
    got dispatched)."""
    notify_admin(scopes)
    notify_branch(branch_id, scopes)


def notify_all(scopes):
    """Broadcast with no room filter — for changes without one obvious
    branch owner that every signed-in tab might still care about (a new
    product added to the catalog, a new branch)."""
    _emit(scopes)


def notify_bell(message, room=None, level="info"):
    """Push a one-line, human-readable alert to the notification bell.

    Unlike everything above (which never carries data, only scope
    names, and exists purely to trigger a silent background refetch),
    this is the one event allowed to carry an actual message — it's
    meant to be read directly, in the bell dropdown in the topbar (see
    initNotificationBell() in main.js), not acted on programmatically.

    room follows the same convention as _emit(): "admin", a specific
    "branch:<id>", or None to reach every signed-in tab. level is a
    free-form hint the frontend uses to color the item's dot —
    "info" | "success" | "warning".
    """
    socketio.emit("bell_notification", {
                  "message": message, "level": level}, room=room)
