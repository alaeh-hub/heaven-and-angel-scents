"""The app shell's JS/CSS are split into files templates load in a fixed order (one
global scope). These checks catch a renamed/removed part a template still loads,
including pages that do not extend base.html (login).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"
STATIC_REF = re.compile(r"url_for\(\s*'static',\s*filename='([^']+)'")


def _template_text(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def test_every_script_and_stylesheet_referenced_by_a_template_exists():
    # Only js/ and css/: those are tracked code. Media such as the login
    # background video is gitignored, so it is absent from a clean checkout (CI).
    """Every script and stylesheet referenced by a template exists."""
    missing = []
    for path in TEMPLATES.rglob("*.html"):
        for rel in STATIC_REF.findall(path.read_text(encoding="utf-8")):
            if not rel.startswith(("js/", "css/")):
                continue
            if not (ROOT / "static" / rel).is_file():
                missing.append(f"{path.relative_to(ROOT)} -> {rel}")
    assert not missing, missing


def test_app_scripts_partial_loads_parts_in_order():
    """App scripts partial loads parts in order."""
    parts = [p.split("/")[-1] for p in STATIC_REF.findall(_template_text("_app_scripts.html"))]
    assert parts, "_app_scripts.html loads nothing"
    assert parts.index("core.js") < parts.index("page-init.js")


def test_pages_with_the_shell_scripts_include_the_partial():
    """Pages with the shell scripts include the partial."""
    for name in ("base.html", "login.html"):
        assert "_app_scripts.html" in _template_text(name), name


def test_shell_stylesheet_loads_after_themes():
    """Shell stylesheet loads after themes."""
    styles = [s for s in STATIC_REF.findall(_template_text("base.html")) if s.startswith("css/")]
    assert styles.index("css/shell.css") > styles.index("css/themes.css")
