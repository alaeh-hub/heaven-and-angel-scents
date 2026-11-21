"""Top bar "Today" sales chip: a branch sees its own, an admin sees all, only today counts."""
from factories import login, make_branch, make_product, make_user
from heaven_angel.utils import compact_peso


def _insert_sale(sql, branch_id, sku, qty, unit_price, days_ago=0):
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO sales (branch_id, sku, qty_sold, unit_price, sold_at, recorded_at)
           VALUES (%s, %s, %s, %s, NOW() - INTERVAL %s DAY, NOW() - INTERVAL %s DAY)""",
        (branch_id, sku, qty, unit_price, days_ago, days_ago),
    )
    sql.commit()
    cur.close()


def test_branch_chip_counts_only_its_own_sales_today(client, sql):
    """Branch chip counts only its own sales today."""
    branch_id = make_branch(sql)
    other_branch = make_branch(sql)
    sku = make_product(sql)
    _insert_sale(sql, branch_id, sku, 2, "150.00")
    _insert_sale(sql, branch_id, sku, 1, "100.00")
    _insert_sale(sql, branch_id, sku, 5, "999.00", days_ago=2)
    _insert_sale(sql, other_branch, sku, 3, "500.00")

    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")

    data = client.get("/api/today-sales").get_json()
    assert data["revenue"] == 400.0
    assert data["count"] == 2
    assert data["revenue_display"] == "₱400.00"

    html = client.get("/branch/").get_data(as_text=True)
    assert 'id="todaySalesChip"' in html
    assert "₱400.00" in html


def test_admin_chip_totals_every_branch(client, sql):
    """Admin chip totals every branch."""
    sku = make_product(sql)
    branch_a = make_branch(sql)
    branch_b = make_branch(sql)

    admin = make_user(sql, role="Admin")
    login(client, admin["username"], admin["password"], "Admin")
    before = client.get("/api/today-sales").get_json()

    _insert_sale(sql, branch_a, sku, 1, "1000.00")
    _insert_sale(sql, branch_b, sku, 2, "250.00")

    after = client.get("/api/today-sales").get_json()
    assert round(after["revenue"] - before["revenue"], 2) == 1500.0
    assert after["count"] - before["count"] == 2
    assert after["scope"] == "all branches"


def test_today_sales_requires_sign_in(client):
    """Today sales requires sign in."""
    assert client.get("/api/today-sales").status_code == 401


def test_today_sales_rejects_deactivated_user_with_live_session(client, sql):
    """Today sales rejects deactivated user with live session."""
    branch_id = make_branch(sql)
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")
    assert client.get("/api/today-sales").status_code == 200

    cur = sql.cursor()
    cur.execute("UPDATE users SET is_active = FALSE WHERE user_id = %s", (user["user_id"],))
    sql.commit()
    cur.close()

    assert client.get("/api/today-sales").status_code == 401


def test_today_sales_uses_current_role_not_login_time_role(client, sql):
    """Today sales uses current role not login time role."""
    branch_id = make_branch(sql)
    sku = make_product(sql)
    _insert_sale(sql, branch_id, sku, 1, "100.00")
    admin = make_user(sql, role="Admin")
    login(client, admin["username"], admin["password"], "Admin")

    cur = sql.cursor()
    cur.execute("UPDATE users SET role = 'Branch', branch_id = %s WHERE user_id = %s",
                (branch_id, admin["user_id"]))
    sql.commit()
    cur.close()

    data = client.get("/api/today-sales").get_json()
    assert data["scope"] != "all branches"


def test_compact_amounts():
    """Compact amounts."""
    assert compact_peso(0) == "₱0"
    assert compact_peso(850) == "₱850"
    assert compact_peso(10000) == "₱10k"
    assert compact_peso(12450) == "₱12.4k"
    assert compact_peso(4030) == "₱4.03k"        # under ten of a unit keeps two decimals
    assert compact_peso(1_250_000) == "₱1.25M"
