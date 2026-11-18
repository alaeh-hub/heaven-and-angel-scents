"""AI assistant tools stay aligned with the pages and reports: every tool
runs cleanly for both roles, and the bottle / bulk-mL split, HQ naming,
bulk-request guard and COGS maths match the rest of the system.
"""
from heaven_angel.routes import ai_tools
from factories import make_branch, make_product, make_scent, unique_suffix


def _add_sale(sql, branch_id, sku, qty, unit_price, customer_name=None, address=None):
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO sales (branch_id, sku, qty_sold, unit_price, customer_name, customer_address)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (branch_id, sku, qty, unit_price, customer_name, address),
    )
    sql.commit()
    cur.close()


def _admin_ctx():
    return {"role": "Admin", "branch_id": None, "user_id": None, "username": "test-admin"}


def _branch_ctx(branch_id):
    return {"role": "Branch", "branch_id": branch_id, "user_id": None, "username": "test-branch"}


READ_TOOLS = {
    "check_stock": {"item_name": "Test Product"},
    "get_low_stock": {},
    "get_pending_deliveries": {},
    "get_sales_summary": {"period": "all_time", "by_branch": True},
    "get_credit_purchases": {},
    "get_customers": {},
    "get_recent_sales": {"limit": 5},
    "get_discrepancies": {},
    "get_voided_sales": {"period": "all_time"},
    "get_inventory_log": {"period": "all_time"},
    "find_report": {"topic": "sales"},
}
ADMIN_TOOLS = {
    "get_raw_materials": {},
    "get_scent_oils": {},
    "get_suppliers": {},
    "get_formulas": {},
    "get_bulk_batches": {"active_only": False},
    "get_production_costs": {"period": "all_time"},
    "get_packages": {"include_inactive": True},
    "get_partner_inquiries": {},
    "get_branch_performance": {},
    "get_financials": {"period": "all_time"},
    "get_material_price_history": {},
}


def test_every_tool_runs_for_its_roles(app, sql):
    branch_id = make_branch(sql)
    sku = make_product(sql)
    _add_sale(sql, branch_id, sku, 1, "100.00", f"Cust {unique_suffix()}")
    with app.test_request_context():
        for name, args in {**READ_TOOLS, **ADMIN_TOOLS}.items():
            out = ai_tools.dispatch(name, args, _admin_ctx())
            assert "error" not in out, (name, out)
        for name, args in READ_TOOLS.items():
            out = ai_tools.dispatch(name, args, _branch_ctx(branch_id))
            assert "error" not in out, (name, out)
        for name in ADMIN_TOOLS:
            out = ai_tools.dispatch(name, {}, _branch_ctx(branch_id))
            assert out.get("error") == "That information is only available to HQ admins."


def test_sales_tools_keep_bulk_ml_apart_and_customer_address(app, sql):
    branch_id = make_branch(sql)
    bottle, base = make_scent(sql)
    bulk = f"{base}-BULK"
    customer = f"AI Buyer {unique_suffix()}"
    _add_sale(sql, branch_id, bottle, 2, "500.00", customer, "First Street")
    _add_sale(sql, branch_id, bulk, 100, "5.00", customer, "Second Street")
    with app.test_request_context():
        summary = ai_tools.dispatch("get_sales_summary", {"period": "all_time"}, _branch_ctx(branch_id))
        cust = ai_tools.dispatch("get_customers", {"name": customer}, _branch_ctx(branch_id))
        recent = ai_tools.dispatch("get_recent_sales", {}, _branch_ctx(branch_id))

    assert summary["units"] == 2 and summary["bulk_ml"] == 100
    row = cust["results"][0]
    assert (row["units"], row["bulk_ml"]) == (2, 100)
    assert row["address"] == "First Street"
    assert {r["qty_unit"] for r in recent["results"]} == {"bottles", "mL"}


def test_branch_check_stock_hides_bulk_and_propose_rejects_bulk(app, sql):
    branch_id = make_branch(sql)
    # Bulk is not a product: there is no such SKU to check or to request.
    bulk = f"NOPE-{unique_suffix().upper()}-BULK"
    with app.test_request_context():
        stock = ai_tools.dispatch("check_stock", {"sku": bulk}, _branch_ctx(branch_id))
        draft = ai_tools.dispatch("propose_stock_request", {"items": [{"sku": bulk, "qty": 5}]},
                                  _branch_ctx(branch_id))
    assert stock["results"] == []
    assert draft["error"]


def test_admin_can_name_hq_for_sales(app, sql):
    with app.test_request_context():
        out = ai_tools.dispatch("get_sales_summary", {"period": "all_time", "branch_name": "HQ"}, _admin_ctx())
    assert "error" not in out, out


def test_scent_oil_tool_lists_each_scents_oil_and_flags_the_shared_pool(app, sql):
    code = "ZZ" + unique_suffix().upper()
    cur = sql.cursor()
    cur.execute("INSERT INTO scent_oils (base_code, scent_name, cost_per_unit, stock_qty) VALUES (%s, 'AI Oil', 3.8, 250)",
                (code,))
    cur.execute("INSERT INTO scent_oil_restocks (base_code, qty, cost, purchased_at) VALUES (%s, 250, 950, '2026-11-04')",
                (code,))
    sql.commit()
    cur.close()
    with app.test_request_context():
        out = ai_tools.dispatch("get_scent_oils", {"name": code}, _admin_ctx())
        materials = ai_tools.dispatch("get_raw_materials", {}, _admin_ctx())
        costs = ai_tools.dispatch("get_production_costs", {"period": "all_time"}, _admin_ctx())
    row = out["results"][0]
    assert (row["scent"], row["stock_qty"], row["price_per_unit"], row["stock_value"]) == ("AI Oil", 250.0, 3.8, 950.0)
    assert (row["bought_qty"], row["bought_cost"]) == (250.0, 950.0)
    assert out["all_scents"]["scents"] >= 1
    assert "get_scent_oils" in materials["note"] and materials["scent_oils"]["scents"] >= 1
    assert "bulk_ml_produced" not in costs and "of_which_opening_balance" in costs
