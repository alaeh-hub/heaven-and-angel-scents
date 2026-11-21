"""login_required / admin_required / branch_required, the chokepoint for protected routes.

Anonymous users go to login; the wrong role gets 403; an account deactivated
mid-session loses access on its next request; must_change_password forces
/change-password (except endpoints that must stay reachable).
"""
from factories import login, make_branch, make_user, set_account_active

ADMIN_ROUTE = "/admin/"
BRANCH_ROUTE = "/branch/inventory"


def test_anonymous_user_redirected_to_login_from_admin_route(client):
    """Anonymous user redirected to login from admin route."""
    resp = client.get(ADMIN_ROUTE)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_anonymous_user_redirected_to_login_from_branch_route(client):
    """Anonymous user redirected to login from branch route."""
    resp = client.get(BRANCH_ROUTE)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_branch_user_gets_403_on_admin_route(client, sql):
    """Branch user gets 403 on admin route."""
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")

    resp = client.get(ADMIN_ROUTE)

    assert resp.status_code == 403


def test_admin_user_gets_403_on_branch_route(client, sql):
    """Admin user gets 403 on branch route."""
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")

    resp = client.get(BRANCH_ROUTE)

    assert resp.status_code == 403


def test_admin_user_can_reach_admin_dashboard(client, sql):
    """Admin user can reach admin dashboard."""
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")

    resp = client.get(ADMIN_ROUTE)

    assert resp.status_code == 200


def test_branch_user_can_reach_branch_inventory(client, sql):
    """Branch user can reach branch inventory."""
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")

    resp = client.get(BRANCH_ROUTE)

    assert resp.status_code == 200


def test_deactivated_account_loses_access_on_its_next_request(client, sql):
    """is_active is re-read from the database every request, so deactivation is immediate.

    This catches any change that trusts the session's cached role/is_active.
    """
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")
    assert client.get(BRANCH_ROUTE).status_code == 200

    set_account_active(sql, user["user_id"], False)

    resp = client.get(BRANCH_ROUTE)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]

    # The session was cleared; a follow-up request must not regain access.
    resp2 = client.get(BRANCH_ROUTE)
    assert resp2.status_code == 302
    assert "/login" in resp2.headers["Location"]


def test_must_change_password_redirects_everywhere_except_the_exempt_endpoints(client, sql):
    """Must change password redirects everywhere except the exempt endpoints."""
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id,
                      must_change_password=True)
    login(client, user["username"], user["password"], "Branch")

    # An ordinary protected page is blocked...
    resp = client.get(BRANCH_ROUTE)
    assert resp.status_code == 302
    assert "change-password" in resp.headers["Location"]

    # ...but change-password must stay reachable, or the account is locked out.
    resp = client.get("/change-password")
    assert resp.status_code == 200
