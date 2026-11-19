
import secrets
import time

from flask import (
    current_app, flash, redirect, render_template,
    request, session, url_for,
)
from werkzeug.security import generate_password_hash

from heaven_angel.db import execute, query
from heaven_angel.decorators import admin_required
from heaven_angel.services.audit import log_action
from heaven_angel.sockets import disconnect_user, notify_admin
from heaven_angel.utils import (
    generate_temp_password, is_duplicate_error,
)

from heaven_angel.routes.admin._common import bp

# One-shot store for a freshly generated temporary password. The signed session
# cookie is readable by anyone who sees it, so it carries only a random token;
# the password itself stays in server memory (single gunicorn worker) for 2 min.
_reveals = {}
_REVEAL_TTL = 120


def _stash_reveal(username, password):
    now = time.time()
    for k in [k for k, v in _reveals.items() if v[0] < now]:
        _reveals.pop(k, None)
    token = secrets.token_urlsafe(16)
    _reveals[token] = (now + _REVEAL_TTL, {"username": username, "password": password})
    return token


def _pop_reveal(token):
    entry = _reveals.pop(token, None) if token else None
    if entry and entry[0] >= time.time():
        return entry[1]
    return None


# ---------------------------------------------------------------- user accounts
@bp.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role")
        branch_id = request.form.get("branch_id") or None

        # For a Branch account, branch_id must be both present AND
        # actually point at a real, non-HQ branch. Without the second
        # check, a hand-crafted POST (bypassing the dropdown, which only
        # ever lists non-HQ branches) could set branch_id=1 — HQ's own
        # branch row — and create a "Branch" account that sees and can
        # sell against the HQ warehouse through the ordinary branch UI,
        # which every branch-side query trusts blindly via session.
        valid_branch = None
        if role == "Branch" and branch_id:
            valid_branch = query(
                "SELECT branch_id FROM branches WHERE branch_id = %s AND is_hq = FALSE",
                (branch_id,), fetchone=True,
            )

        if role not in ("Admin", "Branch"):
            flash("Select a valid role.", "error")
        elif role == "Branch" and not branch_id:
            flash("Select a branch for this Branch account.", "error")
        elif role == "Branch" and not valid_branch:
            flash("Select a valid branch.", "error")
        elif not username or len(password) < 8:
            flash(
                "Username is required and password must be at least 8 characters.", "error")
        else:
            try:
                execute(
                    """INSERT INTO users (username, password_hash, role, branch_id, must_change_password)
                       VALUES (%s, %s, %s, %s, TRUE)""",
                    (username, generate_password_hash(password),
                     role, branch_id if role == "Branch" else None),
                )
                notify_admin("users")
                branch_detail = f", branch_id={branch_id}" if role == "Branch" else ""
                log_action("create_account", target=username,
                           details=f"role={role}{branch_detail}")
                flash(
                    f"Account '{username}' created. They'll be asked to set a new password at first login.", "success")
            except Exception as exc:
                current_app.logger.exception(
                    "create account failed for username=%s role=%s", username, role)
                if is_duplicate_error(exc):
                    flash(f"Username '{username}' is already taken.", "error")
                else:
                    flash("Couldn't create that account - the username may be too long (max 80).", "error")
        return redirect(url_for("admin.users"))

    accounts = query(
        """SELECT u.user_id, u.username, u.role, u.is_active, u.created_at, b.branch_name
           FROM users u LEFT JOIN branches b ON u.branch_id = b.branch_id
           ORDER BY u.role, b.branch_name"""
    )
    branch_list = query(
        "SELECT branch_id, branch_name FROM branches WHERE is_hq = FALSE ORDER BY branch_name")
    # pop(), not get() — this must only ever be readable once. If it's
    # left in the session, refreshing this page (or hitting back/forward)
    # would keep re-showing a temporary password that may already have
    # been handed to the staff member and could be stale or reused.
    temp_password_reveal = _pop_reveal(session.pop("temp_password_reveal", None))
    return render_template(
        "admin/users.html", accounts=accounts, branch_list=branch_list,
        temp_password_reveal=temp_password_reveal,
    )


@bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def toggle_user(user_id):
    if user_id == session.get("user_id"):
        flash("You can't deactivate your own account.", "error")
    else:
        target = query(
            "SELECT username, is_active FROM users WHERE user_id = %s", (user_id,), fetchone=True)
        if not target:
            flash("Account not found.", "error")
            return redirect(url_for("admin.users"))
        # Compare-and-set against the state the admin saw, so a double-click
        # or a stale page can't silently flip it back.
        _, changed = execute(
            "UPDATE users SET is_active = %s WHERE user_id = %s AND is_active = %s",
            (not target["is_active"], user_id, target["is_active"]))
        if changed == 0:
            flash("That account was already changed - refresh and check its status.", "error")
            return redirect(url_for("admin.users"))
        if target["is_active"]:
            disconnect_user(user_id)       # being deactivated: cut off live pushes now
        notify_admin("users")
        new_status = "Deactivated" if target["is_active"] else "Reactivated"
        log_action("toggle_user",
                   target=target["username"], details=new_status)
        flash("Account status updated.", "success")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def reset_user_password(user_id):
    target = query("SELECT username FROM users WHERE user_id = %s",
                   (user_id,), fetchone=True)
    if not target:
        flash("Account not found.", "error")
        return redirect(url_for("admin.users"))

    temp_password = generate_temp_password()
    execute(
        "UPDATE users SET password_hash = %s, must_change_password = TRUE WHERE user_id = %s",
        (generate_password_hash(temp_password), user_id),
    )
    # Hand the plaintext password to the one-shot reveal modal in
    # users.html rather than putting it in a flash message. Flash
    # messages render as plain banner text mixed in with every other
    # message on the page — this way it only ever appears once, inside
    # the dedicated modal that already exists for it (copy button,
    # "won't be shown again" framing), and users() pops it from the
    # session immediately after reading it so a page refresh or back
    # button can't bring it back.
    session["temp_password_reveal"] = _stash_reveal(target["username"], temp_password)
    disconnect_user(user_id)
    log_action("reset_password", target=target["username"])
    flash(
        f"Password for '{target['username']}' reset. They'll choose a new one at next sign-in.",
        "success",
    )
    return redirect(url_for("admin.users"))
