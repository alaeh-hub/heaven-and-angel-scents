"""Guards for the phone/tablet pass: helper text stays short, empty states stay centred, and the Halo
assistant has a close button on phones and no floating button on its full page.

The text check reads the templates (no database): any visible text block this long is a paragraph
that should be one short sentence.
"""
import re
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_CHARS = 130          # the longest helper text the screens may carry (most are under 90)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            text = re.sub(r"\s+", " ", data).strip()
            if text:
                self.blocks.append(text)


def _visible_text_blocks(path):
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"\{#.*?#\}", "", src, flags=re.S)
    src = re.sub(r"\{%.*?%\}", " ", src, flags=re.S)
    src = re.sub(r"\{\{.*?\}\}", "X", src, flags=re.S)
    parser = _Text()
    parser.feed(src)
    return parser.blocks


def test_no_template_carries_a_long_paragraph():
    long_blocks = []
    for path in sorted((ROOT / "templates").rglob("*.html")):
        for block in _visible_text_blocks(path):
            if len(block) > MAX_CHARS and not block.startswith(("//", "/*")):
                long_blocks.append(f"{path.relative_to(ROOT).as_posix()} ({len(block)}): {block[:80]}...")
    assert not long_blocks, "shorten these to one short sentence:\n" + "\n".join(long_blocks)


def test_empty_states_are_centred_at_every_width():
    css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
    start = css.index(".empty-state:has(.empty-icon) {")
    rule = css[start:css.index("}", start)]
    assert "align-items: center" in rule and "text-align: center" in rule and "flex-direction: column" in rule
    # no narrow-screen override that puts them back at the left edge
    assert not re.search(r"\.empty-state:has\(\.empty-icon\)\s*\{[^}]*flex-start", css)


def test_halo_has_a_close_button_for_phones():
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'id="haloClose"' in base
    shell = (ROOT / "static" / "css" / "shell.css").read_text(encoding="utf-8")
    assert "body:has(.chat-back) .halo-widget" in shell          # no floating button on the full chat page
    assert ".halo-widget.open .halo-fab" in shell                # hidden while the full-screen panel is open


# ---- fixes from the second phone pass -------------------------------------------------------

def test_empty_recent_sales_does_not_repeat_the_new_sale_button(client, sql):
    from factories import login, make_branch, make_user
    branch_id = make_branch(sql)                       # a branch with no sales at all
    user = make_user(sql, role="Branch", branch_id=branch_id)
    login(client, user["username"], user["password"], "Branch")
    html = client.get("/branch/record-sale").get_data(as_text=True)
    assert "No sales recorded yet" in html
    assert html.count('id="openNewSaleBtn"') == 1             # the one in the card header
    assert 'data-click-target="openNewSaleBtn"' not in html    # not a second one in the empty state


def test_empty_states_that_sit_under_their_own_button_have_no_action():
    for name in ("admin/record_sale.html", "branch/record_sale.html"):
        src = (ROOT / "templates" / name).read_text(encoding="utf-8")
        line = next(ln for ln in src.splitlines() if "empty.state('No sales recorded yet'" in ln)
        assert "action=" not in line and "trigger=" not in line, name


def test_login_accounts_actions_get_their_own_row_on_phones():
    users = (ROOT / "templates" / "admin" / "users.html").read_text(encoding="utf-8")
    assert "mobile-inline-action mobile-actions-below" in users
    css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
    assert "td.mobile-actions-below" in css


def test_materials_price_trends_stops_listening_when_its_page_is_gone():
    """The visibilitychange/pageshow listeners sit on document/window, so they outlive a soft
    navigation. Without this they ran against a page that no longer had the chart ("Cannot set
    properties of null") when the tab was focused again."""
    src = (ROOT / "templates" / "admin" / "materials.html").read_text(encoding="utf-8")
    assert "window.__materialTrendsCleanup" in src
    assert "removeEventListener('visibilitychange'" in src
    assert src.count("card.isConnected") >= 3        # setState and both fetch callbacks ignore a gone page
