"""Shared pieces for the partner portal's route modules: the blueprint, the
slug gate and the read-endpoint rate limit/cache helpers."""
import secrets

from flask import Blueprint, abort, current_app

from heaven_angel.extensions import limiter


bp = Blueprint("portal", __name__, url_prefix="/partner-portal")


# Reads run several queries per hit and need only the slug, so they share one
# rate-limit bucket per client.
_read_limit = limiter.shared_limit("120 per minute", scope="portal-read")


# Lists change rarely, so browsers may reuse them for 30s ("private": no shared proxy).
# Not used for responses that carry a CSRF token.
_LIST_CACHE_SECONDS = 30


# Add a short private cache header to a read response.
def _cacheable(response):
    response.headers["Cache-Control"] = f"private, max-age={_LIST_CACHE_SECONDS}"
    return response


def _verify_slug(slug):
    """Gate every route in this blueprint behind the configured secret
    slug (see PARTNER_PORTAL_SLUG in config.py). A wrong or missing
    slug 404s exactly like a page that doesn't exist — it never reveals
    that a portal lives at this path at all.

    secrets.compare_digest avoids leaking the real slug's length/prefix
    through response-timing differences.
    """
    configured = current_app.config.get("PARTNER_PORTAL_SLUG", "")
    if not configured or not secrets.compare_digest(slug, configured):
        abort(404)
