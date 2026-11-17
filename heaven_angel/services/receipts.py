"""Goods-received receipt (PDF) for a fulfilled stock request (delivery).

Why this exists / where it's sourced from:
- A stock request is now a *delivery* — one branch-submitted cart that can
  carry any number of different products (see stock_requests as the
  header row, and stock_request_items for the per-product lines: sku,
  requested_qty, unit_price snapshotted at request time, dispatched_qty,
  received_qty, damaged_qty). This module reads both.
- The only point in the request lifecycle where the full picture exists
  for every line (requested vs. dispatched vs. received vs. damaged vs.
  unaccounted) is once a branch confirms receipt in
  routes/branch.py:receive_stock() and the request flips to 'Fulfilled'.
  That route already writes every one of those numbers onto each
  stock_request_items row, and onto stock_movement_logs.
- This module never recomputes or re-derives any of those numbers — it
  only reads them back out of stock_request_items and
  stock_movement_logs (movement_type DISPATCH / RECEIPT / DAMAGE /
  ADJUSTMENT, all tagged reference_type='STOCK_REQUEST',
  reference_id=<request_id>, one row per SKU on the delivery). That
  keeps the receipt physically incapable of drifting from the ledger,
  since it IS the ledger, just formatted.
- Generated on demand as a PDF, in memory, every time it's requested —
  nothing is pre-rendered or stored as a file on disk.

Access control is the caller's job: pass branch_id when called from the
branch side so a branch can only ever pull a receipt for its own
request; admin callers omit it since HQ can see every branch.
"""
import decimal
import io
from xml.sax.saxutils import escape

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    HRFlowable, KeepTogether, Paragraph, Spacer, Table, TableStyle,
)

from heaven_angel.services import brand_assets
from heaven_angel.services.brand_assets import NumberedCanvas, logo_drawing, plain_document
from heaven_angel.db import query
from heaven_angel.services.reports.formatting import fmt_datetime
from heaven_angel.utils import make_receipt_code

INK = colors.HexColor("#17140D")
INK_FAINT = colors.HexColor("#5B5445")
ACCENT = colors.HexColor("#C9A227")
ACCENT_INK = colors.HexColor("#8A6D1F")
ACCENT_SOFT = colors.HexColor("#FBF1D6")
BORDER = colors.HexColor("#E9E0C9")
DANGER = colors.HexColor("#E23A48")
DANGER_SOFT = colors.HexColor("#FCE7EA")
GOOD = colors.HexColor("#17975E")

# This receipt prints the peso sign (₱) inline in the items table, which
# needs a font with real Unicode currency-symbol coverage (built-in
# Helvetica only has WinAnsiEncoding and renders ₱ as a black box) — see
# brand_assets.register_fonts() for the shared vendored-font logic
# (also used by reports.py and this module's own NumberedCanvas), so
# the font files/names only need to be right in one place.
FONT_REGULAR, FONT_BOLD = brand_assets.register_fonts()


def _fetch_request(request_id, branch_id=None):
    """The delivery header — branch, delivery number, overall status."""
    sql = """SELECT sr.*, b.branch_name, b.location
              FROM stock_requests sr
              JOIN branches b ON sr.branch_id = b.branch_id
              WHERE sr.request_id = %s AND sr.status = 'Fulfilled'"""
    params = [request_id]
    if branch_id is not None:
        sql += " AND sr.branch_id = %s"
        params.append(branch_id)
    return query(sql, tuple(params), fetchone=True)


def _fetch_items(request_id):
    """Every product on this delivery, in the same shape review_request()
    and receive_stock() already work with."""
    return query(
        """SELECT sri.*, p.item_name, p.variant, p.unit
           FROM stock_request_items sri
           JOIN products p ON sri.sku = p.sku
           WHERE sri.request_id = %s ORDER BY p.item_name""",
        (request_id,),
    )


def _fetch_movements(request_id):
    """Every ledger entry tied to this delivery — one DISPATCH/RECEIPT/
    DAMAGE/ADJUSTMENT row per SKU on it, since each line item is dispatched
    and received as its own stock_movement_logs entry."""
    return query(
        """SELECT sml.*, u.username
           FROM stock_movement_logs sml
           LEFT JOIN users u ON sml.created_by_user_id = u.user_id
           WHERE sml.reference_type = 'STOCK_REQUEST' AND sml.reference_id = %s
           ORDER BY sml.created_at""",
        (request_id,),
    )


def _fmt_dt(dt):
    """Nov 8, 2026 1:13 PM: no leading zeros, one space."""
    return fmt_datetime(dt) if dt else "None"


def _fmt_qty(value):
    return str(value) if value is not None else "—"


def _styles():
    base = getSampleStyleSheet()
    return {
        "brand": ParagraphStyle("brand", parent=base["Normal"], fontName=FONT_BOLD,
                                fontSize=17, textColor=INK, leading=20),
        "brand_sub": ParagraphStyle("brand_sub", parent=base["Normal"], fontName=FONT_REGULAR,
                                    fontSize=8.5, textColor=INK_FAINT, leading=12),
        "doc_title": ParagraphStyle("doc_title", parent=base["Normal"], fontName=FONT_BOLD,
                                    fontSize=12.5, textColor=ACCENT_INK, alignment=TA_RIGHT, leading=15),
        "doc_meta": ParagraphStyle("doc_meta", parent=base["Normal"], fontName=FONT_REGULAR,
                                   fontSize=8.5, textColor=INK_FAINT, alignment=TA_RIGHT, leading=12),
        "label": ParagraphStyle("label", parent=base["Normal"], fontName=FONT_BOLD,
                                fontSize=7.3, textColor=INK_FAINT, leading=10, spaceAfter=1),
        "value": ParagraphStyle("value", parent=base["Normal"], fontName=FONT_BOLD,
                                fontSize=10.8, textColor=INK, leading=13),
        "value_soft": ParagraphStyle("value_soft", parent=base["Normal"], fontName=FONT_REGULAR,
                                     fontSize=9.5, textColor=INK, leading=12),
        "section": ParagraphStyle("section", parent=base["Normal"], fontName=FONT_BOLD,
                                  fontSize=9.5, textColor=INK, leading=12, spaceBefore=11, spaceAfter=4),
        "qty_label": ParagraphStyle("qty_label", parent=base["Normal"], fontName=FONT_REGULAR,
                                    fontSize=7.5, textColor=INK_FAINT, alignment=TA_CENTER, leading=10),
        "qty_value": ParagraphStyle("qty_value", parent=base["Normal"], fontName=FONT_BOLD,
                                    fontSize=15, textColor=INK, alignment=TA_CENTER, leading=18),
        "timeline_label": ParagraphStyle("timeline_label", parent=base["Normal"], fontName=FONT_BOLD,
                                         fontSize=9, textColor=INK, leading=12),
        "timeline_meta": ParagraphStyle("timeline_meta", parent=base["Normal"], fontName=FONT_REGULAR,
                                        fontSize=8.5, textColor=INK_FAINT, leading=11),
        "note": ParagraphStyle("note", parent=base["Normal"], fontName=FONT_REGULAR,
                               fontSize=8.8, textColor=DANGER, leading=12),
        "footer": ParagraphStyle("footer", parent=base["Normal"], fontName=FONT_REGULAR,
                                 fontSize=7.5, textColor=INK_FAINT, leading=10),
        "sig_label": ParagraphStyle("sig_label", parent=base["Normal"], fontName=FONT_REGULAR,
                                    fontSize=8, textColor=INK_FAINT, leading=10, spaceBefore=4),
        "th_cell": ParagraphStyle("th_cell", parent=base["Normal"], fontName=FONT_BOLD,
                                  fontSize=7, textColor=INK_FAINT, leading=9),
        "th_cell_num": ParagraphStyle("th_cell_num", parent=base["Normal"], fontName=FONT_BOLD,
                                      fontSize=7, textColor=INK_FAINT, leading=9, alignment=TA_RIGHT),
        "item_cell": ParagraphStyle("item_cell", parent=base["Normal"], fontName=FONT_BOLD,
                                    fontSize=8.3, textColor=INK, leading=10.5),
        "row_cell": ParagraphStyle("row_cell", parent=base["Normal"], fontName=FONT_REGULAR,
                                   fontSize=8.3, textColor=INK, leading=10.5),
        "row_cell_num": ParagraphStyle("row_cell_num", parent=base["Normal"], fontName=FONT_REGULAR,
                                       fontSize=8.3, textColor=INK, leading=10.5, alignment=TA_RIGHT),
        "row_cell_num_bold": ParagraphStyle("row_cell_num_bold", parent=base["Normal"], fontName=FONT_BOLD,
                                            fontSize=8.3, textColor=INK, leading=10.5, alignment=TA_RIGHT),
    }


def _fetch_sale(sale_id, branch_id=None):
    """A recorded sale/refill with the product, branch and (for a Credit sale)
    the buyer's name joined in. When the sale belongs to an order of several
    scents (sales.order_id), the result describes the order's first remaining
    row and carries every row of the order in "lines", oldest first; a plain
    one-product sale has no "lines".
    """
    base = """SELECT s.*, p.item_name, p.variant, p.unit, b.branch_name, b.location,
                     COALESCE(s.buyer_name, bu.username) AS buyer_username
              FROM sales s
              JOIN sale_catalog p ON s.sku = p.sku
              JOIN branches b ON s.branch_id = b.branch_id
              LEFT JOIN users bu ON s.buyer_user_id = bu.user_id
              WHERE (s.sale_id = %s OR s.order_id = %s)"""
    scope, extra = "", ()
    if branch_id is not None:
        scope, extra = " AND s.branch_id = %s", (branch_id,)
    rows = query(base + scope + " ORDER BY s.sale_id", (sale_id, sale_id, *extra))
    if not rows:
        return None
    asked = next((r for r in rows if r["sale_id"] == sale_id), rows[0])
    order_id = asked["order_id"] or sale_id
    if order_id != sale_id:                  # asked for a later row: load the whole order
        rows = query(base + scope + " ORDER BY s.sale_id", (order_id, order_id, *extra))
    head = dict(rows[0])
    head["order_number"] = order_id
    if len(rows) > 1:
        head["lines"] = rows
    return head


def build_receipt_pdf(request_id, branch_id=None):
    """Return (BytesIO, request_row) for a Fulfilled delivery.

    Returns (None, None) if the request doesn't exist, isn't Fulfilled
    yet, or — when branch_id is given — doesn't belong to that branch.
    """
    req = _fetch_request(request_id, branch_id)
    if not req:
        return None, None

    items = _fetch_items(request_id)
    movements = _fetch_movements(request_id)

    # A delivery can now have several shipment dispatch/receipt events.
    # Keep the aggregate line-item quantities below and show the first
    # dispatch plus the latest receipt in the compact timeline.
    dispatch_mvs = [m for m in movements if m["movement_type"] == "DISPATCH"]
    receipt_mvs = [m for m in movements if m["movement_type"] == "RECEIPT"]
    damage_mvs = [m for m in movements if m["movement_type"] == "DAMAGE"]
    adjustment_mvs = [
        m for m in movements if m["movement_type"] == "ADJUSTMENT"]
    dispatch_mv = dispatch_mvs[0] if dispatch_mvs else None
    receipt_mv = receipt_mvs[-1] if receipt_mvs else None

    sku_to_name = {item["sku"]: item["item_name"] for item in items}

    total_requested = sum(item["requested_qty"] for item in items)
    total_dispatched = sum(item["dispatched_qty"] or 0 for item in items)
    total_received = sum(item["received_qty"] or 0 for item in items)
    total_damaged = sum(item["damaged_qty"] or 0 for item in items)
    total_value = sum(item["requested_qty"] *
                      item["unit_price"] for item in items)
    shortfall = total_dispatched - total_received - total_damaged

    s = _styles()
    buf = io.BytesIO()
    doc = plain_document(
        buf, pagesize=letter,
        topMargin=16 * mm, bottomMargin=14 * mm, leftMargin=20 * mm, rightMargin=20 * mm,
        title=f"Goods Received Receipt GR-{request_id:06d}",
    )
    story = []

    # ---- Header: what this is on the left, the brand small on the right ----
    def esc(value):
        """Cell text goes through the PDF engine as markup: B&B Works would come out as "B&B; Works"."""
        return escape(str(value))

    title_style = ParagraphStyle("gr_title", parent=s["brand"], fontSize=16, leading=20)
    number_style = ParagraphStyle("gr_number", parent=s["value_soft"], fontSize=9.5, leading=13)
    meta_style = ParagraphStyle("gr_meta", parent=s["doc_meta"], alignment=TA_LEFT)
    brand_style = ParagraphStyle("gr_brand", parent=s["brand"], fontSize=9, leading=12)

    left = [
        Paragraph("Goods received receipt", title_style),
        Paragraph(f"Receipt No. GR-{request_id:06d}", number_style),
        Paragraph(f"Delivery {esc(req['delivery_number'])}", meta_style),
    ]
    name_w = stringWidth("Heaven & Angel Scents", FONT_BOLD, 9) + 4
    brand_w = 22 + name_w
    brand = Table([[logo_drawing(16), Paragraph("Heaven &amp; Angel Scents", brand_style)]],
                  colWidths=[22, name_w], hAlign="RIGHT")
    brand.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    header = Table([[left, brand]], colWidths=[doc.width - brand_w, brand_w])
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header)
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=1.2, color=ACCENT, spaceAfter=12))

    # ---- Who and where: plain label over value, no boxes ----
    def info_cell(label, value):
        return [Paragraph(label, s["label"]), Paragraph(esc(value), s["value"])]

    info_grid = Table(
        [[
            info_cell("Received by branch", req["branch_name"]),
            info_cell("Location", req["location"] or "None on file"),
        ], [
            info_cell("Delivery number", req["delivery_number"]),
            info_cell("Items on this delivery", len(items)),
        ]],
        colWidths=[doc.width / 2] * 2,
    )
    info_grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(info_grid)

    # Section heads share one pattern: bold title, hairline under it.
    def section(title):
        story.append(Paragraph(title, s["section"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=BORDER, spaceBefore=1, spaceAfter=6))

    # ---- Items: sentence case heads over one dark rule, hairlines between rows, no box ----
    section("Items")
    head_style = ParagraphStyle("gr_th", parent=s["th_cell"], fontSize=7.8, textColor=INK, leading=10)
    head_num_style = ParagraphStyle("gr_th_num", parent=head_style, alignment=TA_RIGHT)
    items_rows = [[
        Paragraph("Item", head_style), Paragraph("Unit", head_style),
        Paragraph("Requested", head_num_style), Paragraph("Dispatched", head_num_style),
        Paragraph("Received", head_num_style), Paragraph("Damaged", head_num_style),
        Paragraph("Unit price", head_num_style), Paragraph("Line total", head_num_style),
    ]]
    for item in items:
        line_total = item["requested_qty"] * item["unit_price"]
        items_rows.append([
            Paragraph(
                f"{esc(item['item_name'])}<br/><font size=7 color='#5B5445'>"
                f"{esc(item['variant'])}, {esc(item['sku'])}</font>", s["item_cell"]),
            Paragraph(esc(item["unit"]), s["row_cell"]),
            Paragraph(str(item["requested_qty"]), s["row_cell_num"]),
            Paragraph(_fmt_qty(item["dispatched_qty"]), s["row_cell_num"]),
            Paragraph(_fmt_qty(item["received_qty"]), s["row_cell_num"]),
            Paragraph(str(item["damaged_qty"]), s["row_cell_num"]),
            Paragraph(f"₱{item['unit_price']:,.2f}", s["row_cell_num"]),
            Paragraph(f"₱{line_total:,.2f}", s["row_cell_num_bold"]),
        ])
    items_rows.append([
        Paragraph("Total", s["item_cell"]), "", "", "", "", "", "",
        Paragraph(f"₱{total_value:,.2f}", s["row_cell_num_bold"]),
    ])
    weights = [0.28, 0.08, 0.10, 0.11, 0.10, 0.10, 0.11, 0.12]
    items_table = Table(items_rows, colWidths=[doc.width * w for w in weights], repeatRows=1)
    items_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
        ("LINEBELOW", (0, 0), (-1, 0), 0.9, INK),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, BORDER),
        ("LINEABOVE", (0, -1), (-1, -1), 0.9, INK),
    ]))
    story.append(items_table)

    # ---- Quantities across every item: four plain figures, no boxes ----
    section("Quantities")

    def qty_cell(label, value, color=INK):
        style = ParagraphStyle("qv_%s" % label, parent=s["qty_value"], textColor=color)
        return [Paragraph(str(value), style), Paragraph(label, s["qty_label"])]

    # Colour is for what needs a look: damaged units and anything unaccounted for.
    qty_row = [
        qty_cell("Requested", total_requested),
        qty_cell("Dispatched", total_dispatched),
        qty_cell("Received", total_received),
        qty_cell("Damaged", total_damaged, color=DANGER if total_damaged else INK),
        qty_cell("Unaccounted", shortfall, color=DANGER if shortfall > 0 else INK),
    ]
    qty_table = Table([qty_row], colWidths=[doc.width / 5] * 5)
    qty_table.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(qty_table)

    if shortfall > 0:
        story.append(Spacer(1, 6))
        noun = "unit was" if shortfall == 1 else "units were"
        story.append(Paragraph(
            f"<b>{shortfall} {noun} not accounted for.</b> {'It was' if shortfall == 1 else 'They were'} dispatched "
            "but neither received nor reported damaged. This is flagged in the movement ledger for HQ follow-up.",
            s["note"],
        ))

    # ---- Timeline: what happened, when, and who confirmed it ----
    section("Timeline")
    timeline_rows = [
        ("Requested", req["requested_at"], None),
        ("First shipment dispatched", dispatch_mv["created_at"] if dispatch_mv else None,
         dispatch_mv["username"] if dispatch_mv else None),
        ("Latest shipment received", receipt_mv["created_at"] if receipt_mv else None,
         receipt_mv["username"] if receipt_mv else None),
    ]
    tl_data = []
    for label, dt, who in timeline_rows:
        tl_data.append([
            Paragraph(label, s["timeline_label"]),
            Paragraph(_fmt_dt(dt) if dt else "Not yet", s["timeline_meta"]),
            Paragraph(f"Confirmed by {esc(who)}" if who else "", s["timeline_meta"]),
        ])
    tl_table = Table(tl_data, colWidths=[doc.width * 0.34, doc.width * 0.36, doc.width * 0.30])
    tl_table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, BORDER),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(tl_table)

    # Damage/adjustment notes are per line item (one row per SKU in the
    # ledger), so each is prefixed with which product it's about.
    for mv in damage_mvs:
        if mv.get("notes"):
            label = sku_to_name.get(mv["sku"], mv["sku"])
            story.append(Spacer(1, 4))
            story.append(Paragraph(f"Damage note ({esc(label)}): {esc(mv['notes'])}", s["footer"]))
    for mv in adjustment_mvs:
        if mv.get("notes"):
            label = sku_to_name.get(mv["sku"], mv["sku"])
            story.append(Spacer(1, 2))
            story.append(Paragraph(f"Ledger note ({esc(label)}): {esc(mv['notes'])}", s["footer"]))

    # ---- Signatures + footer ----
    # Kept together as one block: on a delivery that only just fills
    # page one, the footer used to spill onto a page of its own.
    sig_table = Table(
        [[
            Table([[HRFlowable(width=70 * mm, thickness=0.75, color=INK_FAINT)],
                   [Paragraph("Received by (branch staff)", s["sig_label"])]]),
            Table([[HRFlowable(width=70 * mm, thickness=0.75, color=INK_FAINT)],
                   [Paragraph("Verified by (HQ admin)", s["sig_label"])]]),
        ]],
        colWidths=[doc.width / 2] * 2,
    )
    story.append(KeepTogether([
        Spacer(1, 22),
        sig_table,
        Spacer(1, 12),
        Paragraph(
            "Figures come from the stock movement ledger at the moment the delivery was confirmed received, "
            "and are not editable afterwards.",
            s["footer"],
        ),
    ]))

    doc.build(story, canvasmaker=NumberedCanvas)
    buf.seek(0)
    return buf, req


# ---------------------------------------------------------------------------
# Sales receipt — a single Sale or Refill, printable right after Record
# Sale saves it. Unlike the goods-received receipt above, a sale has no
# multi-step lifecycle to wait on (dispatch -> receive): it's complete
# the instant it's inserted, so this can be generated immediately, same
# request-response cycle as the redirect back to Record Sale.
#
# This is deliberately shaped like a small thermal-printer slip (80mm
# roll width) instead of a full letter-size page — one item per sale
# means there's rarely more than a dozen lines to show, and a receipt
# this size is what a customer actually expects to be handed. Height
# isn't fixed up front and can't just be trimmed after the fact either
# (shrinking a PDF's page size after drawing on it would clip content
# near the old top right off the new, shorter page instead of moving it
# down). Instead this renders in two passes: a "dry" pass runs the exact
# same layout with a no-op canvas purely to measure how much vertical
# space the content needs, then a real pass draws it for real onto a
# canvas already sized to fit — so a Refill note or a long customer
# address grows the receipt instead of leaving blank space or clipping.
#
# Every receipt carries a QR code encoding a short signed verification
# code (see utils.make_receipt_code()) — not the sale's data itself,
# just a code that /scan/verify (routes/scan.py) can turn back into
# this exact sale. That's what lets Scan Receipt confirm a printed
# receipt is genuinely on file and pull up what was recorded, without
# ever trusting raw QR content on its own.
#
# Access control is the caller's job, same convention as
# build_receipt_pdf(): pass branch_id from the branch side so a branch
# can only ever pull a receipt for a sale recorded at its own branch;
# admin callers omit it since HQ can see every branch (including its
# own HQ/Main Branch sales, and — if ever needed — any branch's).
# ---------------------------------------------------------------------------
RECEIPT_WIDTH = 80 * mm
RECEIPT_MARGIN = 4.5 * mm
RECEIPT_CONTENT_WIDTH = RECEIPT_WIDTH - (2 * RECEIPT_MARGIN)
RECEIPT_TOP_MARGIN = 7 * mm
RECEIPT_BOTTOM_MARGIN = 7 * mm


def _qr_drawing(data, size):
    """A reportlab Drawing containing a QR code encoding `data`, scaled
    to exactly `size` x `size` points so it can be placed like any other
    flowable/graphic regardless of how many modules the code itself has.
    """
    widget = QrCodeWidget(data)
    x0, y0, x1, y1 = widget.getBounds()
    native_w, native_h = (x1 - x0), (y1 - y0)
    drawing = Drawing(size, size, transform=[size / native_w, 0, 0, size / native_h, 0, 0])
    drawing.add(widget)
    return drawing


def _render_sale_receipt(c, sale, receipt_code, top_y, dry=False, slip=None):
    """Draw the receipt body top-down starting at `top_y`, and return the
    total vertical space it used. When dry=True, `c` is ignored (pass
    None) and nothing is actually drawn — only the layout's height is
    computed, by running every line through the exact same advance()
    calls a real draw would make.

    Layout, top to bottom: a text-only letterhead (no logo — the mark
    used to sit centered above the brand name and, at slip width,
    collided with it), the receipt type and number, the sale details, the
    line item, a prominent total, then the QR verification block. Plain
    hairline rules separate the sections instead of rows of "* * *"
    characters, which read as noise on a small slip.
    """
    # One receipt can carry several products: sale["lines"] is a list of
    # {item_name, variant, unit, sku, qty_sold, unit_price}. A normal single-product
    # sale has no "lines" and is its own only line.
    # `slip` is set when the order is printed over several slips (see
    # build_sale_receipt_pdf): {n, of, first, last, items, units, order_total}.
    lines = sale.get("lines") or [sale]
    line_total = sum((ln["qty_sold"] * ln["unit_price"] for ln in lines), decimal.Decimal("0"))
    slip = slip or {"n": 1, "of": 1, "first": 1, "last": True, "items": len(lines),
                    "units": sum(ln["qty_sold"] for ln in lines), "order_total": line_total}
    is_last = slip["last"]
    is_refill = sale["sale_type"] == "Refill"
    is_freebie = sale["sale_type"] == "Freebie"

    x_left = RECEIPT_MARGIN
    x_right = RECEIPT_WIDTH - RECEIPT_MARGIN
    x_center = RECEIPT_WIDTH / 2
    state = {"y": top_y}

    def advance(pt):
        state["y"] -= pt

    def center(text, font=FONT_BOLD, size=10.5, color=INK, gap=None):
        if not dry:
            c.setFont(font, size)
            c.setFillColor(color)
            c.drawCentredString(x_center, state["y"], text)
        advance(gap if gap is not None else size + 4.5)

    def rule(color=BORDER, width=0.6, gap=12):
        # Drawn a few points above the current baseline so it sits in
        # the gap between the line before it and the one after.
        if not dry:
            c.setStrokeColor(color)
            c.setLineWidth(width)
            y = state["y"] + 3
            c.line(x_left, y, x_right, y)
        advance(gap)

    def kv(label, value, size=7.8, gap=None):
        value = str(value)
        room = RECEIPT_CONTENT_WIDTH - stringWidth(label, FONT_REGULAR, size) - 8
        if stringWidth(value, FONT_BOLD, size) <= room:
            if not dry:
                c.setFont(FONT_REGULAR, size)
                c.setFillColor(INK_FAINT)
                c.drawString(x_left, state["y"], label)
                c.setFont(FONT_BOLD, size)
                c.setFillColor(INK)
                c.drawRightString(x_right, state["y"], value)
            advance(gap if gap is not None else size + 6)
            return
        # Too long for the same line (a long customer name, say): the label gets
        # its own line and the value wraps under it, so nothing runs off the slip
        # or over the label, and the slip simply gets taller.
        if not dry:
            c.setFont(FONT_REGULAR, size)
            c.setFillColor(INK_FAINT)
            c.drawString(x_left, state["y"], label)
        advance(size + 3)
        for wrapped_line in simpleSplit(value, FONT_BOLD, size, RECEIPT_CONTENT_WIDTH):
            if not dry:
                c.setFont(FONT_BOLD, size)
                c.setFillColor(INK)
                c.drawRightString(x_right, state["y"], wrapped_line)
            advance(size + 3)
        advance(5)

    def small_left(text, font=FONT_REGULAR, size=7, color=INK_FAINT, gap=None):
        """Wrapped, left-aligned small text (e.g. a customer address)."""
        for wrapped_line in simpleSplit(text, font, size, RECEIPT_CONTENT_WIDTH):
            if not dry:
                c.setFont(font, size)
                c.setFillColor(color)
                c.drawString(x_left, state["y"], wrapped_line)
            advance(size + 3)
        advance(gap if gap is not None else 3)

    def note(text, font=FONT_REGULAR, size=7, color=INK_FAINT, gap=None):
        if not dry:
            c.setFont(font, size)
            c.setFillColor(color)
            c.drawCentredString(x_center, state["y"], text)
        advance(gap if gap is not None else size + 4)

    def callout(lines, size=7):
        """A short note about this sale (Refill, Freebie): plain centred lines under the total."""
        for text in lines:
            note(text, size=size, color=INK_FAINT, gap=size + 3)
        advance(5)

    # ---- Letterhead (text only) ----
    center("Heaven & Angel Scents", font=FONT_BOLD, size=12.5, gap=12)
    center("Perfume Manufacturing & Retail", font=FONT_REGULAR, size=7, color=INK_FAINT, gap=9)
    if sale.get("location"):
        for loc_line in simpleSplit(sale["location"], FONT_REGULAR, 7, RECEIPT_CONTENT_WIDTH):
            center(loc_line, font=FONT_REGULAR, size=7, color=INK_FAINT, gap=9)
    advance(4)
    rule(color=ACCENT, width=0.9, gap=14)

    # ---- Receipt type + number ----
    if is_refill:
        receipt_title = "Refill receipt"
    elif is_freebie:
        receipt_title = "Freebie receipt"
    else:
        receipt_title = "Sales receipt"
    center(receipt_title, font=FONT_BOLD, size=8, color=ACCENT_INK, gap=13)
    center(f"SR-{sale.get('order_number', sale['sale_id']):06d}", font=FONT_BOLD, size=14, gap=11)
    center(_fmt_dt(sale["sold_at"]), font=FONT_REGULAR, size=7.2, color=INK_FAINT, gap=12 if slip["of"] == 1 else 10)
    if slip["of"] > 1:
        center(f"Slip {slip['n']} of {slip['of']}", font=FONT_BOLD, size=7.6, color=ACCENT_INK, gap=12)
    rule()

    # ---- Sale details ----
    kv("Branch", sale["branch_name"])
    if sale["payment_method"] == "Credit":
        kv("Payment", "Credit")
        if sale["buyer_username"]:
            kv("Charged to", sale["buyer_username"])
    else:
        kv("Payment", sale["payment_method"])
    kv("Customer", sale["customer_name"] or "Walk-in", gap=None if sale["customer_address"] else 13)
    if sale["customer_address"]:
        small_left(sale["customer_address"], gap=6)
    rule()

    # ---- Line items ----
    # Each name wraps in the space its price leaves, so a long scent name (or a
    # price in the thousands) grows the slip instead of running into the price.
    if slip["items"] > 1:
        if slip["of"] > 1:
            first = slip["first"]
            note(f"Items {first}-{first + len(lines) - 1} of {slip['items']}", font=FONT_BOLD, size=7,
                 color=INK_FAINT, gap=11)
        else:
            note(f"{slip['items']} items, {slip['units']:,} units", font=FONT_BOLD, size=7, color=INK_FAINT, gap=11)
    for n, item in enumerate(lines):
        if n:
            rule(gap=11)           # hairline between products
        item_total = item["qty_sold"] * item["unit_price"]
        price_text = f"₱{item_total:,.2f}"
        name_room = RECEIPT_CONTENT_WIDTH - stringWidth(price_text, FONT_BOLD, 8.6) - 8
        for i, name_line in enumerate(simpleSplit(str(item["item_name"]), FONT_BOLD, 8.6, name_room) or [""]):
            if not dry:
                c.setFont(FONT_BOLD, 8.6)
                c.setFillColor(INK)
                c.drawString(x_left, state["y"], name_line)
                if i == 0:
                    c.drawRightString(x_right, state["y"], price_text)
            advance(10.5)
        detail = f"{item['variant']}, {item['unit']}, {item['sku']}"
        qty_label = f"{item['qty_sold']:,} mL" if item["unit"] == "BULK" else f"{item['qty_sold']:,}"
        calc = f"{qty_label} × ₱{item['unit_price']:,.2f}"
        if stringWidth(detail, FONT_REGULAR, 7) + stringWidth(calc, FONT_REGULAR, 7) + 8 <= RECEIPT_CONTENT_WIDTH:
            if not dry:
                c.setFont(FONT_REGULAR, 7)
                c.setFillColor(INK_FAINT)
                c.drawString(x_left, state["y"], detail)
                c.drawRightString(x_right, state["y"], calc)
            advance(13)
        else:       # not enough room side by side: the sum goes on its own line
            for wrapped_line in simpleSplit(detail, FONT_REGULAR, 7, RECEIPT_CONTENT_WIDTH):
                if not dry:
                    c.setFont(FONT_REGULAR, 7)
                    c.setFillColor(INK_FAINT)
                    c.drawString(x_left, state["y"], wrapped_line)
                advance(9.5)
            if not dry:
                c.setFont(FONT_REGULAR, 7)
                c.setFillColor(INK_FAINT)
                c.drawRightString(x_right, state["y"], calc)
            advance(13)
    rule(color=INK, width=0.8, gap=15)

    # ---- Total ----
    # Over several slips each one shows its own subtotal; the last also shows
    # the whole order's total.
    if slip["of"] > 1 and is_last:
        sub_text = f"₱{line_total:,.2f}"
        if not dry:
            c.setFont(FONT_REGULAR, 7.6)
            c.setFillColor(INK_FAINT)
            c.drawString(x_left, state["y"], "Subtotal, this slip")
            c.setFont(FONT_BOLD, 7.6)
            c.setFillColor(INK)
            c.drawRightString(x_right, state["y"], sub_text)
        advance(15)
    shown_total = slip["order_total"] if is_last else line_total
    label = "Total" if is_last else "Subtotal"
    total_text = f"₱{shown_total:,.2f}"
    total_room = RECEIPT_CONTENT_WIDTH - stringWidth(label, FONT_BOLD, 9) - 8
    total_size = 15.0 if is_last else 11.0
    while total_size > 9 and stringWidth(total_text, FONT_BOLD, total_size) > total_room:
        total_size -= 0.5          # a very large amount shrinks to fit rather than overlapping the label
    if not dry:
        c.setFont(FONT_BOLD, 9)
        c.setFillColor(INK)
        c.drawString(x_left, state["y"] + 1, label)
        c.setFont(FONT_BOLD, total_size)
        c.drawRightString(x_right, state["y"], total_text)
    advance(16)

    if not is_last:
        advance(2)
        callout([f"Continued on slip {slip['n'] + 1} of {slip['of']}."])
    elif is_refill:
        advance(2)
        callout(["Refill: the customer's own bottle.", "Product cost only. No bottle was taken from stock."])
    elif is_freebie:
        advance(2)
        callout(["Freebie: a giveaway, no charge."])
    else:
        advance(4)
    rule()

    # ---- QR verification ----
    qr_size = 26 * mm
    if not dry:
        qr_drawing = _qr_drawing(receipt_code, qr_size)
        renderPDF.draw(qr_drawing, c, x_center - (qr_size / 2), state["y"] - qr_size + 6)
    advance(qr_size + 4)
    note("Scan to verify this receipt", font=FONT_BOLD, size=7, color=INK, gap=9)
    note(receipt_code, size=6.6, gap=13)
    rule()

    if is_last:
        note("Thank you." if is_freebie else "Thank you for your purchase.",
             font=FONT_BOLD, size=8.4, color=INK, gap=2)
    else:
        note("Keep all slips together.", font=FONT_REGULAR, size=7, color=INK_FAINT, gap=2)

    return top_y - state["y"]


SLIP_MAX_LINES = 10      # products per printed slip; a bigger order continues on the next slip


def build_sale_receipt_pdf(sale_id, branch_id=None):
    """Return (BytesIO, sale_row) for a recorded sale/refill, or for the whole order
    it belongs to when it has several scents.

    An order of more than SLIP_MAX_LINES products is printed as several slips, one
    PDF page each, every page exactly as tall as its content. Returns (None, None)
    if the sale doesn't exist, or - when branch_id is given - didn't happen at that branch.
    """
    sale = _fetch_sale(sale_id, branch_id)
    if not sale:
        return None, None

    number = sale.get("order_number", sale["sale_id"])
    receipt_code = make_receipt_code(number)
    all_lines = sale.get("lines") or [sale]
    chunks = [all_lines[i:i + SLIP_MAX_LINES] for i in range(0, len(all_lines), SLIP_MAX_LINES)]
    order_total = sum((ln["qty_sold"] * ln["unit_price"] for ln in all_lines), decimal.Decimal("0"))
    units = sum(ln["qty_sold"] for ln in all_lines)

    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=(RECEIPT_WIDTH, 100))
    c.setTitle(f"Sales Receipt SR-{number:06d}")
    for n, chunk in enumerate(chunks, start=1):
        part = {**sale, "lines": chunk if len(all_lines) > 1 else None}
        slip = {"n": n, "of": len(chunks), "first": (n - 1) * SLIP_MAX_LINES + 1, "last": n == len(chunks),
                "items": len(all_lines), "units": units, "order_total": order_total}
        # Pass 1 (dry): run the identical layout with no canvas, purely to find
        # out how tall this slip needs to be. Pass 2 draws it for real onto a page
        # already sized to fit: nothing clipped, nothing left blank underneath.
        content_height = _render_sale_receipt(None, part, receipt_code, top_y=0, dry=True, slip=slip)
        page_height = content_height + RECEIPT_TOP_MARGIN + RECEIPT_BOTTOM_MARGIN
        c.setPageSize((RECEIPT_WIDTH, page_height))
        _render_sale_receipt(c, part, receipt_code, top_y=page_height - RECEIPT_TOP_MARGIN, dry=False, slip=slip)
        c.showPage()
    c.save()
    buf.seek(0)
    return buf, sale
