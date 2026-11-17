"""PDF rendering of a report.

A plain, printable document: the title and what it covers at the top left, the brand small at
the top right, then one table. The table follows the usual rules for a printed table: heads in
sentence case over a single dark rule, hairlines between rows, no outer box, numbers right
aligned under right aligned heads, columns sized to what they hold. The one accent colour is
the rule under the header."""
import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    HRFlowable, Paragraph, Spacer, Table, TableStyle,
)

from heaven_angel.services.brand_assets import NumberedCanvas, logo_drawing, plain_document, register_fonts
from heaven_angel.services.reports.constants import MAX_ROWS
from heaven_angel.services.reports.formatting import ACCENT, BORDER, INK, INK_FAINT, badge_colors, fmt_cell, sentence_case


# Built-in PDF fonts (Helvetica etc.) only cover Latin-1 and have no glyph
# for the ₱ (Philippine peso) sign — it silently renders as a black "tofu"
# box instead of erroring, which is easy to miss until someone opens the
# PDF. IBM Plex Sans does have that glyph, and matches the IBM Plex Mono
# already used for SKU/mono styling elsewhere in the app, so reports and
# the web UI share a type family. See brand_assets.register_fonts() for
# the shared vendored-font registration (also used by receipts.py and
# this module's own NumberedCanvas), so the font files/names only need
# to be right in one place.
register_fonts()


BODY_PT = 8
NUM_TYPES = ("money", "price", "int", "num")
CELL_PAD = 6          # pt of padding each side of a cell
SLACK = 4             # pt of room beyond a word's exact width, so it never breaks at the last letter
LONGEST_COLUMN = 170  # pt: a text column never asks for more than this before it wraps


# ---------------------------------------------------------------- PDF rendering
def _pdf_styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("title", parent=base["Normal"], fontName="IBMPlexSans-Bold",
                                fontSize=16, textColor=INK, leading=20),
        "subtitle": ParagraphStyle("subtitle", parent=base["Normal"], fontName="IBMPlexSans",
                                   fontSize=9, textColor=INK, leading=13),
        "meta": ParagraphStyle("meta", parent=base["Normal"], fontName="IBMPlexSans",
                               fontSize=8, textColor=INK_FAINT, leading=11),
        "brand": ParagraphStyle("brand", parent=base["Normal"], fontName="IBMPlexSans-Bold",
                                fontSize=9, textColor=INK, leading=12),
        "th": ParagraphStyle("th", parent=base["Normal"], fontName="IBMPlexSans-Bold",
                             fontSize=BODY_PT, textColor=INK, leading=10.5),
        "th_num": ParagraphStyle("th_num", parent=base["Normal"], fontName="IBMPlexSans-Bold",
                                 fontSize=BODY_PT, textColor=INK, leading=10.5, alignment=TA_RIGHT),
        "td": ParagraphStyle("td", parent=base["Normal"], fontName="IBMPlexSans",
                             fontSize=BODY_PT, textColor=INK, leading=10.5),
        "td_num": ParagraphStyle("td_num", parent=base["Normal"], fontName="IBMPlexSans",
                                 fontSize=BODY_PT, textColor=INK, leading=10.5, alignment=TA_RIGHT),
        "footer": ParagraphStyle("footer", parent=base["Normal"], fontName="IBMPlexSans",
                                 fontSize=8, textColor=INK_FAINT, leading=11),
    }


def _p(text, style):
    """A table cell. The PDF engine reads cell text as markup, so a name like B&B Works or D&G has to be
    escaped or it comes out as "B&B; Works"."""
    return Paragraph(escape(str(text)), style)


def _column_widths(columns, texts, heads, available):
    """Width in points for each column, sized to its content.

    A column wants the width of its widest value (up to LONGEST_COLUMN, past which text wraps)
    and never less than its longest single word, so nothing breaks mid-word. Spare space is
    shared in proportion to what each column wants, so the table always fills the page."""
    ideal, floor = [], []
    for c in range(len(columns)):
        values = texts[c] + [heads[c]]
        widest = max(stringWidth(v, "IBMPlexSans", BODY_PT) for v in values)
        longest_word = max(
            (stringWidth(w, "IBMPlexSans-Bold" if v is heads[c] else "IBMPlexSans", BODY_PT)
             for v in values for w in v.split()), default=0)
        ctype = columns[c][2]
        ideal.append(min(widest, LONGEST_COLUMN) + CELL_PAD * 2 + SLACK)
        # Dates, amounts, counts and statuses are short and fixed in shape: they never wrap, so only
        # free text columns (names, suppliers, notes) give up width when the table is tight.
        fixed = ctype in NUM_TYPES or ctype == "datetime" or ctype.startswith("badge:")
        floor.append((ideal[-1]) if fixed else min(longest_word, LONGEST_COLUMN) + CELL_PAD * 2 + SLACK)
    if sum(ideal) <= available:
        spare = available - sum(ideal)
        return [w + spare * (w / sum(ideal)) for w in ideal]
    if sum(floor) >= available:
        return [w * available / sum(floor) for w in floor]
    room = available - sum(floor)
    want = [i - f for i, f in zip(ideal, floor)]
    total_want = sum(want) or 1
    return [f + room * (w / total_want) for f, w in zip(floor, want)]


def render_report_pdf(report):
    """Return a BytesIO PDF for a report dict built by get_report()."""
    s = _pdf_styles()
    buf = io.BytesIO()
    doc = plain_document(
        buf, pagesize=landscape(letter),
        topMargin=15 * mm, bottomMargin=14 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
        title=f"{report['title']} report", author="Heaven & Angel Scents",
    )
    story = []

    # Title and what it covers on the left, the brand small on the right.
    generated = f"Generated {report['generated_at'].strftime('%b')} {report['generated_at'].day}, " \
                f"{report['generated_at'].year} {report['generated_at'].strftime('%I:%M %p').lstrip('0')}"
    if report["actor_label"]:
        generated += f" by {report['actor_label']}"
    left = [_p(report["title"] + " report", s["title"])]
    if report["subtitle"]:
        left.append(_p(report["subtitle"], s["subtitle"]))
    left.append(Spacer(1, 2))
    left.append(_p(generated, s["meta"]))

    name_w = stringWidth("Heaven & Angel Scents", "IBMPlexSans-Bold", 9) + 4
    brand_w = 22 + name_w
    brand = Table([[logo_drawing(16), Paragraph("Heaven &amp; Angel Scents", s["brand"])]],
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
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(header)
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=1.2, color=ACCENT, spaceAfter=10))

    # Columns are (key, label, ctype) from most builders, or (key, label, ctype, weight) from ones
    # that set a width weight. Widths now come from the content, so the weight is no longer used.
    columns = [tuple(c[:3]) for c in report["columns"]]
    rows = report["rows"]

    if not rows:
        story.append(Spacer(1, 30))
        story.append(Paragraph("No data matches the selected filters.", s["footer"]))
    else:
        heads = [sentence_case(label) for _, label, _ in columns]

        # Plain text of every cell first, so the columns can be sized from it.
        texts = [[] for _ in columns]
        for row in rows:
            for c_idx, (key, _, ctype) in enumerate(columns):
                texts[c_idx].append(fmt_cell(row.get(key), "str" if ctype.startswith("badge:") else ctype))
        totals = report.get("totals") or {}
        if totals:
            for c_idx, (key, _, ctype) in enumerate(columns):
                if c_idx == 0:
                    texts[c_idx].append("Total")
                elif key in totals:
                    texts[c_idx].append(fmt_cell(totals[key], ctype))

        data = [[_p(heads[i], s["th_num"] if columns[i][2] in NUM_TYPES else s["th"])
                 for i in range(len(columns))]]
        for row in rows:
            cells = []
            for key, _, ctype in columns:
                value = row.get(key)
                if ctype.startswith("badge:"):
                    # A status reads as coloured bold text, not a filled chip: lighter on paper and
                    # on a black-and-white printer it is still just bold text.
                    badge = badge_colors(ctype.split(":", 1)[1], value)
                    style = s["td"]
                    if badge:
                        style = ParagraphStyle("status", parent=s["td"], fontName="IBMPlexSans-Bold",
                                               textColor=colors.HexColor(badge[1]))
                    cells.append(_p(fmt_cell(value, "str"), style))
                else:
                    cells.append(_p(fmt_cell(value, ctype), s["td_num"] if ctype in NUM_TYPES else s["td"]))
            data.append(cells)

        total_row_idx = None
        if totals:
            bold = ParagraphStyle("total", parent=s["td"], fontName="IBMPlexSans-Bold")
            bold_num = ParagraphStyle("total_num", parent=s["td_num"], fontName="IBMPlexSans-Bold")
            total_row = []
            for c_idx, (key, _, ctype) in enumerate(columns):
                if c_idx == 0:
                    total_row.append(_p("Total", bold))
                elif key in totals:
                    total_row.append(_p(fmt_cell(totals[key], ctype), bold_num))
                else:
                    total_row.append(Paragraph("", s["td"]))
            data.append(total_row)
            total_row_idx = len(data) - 1

        col_widths = _column_widths(columns, texts, heads, doc.width)
        table = Table(data, colWidths=col_widths, repeatRows=1)
        style = [
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), CELL_PAD),
            ("RIGHTPADDING", (0, 0), (-1, -1), CELL_PAD),
            # The first and last column sit on the page margin, not inside a box.
            ("LEFTPADDING", (0, 0), (0, -1), 0),
            ("RIGHTPADDING", (-1, 0), (-1, -1), 0),
            ("LINEBELOW", (0, 0), (-1, 0), 0.9, INK),           # one dark rule under the heads
            ("LINEBELOW", (0, 1), (-1, -1), 0.4, BORDER),       # hairlines between rows
        ]
        if total_row_idx is not None:
            style.append(("LINEABOVE", (0, total_row_idx), (-1, total_row_idx), 0.9, INK))
            style.append(("LINEBELOW", (0, total_row_idx), (-1, total_row_idx), 0, colors.white))
        table.setStyle(TableStyle(style))
        story.append(table)

        story.append(Spacer(1, 8))
        count_note = f"{report['row_count']:,} row{'s' if report['row_count'] != 1 else ''}."
        if report["truncated"]:
            count_note += f" Results were capped at {MAX_ROWS:,} rows. Narrow the filters for a complete report."
        story.append(Paragraph(count_note, s["footer"]))

    doc.build(story, canvasmaker=NumberedCanvas)
    buf.seek(0)
    return buf
