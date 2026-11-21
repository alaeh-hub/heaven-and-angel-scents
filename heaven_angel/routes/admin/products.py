import csv
import glob
import io
import os
import uuid

from flask import (
    current_app, flash, redirect, render_template,
    request, send_file, url_for,
)
from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename

from heaven_angel.db import execute, query, transaction
from heaven_angel.decorators import admin_required
from heaven_angel.services.audit import log_action
from heaven_angel.sockets import notify_all
from heaven_angel.utils import (
    BOTTLE_UNITS, PRODUCT_UNITS, ValidationError,
    base_code_from_sku, build_sku, business_now, is_duplicate_error, parse_base_code,
    parse_non_negative_decimal,
)

from heaven_angel.routes.admin._common import bp


# ---------------------------------------------------------------- product images
# Product photos live in <static>/uploads/products/; products.image_path is relative to static.
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


PRODUCT_IMAGE_SUBDIR = "uploads/products"


# Caps the image field itself, on top of the app-wide MAX_CONTENT_LENGTH.
MAX_PRODUCT_IMAGE_BYTES = 5 * 1024 * 1024


# Pillow's Image.format per accepted extension, to confirm the bytes match the extension.
_IMAGE_FORMAT_BY_EXTENSION = {
    "jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP",
}


def _save_product_image(file_storage):
    """Validate and save an uploaded product image; return its image_path (None if none).

    Raises ValidationError for a bad type, size or undecodable content.
    """
    if not file_storage or not file_storage.filename:
        return None

    filename = secure_filename(file_storage.filename)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError(
            "Product image must be a JPG, PNG, or WEBP file.")

    stream = file_storage.stream
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    if size > MAX_PRODUCT_IMAGE_BYTES:
        raise ValidationError("Product image is too large (max 5MB).")

    # Confirm it decodes as the claimed type: verify() first, then a fresh open + load()
    # (verify alone can miss bad files). Pillow's MAX_IMAGE_PIXELS also rejects bombs.
    try:
        with Image.open(stream) as img:
            img.verify()
        stream.seek(0)
        with Image.open(stream) as img:
            img.load()
            actual_format = img.format
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ValidationError("That file isn't a valid image.")
    finally:
        stream.seek(0)

    if actual_format != _IMAGE_FORMAT_BY_EXTENSION.get(ext):
        raise ValidationError(
            "That file's contents don't match a JPG/PNG/WEBP image.")

    upload_dir = os.path.join(
        current_app.static_folder, *PRODUCT_IMAGE_SUBDIR.split("/"))
    os.makedirs(upload_dir, exist_ok=True)

    # Random filename: never reuse the uploader's name (also avoids collisions).
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(upload_dir, stored_name))
    return f"{PRODUCT_IMAGE_SUBDIR}/{stored_name}"


SCENT_IMAGE_DIR = "img/scents"


def default_scent_image(base_code):
    """Built-in square thumbnail path for a scent code (A1, H4...), or None if it has none."""
    code = (base_code or "").strip().lower()
    if not code:
        return None
    root = os.path.join(current_app.static_folder, *SCENT_IMAGE_DIR.split("/"))
    for variant in ("male", "female"):
        found = sorted(glob.glob(os.path.join(root, variant, f"{glob.escape(code)}-*-thumb.webp")))
        if found:
            return f"{SCENT_IMAGE_DIR}/{variant}/{os.path.basename(found[0])}"
    return None


def _delete_product_image(image_path):
    """Best-effort delete of a replaced or cleared image; never raises.

    Only uploads/products/ files go; the shared img/scents/ photos must stay.
    """
    if not image_path or not image_path.startswith(PRODUCT_IMAGE_SUBDIR + "/") or ".." in image_path:
        return
    full_path = os.path.join(current_app.static_folder, image_path)
    try:
        if os.path.isfile(full_path):
            os.remove(full_path)
    except OSError:
        pass


# ---------------------------------------------------------------- products
@bp.route("/products", methods=["GET", "POST"])
@admin_required
def products():
    if request.method == "POST":
        item_name = request.form.get("item_name", "").strip()
        variant = request.form.get("variant")
        # Every product is a bottle: pick one of the real bottle sizes.
        # (Bulk is not a product, it is sold by the mL from the bulk batches.)
        category = "Bottled"
        unit = request.form.get("unit")

        try:
            base_code = parse_base_code(request.form.get("base_code"))
            price = parse_non_negative_decimal(
                request.form.get("price"), "Price")
            if not item_name or variant not in ("Male", "Female") or unit not in BOTTLE_UNITS:
                raise ValidationError(
                    "Please fill in every field with a valid value.")
            # The base code is reused across sizes; build_sku() makes each a distinct SKU row.
            sku = build_sku(base_code, unit)
            # Optional image; validated before touching the database.
            uploaded_image = _save_product_image(request.files.get("image"))
            image_path = uploaded_image or default_scent_image(base_code)
        except ValidationError as err:
            flash(str(err), "error")
            return redirect(url_for("admin.products"))

        try:
            with transaction() as conn:
                cur = conn.cursor()
                cur.execute(
                    "INSERT INTO products (sku, item_name, variant, category, unit, price, image_path) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (sku, item_name, variant, category, unit, price, image_path),
                )
                # Give every branch (and HQ) a zero-stock row, in one transaction with the insert.
                cur.execute("SELECT branch_id FROM branches")
                branch_ids = [row[0] for row in cur.fetchall()]
                for branch_id in branch_ids:
                    cur.execute(
                        "INSERT IGNORE INTO branch_inventory (branch_id, sku, stock_qty) VALUES (%s, %s, 0)",
                        (branch_id, sku),
                    )
                cur.close()
            notify_all(["products", "inventory"])
            log_action("add_product", target=sku,
                       details=f"{item_name} ({variant}, {unit}) — ₱{price:,.2f}")
            flash(f"{item_name} — {unit} ({sku}) added to the catalog.", "success")
        except Exception as exc:
            # Only a real duplicate (MySQL 1062) is reported as one; any other
            # failure gets a generic message. The log has the real cause.
            current_app.logger.exception(
                "add product failed for sku=%s base_code=%s unit=%s", sku, base_code, unit)
            # Don't leave an orphaned image for a product that was never created.
            if uploaded_image:
                _delete_product_image(uploaded_image)
            if is_duplicate_error(exc):
                flash(f"'{base_code}' already has a {unit} entry (SKU {sku}).", "error")
            else:
                flash("Couldn't add that product - please try again. If it keeps happening, "
                      "check the server log.", "error")
        return redirect(url_for("admin.products"))

    catalog = query(
        """SELECT p.*, COALESCE(SUM(bi.stock_qty), 0) AS total_stock
           FROM products p
           LEFT JOIN branch_inventory bi ON p.sku = bi.sku
           GROUP BY p.sku ORDER BY p.item_name"""
    )
    # SKU counts are by base code (A1-85ML and A1-50ML are one product).
    for row in catalog:
        row["base_code"] = base_code_from_sku(row["sku"], row["unit"])
    return render_template(
        "admin/products.html", catalog=catalog, unit_choices=BOTTLE_UNITS,
    )


# POST: edit a product's name, variant, price and image (not its unit).
@bp.route("/products/edit", methods=["POST"])
@admin_required
def edit_product():
    sku = request.form.get("sku")
    item_name = request.form.get("item_name", "").strip()
    variant = request.form.get("variant")
    # Checkbox only exists when the product already has an image.
    remove_image = request.form.get("remove_image") == "1"

    product = query(
        "SELECT sku, item_name, unit, image_path FROM products WHERE sku = %s", (sku,), fetchone=True
    )
    if not product:
        flash("That product no longer exists.", "error")
        return redirect(url_for("admin.products"))

    try:
        price = parse_non_negative_decimal(request.form.get("price"), "Price")
        if not item_name or variant not in ("Male", "Female"):
            raise ValidationError(
                "Please fill in every field with a valid value.")
        # Optional new image replaces the old; validated before the row is touched.
        new_image_path = _save_product_image(request.files.get("image"))
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.products"))

    old_image_path = product["image_path"]
    if new_image_path:
        image_path = new_image_path
    elif remove_image:
        # Back to the scent's built-in photo (None if it has none).
        image_path = default_scent_image(base_code_from_sku(sku, product["unit"]))
    else:
        image_path = old_image_path

    # unit is not editable: it is baked into the SKU. To change size, add the base code
    # again with the new unit.
    try:
        execute(
            "UPDATE products SET item_name = %s, variant = %s, price = %s, image_path = %s WHERE sku = %s",
            (item_name, variant, price, image_path, sku),
        )
    except Exception:
        current_app.logger.exception("edit product failed for sku=%s", sku)
        if new_image_path:
            _delete_product_image(new_image_path)
        flash("Couldn't save that product - check the name length and that the price isn't too large.", "error")
        return redirect(url_for("admin.products"))
    # Delete the old file only after the row is updated, so a crash can't leave a dead reference.
    if old_image_path and old_image_path != image_path:
        _delete_product_image(old_image_path)

    notify_all(["products", "inventory"])
    log_action("edit_product", target=sku,
               details=f"{item_name} ({variant}, {product['unit']}) — ₱{price:,.2f}")
    flash(f"{item_name} ({sku}) updated.", "success")
    return redirect(url_for("admin.products"))


# ---------------------------------------------------------------- product bulk import/export (CSV)
# Both routes use the manual form's base_code + unit shape, so an edited export goes
# through the same parse_base_code()/build_sku() validation.
_PRODUCT_CSV_COLUMNS = ["base_code", "item_name", "variant", "unit", "price"]
# Short headers used by the styled .xlsx export; import accepts both sets.
_PRODUCT_XLSX_HEADERS = ["Code*", "Name*", "Variant*", "Unit*", "Price*"]  # all required
_PRODUCT_HEADER_ALIASES = dict(zip(
    (h.rstrip("*").lower() for h in _PRODUCT_XLSX_HEADERS), _PRODUCT_CSV_COLUMNS))


@bp.route("/products/export")
@admin_required
def export_products():
    """Download the catalog as CSV (import shape), so it can be edited and re-uploaded."""
    catalog = query(
        "SELECT sku, item_name, variant, unit, price FROM products ORDER BY item_name, unit"
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_PRODUCT_CSV_COLUMNS)
    for row in catalog:
        writer.writerow([
            base_code_from_sku(row["sku"], row["unit"]),
            row["item_name"],
            row["variant"],
            row["unit"],
            f"{row['price']:.2f}",
        ])

    log_action("export_products", details=f"{len(catalog)} SKUs")
    stamp = business_now().strftime('%Y%m%d_%H%M%S')

    # Default is a branded .xlsx; ?format=csv gives plain CSV. Same columns; import takes either.
    if request.args.get("format") != "csv":
        return send_file(
            _build_products_workbook(catalog),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True, download_name=f"products_{stamp}.xlsx",
        )

    # utf-8-sig (BOM) so Excel reads peso signs and accents correctly.
    payload = buffer.getvalue().encode("utf-8-sig")
    return send_file(
        io.BytesIO(payload), mimetype="text/csv",
        as_attachment=True, download_name=f"products_{stamp}.csv",
    )


def _build_products_workbook(catalog):
    """The catalog as a styled workbook, in the shape import_products() reads.

    Row 1 is the header and products start on row 2; nothing may go above the header,
    and extra columns are ignored on import.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side
    from heaven_angel.services.reports.excel import XL_MONEY_FMT

    wb = Workbook()
    ws = wb.active
    ws.title = "Products"
    wb.properties.creator = "Heaven & Angel Scents"
    wb.properties.title = "Products"
    ws.sheet_view.showGridLines = False

    ink = "17140D"
    heading_rule = Border(bottom=Side(style="medium", color=ink))
    hairline = Border(bottom=Side(style="hair", color="B8AF98"))
    for c, label in enumerate(_PRODUCT_XLSX_HEADERS, start=1):
        cell = ws.cell(row=1, column=c, value=label)
        cell.font = Font(name="Calibri", size=10, bold=True, color=ink)
        cell.border = heading_rule
        cell.alignment = Alignment(horizontal="right" if c == 5 else "left", vertical="center")
    ws.row_dimensions[1].height = 22

    for r, row in enumerate(catalog, start=2):
        values = [base_code_from_sku(row["sku"], row["unit"]), row["item_name"],
                  row["variant"], row["unit"], float(row["price"])]
        for c, value in enumerate(values, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.font = Font(name="Calibri", size=10, color=ink)
            cell.border = hairline
            cell.alignment = Alignment(horizontal="right" if c == 5 else "left", vertical="center")
        ws.cell(row=r, column=5).number_format = XL_MONEY_FMT

    for col, width in zip("ABCDE", (14, 36, 12, 12, 14)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:E{max(len(catalog) + 1, 2)}"

    # Printing: portrait, one page wide, heads on every page.
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "1:1"
    ws.oddFooter.right.text = "Page &P of &N"
    ws.oddFooter.right.size = 9

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


@bp.route("/products/import", methods=["POST"])
@admin_required
def import_products():
    """Bulk create/update products from an uploaded CSV/xlsx in export_products() shape.

    Each row resolves to a SKU via build_sku(): an existing SKU updates name/variant/price
    (unit and image stay); a new SKU is inserted with a zero-stock row per branch, taking
    the name/variant of a sibling size if the code exists. A bad or duplicate row is
    skipped and reported by row number; each row is atomic on its own.
    """
    file_storage = request.files.get("csv_file")
    if not file_storage or not file_storage.filename:
        flash("Choose a CSV file to import.", "error")
        return redirect(url_for("admin.products"))

    if file_storage.filename.lower().endswith(".xlsx"):
        # Styled export: read the first sheet into header-keyed dict rows like csv.DictReader.
        from openpyxl import load_workbook
        try:
            sheet = load_workbook(file_storage, read_only=True, data_only=True).active
            sheet_rows = list(sheet.iter_rows(max_row=5001, max_col=20, values_only=True))
        except Exception:
            flash("That doesn't look like a valid .xlsx file.", "error")
            return redirect(url_for("admin.products"))
        fieldnames = [
            _PRODUCT_HEADER_ALIASES.get(str(h).strip().rstrip("*").strip().lower(), str(h).strip())
            if h is not None else ""
            for h in (sheet_rows[0] if sheet_rows else [])
        ]
        records = [
            {name: ("" if v is None else str(v).strip()) for name, v in zip(fieldnames, vals)}
            for vals in sheet_rows[1:] if any(v not in (None, "") for v in vals)
        ]

        reader = type("SheetReader", (), {"fieldnames": fieldnames})()
        row_iter = records
    else:
        try:
            raw = file_storage.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            flash(
                "That file doesn't look like a valid CSV (couldn't read it as UTF-8).", "error")
            return redirect(url_for("admin.products"))
        reader = csv.DictReader(io.StringIO(raw))
        row_iter = reader
    required_columns = {"base_code", "item_name", "variant", "unit", "price"}
    if not required_columns.issubset(set(reader.fieldnames or [])):
        flash(
            "CSV must have columns: base_code, item_name, variant, unit, price "
            "(e.g. from Export CSV).", "error",
        )
        return redirect(url_for("admin.products"))

    created = 0
    updated = 0
    errors = []
    seen_skus = set()

    for row_num, row in enumerate(row_iter, start=2):  # row 1 is the header
        try:
            base_code = parse_base_code(row.get("base_code"))
            item_name = (row.get("item_name") or "").strip()
            variant = (row.get("variant") or "").strip()
            unit = (row.get("unit") or "").strip()
            price = parse_non_negative_decimal(row.get("price"), "Price")
            # Any unit export_products() writes is accepted back. Category
            # isn't a CSV column: every product is Bottled.
            if not item_name or variant not in ("Male", "Female") or unit not in PRODUCT_UNITS:
                raise ValidationError(
                    f"item_name, variant, and unit must all be valid (unit is one of "
                    f"{', '.join(PRODUCT_UNITS)}).")
            category = "Bottled"
            sku = build_sku(base_code, unit)
        except ValidationError as err:
            errors.append(f"Row {row_num}: {err}")
            continue

        if sku in seen_skus:
            errors.append(f"Row {row_num}: duplicate of SKU {sku} earlier in the file, skipped.")
            continue
        seen_skus.add(sku)

        existing = query(
            "SELECT sku FROM products WHERE sku = %s", (sku,), fetchone=True)

        # A new size of an existing code takes that product's name/variant (so a typo can't
        # rename a scent across sizes). Only for new SKUs.
        if not existing:
            sibling_skus = [build_sku(base_code, u)
                            for u in PRODUCT_UNITS if u != unit]
            if sibling_skus:
                placeholders = ",".join(["%s"] * len(sibling_skus))
                sibling = query(
                    f"SELECT item_name, variant FROM products WHERE sku IN ({placeholders}) LIMIT 1",
                    tuple(sibling_skus), fetchone=True,
                )
                if sibling:
                    item_name = sibling["item_name"]
                    variant = sibling["variant"]

        try:
            if existing:
                execute(
                    "UPDATE products SET item_name = %s, variant = %s, price = %s WHERE sku = %s",
                    (item_name, variant, price, sku),
                )
                updated += 1
            else:
                with transaction() as conn:
                    cur = conn.cursor()
                    cur.execute(
                        "INSERT INTO products (sku, item_name, variant, category, unit, price, image_path) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (sku, item_name, variant, category, unit, price, default_scent_image(base_code)),
                    )
                    cur.execute("SELECT branch_id FROM branches")
                    branch_ids = [r[0] for r in cur.fetchall()]
                    for branch_id in branch_ids:
                        cur.execute(
                            "INSERT IGNORE INTO branch_inventory (branch_id, sku, stock_qty) VALUES (%s, %s, 0)",
                            (branch_id, sku),
                        )
                    cur.close()
                created += 1
        except Exception:
            current_app.logger.exception(
                "import_products failed on row %s (sku=%s)", row_num, sku)
            errors.append(f"Row {row_num}: couldn't save SKU {sku}.")

    if created or updated:
        notify_all(["products", "inventory"])
        log_action(
            "import_products",
            details=f"{created} created, {updated} updated, {len(errors)} skipped",
        )
        flash(
            f"Import complete — {created} added, {updated} updated.", "success")
    if errors:
        shown = errors[:10]
        more = f" (+{len(errors) - 10} more)" if len(errors) > 10 else ""
        flash("Some rows were skipped: " + "; ".join(shown) + more, "error")
    if not created and not updated and not errors:
        flash("No rows found in that file.", "error")

    return redirect(url_for("admin.products"))
