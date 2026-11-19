"""Catalog products show their scent's built-in bottle photo unless one is uploaded."""
import re
from pathlib import Path

from heaven_angel.routes.admin.products import _delete_product_image, default_scent_image

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = (ROOT / "schema.sql").read_text(encoding="utf-8")
SCENTS = ROOT / "static" / "img" / "scents"


def _schema_defaults():
    block = SCHEMA[SCHEMA.index("54a. Default product photos"):]
    block = block[:block.index("WHERE p.image_path IS NULL")]
    return dict(re.findall(r"'([AH]\d+)',? ?(?:AS code, )?'(img/scents/[^']+)'", block.replace("AS img", "")))


def test_schema_maps_every_scent_render_to_its_code():
    defaults = _schema_defaults()
    on_disk = {p.name.split("-")[0].upper(): p for p in SCENTS.glob("*/*-bottle-m.webp")}
    assert defaults, "no default photo mapping found in schema.sql"
    assert set(defaults) == set(on_disk)
    for code, rel in defaults.items():
        assert (ROOT / "static" / rel).is_file(), f"{code}: {rel} is missing"
        assert rel.split("/")[-1].startswith(code.lower() + "-")


def test_default_scent_image_matches_the_code_exactly(app):
    with app.app_context():
        assert default_scent_image("A1").endswith("/a1-zadkiel-bottle-m.webp")
        assert default_scent_image("a10").endswith("/a10-afriel-bottle-m.webp")  # not A1
        assert default_scent_image("H4").startswith("img/scents/female/h4-")
        assert default_scent_image("ZZ9") is None
        assert default_scent_image("") is None


def test_replacing_a_photo_never_deletes_the_shared_scent_render(app):
    with app.app_context():
        rel = default_scent_image("A1")
        _delete_product_image(rel)
        assert (ROOT / "static" / rel).is_file()
