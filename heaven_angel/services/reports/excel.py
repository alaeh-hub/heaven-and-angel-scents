"""Excel (.xlsx) rendering of a report."""
import datetime
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from heaven_angel.services.reports.constants import MAX_ROWS
from heaven_angel.services.reports.formatting import badge_colors, sentence_case



# Brand gold, same hexes as the PDF palette above (ACCENT / ACCENT_SOFT /
# BORDER). Also used by the sales-import template (sales_import.py).
XL_HEADER_FILL = PatternFill("solid", fgColor="C9A227")


XL_TITLE_FILL = PatternFill("solid", fgColor="FBF1D6")


XL_BORDER = Border(bottom=Side(style="thin", color="E9E0C9"))


XL_MONEY_FMT = '"₱"#,##0.00'


XL_DATE_FMT = "mmm d, yyyy h:mm AM/PM"


# Up to 3 decimals, trailing zeros trimmed by Excel's own "#" placeholders
# (2.500 shows as "2.5", 10.000 shows as "10") — for fractional quantity
# columns (see the "num" column type), which range from whole numbers up
# to schema's DECIMAL(10,3)/DECIMAL(10,4) precision.
XL_NUM_FMT = "#,##0.###"


# ---------------------------------------------------------------- Excel rendering
# Kept for the Products export (routes/admin/products.py), which still uses the brand band.
XL_BAND_FILL = PatternFill("solid", fgColor="0C0B0A")   # matches the logo's own dark


XL_RULE_FILL = PatternFill("solid", fgColor="C9A227")


XL_ALT_FILL = PatternFill("solid", fgColor="F5F0E1")


_NUM_TYPES = ("money", "price", "int", "num")
_INK = "17140D"
_MUTED = "5B5445"
_HAIRLINE = Border(bottom=Side(style="hair", color="B8AF98"))


def render_report_excel(report):
    """Return a BytesIO .xlsx for a report dict built by get_report().

    The same plain document as the PDF: the title and what it covers on top, one table with sentence
    case heads over a single dark rule and hairlines between rows, no fills, and a bold Total row."""
    wb = Workbook()
    ws = wb.active
    ws.title = report["title"][:31] or "Report"
    ws.sheet_view.showGridLines = False
    wb.properties.creator = "Heaven & Angel Scents"
    wb.properties.title = f"{report['title']} report"

    # Some reports' columns carry a 4th PDF width weight; Excel sizes each column from its own content.
    columns = [c[:3] for c in report["columns"]]
    rows = report["rows"]

    generated = f"Generated {report['generated_at'].strftime('%b')} {report['generated_at'].day}, {report['generated_at'].year} " \
                f"{report['generated_at'].strftime('%I:%M %p').lstrip('0')}"
    if report["actor_label"]:
        generated += f" by {report['actor_label']}"
    ws.cell(row=1, column=1, value=f"{report['title']} report").font = Font(name="Calibri", size=15, bold=True, color=_INK)
    if report["subtitle"]:
        ws.cell(row=2, column=1, value=report["subtitle"]).font = Font(name="Calibri", size=10, color=_INK)
    ws.cell(row=3, column=1, value=generated).font = Font(name="Calibri", size=9, color=_MUTED)
    ws.row_dimensions[1].height = 24

    header_row_idx = 5
    if not rows:
        ws.cell(row=header_row_idx, column=1, value="No data matches the selected filters.").font = Font(
            name="Calibri", size=10, color=_MUTED)
        ws.column_dimensions["A"].width = 40
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf

    # ---- heads: sentence case, bold, one dark rule under them, aligned like their numbers ----
    col_widths = []
    for c, (_, label, ctype) in enumerate(columns, start=1):
        text = sentence_case(label)
        cell = ws.cell(row=header_row_idx, column=c, value=text)
        cell.font = Font(name="Calibri", size=10, bold=True, color=_INK)
        cell.border = Border(bottom=Side(style="medium", color=_INK))
        cell.alignment = Alignment(horizontal="right" if ctype in _NUM_TYPES else "left",
                                   vertical="center", wrap_text=True)
        col_widths.append(len(text))
    ws.row_dimensions[header_row_idx].height = 24

    # ---- data ----
    for r_off, row in enumerate(rows):
        r = header_row_idx + 1 + r_off
        for c, (key, _, ctype) in enumerate(columns, start=1):
            value = row.get(key)
            cell = ws.cell(row=r, column=c)
            cell.border = _HAIRLINE
            cell.font = Font(name="Calibri", size=10, color=_INK)
            cell.alignment = Alignment(vertical="center")
            if value is None:
                cell.value = "—"
            elif ctype == "money":
                cell.value = float(value)
                cell.number_format = XL_MONEY_FMT
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif ctype == "price":
                cell.value = float(value)
                cell.number_format = '"₱"#,##0.0000'
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif ctype == "int":
                cell.value = int(value)
                cell.number_format = "#,##0"
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif ctype == "num":
                # A fractional quantity: a real number with a number format, not int()'d,
                # so e.g. 2.5 grams isn't silently truncated.
                cell.value = float(value)
                cell.number_format = XL_NUM_FMT
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif ctype == "datetime" and isinstance(value, (datetime.date, datetime.datetime)):
                cell.value = value
                cell.number_format = XL_DATE_FMT if isinstance(value, datetime.datetime) else "mmm d, yyyy"
                cell.alignment = Alignment(horizontal="left", vertical="center")
            elif ctype.startswith("badge:"):
                cell.value = str(value)
                # A status is coloured bold text (the same colour as its badge on screen), not a filled chip.
                badge = badge_colors(ctype.split(":", 1)[1], value)
                if badge:
                    cell.font = Font(name="Calibri", size=10, bold=True, color=badge[1].lstrip("#"))
            else:
                cell.value = str(value)
            width = len(str(cell.value)) if cell.value is not None else 0
            if ctype == "datetime":
                width = 20
            elif ctype in ("money", "price"):
                width += 2
            if width > col_widths[c - 1]:
                col_widths[c - 1] = width

    last_col_letter = get_column_letter(len(columns))
    ws.auto_filter.ref = f"A{header_row_idx}:{last_col_letter}{header_row_idx + len(rows)}"
    ws.freeze_panes = f"A{header_row_idx + 1}"

    # ---- totals: bold, one dark rule above, outside the filter range since it isn't a data row ----
    totals = report.get("totals")
    total_row_idx = None
    if totals:
        total_row_idx = header_row_idx + 1 + len(rows)
        for c, (key, _, ctype) in enumerate(columns, start=1):
            cell = ws.cell(row=total_row_idx, column=c)
            cell.border = Border(top=Side(style="medium", color=_INK))
            cell.font = Font(name="Calibri", size=10, bold=True, color=_INK)
            if c == 1:
                cell.value = "Total"
            elif key in totals:
                if ctype == "money":
                    cell.value = float(totals[key])
                    cell.number_format = XL_MONEY_FMT
                elif ctype == "num":
                    cell.value = float(totals[key])
                    cell.number_format = XL_NUM_FMT
                else:
                    cell.value = int(totals[key])
                    cell.number_format = "#,##0"
                cell.alignment = Alignment(horizontal="right")
        ws.row_dimensions[total_row_idx].height = 20

    for c, width in enumerate(col_widths, start=1):
        ws.column_dimensions[get_column_letter(c)].width = min(max(width + 3, 10), 46)

    footer_row = (total_row_idx or header_row_idx + len(rows)) + 2
    note = f"{report['row_count']:,} row{'s' if report['row_count'] != 1 else ''}."
    if report["truncated"]:
        note += f" Results were capped at {MAX_ROWS:,} rows. Narrow the filters for a complete report."
    ws.cell(row=footer_row, column=1, value=note).font = Font(name="Calibri", size=9, color=_MUTED)

    # Printing: landscape, one page wide, the heads repeat on every page, the brand in the page header.
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"{header_row_idx}:{header_row_idx}"
    ws.oddHeader.right.text = "Heaven & Angel Scents"
    ws.oddHeader.right.size = 9
    ws.oddFooter.right.text = "Page &P of &N"
    ws.oddFooter.right.size = 9

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
