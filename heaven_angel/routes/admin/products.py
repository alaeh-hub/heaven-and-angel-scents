import csv
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
    base_code_from_sku, build_sku, business_now, parse_base_code, parse_non_negative_decimal,
)

from heaven_angel.routes.admin._common import bp


# ---------------------------------------------------------------- product images
# Uploaded product photos are stored under <static>/uploads/products/ and
# referenced from products.image_path as a path relative to the static
# folder (e.g. "uploads/products/<uuid>.jpg"), so they can be rendered
# anywhere with url_for('static', filename=...).
ALLOWED_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


PRODUCT_IMAGE_SUBDIR = "uploads/products"


# Belt-and-suspenders on top of app.py's global MAX_CONTENT_LENGTH — this
# caps just the image field itself to something no legitimate product
# photo needs, rather than however big the whole request is allowed to be.
MAX_PRODUCT_IMAGE_BYTES = 5 * 1024 * 1024


# What Pillow's Image.format reports for each extension we accept — used
# to confirm the file's actual bytes are the kind of image its extension
# claims, not just anything renamed to end in .png/.jpg/.webp.
_IMAGE_FORMAT_BY_EXTENSION = {
    "jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP",
}


def _save_product_image(file_storage):
    """Validate and persist an uploaded product image. Returns the
    image_path to store on the product row, or None if no file was
    actually chosen (the field is optional). Raises ValidationError on
    an unsupported file type, an oversized file, or a file whose actual
    content isn't a decodable image matching its extension.
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

    # Confirm this is actually a decodable image of the claimed type —
    # trusting the extension alone would let anything (a non-image blob,
    # a corrupt/hostile file) through, to later be served back as
    # image/* purely because of its filename. Image.open() is lazy, so
    # a first pass calls .verify() (checks structural integrity without
    # decoding pixel data), then a second, fresh open decodes it for
    # real via .load() — verify() alone can pass some files a real
    # decode would still reject, and an object that's been verify()'d
    # can't be reused for anything else per Pillow's own docs. Pillow's
    # default Image.MAX_IMAGE_PIXELS guard also rejects a decompression-
    # bomb-style image here (raises DecompressionBombError, caught by
    # the bare except below same as any other bad-image case).
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

    # Random filename — never trust/re-use the uploader's own filename
    # beyond checking its extension, and this also sidesteps any
    # collision between products.
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(upload_dir, stored_name))
    return f"{PRODUCT_IMAGE_SUBDIR}/{stored_name}"


def _delete_product_image(image_path):
    """Best-effort removal of a product image file that's being replaced
    or cleared. Never raises — a missing/already-gone file shouldn't
    block the request that's replacing it."""
    if not image_path:
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
            # The base code is reusable across sizes (e.g. base 'A1' + unit
            # '85ML' and base 'A1' + unit '15ML' both come from the same
            # admin-entered code) — build_sku() is what actually makes each
            # one a distinct SKU/row, with its own price and stock.
            sku = build_sku(base_code, unit)
            # Optional — validated and saved to disk here so a bad file
            # type is caught before we touch the database at all.
            image_path = _save_product_image(request.files.get("image"))
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
                # Give every existing branch (and HQ) a zero-stock row so
                # it shows up everywhere. Both writes happen in one
                # transaction so a crash partway through can't leave the
                # product created with no inventory rows.
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
        except Exception:
            # Almost always a duplicate base code + unit, but log it
            # properly regardless — a real DB failure here would
            # otherwise look identical to a duplicate SKU, with no
            # trace to tell the two apart.
            current_app.logger.exception(
                "add product failed for sku=%s base_code=%s unit=%s", sku, base_code, unit)
            # Don't leave an orphaned image file behind for a product
            # row that was never created.
            if image_path:
                _delete_product_image(image_path)
            flash(f"'{base_code}' already has a {unit} entry (SKU {sku}).", "error")
        return redirect(url_for("admin.products"))

    catalog = query(
        """SELECT p.*, COALESCE(SUM(bi.stock_qty), 0) AS total_stock
           FROM products p
           LEFT JOIN branch_inventory bi ON p.sku = bi.sku
           GROUP BY p.sku ORDER BY p.item_name"""
    )
    # SKU counts on this page are by base code (A1-85ML and A1-50ML
    # are one product, "A1"), not one per size row.
    for row in catalog:
        row["base_code"] = base_code_from_sku(row["sku"], row["unit"])
    return render_template(
        "admin/products.html", catalog=catalog, unit_choices=BOTTLE_UNITS,
    )


@bp.route("/products/edit", methods=["POST"])
@admin_required
def edit_product():
    sku = request.form.get("sku")
    item_name = request.form.get("item_name", "").strip()
    variant = request.form.get("variant")
    # Checkbox only rendered (and only meaningful) when the product
    # already has an image — see products.html.
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
        # Optional — a new file replaces the existing image. Validated
        # and saved here so a bad file type is caught before the row
        # is touched at all.
        new_image_path = _save_product_image(request.files.get("image"))
    except ValidationError as err:
        flash(str(err), "error")
        return redirect(url_for("admin.products"))

    old_image_path = product["image_path"]
    if new_image_path:
        image_path = new_image_path
    elif remove_image:
        image_path = None
    else:
        image_path = old_image_path

    # unit is intentionally not editable here — it's baked into the SKU
    # itself (see build_sku() above), so changing it on an existing row
    # would leave the SKU claiming a size the row no longer has. To
    # change a product's size, add it again under the same base code
    # with the new unit — that gets its own SKU, same as any other size.
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
    # Only delete the old file once the row has been updated to point
    # elsewhere (or nowhere) — never delete first, so a crash mid-request
    # can't leave the row referencing a file that's already gone.
    if old_image_path and old_image_path != image_path:
        _delete_product_image(old_image_path)

    notify_all(["products", "inventory"])
    log_action("edit_product", target=sku,
               details=f"{item_name} ({variant}, {product['unit']}) — ₱{price:,.2f}")
    flash(f"{item_name} ({sku}) updated.", "success")
    return redirect(url_for("admin.products"))


# ---------------------------------------------------------------- product bulk import/export (CSV)
# Both routes below round-trip through the same shape the manual "Add a
# product" form uses: base_code + unit (not the raw sku column) — so a
# downloaded export, edited in a spreadsheet and re-uploaded, goes
# through the exact same parse_base_code()/build_sku() validation as a
# hand-typed product, rather than a separate, looser path for
# CSV-sourced rows.
_PRODUCT_CSV_COLUMNS = ["base_code", "item_name", "variant", "unit", "price"]
# Short headers used by the styled .xlsx export; import accepts both sets.
_PRODUCT_XLSX_HEADERS = ["Code*", "Name*", "Variant*", "Unit*", "Price*"]  # all required
_PRODUCT_HEADER_ALIASES = dict(zip(
    (h.rstrip("*").lower() for h in _PRODUCT_XLSX_HEADERS), _PRODUCT_CSV_COLUMNS))


@bp.route("/products/export")
@admin_required
def export_products():
    """Download the full product catalog as CSV, in the same
    base_code/item_name/variant/unit/price shape /products/import
    expects — so a downloaded file can be edited (prices changed, new
    rows appended) and re-uploaded there directly with no reshaping.
    """
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

    # Default download is a branded .xlsx (a CSV can't carry colours);
    # ?format=csv keeps the plain file for anything that needs it. Both
    # have the same columns, and import_products() accepts either.
    if request.args.get("format") != "csv":
        return send_file(
            _build_products_workbook(catalog),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True, download_name=f"products_{stamp}.xlsx",
        )

    # utf-8-sig (BOM) so Excel opens ₱/accented text correctly instead
    # of guessing cp1252 on a plain utf-8 file, same reasoning as any
    # other CSV this app hands to a non-technical admin.
    payload = buffer.getvalue().encode("utf-8-sig")
    return send_file(
        io.BytesIO(payload), mimetype="text/csv",
        as_attachment=True, download_name=f"products_{stamp}.csv",
    )


def _build_products_workbook(catalog):
    """The catalog as a plain workbook: bold heads over one dark rule, hairlines between rows, peso prices.

    Row 1 is the header with the column names import_products() reads, and the products start on row 2,
    so the file can be edited (a price changed, new rows added underneath) and uploaded again as it is.
    Nothing may go above the header row, and the import ignores any extra columns."""
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
    """Bulk create/update products from an uploaded CSV — same
    base_code,item_name,variant,unit,price shape export_products()
    above produces.

    Each row resolves to a SKU via build_sku(), exactly like the manual
    "Add a product" form:
      - SKU already exists -> update item_name/variant/price. unit and
        image_path are left alone (same restriction as edit_product() —
        change a size by adding a new base_code/unit row instead).
      - SKU is new          -> insert it and give every existing branch
        a zero-stock row, same as the manual add path. If this base_code
        already has another size on file, that size's item_name/variant
        win over whatever this row says (see the sibling_skus lookup
        below) — same "reusing a code means the same product" autofill
        the manual form does live, so one row's typo can't leave a scent
        named differently across its own sizes.

    One bad or duplicate-in-file row is skipped and reported by row
    number rather than aborting the whole upload, so a single typo
    doesn't block importing the rest of a large catalog. Each row's own
    insert/update is still atomic on its own, so nothing partial is
    ever left behind for the rows that do fail.
    """
    file_storage = request.files.get("csv_file")
    if not file_storage or not file_storage.filename:
        flash("Choose a CSV file to import.", "error")
        return redirect(url_for("admin.products"))

    if file_storage.filename.lower().endswith(".xlsx"):
        # The styled export: read its first sheet back into the same
        # header-keyed dict rows csv.DictReader would give.
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

        # A brand-new size for a code that already has other sizes on
        # file (e.g. this row is A1-1L and A1-85ML already exists) takes
        # THAT product's name/variant instead of whatever this row says —
        # same "reusing a code means the same product" behavior as the
        # live autofill on the manual Add a Product form, just applied
        # here so a typo in one CSV row can't quietly rename a scent
        # differently across its own sizes. Only for a genuinely new SKU;
        # editing an existing one's own name/variant already goes through
        # this same row further down (or the Edit form) untouched.
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
                        "INSERT INTO products (sku, item_name, variant, category, unit, price) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        (sku, item_name, variant, category, unit, price),
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
