"""The built-in bulk batch formulas (formula.txt, see batch_formulas.py and
migration 50 in schema.sql): the user picks Formula 15% / 14% / 13% and a
size, the materials and amounts are fixed, and the alcohol + water mix is
split automatically.

Numbers mirror Costing_Calculator_By_Formula.xlsx, Formula 15% / 500 ml:
oil scent 500, fixative 10 g, mix 2800, moisturizer 26 -> 3336 ml,
39 bottles of 85 ml, mix split 200 : 1000 alcohol : water.
"""
from decimal import Decimal

from factories import (get_form_token, get_raw_material, login, make_raw_material,
                       make_user)
from heaven_angel.routes.admin.batch_formulas import (
    PACKAGING_ROLES, ROLES, compute_formula, formula_for_scent, load_formula_config, split_mix)

BULK_BATCHES_URL = "/admin/bulk-batches"
D = Decimal
# Hand-written config for the pure-function tests (the real one is read from the DB).
CONFIG = {
    "bottle_unit": "85ML", "bottle_ml": D(85), "alcohol_parts": D(200), "water_parts": D(1000),
    "packaging": {role: D(1) for role in PACKAGING_ROLES},
    "formulas": {"15%": {"scents": "", "sizes": {500: (D(10), D(2800), D(26))}}},
}
MATERIALS_URL = "/admin/bulk-batches/formula-materials"
PRODUCE_URL = "/admin/bulk-batches/produce"


def _signed_in_admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")
    return user


def _rows(sql, query, params=()):
    cur = sql.cursor(dictionary=True)
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    return rows


def _make_role_materials(sql):
    """One raw material per role, priced like the spreadsheet's Settings sheet."""
    return {
        "oil": make_raw_material(sql, unit="Milliliter", package_qty="1000", package_cost="4000"),
        "fixative": make_raw_material(sql, unit="Gram", package_qty="140", package_cost="490"),
        "alcohol": make_raw_material(sql, unit="Milliliter", package_qty="10000", package_cost="1320.9"),
        # Stocked by the Liter, while the formula measures it in mL.
        "water": make_raw_material(sql, unit="Liter", package_qty="10", package_cost="82"),
        "moisturizer": make_raw_material(sql, unit="Milliliter", package_qty="1000", package_cost="310"),
        "bottle": make_raw_material(sql, unit="Piece", package_qty="500", package_cost="7500"),      # ₱15
        "sticker": make_raw_material(sql, unit="Piece", package_qty="500", package_cost="1500"),     # ₱3
        "seal": make_raw_material(sql, unit="Piece", package_qty="500", package_cost="750"),         # ₱1.50
    }


def _set_scent_oil(sql, code, price, stock):
    cur = sql.cursor()
    cur.execute("UPDATE scent_oils SET cost_per_unit = %s, stock_qty = %s WHERE base_code = %s",
                (price, stock, code))
    sql.commit()
    cur.close()


def _save_materials(client, m):
    resp = client.post(MATERIALS_URL, data={role: str(m[role]) for role in ROLES})
    assert resp.status_code == 302


def test_formula_amounts_in_the_database_match_formula_txt(app):
    """The seed in schema.sql (migration 50) holds formula.txt's numbers."""
    with app.app_context():
        config = load_formula_config()
    sizes = {name: {size: tuple(int(x) for x in amounts) for size, amounts in spec["sizes"].items()}
             for name, spec in config["formulas"].items()}
    assert sizes["15%"] == {500: (10, 2800, 26), 250: (5, 1400, 13), 125: (3, 700, 7)}
    assert sizes["14%"] == {500: (10, 3000, 26), 250: (5, 1500, 13), 125: (3, 750, 7)}
    assert sizes["13%"] == {500: (10, 3300, 26), 250: (5, 1650, 13), 125: (3, 825, 7)}
    assert config["bottle_unit"] == "85ML" and config["bottle_ml"] == 85
    assert (config["alcohol_parts"], config["water_parts"]) == (200, 1000)
    assert config["packaging"] == {"bottle": 1, "sticker": 1, "seal": 1}
    # The totals written in formula.txt: oil + fixative + alcohol + moisturizer.
    for formula, totals in (("15%", (3336, 1668, 835)), ("14%", (3536, 1768, 885)),
                            ("13%", (3836, 1918, 960))):
        for size, total in zip((500, 250, 125), totals):
            fixative, mix, moisturizer = sizes[formula][size]
            assert size + fixative + mix + moisturizer == total


def test_split_mix_matches_the_spreadsheet():
    alcohol_ml, water_ml = split_mix(Decimal("2800"), D(200), D(1000))
    assert alcohol_ml == Decimal("466.667")
    assert water_ml == Decimal("2333.333")
    assert alcohol_ml + water_ml == Decimal("2800")


def test_compute_formula_costs_like_the_spreadsheet():
    def mat(i, unit, cost):
        return {"material_id": i, "material_name": f"m{i}", "unit": unit, "cost_per_unit": Decimal(cost)}
    resolved = {
        "oil": mat(1, "Milliliter", "4"), "fixative": mat(2, "Gram", "3.5"),
        "alcohol": mat(3, "Milliliter", "0.13209"), "moisturizer": mat(4, "Milliliter", "0.31"),
        "water": mat(5, "Liter", "8.2"),
    }
    resolved.update({"bottle": mat(6, "Piece", "15"), "sticker": mat(7, "Piece", "3"),
                     "seal": mat(8, "Piece", "1.5")})
    calc = compute_formula("15%", 500, resolved, CONFIG)
    assert calc["total_ml"] == Decimal("3336")
    assert calc["bottles"] == 39
    # Spreadsheet: liquid subtotal ₱2,123.84 (mix = ₱80.78).
    assert abs(calc["liquid_cost"] - Decimal("2123.84")) < Decimal("0.02")
    # 39 bottles x (15 + 3 + 1.50), worked out automatically from the bottle count.
    assert calc["packaging_cost"] == Decimal("760.50")
    assert [p["qty"] for p in calc["packaging"]] == [Decimal("39")] * 3
    assert abs(calc["cost_per_refill_bottle"] - Decimal("54.46")) < Decimal("0.02")
    assert compute_formula("15%", 500, {}, CONFIG) is None


def test_page_lists_the_three_formulas_and_sizes(client, sql):
    _signed_in_admin(client, sql)
    page = client.get(BULK_BATCHES_URL).get_data(as_text=True)
    for label in ("Formula 15%", "Formula 14%", "Formula 13%", "500 ml", "250 ml", "125 ml"):
        assert label in page
    assert "fmProduceForm" in page


def test_producing_from_a_formula_splits_the_mix_and_deducts_stock(client, sql):
    _signed_in_admin(client, sql)
    m = _make_role_materials(sql)
    _save_materials(client, m)

    token = get_form_token(client, BULK_BATCHES_URL)
    resp = client.post(PRODUCE_URL, data={
        "form_token": token, "formula": "15%", "size": "500",
        "scent_name": "Lacoste Red", "batch_code": "", "notes": "",
    })
    assert resp.status_code == 302

    batch = _rows(sql, "SELECT * FROM bulk_batches WHERE scent_name = 'Lacoste Red' "
                       "ORDER BY batch_id DESC")[0]
    assert batch["formula_label"] == "Formula 15%, 500 ml"
    assert batch["total_volume_ml"] == Decimal("3336.000")
    assert batch["target_unit"] == "85ML"
    assert batch["target_bottle_qty"] == 39            # floor(3336 / 85)

    lines = {r["material_id"]: r for r in _rows(
        sql, "SELECT * FROM bulk_batch_materials WHERE batch_id = %s AND is_packaging = FALSE",
        (batch["batch_id"],))}
    assert lines[m["oil"]]["qty_used"] == Decimal("500")
    assert lines[m["fixative"]]["qty_used"] == Decimal("10")
    assert lines[m["alcohol"]]["qty_used"] == Decimal("466.667")
    assert lines[m["water"]]["qty_used"] == Decimal("2333.333")
    assert lines[m["moisturizer"]]["qty_used"] == Decimal("26")

    # Stock is deducted in each material's own unit — water by the Liter.
    assert get_raw_material(sql, m["alcohol"])["stock_qty"] == Decimal("10000") - Decimal("466.667")
    assert abs(get_raw_material(sql, m["water"])["stock_qty"] - Decimal("7.667")) < Decimal("0.001")

    liquid = sum(r["line_cost"] for r in lines.values())
    assert abs(liquid - Decimal("2123.84")) < Decimal("0.05")

    # Packaging is automatic: 39 bottles -> 39 of each, deducted and costed.
    packaging = {r["material_id"]: r for r in _rows(
        sql, "SELECT * FROM bulk_batch_materials WHERE batch_id = %s AND is_packaging = TRUE",
        (batch["batch_id"],))}
    assert {pid: r["qty_used"] for pid, r in packaging.items()} == {
        m["bottle"]: Decimal("39"), m["sticker"]: Decimal("39"), m["seal"]: Decimal("39")}
    assert get_raw_material(sql, m["bottle"])["stock_qty"] == Decimal("500") - 39
    assert batch["total_cost"] == liquid.quantize(Decimal("0.01")) + Decimal("760.50")


def test_producing_without_materials_set_up_is_refused(client, sql):
    _signed_in_admin(client, sql)
    token = get_form_token(client, BULK_BATCHES_URL)
    before = len(_rows(sql, "SELECT 1 FROM bulk_batches"))
    # Bad formula / size are refused whatever the materials are.
    client.post(PRODUCE_URL, data={"form_token": token, "formula": "99%", "size": "500",
                                   "scent_name": "Nope"})
    token = get_form_token(client, BULK_BATCHES_URL)
    client.post(PRODUCE_URL, data={"form_token": token, "formula": "15%", "size": "7",
                                   "scent_name": "Nope"})
    assert len(_rows(sql, "SELECT 1 FROM bulk_batches")) == before


def test_materials_must_be_distinct_per_role(client, sql):
    _signed_in_admin(client, sql)
    m = _make_role_materials(sql)
    before = _rows(sql, "SELECT COUNT(*) AS n FROM batch_formula_materials")[0]["n"]
    bad = {role: str(m[role]) for role in ROLES}
    bad["water"] = bad["alcohol"]
    client.post(MATERIALS_URL, data=bad)
    assert _rows(sql, "SELECT COUNT(*) AS n FROM batch_formula_materials")[0]["n"] == before


def test_schema_seeds_the_constant_suppliers_and_materials(sql):
    suppliers = {r["supplier_name"]: r["address"] for r in _rows(
        sql, "SELECT supplier_name, address FROM suppliers")}
    assert suppliers["BubbleMix Fragrances and Household Goods Store"].startswith("107 E. Rodriguez Sr. Ave.")
    assert suppliers["Centralpack"].startswith("58 F. Bautista St.")
    assert suppliers["Alfamart - Lipa"] == "Lipa City, Batangas"
    assert suppliers["Shopee"] is None

    by_name = {r["material_name"]: r for r in _rows(
        sql, """SELECT rm.material_name, rm.unit, rm.cost_per_unit, s.supplier_name
                FROM raw_materials rm LEFT JOIN suppliers s ON s.supplier_id = rm.supplier_id""")}
    bubblemix = "BubbleMix Fragrances and Household Goods Store"
    for name in ("Oil Scent", "Fixative", "Moisturizer"):
        assert by_name[name]["supplier_name"] == bubblemix
    for name in ("Perfume Alcohol", "Frosted 85 ml Bottle"):
        assert by_name[name]["supplier_name"] == "Centralpack"
    assert by_name["Distilled Water"]["supplier_name"] == "Alfamart - Lipa"
    for name in ("Sticker", "Plastic Seal"):
        assert by_name[name]["supplier_name"] == "Shopee"
    assert by_name["Oil Scent"]["cost_per_unit"] == Decimal("4.0000")
    assert by_name["Distilled Water"]["unit"] == "Liter"

    # Every formula role is linked, so the formulas price with no setup.
    roles = {r["role"] for r in _rows(sql, "SELECT role FROM batch_formula_materials")}
    assert roles == set(ROLES)
    assert set(PACKAGING_ROLES) <= roles


def test_compute_formula_reports_which_materials_are_short():
    def mat(i, unit, cost):
        return {"material_id": i, "material_name": f"m{i}", "unit": unit, "cost_per_unit": Decimal(cost)}
    resolved = {
        "oil": mat(1, "Milliliter", "4"), "fixative": mat(2, "Gram", "3.5"),
        "alcohol": mat(3, "Milliliter", "0.13"), "moisturizer": mat(4, "Milliliter", "0.31"),
        "water": mat(5, "Liter", "8.2"),
    }
    resolved.update({"bottle": mat(6, "Piece", "15"), "sticker": mat(7, "Piece", "3"),
                     "seal": mat(8, "Piece", "1.5")})
    plenty = {i: Decimal("100000") for i in range(1, 9)}
    assert compute_formula("15%", 500, resolved, CONFIG, plenty)["enough_stock"] is True

    # Oil: 500 ml needed, 400 in stock. Water is stocked in liters: 2333.333 ml
    # needed = 2.333 L, 2 L in stock. Bottles: 39 needed, 30 in stock.
    stock = dict(plenty)
    stock.update({1: Decimal("400"), 5: Decimal("2"), 6: Decimal("30")})
    calc = compute_formula("15%", 500, resolved, CONFIG, stock)
    assert calc["enough_stock"] is False
    short = {s["name"]: s["short_by"] for s in calc["shortages"]}
    assert short["m1"] == Decimal("100")
    assert abs(short["m5"] - Decimal("333.333")) < Decimal("0.001")   # reported in mL
    assert short["m6"] == Decimal("9")           # packaging: 39 bottles needed, 30 in stock
    assert set(short) == {"m1", "m5", "m6"}


def test_producing_with_too_little_stock_is_refused(client, sql):
    _signed_in_admin(client, sql)
    m = _make_role_materials(sql)
    _save_materials(client, m)
    cur = sql.cursor()
    cur.execute("UPDATE raw_materials SET stock_qty = 100 WHERE material_id = %s", (m["oil"],))
    sql.commit()
    cur.close()

    page = client.get(BULK_BATCHES_URL).get_data(as_text=True)
    assert "enough_stock" in page          # stock status is sent to the summary panel

    token = get_form_token(client, BULK_BATCHES_URL)
    resp = client.post(PRODUCE_URL, data={
        "form_token": token, "formula": "15%", "size": "500", "scent_name": "Too Little Oil"},
        follow_redirects=True)
    assert b"Not enough materials" in resp.data
    assert not _rows(sql, "SELECT 1 FROM bulk_batches WHERE scent_name = 'Too Little Oil'")
    assert get_raw_material(sql, m["oil"])["stock_qty"] == Decimal("100")


def test_scents_map_to_their_formula_from_the_database(app):
    """The SKU Map sheet: A9, H14, H13, H10, H2 use 14%; H8, H9 use 13%; the rest 15%."""
    with app.app_context():
        config = load_formula_config()
        assert config["default_formula"] == "15%"
        for code in ("A9", "H14", "H13", "H10", "H2"):
            assert formula_for_scent(config, code) == "14%"
        for code in ("H8", "H9"):
            assert formula_for_scent(config, code) == "13%"
        for code in ("A1", "H1", "H17", "SOMETHING-NEW"):
            assert formula_for_scent(config, code) == "15%"
        # The page text is built from the same table.
        assert config["formulas"]["14%"]["scents"] == "A9, H10, H13, H14, H2"
        assert config["formulas"]["13%"]["scents"] == "H8, H9"
        assert config["formulas"]["15%"]["scents"] == "All other scents"


def test_compute_formula_includes_the_mixing_batches_like_the_sheet():
    def mat(i, unit, cost):
        return {"material_id": i, "material_name": f"m{i}", "unit": unit, "cost_per_unit": Decimal(cost)}
    resolved = {
        "oil": mat(1, "Milliliter", "4"), "fixative": mat(2, "Gram", "3.5"),
        "alcohol": mat(3, "Milliliter", "0.13"), "moisturizer": mat(4, "Milliliter", "0.31"),
        "water": mat(5, "Liter", "8.2"), "bottle": mat(6, "Piece", "15"), "sticker": mat(7, "Piece", "3"),
        "seal": mat(8, "Piece", "1.5"),
    }
    calc = compute_formula("15%", 500, resolved, CONFIG)
    # Sheet, Formula 15% / 500 ml, Table 2: one mixing batch 1200 ml, 3 batches, 3600 prepared, 800 left over.
    assert calc["mix_batch_ml"] == Decimal("1200")
    assert calc["mix_batches"] == 3
    assert calc["mix_prepared_ml"] == Decimal("3600")
    assert calc["mix_leftover_ml"] == Decimal("800")


def test_producing_checks_the_scent_matches_its_formula(client, sql):
    _signed_in_admin(client, sql)
    m = _make_role_materials(sql)
    _save_materials(client, m)

    # Each scent has its own oil row (made when the page opens); give A9 some.
    client.get(BULK_BATCHES_URL)
    _set_scent_oil(sql, "A9", price="4", stock="1000")

    # A9 uses Formula 14%, so posting it with 15% is refused and nothing is made.
    token = get_form_token(client, BULK_BATCHES_URL)
    resp = client.post(PRODUCE_URL, data={
        "form_token": token, "formula": "15%", "size": "250", "scent_code": "A9",
        "scent_name": "Drakkar Noir Wrong"}, follow_redirects=True)
    assert b"uses Formula 14%" in resp.data
    assert not _rows(sql, "SELECT 1 FROM bulk_batches WHERE scent_name = 'Drakkar Noir Wrong'")

    # With the right formula it goes through and is labelled with it.
    token = get_form_token(client, BULK_BATCHES_URL)
    client.post(PRODUCE_URL, data={
        "form_token": token, "formula": "14%", "size": "250", "scent_code": "A9",
        "scent_name": "Drakkar Noir"})
    batch = _rows(sql, "SELECT * FROM bulk_batches WHERE scent_name = 'Drakkar Noir'")[0]
    assert batch["formula_label"] == "Formula 14%, 250 ml"
    assert batch["total_volume_ml"] == Decimal("1768.000")      # 250 + 5 + 1500 + 13


def test_bulk_batches_page_offers_a_scent_picker(client, sql):
    _signed_in_admin(client, sql)
    page = client.get(BULK_BATCHES_URL).get_data(as_text=True)
    assert "fmScentSelect" in page and "Other scent" in page


def test_tools_page_renders_for_admins_only(client, sql, app):
    _signed_in_admin(client, sql)
    resp = client.get("/admin/tools")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    for title in ("Unit converter", "Alcohol and water mix", "Bottle yield", "Price per unit", "Formula cost calculator"):
        assert title in page
    # The US gallon factor the batches use is what the converter uses.
    assert "3785.411784" in page

    anonymous = app.test_client()
    assert anonymous.get("/admin/tools").status_code in (302, 401, 403)


def test_tools_has_its_own_sidebar_category_not_the_production_tabs(client, sql):
    _signed_in_admin(client, sql)
    # On a Production page, Tools is not one of the page tabs; it appears once, in the sidebar.
    page = client.get(BULK_BATCHES_URL).get_data(as_text=True)
    assert page.count('href="/admin/tools"') == 1
    assert 'class="subnav-link' in page and "Production Log" in page
    subnav = page[page.index('<nav class="subnav"'):page.index("</nav>", page.index('<nav class="subnav"'))]
    assert "Tools" not in subnav
    # It has its own category label, and the link is the active one on the Tools page.
    tools = client.get("/admin/tools").get_data(as_text=True)
    assert '<div class="nav-label">Tools</div>' in tools
    # Attribute order and whitespace are not the point: the Tools link carries the "active" class.
    import re
    assert re.search(r'<a class="nav-link active"[^>]*href="/admin/tools"', tools)


def test_a_batch_uses_its_own_scents_oil_price_and_stock(client, sql):
    _signed_in_admin(client, sql)
    m = _make_role_materials(sql)
    _save_materials(client, m)
    client.get(BULK_BATCHES_URL)                      # creates a row per scent
    _set_scent_oil(sql, "A1", price="10", stock="600")
    _set_scent_oil(sql, "H1", price="2", stock="600")
    pooled_before = get_raw_material(sql, m["oil"])["stock_qty"]

    token = get_form_token(client, BULK_BATCHES_URL)
    client.post(PRODUCE_URL, data={
        "form_token": token, "formula": "15%", "size": "500", "scent_code": "A1", "scent_name": "Pricey"})
    batch = _rows(sql, "SELECT * FROM bulk_batches WHERE scent_name = 'Pricey'")[0]
    oil_line = _rows(sql, "SELECT * FROM bulk_batch_materials WHERE batch_id = %s AND material_id = %s",
                     (batch["batch_id"], m["oil"]))[0]
    assert oil_line["cost_per_unit_snapshot"] == Decimal("10")      # A1's price, not the pooled 4
    assert oil_line["line_cost"] == Decimal("5000")
    stock = {r["base_code"]: r["stock_qty"] for r in _rows(sql, "SELECT base_code, stock_qty FROM scent_oils")}
    assert stock["A1"] == Decimal("100")              # 600 - 500
    assert stock["H1"] == Decimal("600")              # another scent's oil is untouched
    assert get_raw_material(sql, m["oil"])["stock_qty"] == pooled_before

    # A1 now has too little oil for another 500 ml, although H1 has plenty.
    token = get_form_token(client, BULK_BATCHES_URL)
    resp = client.post(PRODUCE_URL, data={
        "form_token": token, "formula": "15%", "size": "500", "scent_code": "A1", "scent_name": "Again"},
        follow_redirects=True)
    assert b"Not enough materials" in resp.data
    assert not _rows(sql, "SELECT 1 FROM bulk_batches WHERE scent_name = 'Again'")


def test_scent_oil_restock_averages_the_price_and_import_checks_every_row(client, sql):
    _signed_in_admin(client, sql)
    m = _make_role_materials(sql)
    _save_materials(client, m)
    client.get("/admin/materials")
    _set_scent_oil(sql, "A1", price="4", stock="100")

    resp = client.post("/admin/materials/scent-oils/restock", data={
        "base_code": "A1", "qty": "100", "cost": "600"})   # 100 ml at 4 + 100 ml at 6
    assert resp.status_code == 302
    row = _rows(sql, "SELECT * FROM scent_oils WHERE base_code = 'A1'")[0]
    assert row["stock_qty"] == Decimal("200") and row["cost_per_unit"] == Decimal("5")
    assert _rows(sql, "SELECT qty FROM scent_oil_restocks WHERE base_code = 'A1'")[0]["qty"] == Decimal("100")

    import io
    bad = io.BytesIO("\n".join(["code,price_per_unit,stock", "A1,7,50", "NOPE,1,1"]).encode())
    client.post("/admin/materials/scent-oils/import", data={"file": (bad, "x.csv")},
                content_type="multipart/form-data")
    assert _rows(sql, "SELECT cost_per_unit FROM scent_oils WHERE base_code = 'A1'")[0]["cost_per_unit"] == Decimal("5")

    good = io.BytesIO("\n".join(["code,price_per_unit,stock", "A1,7,", "H1,3,40"]).encode())
    client.post("/admin/materials/scent-oils/import", data={"file": (good, "x.csv")},
                content_type="multipart/form-data")
    a1 = _rows(sql, "SELECT * FROM scent_oils WHERE base_code = 'A1'")[0]
    h1 = _rows(sql, "SELECT * FROM scent_oils WHERE base_code = 'H1'")[0]
    assert a1["cost_per_unit"] == Decimal("7") and a1["stock_qty"] == Decimal("200")   # blank stock kept
    assert h1["cost_per_unit"] == Decimal("3") and h1["stock_qty"] == Decimal("40")

    page = client.get("/admin/materials").get_data(as_text=True)
    assert "Scent oils" in page and "scentOilEditOverlay" in page
