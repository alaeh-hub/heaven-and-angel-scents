"""The Process Flow page (a read-only picture of how a bottle is made and costed) and
the Formulas page's new name, Base Cost of Goods."""
from factories import login, make_branch, make_user
from test_batch_formulas import _make_role_materials, _save_materials

PROCESS_FLOW_URL = "/admin/process-flow"


def _admin(client, sql):
    user = make_user(sql, role="Admin", branch_id=None)
    login(client, user["username"], user["password"], "Admin")


def test_process_flow_page_is_admin_only(client, sql, app):
    """Process flow page is admin only."""
    assert app.test_client().get(PROCESS_FLOW_URL).status_code in (302, 401, 403)

    branch_id = make_branch(sql)
    branch = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, branch["username"], branch["password"], "Branch")
    assert client.get(PROCESS_FLOW_URL).status_code == 403


def test_process_flow_page_renders_the_flow_for_each_formula(client, sql):
    """Process flow page renders the flow for each formula."""
    _admin(client, sql)
    _save_materials(client, _make_role_materials(sql))
    resp = client.get(PROCESS_FLOW_URL)
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    # Every formula can be picked, and the six steps are written out by the page's script.
    for label in ("Formula 15%", "Formula 14%", "Formula 13%", "Process Flow"):
        assert label in page
    for step in ("Materials", "Cost the liquid", "Make the bulk batch", "Bottle it", "Sell it"):
        assert step in page
    # The numbers are the ones producing a batch uses: the page carries the same calculation
    # (39 bottles, and chemist and labor 195 each, for the 15% formula at 500 mL).
    compact = page.replace(" ", "")
    assert '"bottles":39' in compact
    assert '"chemist_cost":195.0' in compact and '"labor_cost":195.0' in compact


def test_process_flow_is_in_the_production_tabs(client, sql):
    """Process flow is in the production tabs."""
    _admin(client, sql)
    page = client.get("/admin/bulk-batches").get_data(as_text=True)
    subnav = page[page.index('<nav class="subnav"'):page.index("</nav>", page.index('<nav class="subnav"'))]
    assert 'href="/admin/process-flow"' in subnav and "Process Flow" in subnav
    assert 'href="/admin/formulas"' in subnav and "Base Cost of Goods" in subnav
    assert ">Formulas<" not in subnav


def test_the_formulas_page_is_now_base_cost_of_goods(client, sql):
    """The formulas page is now base cost of goods."""
    _admin(client, sql)
    resp = client.get("/admin/formulas")
    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert "Base Cost of Goods" in page
    # Its sidebar entry and its page tab carry the new name too, not "Formulas".
    assert ">Formulas<" not in page


def _content(html):
    """The page content the live refresh compares (it swaps only when this HTML changes)."""
    start = html.index('<main class="content"')
    return html[start:html.index("</main>", start)]


def test_a_price_change_changes_the_page_content_so_the_live_refresh_swaps_it(client, sql):
    """The numbers are drawn by script from a data block INSIDE the content. If that block lived
    only in the script, a price change would leave the content identical and the live refresh would
    skip it as "nothing new" -- so both pages must change their content when a price changes."""
    _admin(client, sql)
    m = _make_role_materials(sql)
    _save_materials(client, m)
    before = {url: _content(client.get(url).get_data(as_text=True))
              for url in (PROCESS_FLOW_URL, "/admin/bulk-batches")}
    assert 'id="pfData"' in before[PROCESS_FLOW_URL] and 'id="fmData"' in before["/admin/bulk-batches"]
    # The same data renders the same content (nothing to swap)...
    assert _content(client.get(PROCESS_FLOW_URL).get_data(as_text=True)) == before[PROCESS_FLOW_URL]

    # ...and a restock-style change to a material's price changes both pages' content.
    cur = sql.cursor()
    cur.execute("UPDATE raw_materials SET cost_per_unit = cost_per_unit + 1 WHERE material_id = %s", (m["fixative"],))
    sql.commit()
    cur.close()
    for url in (PROCESS_FLOW_URL, "/admin/bulk-batches"):
        assert _content(client.get(url).get_data(as_text=True)) != before[url], url
