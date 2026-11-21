
from flask import (
    flash, redirect, render_template,
    request, url_for,
)

from heaven_angel.db import query
from heaven_angel.decorators import admin_required
from heaven_angel.services.audit import log_action
from heaven_angel.sockets import notify_bell
from heaven_angel.utils import (
    ValidationError,
    parse_required_text,
)

from heaven_angel.routes.admin._common import bp


# ---------------------------------------------------------------- announcements
@bp.route("/announcements", methods=["GET", "POST"])
@admin_required
def announcements():
    """Send a one-line bell message to all branches or one (not stored; logged)."""
    branch_list = query(
        "SELECT branch_id, branch_name FROM branches ORDER BY branch_name")

    if request.method == "POST":
        target = request.form.get("target", "all")
        level = request.form.get("level", "info")
        if level not in ("info", "success", "warning"):
            level = "info"

        try:
            message = parse_required_text(
                request.form.get("message", ""), "Message", max_length=500)
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("admin.announcements"))

        if target == "all":
            notify_bell(message, room=None, level=level)
            log_action("send_announcement", target="All branches",
                       details=message[:255])
            flash("Announcement sent to all branches currently online.", "success")
        else:
            branch = query(
                "SELECT branch_id, branch_name FROM branches WHERE branch_id = %s",
                (target,), fetchone=True,
            )
            if not branch:
                flash("Select a valid branch.", "error")
                return redirect(url_for("admin.announcements"))
            notify_bell(
                message, room=f"branch:{branch['branch_id']}", level=level)
            log_action("send_announcement",
                       target=branch["branch_name"], details=message[:255])
            flash(
                f"Announcement sent to {branch['branch_name']} (if currently online).", "success")
        return redirect(url_for("admin.announcements"))

    return render_template("admin/announcements.html", branch_list=branch_list)
