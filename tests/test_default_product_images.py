"""Catalog products show their scent's built-in photo unless one is uploaded."""
import re
from pathlib import Path

from heaven_angel.routes.admin.products import _delete_product_image, default_scent_image

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "schema.sql").read_text(encoding="utf-8")
SCENTS = ROOT / "static" / "img" / "scents"
SITE_SCENTS = (ROOT / "public-site" / "src" / "content" / "scents.js").read_text(encoding="utf-8")


def _schema_defaults():
    block = SCHEMA[SCHEMA.index("54a. Default product photos"):]
    block = block[:block.index("WHERE p.image_path IS NULL")]
    return dict(re.findall(r"'([AH]\d+)'(?: AS code)?, '(img/scents/[^']+)'", block))


def test_schema_maps_every_scent_render_to_its_code():
    defaults = _schema_defaults()
    on_disk = {p.name.split("-")[0].upper() for p in SCENTS.glob("*/*-thumb.webp")}
    assert defaults, "no default photo mapping found in schema.sql"
    assert set(defaults) == on_disk
    for code, rel in defaults.items():
        assert (ROOT / "static" / rel).is_file(), f"{code}: {rel} is missing"
        assert rel.split("/")[-1].startswith(code.lower() + "-")


def test_every_scent_has_its_full_set_of_renders_and_is_on_the_public_site():
    codes = {p.name.split("-")[0].upper() for p in SCENTS.glob("*/*-thumb.webp")}
    assert {"A12", "A13", "A14", "H17"} <= codes
    for code in codes:
        files = list(SCENTS.glob(f"*/{code.lower()}-*"))
        names = {f.name for f in files}
        for kind in ("bottle", "box"):
            for suffix in ("", "-m", "-s"):
                assert any(re.fullmatch(rf"{code.lower()}-[a-z-]+-{kind}{suffix}\.webp", n) for n in names), \
                    f"{code} is missing its {kind}{suffix} render"
        assert f"code: '{code}'" in SITE_SCENTS, f"{code} is not in public-site/src/content/scents.js"


def test_default_scent_image_matches_the_code_exactly(app):
    with app.app_context():
        assert default_scent_image("A1").endswith("/a1-zadkiel-thumb.webp")
        assert default_scent_image("a10").endswith("/a10-afriel-thumb.webp")  # not A1
        assert default_scent_image("H17").startswith("img/scents/female/h17-")
        assert default_scent_image("ZZ9") is None
        assert default_scent_image("") is None


def test_replacing_a_photo_never_deletes_the_shared_scent_render(app):
    with app.app_context():
        rel = default_scent_image("A1")
        _delete_product_image(rel)
        assert (ROOT / "static" / rel).is_file()
