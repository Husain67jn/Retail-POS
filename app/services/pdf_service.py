"""POS thermal receipt PDF generation — PyQt5 (QTextDocument + QPrinter).

This module renders the compact 80mm thermal-roll receipt printed from the
POS ("Print Receipt" / "Generate Receipt PDF"). It exposes a small
``InvoiceOutputService`` (``generate`` / ``generate_and_print`` /
``default_path``) plus ``InvoiceOutputError`` / ``PrintFallbackToPreview`` —
exactly the interface ``app/ui/pos_page.py`` imports from here.

It is deliberately SEPARATE from ``app/services/invoice_output_service.py``,
a different, reportlab-based pipeline that produces the full-page A4/A5/180mm
invoices reprinted from the Invoices page. The two coexist on purpose: this
one measures and auto-sizes a single continuous 80mm slip to the bill's own
content (see below), while the other lays out paginated, fixed-size
documents. Which page uses which service is wired in
``app/ui/main_window.py`` — the POS page is pointed at this module there.

Expected Invoice/Settings shape (adjust the ``_field`` calls below if yours
differs — every read goes through ``_field`` / ``getattr(..., default)`` for
exactly this reason, so most real invoice objects should work with only the
attribute-name list changed, not the layout logic):
    invoice.invoice_number   -> str/int
    invoice.date / created_at -> str (already formatted) or datetime
    invoice.customer_name    -> str | None
    invoice.payment_type     -> "cash" | "credit"
    invoice.items            -> iterable of objects with:
                                   product_name, quantity, unit_price, line_total
    invoice.subtotal         -> Decimal/float (optional — recomputed if absent)
    invoice.discount_total   -> Decimal/float (optional)
    invoice.total            -> Decimal/float (optional — recomputed if absent)
    settings.shop_name / .shop_address / .shop_phone / .currency_symbol -> str (all optional)

HOW THE SINGLE-PAGE, ZERO-BLANK-SPACE SIZING WORKS
===================================================
QTextDocument lays out HTML at a *fixed width* but a naturally-growing
*height* — there is no "auto height" CSS property, so the height Qt would
give a bill on a generic tall page is whatever the content needs. This file
measures that directly instead of estimating line heights by hand:

  1. A bare QTextDocument (no printer attached) is given the receipt HTML and
     a fixed text width equal to the *printable* width — the 80mm paper
     width minus both left/right margins — converted to pixels using the
     conventional 96-px-per-inch mapping QTextDocument uses by default when
     it isn't bound to a specific paint device.
  2. ``document.documentLayout().documentSize().height()`` is read back —
     the exact height, in that same px convention, the content occupies at
     that width and font — and converted back to millimetres.
  3. The REAL page height is that measured content height *plus* the
     top+bottom margins (so the margins are genuine extra whitespace around
     the content, not eaten out of it) plus a small rounding buffer — not a
     fixed A4/roll length — so there is, by construction, no page 2 to fall
     onto and no meaningful blank space below the footer.
"""

from __future__ import annotations

import tempfile
from datetime import datetime
from decimal import Decimal
from html import escape
from pathlib import Path

from PyQt5.QtCore import QSizeF
from PyQt5.QtGui import QPageSize, QPainter, QTextDocument
from PyQt5.QtPrintSupport import QPrintDialog, QPrinter
from PyQt5.QtWidgets import QApplication

# ---------------------------------------------------------------------------
# Requirement — fixed 80mm (3") thermal roll width; height is measured, not
# hardcoded (see module docstring).
# ---------------------------------------------------------------------------
PAPER_WIDTH_MM = 80.0

# Requirement — page margins of 2mm on every side (within the requested
# 5-10px range: at a typical 96dpi screen-pixel convention, 2mm ≈ 7.6px).
PAGE_MARGIN_MM = 2.0

# Requirement — the printed header must start flush at the very top edge of
# the thermal roll (no leading blank strip), so the TOP margin is zero while
# the left/right margins stay at PAGE_MARGIN_MM to keep the printable width
# correct, and the bottom stays at PAGE_MARGIN_MM only so the final footer
# line is never clipped by the cutter.
TOP_MARGIN_MM = 0.0

# QTextDocument's conventional px-per-mm when it isn't bound to a specific
# paint device: 96 px/inch (the standard CSS/screen-DPI assumption Qt's text
# layout falls back to) / 25.4mm per inch. Both the measuring pass and the
# final render use this SAME constant throughout, which is what keeps the
# two consistent with each other regardless of what DPI the actual printer
# ends up using — the printer is told the exact physical page size in
# millimetres directly (see build_receipt_pdf), so it never needs to agree
# with this constant, only this file's own two passes need to agree with
# each other.
PX_PER_MM = 96 / 25.4

# A generous placeholder height used only for the first, measuring pass
# (see module docstring) — large enough for a very long bill; the real
# output page is then sized to the exact measured content height, not this.
_MEASURE_HEIGHT_MM = 2000.0

# Small safety margin added to the measured content height, covering
# sub-pixel rounding differences between the measuring pass and the render
# pass, without meaningfully re-introducing the "extra blank paper" this file
# exists to remove. Kept far smaller than a flat "+10mm" pad would be.
_HEIGHT_SAFETY_BUFFER_MM = 1.5

_FONT_FAMILY = "Consolas, 'Courier New', monospace"


def _field(obj, name: str, default=None):
    """Defensive attribute lookup — see the module docstring: real Invoice /
    InvoiceItem objects may name these fields slightly differently, so every
    read goes through here instead of a bare attribute access that would
    crash on the first mismatch.
    """
    return getattr(obj, name, default)


def _money(value, currency_symbol: str = "") -> str:
    if value is None:
        value = Decimal("0")
    if not isinstance(value, Decimal):
        try:
            value = Decimal(str(value))
        except Exception:
            value = Decimal("0")
    text = f"{value:,.2f}"
    return f"{currency_symbol} {text}" if currency_symbol else text


def _as_decimal(value):
    """Coerce to Decimal, or None when the value is absent or unparseable.
    Unlike ``_money`` (which folds ``None`` into ``0.00``), this preserves the
    distinction between "this invoice never set the field" and a real zero, so
    the Paid / Remaining / payment-status block is skipped entirely for a bare
    invoice-like object that has no such fields, while a genuine fully-paid
    sale (remaining == 0) still renders it.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _quantity(value) -> str:
    if value is None:
        return "0"
    if isinstance(value, Decimal):
        return str(value.normalize())
    return str(value)


def _ensure_qapplication() -> None:
    """QTextDocument/QPrinter need a QApplication instance to exist. The
    running POS app already has one; this only creates a throwaway instance
    when the function is called standalone (e.g. a script or test) without
    one, so the module doesn't crash outside the full application.
    """
    if QApplication.instance() is None:
        QApplication([])


def _build_rows_html(items, currency_symbol: str = "") -> tuple[str, Decimal, Decimal]:
    """Returns (html_row_markup, gross_subtotal, item_discount_total). No
    Unit column anywhere — the row markup is exactly
    [#, Item Description, Qty, Price, Total].

    ``gross_subtotal`` is the sum of qty*unit_price *before* any per-item
    discount (matching invoice_output_service.py's ``_gross_subtotal``), and
    ``item_discount_total`` is the sum of each item's own discount — both
    needed by _build_html to print the Subtotal / Item discount / Bill
    discount breakdown instead of one lump "Discount" line.
    """
    parts = []
    gross_subtotal = Decimal("0")
    item_discount_total = Decimal("0")
    for idx, item in enumerate(items, start=1):
        name = str(_field(item, "product_name", _field(item, "name", "")))
        qty = _field(item, "quantity", 0)
        unit_price = _field(item, "unit_price", 0)
        line_total = _field(item, "line_total", None)
        if line_total is None:
            try:
                line_total = Decimal(str(qty)) * Decimal(str(unit_price))
            except Exception:
                line_total = Decimal("0")
        if not isinstance(line_total, Decimal):
            try:
                line_total = Decimal(str(line_total))
            except Exception:
                line_total = Decimal("0")
        try:
            gross_subtotal += Decimal(str(qty)) * Decimal(str(unit_price))
        except Exception:
            gross_subtotal += line_total
        try:
            item_discount_total += Decimal(str(_field(item, "item_discount", 0) or 0))
        except Exception:
            pass
        # Price column shows the EFFECTIVE per-unit rate actually charged
        # (line_total / qty). When a per-item discount was applied this is the
        # discounted rate, not the original list price — so the printed
        # Price × Qty always reconciles to the Total on the same row.
        try:
            qty_dec = Decimal(str(qty))
            effective_price = (line_total / qty_dec) if qty_dec else Decimal(str(unit_price))
        except Exception:
            effective_price = line_total
        parts.append(
            "<tr>"
            f'<td class="col-idx">{idx}</td>'
            f'<td class="col-desc">{escape(name)}</td>'
            f'<td class="col-num">{escape(_quantity(qty))}</td>'
            f'<td class="col-num">{escape(_money(effective_price, currency_symbol))}</td>'
            f'<td class="col-num">{escape(_money(line_total, currency_symbol))}</td>'
            "</tr>"
        )
    return "".join(parts), gross_subtotal, item_discount_total


def _build_html(invoice, settings) -> str:
    """Requirement #1 — table header/rows are exactly
    [#, Item Description, Qty, Price, Total]; no Unit column exists anywhere
    in this markup.
    Requirement #4 — compact padding, dashed dividers, left-aligned
    description / right-aligned numeric columns, a visually distinct bold
    Grand Total block, and a single footer line.
    """
    business_name = _field(settings, "business_name", _field(settings, "shop_name", ""))
    # Bug fix: the real Settings model names these ``shop_address`` /
    # ``shop_phone`` (see invoice_output_service.py's InvoiceOutputService,
    # which reads the exact same settings object) — this was reading
    # ``address`` / ``phone`` first, which don't exist on that model, so
    # _field's getattr silently fell through to "" and the receipt printed
    # with no address or phone line at all. shop_* is checked first now;
    # the old names are kept only as a fallback for any other settings-like
    # object that happens to use them instead.
    address = _field(settings, "shop_address", _field(settings, "address", ""))
    phone = _field(settings, "shop_phone", _field(settings, "phone", _field(settings, "phone_number", "")))
    # Same bug, same fix: this was never reading a currency symbol at all,
    # so every amount printed as a bare number with no "Rs." (or whatever
    # the shop's configured symbol is) — unlike invoice_output_service.py's
    # ``_currency()``, which always includes it.
    currency_symbol = str(_field(settings, "currency_symbol", "") or "")

    items = list(_field(invoice, "items", []) or [])
    rows_html, gross_subtotal, item_discount_total = _build_rows_html(items, currency_symbol)

    invoice_number = _field(invoice, "invoice_number", _field(invoice, "id", ""))
    raw_date = _field(invoice, "date", _field(invoice, "created_at", None))
    date_text = raw_date.strftime("%d %b %Y %I:%M %p") if isinstance(raw_date, datetime) else str(raw_date or "")
    customer_name = _field(invoice, "customer_name")
    # Keep the raw (lowercase) payment type for the PAID / UDHAAR / UNPAID
    # logic below; the title-cased form is only for the meta table display.
    payment_type_raw = str(_field(invoice, "payment_type", "cash") or "cash").lower()
    payment_type = payment_type_raw.title()
    # Optional retail fields — every one of these is read defensively via
    # _field, exactly like paid_amount/remaining_amount below, so an
    # Invoice-like object that never set them (older callers, tests) still
    # renders a valid receipt with that row simply omitted, rather than
    # crashing on a missing attribute.
    cashier_name = _field(invoice, "cashier_name", _field(invoice, "staff_name", _field(invoice, "served_by", None)))

    subtotal = _field(invoice, "subtotal", gross_subtotal)
    if not isinstance(subtotal, Decimal):
        try:
            subtotal = Decimal(str(subtotal))
        except Exception:
            subtotal = gross_subtotal
    discount_total = _as_decimal(_field(invoice, "discount_total", None))
    # Bill discount is whatever part of the total discount isn't already
    # accounted for by the sum of each item's own discount — matching
    # invoice_output_service.py's ``_bill_discount``. Falls back to the
    # item-discount-only figure when the invoice has no separate
    # discount_total field at all.
    bill_discount = (discount_total if discount_total is not None else item_discount_total) - item_discount_total
    # The real Invoice model names this ``grand_total``; ``total`` is only a
    # fallback for other invoice-like shapes. Read both before recomputing, so
    # the stored total is used rather than silently re-derived from subtotal.
    grand_total = _field(invoice, "total", _field(invoice, "grand_total", None))
    if grand_total is None:
        grand_total = subtotal - (discount_total if discount_total is not None else Decimal("0"))
    if not isinstance(grand_total, Decimal):
        try:
            grand_total = Decimal(str(grand_total))
        except Exception:
            grand_total = subtotal

    # Paid / Remaining + a PAID/UDHAAR/UNPAID badge — essential on a credit
    # (udhaar) receipt so the customer's outstanding balance is on the slip,
    # not just the grand total. Read defensively: a bare invoice-like object
    # that never set these fields omits the whole block (see _as_decimal).
    paid_amount = _as_decimal(_field(invoice, "paid_amount", None))
    remaining_amount = _as_decimal(_field(invoice, "remaining_amount", None))
    tax_total = _as_decimal(_field(invoice, "tax_amount", _field(invoice, "tax_total", None)))
    amount_tendered = _as_decimal(_field(invoice, "amount_tendered", _field(invoice, "tendered_amount", None)))
    # change_due is read first, then derived from tendered - grand_total when
    # the invoice tracks tendered cash but not the change itself — never
    # derived (or shown) for a negative result, which would just mean the
    # tendered amount didn't cover the bill and belongs on the Remaining row
    # instead.
    change_due = _as_decimal(_field(invoice, "change_due", None))
    if change_due is None and amount_tendered is not None:
        derived_change = amount_tendered - grand_total
        change_due = derived_change if derived_change >= 0 else None
    terms_text = _field(settings, "receipt_terms", _field(settings, "return_policy", None))

    header_lines = []
    if business_name:
        header_lines.append(f'<div class="store-name">{escape(str(business_name))}</div>')
    # Address and phone share one compact line when both are present, rather
    # than each claiming a full line of their own — Requirement #4's "compact
    # address/phone layout".
    contact_bits = [escape(str(v)) for v in (address, phone) if v]
    if contact_bits:
        header_lines.append(f'<div class="store-contact">{" &middot; ".join(contact_bits)}</div>')

    meta_rows = [("Invoice #", str(invoice_number)), ("Date", date_text)]
    if customer_name:
        meta_rows.append(("Customer", str(customer_name)))
    meta_rows.append(("Payment", payment_type))
    if cashier_name:
        meta_rows.append(("Cashier", str(cashier_name)))
    meta_html = "".join(
        f'<tr><td class="meta-label">{escape(label)}</td><td class="meta-value">{escape(value)}</td></tr>'
        for label, value in meta_rows
    )

    totals_rows = [("Subtotal", _money(subtotal, currency_symbol))]
    # Item discount / Bill discount breakdown — matching
    # invoice_output_service.py's professional layout (always shown, even at
    # Rs. 0.00) rather than one lump "Discount" line that only appeared when
    # non-zero.
    totals_rows.append(("Item discount", _money(item_discount_total, currency_symbol)))
    totals_rows.append(("Bill discount", _money(bill_discount, currency_symbol)))
    if tax_total is not None and tax_total > 0:
        totals_rows.append(("Tax", _money(tax_total, currency_symbol)))
    totals_html = "".join(
        f'<tr><td class="totals-label">{escape(label)}</td><td class="totals-value">{escape(value)}</td></tr>'
        for label, value in totals_rows
    )

    # Paid / Remaining rows — shown only when the invoice actually carries
    # those fields — reusing the same two-column totals styling. Placed after
    # the grand total in the body below (money received against the bill, and
    # what is still owed), which is where a customer expects to read them.
    payment_rows = []
    if paid_amount is not None:
        payment_rows.append(("Paid", _money(paid_amount, currency_symbol)))
    if remaining_amount is not None:
        payment_rows.append(("Remaining", _money(remaining_amount, currency_symbol)))
    payment_html = "".join(
        f'<tr><td class="totals-label">{escape(label)}</td><td class="totals-value">{escape(value)}</td></tr>'
        for label, value in payment_rows
    )
    payment_block = f'<table class="totals payment">{payment_html}</table>' if payment_rows else ""

    # Amount Tendered / Change Due — cash-drawer detail, shown only when the
    # invoice actually carries a tendered amount (most invoice-like test
    # objects won't, so this block is entirely optional, same as paid/
    # remaining above).
    tendered_rows = []
    if amount_tendered is not None:
        tendered_rows.append(("Tendered", _money(amount_tendered, currency_symbol)))
    if change_due is not None:
        tendered_rows.append(("Change Due", _money(change_due, currency_symbol)))
    tendered_html = "".join(
        f'<tr><td class="totals-label">{escape(label)}</td><td class="totals-value">{escape(value)}</td></tr>'
        for label, value in tendered_rows
    )
    tendered_block = f'<table class="totals payment">{tendered_html}</table>' if tendered_rows else ""

    # PAID / UDHAAR / UNPAID badge, mirroring the reportlab invoice's
    # _payment_status: nothing left to pay reads PAID; an unsettled credit
    # sale reads UDHAAR; any other unsettled bill, UNPAID. Monochrome (bold
    # text in a dashed box) since thermal printers don't reproduce colour —
    # the word itself carries the meaning, not a tint.
    status_block = ""
    if remaining_amount is not None:
        if remaining_amount <= 0:
            status_text = "PAID"
        elif payment_type_raw == "credit":
            status_text = "UDHAAR"
        else:
            status_text = "UNPAID"
        status_block = f'<div class="status">{escape(status_text)}</div>'

    # Requirement — dashed dividers use border-bottom: 1px dashed #000, per
    # spec. Table cell padding is 3px 0px (Requirement #4). Requirement #3
    # (single continuous page): no page-break CSS of any kind is used
    # anywhere below, and the actual page-splitting prevention happens at
    # the QPrinter/QTextDocument sizing layer in build_receipt_pdf, which is
    # what actually determines pagination — CSS alone cannot guarantee it.
    return f"""
    <html>
    <head>
    <style>
        body {{
            font-family: {_FONT_FAMILY};
            font-size: 9pt;
            color: #000000;
            margin: 0;
            padding: 0;
        }}
        .store-name {{
            text-align: center;
            font-size: 13pt;
            font-weight: bold;
            margin-bottom: 2px;
        }}
        .store-contact {{
            text-align: center;
            font-size: 8pt;
            margin-bottom: 6px;
        }}
        table.meta {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 4px;
        }}
        table.meta td {{
            padding: 1px 0px;
            font-size: 9pt;
        }}
        .meta-value {{ text-align: right; }}
        hr.dashed {{
            border: none;
            border-bottom: 1px dashed #000;
            margin: 3px 0;
        }}
        table.items {{
            width: 100%;
            border-collapse: collapse;
        }}
        table.items th {{
            font-size: 9pt;
            font-weight: bold;
            padding: 3px 0px;
            border-bottom: 1px dashed #000;
        }}
        table.items td {{
            font-size: 9pt;
            padding: 3px 0px;
        }}
        .col-idx {{ text-align: left; width: 6%; }}
        .col-desc {{
            text-align: left;
            width: 43%;
            word-wrap: break-word;
            overflow-wrap: break-word;
        }}
        .col-num {{ text-align: right; width: 17%; }}
        table.totals {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 4px;
        }}
        table.totals td {{
            padding: 2px 0px;
            font-size: 9pt;
        }}
        .totals-value {{ text-align: right; }}
        table.grand-total {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 3px;
            border-top: 1px dashed #000;
        }}
        table.grand-total td {{
            padding: 4px 0px;
            font-size: 12pt;
            font-weight: bold;
        }}
        .grand-total-label {{
            width: 60%;
            white-space: nowrap;
        }}
        .grand-total-value {{ text-align: right; }}
        .status {{
            text-align: center;
            font-size: 10pt;
            font-weight: bold;
            letter-spacing: 1px;
            margin-top: 5px;
            padding: 3px 0;
            border: 1px dashed #000;
        }}
        .footer {{
            text-align: center;
            font-size: 9pt;
            margin-top: 6px;
        }}
        .terms {{
            text-align: center;
            font-size: 7.5pt;
            color: #333333;
            margin-top: 3px;
        }}
    </style>
    </head>
    <body>
        {''.join(header_lines)}
        <hr class="dashed">
        <table class="meta">{meta_html}</table>
        <hr class="dashed">
        <table class="items">
            <thead>
                <tr>
                    <th class="col-idx">#</th>
                    <th class="col-desc">Item Description</th>
                    <th class="col-num">Qty</th>
                    <th class="col-num">Price</th>
                    <th class="col-num">Total</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
        <table class="totals">{totals_html}</table>
        <table class="grand-total">
            <tr>
                <td class="grand-total-label">GRAND TOTAL</td>
                <td class="grand-total-value">{escape(_money(grand_total, currency_symbol))}</td>
            </tr>
        </table>
        {payment_block}
        {tendered_block}
        {status_block}
        <hr class="dashed">
        <div class="footer">Thank you for your business!</div>
        {f'<div class="terms">{escape(str(terms_text))}</div>' if terms_text else ''}
    </body>
    </html>
    """


def _receipt_layout(invoice, settings, paper_width_mm: float = PAPER_WIDTH_MM):
    """Measure the receipt once and return everything both the PDF-export and
    the native-print paths need to render it identically:
    ``(html, text_width_px, rendered_height_mm)``.

    See the module docstring for how the measure-then-render approach works.
    Factored out of build_receipt_pdf so printing straight to a QPrinter (see
    InvoiceOutputService.generate_and_print) reuses the exact same layout maths
    — the on-screen print and the saved PDF are then guaranteed identical.
    """
    _ensure_qapplication()
    html = _build_html(invoice, settings)

    # The document is laid out at the PRINTABLE width — paper width minus
    # both left/right margins — not the full 80mm paper width. Handing the
    # full paper width to setTextWidth here while separately telling the
    # printer to inset 2mm margins on each side would make Qt's own
    # word-wrapping think it has ~4mm more horizontal room than the printer
    # will actually give it, risking the rightmost column being pushed
    # outside the printable area or wrapping differently than what
    # ultimately gets painted.
    printable_width_mm = paper_width_mm - (2 * PAGE_MARGIN_MM)
    text_width_px = printable_width_mm * PX_PER_MM

    # ---- Measure the exact content height at the printable width ----
    measuring_document = QTextDocument()
    measuring_document.setDocumentMargin(0)
    measuring_document.setHtml(html)
    measuring_document.setTextWidth(text_width_px)
    content_height_px = measuring_document.documentLayout().documentSize().height()
    content_height_mm = content_height_px / PX_PER_MM

    # The final PAGE height needs the top+bottom margins added back on top
    # of the measured content height (the content was measured at the
    # printable width/height, which is margin-EXCLUSIVE), plus a small
    # rounding buffer — not a flat, visually-obvious pad, since Requirement
    # #3 explicitly asks for zero extra blank space at the bottom. The top
    # margin is zero (header flush to the paper edge), so only the bottom
    # margin contributes vertical padding here.
    rendered_height_mm = content_height_mm + TOP_MARGIN_MM + PAGE_MARGIN_MM + _HEIGHT_SAFETY_BUFFER_MM
    return html, text_width_px, rendered_height_mm


def _apply_receipt_page(printer, paper_width_mm: float, rendered_height_mm: float) -> None:
    """Set ``printer``'s page size to the exact measured receipt geometry.

    Kept separate because a native print DIALOG can reset a printer's page
    geometry to the chosen device's own default when the user picks a printer,
    so generate_and_print re-applies this after the dialog closes, right before
    painting, to guarantee the thermal page size actually sticks.
    """
    # setFullPage(False) (the default) is deliberate: it makes Qt map a
    # QPainter's (0, 0) to just inside the margins set below automatically,
    # so the content lands inside them with no manual offset math needed.
    printer.setPageSize(QPageSize(QSizeF(paper_width_mm, rendered_height_mm), QPageSize.Unit.Millimeter))
    printer.setPageMargins(
        PAGE_MARGIN_MM, TOP_MARGIN_MM, PAGE_MARGIN_MM, PAGE_MARGIN_MM, QPrinter.Millimeter
    )


def _paint_receipt(printer, html: str, text_width_px: float) -> None:
    """Paint the receipt document onto ``printer`` in a single continuous pass.

    Requirement #3 — single continuous page: drawContents (rather than
    QTextDocument.print_, which paginates across the printer's own page rects)
    paints the whole laid-out document in one pass onto one page. Since
    _receipt_layout already sized the page to be exactly tall enough for that
    same content, there is no second page for anything to land on.
    """
    render_document = QTextDocument()
    render_document.setDocumentMargin(0)
    render_document.setHtml(html)
    render_document.setTextWidth(text_width_px)

    painter = QPainter(printer)
    render_document.drawContents(painter)
    painter.end()


def build_receipt_pdf(invoice, settings, output_path: str, paper_width_mm: float = PAPER_WIDTH_MM) -> str:
    """Render ``invoice`` as an 80mm thermal receipt PDF at ``output_path``,
    sized to exactly one continuous page with (as close to) zero trailing
    blank space as a font-metrics measurement can guarantee.

    See the module docstring for how the measure-then-render approach works.
    Uses ``QPageSize`` (the modern Qt page-geometry API) rather than
    ``QPrinter.setPaperSize`` directly, per this turn's request.
    """
    html, text_width_px, rendered_height_mm = _receipt_layout(invoice, settings, paper_width_mm)

    # QPrinter.ScreenResolution (not HighResolution) is required here, not a
    # quality tradeoff. QTextDocument lays out HTML in device-independent
    # pixels on the assumption that 1px == 1/96 inch (the same assumption
    # PX_PER_MM above is built on) — it has no idea what physical printer
    # it will eventually be painted onto. drawContents() then paints the
    # document's pixel coordinates straight into the printer's native
    # device units with no rescaling in between. HighResolution mode gives
    # the printer a DPI far above 96 (commonly 1200), so the very same
    # "283 px wide" receipt that measured correctly at 80mm in
    # _receipt_layout gets painted at 283 raw device units — at 1200 DPI
    # that's under a quarter of an inch, i.e. the "tiny content in the
    # top-left corner of an otherwise blank page" bug. ScreenResolution
    # sets the printer's logical DPI to ~96, matching PX_PER_MM exactly, so
    # document pixels map 1:1 onto the page instead of collapsing into one
    # corner of it.
    printer = QPrinter(QPrinter.PrinterMode.ScreenResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(output_path))
    _apply_receipt_page(printer, paper_width_mm, rendered_height_mm)
    _paint_receipt(printer, html, text_width_px)

    return str(output_path)


# ---------------------------------------------------------------------------
# The POS page delegates all receipt PDF generation and printing to this
# module. This class is the small 3-method interface (generate /
# generate_and_print / default_path) plus the two exception types that
# app/ui/pos_page.py imports from here. build_receipt_pdf above remains the
# only place that builds the receipt's HTML or lays out its PDF — this class
# is purely about WHERE the PDF goes and HOW it reaches paper, never WHAT it
# looks like.
# ---------------------------------------------------------------------------
class InvoiceOutputError(Exception):
    """Raised when a receipt PDF could not be generated or printed."""


class PrintFallbackToPreview(InvoiceOutputError):
    """Raised (informationally, not as a failure) when the PDF generated
    fine but this OS/environment has no registered "print" command to send
    it straight to a printer — the PDF is opened for the person to print
    manually instead.
    """


class InvoiceOutputService:
    """Thin orchestration around build_receipt_pdf: decides where the PDF
    file goes and how it reaches paper (or the screen, as a fallback).
    """

    def default_path(self, invoice, size: str = "80mm") -> Path:
        number = _field(invoice, "invoice_number", _field(invoice, "id", "receipt"))
        directory = Path(tempfile.gettempdir()) / "pos_receipts"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"Receipt-{number}.pdf"

    def generate(self, invoice, settings, path, size: str = "80mm") -> str:
        """``size`` is accepted for interface compatibility with the old
        caller, but paper WIDTH is always the standard 80mm thermal roll —
        there is no longer a fixed page LENGTH preset to choose between,
        since build_receipt_pdf now always computes the height from the
        bill's own content.
        """
        try:
            return build_receipt_pdf(invoice, settings, str(path), paper_width_mm=PAPER_WIDTH_MM)
        except InvoiceOutputError:
            raise
        except Exception as exc:  # pragma: no cover - defensive wrapper
            raise InvoiceOutputError(f"Could not generate receipt PDF: {exc}") from exc

    def generate_and_print(self, invoice, settings, size: str = "80mm") -> bool:
        """Print the receipt straight to a printer through Qt's own print
        stack — never a web browser or the OS "open file" shell.

        Req 2 — the old implementation handed the generated PDF to
        ``os.startfile(path, "print")`` / ``QDesktopServices.openUrl``, which
        on many machines routes a PDF to whatever app owns the file
        association (frequently a web browser) instead of a printer. This now
        renders the very same measured receipt document onto a ``QPrinter``
        selected in a native ``QPrintDialog`` and paints it directly, so the
        output goes to real paper with no external viewer in the loop.

        Also writes the PDF to ``default_path`` (unchanged) so a copy of the
        exact receipt still exists on disk. Returns ``True`` when the user
        confirmed the print dialog, ``False`` when they cancelled it.
        """
        # Keep the on-disk PDF copy (same location/behaviour as before) so the
        # receipt is archived even when printed directly.
        path = self.default_path(invoice, size)
        self.generate(invoice, settings, path, size)

        html, text_width_px, rendered_height_mm = _receipt_layout(
            invoice, settings, paper_width_mm=PAPER_WIDTH_MM
        )
        # Same ScreenResolution requirement as build_receipt_pdf above — a
        # physical printer selected in the dialog still gets driven at 96
        # logical DPI so the document's own pixel coordinates land correctly
        # on the page instead of shrinking into one corner of it.
        printer = QPrinter(QPrinter.PrinterMode.ScreenResolution)
        _apply_receipt_page(printer, PAPER_WIDTH_MM, rendered_height_mm)

        dialog = QPrintDialog(printer)
        dialog.setWindowTitle("Print Receipt")
        if dialog.exec_() != QPrintDialog.DialogCode.Accepted:
            # Cashier cancelled the printer picker — not an error; the PDF copy
            # is still on disk at ``path`` if they want it.
            return False
        # A printer picked in the dialog can carry its own default page size,
        # so re-assert the thermal geometry right before painting.
        _apply_receipt_page(printer, PAPER_WIDTH_MM, rendered_height_mm)
        _paint_receipt(printer, html, text_width_px)
        return True
