"""Report generation stays aligned with the pages: every report type
builds and renders (PDF + Excel) for the roles that can pull it, in every
time mode, and the page rules (low stock, Closed-only package sales,
bulk mL kept apart, branch-only filters) hold in the generated data.
"""
from factories import make_branch, make_inventory, make_product, unique_suffix
from heaven_angel.services.reports import REPORT_TYPES, get_report, parse_report_filters, render_report_excel, render_report_pdf


def test_every_report_builds_and_renders(app, sql):
    branch_id = make_branch(sql)
    sku = make_product(sql)
    make_inventory(sql, branch_id, sku, stock_qty=1, reorder_level=10)
    with app.test_request_context():
        for key, meta in REPORT_TYPES.items():
            modes = ("recent", "all", "month") if meta["windowed"] else ("recent",)
            scopes = [None] + ([branch_id] if meta["branch"] else [])
            for mode in modes:
                for scope in scopes:
                    report = get_report(key, parse_report_filters({"mode": mode}), branch_scope=scope)
                    if report["rows"]:
                        assert render_report_pdf(report).getvalue(), key
                        assert render_report_excel(report).getvalue(), key


def test_low_stock_report_matches_low_stock_rule(app, sql):
    branch_id = make_branch(sql)
    low = make_product(sql)
    make_inventory(sql, branch_id, low, stock_qty=2, reorder_level=10)
    with app.test_request_context():
        report = get_report("low_stock", parse_report_filters({}), branch_scope=branch_id)
    skus = {r["sku"] for r in report["rows"]}
    assert low in skus
    assert not any(sku.endswith("-BULK") for sku in skus)


def test_branch_filter_only_labels_reports_that_use_it(app, sql):
    branch_id = make_branch(sql)
    with app.test_request_context():
        products = get_report("products", parse_report_filters({"branch_id": str(branch_id)}))
    # Products ignores the Branch filter, so a leftover branch_id must not
    # put a branch name in the subtitle.
    assert products["subtitle"] == "Snapshot as of now"


def test_partner_inquiry_amounts_only_total_closed_sales(app, sql):
    cur = sql.cursor()
    cur.execute(
        """INSERT INTO partner_inquiries (partner_type, company_name, contact_person, phone, email,
                                          package_name_snapshot, order_amount, status)
           VALUES ('Reseller', 'Lead Co', 'A', '0917', 'a@example.com', 'Starter (5 items)', 1000, 'New'),
                  ('Reseller', 'Buyer Co', 'B', '0918', 'b@example.com', 'Starter (5 items)', 2500, 'Closed')"""
    )
    sql.commit()
    cur.close()
    with app.test_request_context():
        report = get_report("partner_inquiries", parse_report_filters({"mode": "all"}))
    rows = {r["company_name"].split(" — ")[0]: r for r in report["rows"]}
    assert rows["Lead Co"]["sale_amount"] is None
    assert rows["Buyer Co"]["sale_amount"] == 2500
    assert rows["Buyer Co"]["package_name_snapshot"] == "Starter"
    assert "package_value" not in (report["totals"] or {})


def _scent_oil_with_restock(sql, code, name, price, qty, cost, purchased="2026-11-04"):
    cur = sql.cursor()
    cur.execute("INSERT INTO scent_oils (base_code, scent_name, cost_per_unit, stock_qty) VALUES (%s, %s, %s, %s)",
                (code, name, price, qty))
    cur.execute("INSERT INTO scent_oil_restocks (base_code, qty, cost, purchased_at) VALUES (%s, %s, %s, %s)",
                (code, qty, cost, purchased))
    sql.commit()
    cur.close()


def test_scent_oil_purchases_show_in_the_materials_and_scent_oils_reports(app, sql):
    code = "ZZ" + unique_suffix().upper()
    _scent_oil_with_restock(sql, code, "Report Oil", "3.8", 250, 950)
    with app.test_request_context():
        scents = get_report("scent_oils", parse_report_filters({"search": code}))
        materials = get_report("materials", parse_report_filters({"mode": "all", "search": "Report Oil"}))
    assert [r["scent_name"] for r in scents["rows"]] == ["Report Oil"]
    row = scents["rows"][0]
    assert (row["stock_qty"], row["cost_per_unit"], row["stock_value"]) == (250.0, 3.8, 950.0)
    assert (row["bought_qty"], row["bought_cost"]) == (250.0, 950.0)
    bought = [r for r in materials["rows"] if r["material_name"].endswith("Report Oil")]
    assert len(bought) == 1 and bought[0]["package_cost"] == 950.0 and bought[0]["cost_per_unit"] == 3.8


def test_raw_materials_purchased_includes_oil_bought_per_scent(app, sql):
    def purchased():
        with app.test_request_context():
            report = get_report("financial_summary", parse_report_filters({"mode": "all"}))
        return next(r["figure_amount"] for r in report["rows"] if r["figure"].startswith("Raw materials purchased"))
    before = purchased()
    _scent_oil_with_restock(sql, "ZZ" + unique_suffix().upper(), "Spend Oil", "4", 100, 400)
    assert purchased() == before + 400


def test_oil_bought_per_scent_counts_toward_the_oil_materials_supplier(app, client, sql):
    from factories import login, make_raw_material, make_user
    name = f"Oil Supplier {unique_suffix()}"
    cur = sql.cursor()
    cur.execute("INSERT INTO suppliers (supplier_name) VALUES (%s)", (name,))
    supplier_id = cur.lastrowid
    oil = make_raw_material(sql, unit="Milliliter", package_qty="100", package_cost="100.00")
    cur.execute("UPDATE raw_materials SET supplier_id = %s WHERE material_id = %s", (supplier_id, oil))
    cur.execute("UPDATE batch_formula_materials SET material_id = %s WHERE role = 'oil'", (oil,))
    sql.commit()
    cur.close()

    def spent():
        with app.test_request_context():
            return get_report("suppliers", parse_report_filters({"search": name}))["rows"][0]["total_spent"]

    before = spent()                                    # the material's own 100, plus any oil already bought
    assert before >= 100.0
    _scent_oil_with_restock(sql, "ZZ" + unique_suffix().upper(), "Supplier Oil", "4", 100, 400)
    assert spent() == before + 400.0                    # 400 more of oil bought lands on the supplier

    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    page = client.get("/admin/materials").get_data(as_text=True)
    assert name in page and f"{before + 400:,.2f}" in page
