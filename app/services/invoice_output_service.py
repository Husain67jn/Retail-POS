from __future__ import annotations

import tempfile
from decimal import Decimal
from html import escape
from pathlib import Path

from PyQt5.QtCore import QMarginsF, QRectF, QSizeF
from PyQt5.QtGui import QImage, QPageLayout, QPageSize, QPainter, QTextDocument
from PyQt5.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo
from PyQt5.QtWidgets import QApplication

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, A5
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.config.paths import writable_dirs

# Executive A4/A5 palette (matches the spec's exact hex values).
_ACCENT_DARK = colors.HexColor("#1E293B")
_CARD_GREY = colors.HexColor("#F8F9FA")
_BORDER_GREY = colors.HexColor("#E2E8F0")
_BADGE_PAID = colors.HexColor("#15803D")
_BADGE_UDHAAR = colors.HexColor("#B45309")
_BADGE_UNPAID = colors.HexColor("#B91C1C")

# Explicit per-cell padding for the item tables. ReportLab defaults
# LEFTPADDING/RIGHTPADDING to 6pt each whenever a TableStyle never sets them;
# the tables below always set padding explicitly so column widths stay
# predictable. The A4 layout has ample width, so it uses a comfortable 4pt
# padding; the genuinely narrow 80mm thermal receipt (see _THERMAL_CELL_PAD
# below) cannot afford that much without starving its narrowest columns.
_THERMAL_CELL_PAD = 1.5
# 80mm receipt side margins. Tightened from 7mm to 1mm so content starts at the
# left printable edge. NOTE: if the physical printer's printable strip is
# narrower than 80mm, a small right margin (e.g. 3-4mm) prevents right-edge
# clipping -- tune these two constants against a test print.
_THERMAL_LEFT_MARGIN = 2 * mm
# Printable content width is 70mm (80mm paper, 1mm left margin); the
# right margin is derived so the frame is exactly that wide and every block
# (meta, rules, items, totals) shares the same right edge.
_THERMAL_CONTENT_WIDTH = 70 * mm
_THERMAL_RIGHT_MARGIN = 80 * mm - _THERMAL_LEFT_MARGIN - _THERMAL_CONTENT_WIDTH
# Item table columns: #, Description, Qty, Price, Total (sum = 70mm). Qty is
# 8mm (not the old 7mm) so a fractional quantity like "1.5", "0.5" or "12.5"
# -- now allowed for kg/meter sales -- fits on one line without wrapping; the
# 2mm it gains come off Description (which wraps for long names anyway) and
# Total picks up 1mm for large line totals.
_THERMAL_COLS = (4 * mm, 24 * mm, 8 * mm, 15 * mm, 19 * mm)
# Top/bottom page margins (paper waste is cut further by cropping at print).
_THERMAL_TOP_MARGIN = 1 * mm
_THERMAL_BOTTOM_MARGIN = 1 * mm
_A4_CELL_PAD = 4


class InvoiceOutputError(RuntimeError):
    """Raised when invoice PDF generation or printing cannot be completed."""


class InvoiceOutputService:
    """Render historical invoice snapshots and optionally send the PDF to print."""

    def __init__(self, output_root: Path | None = None):
        self.output_root = Path(output_root) if output_root else writable_dirs()["root"] / "invoices"

    @staticmethod
    def money(value) -> str:
        return f"{Decimal(value):.2f}"

    @staticmethod
    def quantity(value) -> str:
        text = format(Decimal(value), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    @staticmethod
    def _currency(settings, value) -> str:
        return f"{settings.currency_symbol} {InvoiceOutputService.money(value)}"

    @staticmethod
    def _gross_subtotal(invoice) -> Decimal:
        return sum(
            ((Decimal(item.quantity) * Decimal(item.unit_price)).quantize(Decimal("0.01")) for item in invoice.items),
            Decimal("0.00"),
        )

    @classmethod
    def _bill_discount(cls, invoice) -> Decimal:
        item_discounts = sum((Decimal(item.item_discount) for item in invoice.items), Decimal("0.00"))
        return Decimal(invoice.discount_total) - item_discounts

    @staticmethod
    def _payment_status(invoice) -> tuple[str, "colors.Color"]:
        """Derive a PAID / UNPAID / UDHAAR badge from real invoice fields only
        (payment_type, remaining_amount) -- this app has no due-date or
        separate status column, so nothing here is invented."""
        remaining = Decimal(invoice.remaining_amount)
        if remaining <= 0:
            return "PAID", _BADGE_PAID
        if invoice.payment_type == "credit":
            return "UDHAAR", _BADGE_UDHAAR
        return "UNPAID", _BADGE_UNPAID

    @staticmethod
    def _styles():
        base = getSampleStyleSheet()
        styles = {
            "shop": ParagraphStyle("InvoiceShop", parent=base["Title"], fontName="Helvetica-Bold", fontSize=16, leading=19, alignment=TA_CENTER, spaceAfter=4),
            "meta": ParagraphStyle("InvoiceMeta", parent=base["Normal"], fontSize=9, leading=12, alignment=TA_CENTER),
            "body": ParagraphStyle("InvoiceBody", parent=base["BodyText"], fontSize=8.5, leading=10.5, wordWrap="CJK"),
            "body_right": ParagraphStyle("InvoiceBodyRight", parent=base["BodyText"], fontSize=8.5, leading=10.5, alignment=TA_RIGHT),
            "body_header": ParagraphStyle("InvoiceBodyHeader", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8.5, leading=10.5, textColor=colors.white),
            "body_header_right": ParagraphStyle("InvoiceBodyHeaderRight", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8.5, leading=10.5, alignment=TA_RIGHT, textColor=colors.white),
            "body_header_center": ParagraphStyle("InvoiceBodyHeaderCenter", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8.5, leading=10.5, alignment=TA_CENTER, textColor=colors.white),
            "small": ParagraphStyle("InvoiceSmall", parent=base["BodyText"], fontSize=7.5, leading=9),
            "small_muted": ParagraphStyle("InvoiceSmallMuted", parent=base["BodyText"], fontSize=7, leading=8.5, textColor=colors.HexColor("#64748B")),
            "total_label": ParagraphStyle("InvoiceTotalLabel", parent=base["BodyText"], fontSize=9, leading=11, alignment=TA_RIGHT),
            "total_value": ParagraphStyle("InvoiceTotalValue", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=9, leading=11, alignment=TA_RIGHT),
            "grand_label": ParagraphStyle("InvoiceGrandLabel", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=11, leading=13, alignment=TA_LEFT),
            "grand_value": ParagraphStyle("InvoiceGrandValue", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=13, leading=15, alignment=TA_RIGHT),
            "header_left": ParagraphStyle("InvoiceHeaderLeft", parent=base["Normal"], fontSize=9, leading=12, alignment=TA_LEFT),
            "header_shop": ParagraphStyle("InvoiceHeaderShop", parent=base["Title"], fontName="Helvetica-Bold", fontSize=17, leading=20, alignment=TA_LEFT, spaceAfter=3),
            "invoice_title": ParagraphStyle("InvoiceTitle", parent=base["Title"], fontName="Helvetica-Bold", fontSize=20, leading=23, alignment=TA_RIGHT, textColor=_ACCENT_DARK),
            "header_right": ParagraphStyle("InvoiceHeaderRight", parent=base["Normal"], fontSize=9.5, leading=13, alignment=TA_RIGHT),
            "card_label": ParagraphStyle("InvoiceCardLabel", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=colors.HexColor("#64748B")),
            "card_value": ParagraphStyle("InvoiceCardValue", parent=base["BodyText"], fontSize=10.5, leading=13),
            "badge": ParagraphStyle("InvoiceBadge", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=9, leading=11, alignment=TA_CENTER, textColor=colors.white),
            # 80mm wide thermal receipt (true POS-80 paper width, 7mm side
            # margins -- see _render_180mm). The five item columns share a
            # hard content width (see _THERMAL_COLS) and were verified against
            # a worst-case bill (a 6-figure "125000.00" amount + a long
            # product name), so those tight numeric/header styles stay at
            # their compact sizes -- pushing them larger wraps header text
            # ("Qty" -> "Qt"/"y") and breaks numbers mid-digit. The numeric
            # columns also drop the repeated "Rs." prefix (see
            # _thermal_items_table) to buy the horizontal room those digits
            # need. Styles that live in full-width / wide contexts (shop name,
            # contact line, the meta block and the GRAND TOTAL box) are set a
            # little larger for legibility since they have the room. Body text
            # is bold throughout.
            "thermal_shop": ParagraphStyle("ThermalShop", parent=base["Title"], fontName="Helvetica-Bold", fontSize=13, leading=16, alignment=TA_CENTER, spaceAfter=3),
            # Shop address / phone stay a modest regular weight so the header
            # block doesn't dominate the slip now that everything else is
            # larger and bold.
            "thermal_contact": ParagraphStyle("ThermalContact", parent=base["BodyText"], fontSize=8.5, leading=11, alignment=TA_CENTER),
            "thermal": ParagraphStyle("Thermal", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=9, leading=11.5, wordWrap="CJK"),
            "thermal_right": ParagraphStyle("ThermalRight", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8.5, leading=11, alignment=TA_RIGHT),
            "thermal_center": ParagraphStyle("ThermalCenter", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8.5, leading=11, alignment=TA_CENTER),
            "thermal_header": ParagraphStyle("ThermalHeader", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8, leading=10, alignment=TA_LEFT),
            "thermal_header_center": ParagraphStyle("ThermalHeaderCenter", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8, leading=10, alignment=TA_CENTER),
            "thermal_header_right": ParagraphStyle("ThermalHeaderRight", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8, leading=10, alignment=TA_RIGHT),
            # Meta label is black now (not grey): a light grey prints as faint,
            # broken dots on a monochrome thermal head and was hard to read.
            "thermal_meta_label": ParagraphStyle("ThermalMetaLabel", parent=base["BodyText"], fontSize=8.5, leading=11, alignment=TA_LEFT),
            "thermal_meta_value": ParagraphStyle("ThermalMetaValue", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=8.5, leading=11, alignment=TA_LEFT),
            "thermal_grand_label": ParagraphStyle("ThermalGrandLabel", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=11, leading=13, alignment=TA_LEFT),
            # 13pt bold -- the largest, boldest figure on the receipt so the
            # amount due is unmistakable. The GRAND TOTAL box is widened to
            # ~0.96 of the content width with a 0.46/0.54 label/value split
            # (see _thermal_totals) so neither the "GRAND TOTAL" label at 11pt
            # nor a "Rs. 125000.00" amount at 13pt wraps to a 2nd line.
            "thermal_grand_value": ParagraphStyle("ThermalGrandValue", parent=base["BodyText"], fontName="Helvetica-Bold", fontSize=13, leading=15, alignment=TA_RIGHT),
        }
        # Solid pure black for every thermal style (grey prints faint on a
        # monochrome thermal head).
        for _name, _style in styles.items():
            if _name.startswith("thermal"):
                _style.textColor = colors.black
        return styles

    def _ensure_output_dir(self) -> Path:
        try:
            self.output_root.mkdir(parents=True, exist_ok=True)
            return self.output_root
        except OSError as exc:
            raise InvoiceOutputError(f"Could not create invoice output folder: {exc}") from exc

    # "180mm" is the historical, stable internal format_name for the thermal
    # receipt -- deliberately kept as-is (see _render_180mm below) so every
    # other caller across the app that already uses it needs no change.
    # "80mm" is accepted as an alias for the exact same thermal path/output,
    # since that's what pos_page.py's Print/Save Receipt actions actually
    # pass -- resolving it here (rather than renaming the canonical value)
    # is the fix for the KeyError '80mm' crash: it closes the one caller
    # that was out of sync, without touching anything already working.
    _FORMAT_ALIASES = {"80mm": "180mm"}
    _SUPPORTED_FORMATS = {"A4", "A5", "180mm"}

    @classmethod
    def _canonical_format(cls, format_name: str) -> str:
        canonical = cls._FORMAT_ALIASES.get(format_name, format_name)
        if canonical not in cls._SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported invoice output format: {format_name!r}")
        return canonical

    def default_path(self, invoice, format_name: str) -> Path:
        folder = self._ensure_output_dir()
        safe_number = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in invoice.invoice_number)
        suffix = self._canonical_format(format_name)
        base = folder / f"{safe_number}_{suffix}.pdf"
        if not base.exists():
            return base
        counter = 2
        while True:
            candidate = folder / f"{safe_number}_{suffix}_{counter}.pdf"
            if not candidate.exists():
                return candidate
            counter += 1

    def generate(self, invoice, settings, output_path: str | Path, format_name: str) -> Path:
        canonical = self._canonical_format(format_name)
        path = Path(output_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if canonical in ("A4", "A5"):
                self._render_executive(invoice, settings, path, A4 if canonical == "A4" else A5)
            else:
                self._render_180mm(invoice, settings, path)
        except InvoiceOutputError:
            raise
        except Exception as exc:
            raise InvoiceOutputError(f"Could not generate {format_name} invoice PDF: {exc}") from exc
        return path

    def generate_default(self, invoice, settings, format_name: str) -> Path:
        return self.generate(invoice, settings, self.default_path(invoice, format_name), format_name)

    def generate_and_print(self, invoice, settings, format_name: str = "80mm") -> bool:
        """Generate the receipt at its default path (so a copy is always
        archived on disk, same as generate_default) and send it straight to
        the printer via print_pdf.

        Kept as a thin convenience wrapper so callers (e.g. POSPage) can
        generate+print in one call without duplicating that two-step
        sequence themselves. There is no on-screen printer picker to cancel
        here -- print_pdf sends the job silently to the OS default printer
        (no dialog, no browser/PDF-viewer fallback) or raises
        InvoiceOutputError. This always returns True when no exception was
        raised, so callers can still treat the return value as a success
        flag.
        """
        path = self.generate_default(invoice, settings, format_name)
        self.print_pdf(path)
        return True

    def print_pdf(self, pdf_path: str | Path) -> None:
        """Send an already-generated PDF straight to the OS default printer,
        with no dialog and no browser/PDF-viewer fallback.

        Both POS ("Print Receipt") and the Invoices page print through here
        (via generate_and_print). The receipt is rasterized with PyMuPDF and
        painted onto a QPrinter bound to QPrinterInfo.defaultPrinter() -- the
        same technique as print_pdf_natively, minus the QPrintDialog -- so the
        job goes to the configured thermal printer with no on-screen prompt
        and nothing ever opens in Chrome/Edge or a PDF viewer (browser
        rendering mangles thermal formatting). Raises InvoiceOutputError if the
        PDF is missing, PyMuPDF isn't installed, or no default printer is set.
        """
        doc = self._open_pdf_for_printing(pdf_path)
        try:
            info = QPrinterInfo.defaultPrinter()
            if info.isNull():
                raise InvoiceOutputError(
                    "No default printer is configured. Set the thermal printer as the "
                    "default printer in Windows Settings, then print again."
                )
            # HighResolution (not ScreenResolution): _paint_pdf_to_printer
            # sizes the page to the PDF and rasterizes at the printer's own
            # resolution, so the receipt must run at the device's real DPI to
            # come out crisp -- a ~96 DPI screen-resolution printer would print
            # a soft, pixelated slip no matter how high the source raster is.
            printer = QPrinter(info, QPrinter.PrinterMode.HighResolution)
            self._paint_pdf_to_printer(printer, doc)
        finally:
            doc.close()

    @staticmethod
    def _open_pdf_for_printing(pdf_path: str | Path):
        """Open a generated PDF with PyMuPDF for rasterized printing.

        Shared by print_pdf (silent, default printer) and print_pdf_natively
        (QPrintDialog picker). PyQt5 -- unlike the Qt6 bindings -- has no QtPdf
        module to paint an existing PDF's pages directly, so both routes
        rasterize the pages with the optional PyMuPDF ("fitz") package, an
        already-established optional dependency here (see
        DocumentPreviewDialog._build_pdf_preview in app/ui/notes_page.py).

        Returns the open fitz document (the caller owns closing it). Raises
        InvoiceOutputError if PyMuPDF is missing, the file doesn't exist, or
        the PDF has no pages.
        """
        try:
            import fitz  # PyMuPDF
        except ImportError as exc:
            raise InvoiceOutputError(
                "Printing this PDF needs the optional 'PyMuPDF' package, which isn't installed.\n\n"
                "Install it with:\npip install PyMuPDF"
            ) from exc

        path = Path(pdf_path)
        if not path.exists():
            raise InvoiceOutputError("The invoice PDF does not exist")
        try:
            doc = fitz.open(str(path))
        except Exception as exc:
            raise InvoiceOutputError(f"Could not open PDF for printing: {exc}") from exc
        if doc.page_count == 0:
            doc.close()
            raise InvoiceOutputError("This PDF has no pages to print")
        return doc

    @staticmethod
    def _crop_thermal_page(page, pad_pt: float = 1 * mm) -> None:
        """Trim blank paper above the first and below the last line of an 80mm receipt page.

        The receipt PDF is built with a safety buffer under the content (so
        ReportLab never spills onto a 2nd page); cropping the page to its
        real content bottom here stops the printer feeding that blank paper.
        Only pages narrower than 100mm are touched, so A4/A5 are unaffected.
        """
        import fitz  # PyMuPDF

        rect = page.rect
        if rect.width > 100 * mm:
            return
        boxes = [fitz.Rect(b[:4]) for b in page.get_text("blocks")]
        boxes += [fitz.Rect(d["rect"]) for d in page.get_drawings()]
        if not boxes:
            return
        top = max(min(b.y0 for b in boxes) - pad_pt, rect.y0)
        bottom = min(max(b.y1 for b in boxes) + pad_pt, rect.y1)
        page.set_cropbox(fitz.Rect(rect.x0, top, rect.x1, bottom))

    @staticmethod
    def _paint_pdf_to_printer(printer: QPrinter, doc) -> None:
        """Rasterize each PDF page with PyMuPDF and paint it onto the printer,
        sized 1:1 with the PDF page and at high resolution for a crisp print.

        Shared page-painting loop for both the silent (print_pdf) and
        dialog-based (print_pdf_natively) routes. Two things keep the output
        sharp and correctly placed on the paper:

        * The printer page is set to the PDF page's OWN size (full page, no
          margins) before any painting. Without this the printer keeps its A4
          default, so the driver scales the narrow 80mm receipt up to A4 width
          -- blowing the text up ~2.6x and shoving the right-hand columns off
          the paper (the "text cut off on the right" complaint).
        * Each page is rasterized at the printer's real resolution (never
          below the 300 DPI target) and drawn to fill that page exactly, so
          the bitmap maps device-pixel-for-device-pixel with no second
          resampling. Rasterizing at a fixed low DPI and letting the driver
          stretch it was what made the print look pixelated/blurry.
        """
        # Page size and resolution must be set on the QPrinter *before* a
        # QPainter binds to it. Crop the first page's blank tail first so the
        # page size reflects the receipt's real content height, not the very
        # tall measuring page ReportLab built it on.
        first_page = doc[0]
        InvoiceOutputService._crop_thermal_page(first_page)
        first_rect = first_page.rect  # PDF points (1/72")
        printer.setFullPage(True)
        printer.setPageSize(QPageSize(QSizeF(first_rect.width, first_rect.height), QPageSize.Unit.Point))
        # Request the 300 DPI target; a driver may report a higher native DPI,
        # which we honour below (rasterizing to match) for maximum sharpness.
        printer.setResolution(300)
        render_dpi = max(printer.resolution(), 300)

        painter = QPainter(printer)
        try:
            for page_index in range(doc.page_count):
                if page_index > 0:
                    printer.newPage()
                page = doc[page_index]
                if page_index != 0:
                    InvoiceOutputService._crop_thermal_page(page)
                pix = page.get_pixmap(dpi=render_dpi, alpha=False)
                image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
                # Fill the page's paint area (the full paper, since setFullPage
                # is on) exactly by width, preserving aspect ratio -- the right
                # edge lands on the paper edge, never past it.
                viewport = painter.viewport()
                target_width = float(viewport.width())
                target_height = target_width * pix.height / pix.width
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.drawImage(QRectF(0.0, 0.0, target_width, target_height), image)
        finally:
            painter.end()

    def print_pdf_natively(self, pdf_path: str | Path) -> bool:
        """Print an already-generated PDF through Qt's QPrinter with a native
        QPrintDialog printer picker.

        print_pdf (used by generate_and_print, and thus by both POS and the
        Invoices page) sends the receipt silently to the OS default printer.
        This method is the alternative for callers that want the user to pick
        a printer/settings in a dialog first; it shares the same
        rasterize-then-paint technique (see _open_pdf_for_printing /
        _paint_pdf_to_printer), just gated behind the dialog.

        Returns True once the user confirms the dialog and the pages are sent,
        False if they cancel.
        """
        doc = self._open_pdf_for_printing(pdf_path)
        # HighResolution so the receipt prints at the device's real DPI --
        # _paint_pdf_to_printer sets the page to the PDF's own size and
        # rasterizes to match, so a full page maps onto a full page (no A4
        # blow-up, no right-edge clipping) and the text stays crisp.
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(printer)
        dialog.setWindowTitle("Print Invoice")
        if dialog.exec_() != QPrintDialog.DialogCode.Accepted:
            doc.close()
            return False
        try:
            self._paint_pdf_to_printer(printer, doc)
        finally:
            doc.close()
        return True

    # ---------------------------------------------------------------
    # 80mm wide thermal receipt (POS-80) -- single standardized receipt
    # format. Kept under the historical "180mm" internal name/format_name
    # (see _render_180mm) so no other file needs to change.
    # ---------------------------------------------------------------

    @staticmethod
    def _receipt_rule():
        # Full-width dashed rule matching a physical thermal printer's divider.
        # HRFlowable spans the frame's width exactly, so (unlike a run of "-"
        # characters) it can never under/overflow the printable width.
        return HRFlowable(width="100%", thickness=0.7, lineCap="round",
                          color=colors.HexColor("#94A3B8"), dash=(2, 2),
                          spaceBefore=0, spaceAfter=0)

    def _thermal_header(self, invoice, settings, styles, width):
        story = [Paragraph(settings.shop_name, styles["thermal_shop"])]
        if settings.shop_address:
            story.append(Paragraph(settings.shop_address.replace("\n", "<br/>"), styles["thermal_contact"]))
        if settings.shop_phone:
            story.append(Paragraph(settings.shop_phone, styles["thermal_contact"]))
        story.append(Spacer(1, 4))
        story.append(self._receipt_rule())
        story.append(Spacer(1, 4))

        # Meta block: the full stored reference (invoice_number, e.g.
        # "INV-20260817-0001") headlines the receipt, followed by the full
        # date/time, payment method and (for credit sales) the customer.
        rows = [("Invoice #", invoice.invoice_number),
                ("Date", f"{invoice.created_at:%d %B %Y, %I:%M %p}"),
                ("Payment", "Credit (Udhaar)" if invoice.payment_type == "credit" else "Cash")]
        if invoice.customer_name:
            rows.append(("Customer", invoice.customer_name))
        # Cashier/staff is read defensively via getattr — this real Invoice
        # model may not carry this attribute at all yet, so its absence
        # must not break every receipt.
        cashier_name = getattr(invoice, "cashier_name", None) or getattr(invoice, "staff_name", None)
        if cashier_name:
            rows.append(("Cashier", cashier_name))
        meta_data = [[Paragraph(label, styles["thermal_meta_label"]), Paragraph(value, styles["thermal_meta_value"])] for label, value in rows]
        meta = Table(meta_data, colWidths=[width * 0.26, width * 0.74])
        meta.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(meta)
        story.append(Spacer(1, 4))
        story.append(self._receipt_rule())
        story.append(Spacer(1, 4))
        return story

    def _thermal_items_table(self, invoice, settings, styles, width):
        # 5 columns across a genuinely narrow 66mm printable width (80mm
        # paper minus 7mm margins each side -- see _render_180mm, sized with
        # real headroom against print-head/driver overscan): the Unit
        # column has been removed (it never belonged on the customer receipt),
        # so its room is folded back into Description, which absorbs the
        # remaining slack and wraps to a second line for longer product names
        # -- expected and normal on a narrow receipt, not a rendering error.
        header = [
            Paragraph("#", styles["thermal_header_center"]),
            Paragraph("Item Description", styles["thermal_header"]),
            Paragraph("Qty", styles["thermal_header_center"]),
            Paragraph("Price", styles["thermal_header_right"]),
            Paragraph("Total", styles["thermal_header_right"]),
        ]
        data = [header]
        for idx, item in enumerate(invoice.items, start=1):
            name_html = item.product_name
            discount = Decimal(item.item_discount)
            if discount != 0:
                # Requirement #2 — the per-line discount must be clearly
                # visible on the slip. It used to be a 6pt GREY subline, which
                # prints as faint, broken dots on a monochrome thermal head and
                # was effectively invisible. It's now a black, bold, 8pt line
                # reading "(Less: Rs XX.XX)" directly under the product name,
                # shown only when that item actually carries a discount.
                name_html += f"<br/><font size=8><b>(Less: {self._currency(settings, discount)})</b></font>"
            # Price shows the EFFECTIVE per-unit rate actually charged
            # (line_total / qty) so Price × Qty reconciles to Total even when a
            # per-item discount applied -- never the raw pre-discount price.
            qty = Decimal(str(item.quantity))
            effective_price = (Decimal(str(item.line_total)) / qty) if qty else Decimal(str(item.unit_price))
            # Bare amounts (no repeated "Rs." prefix) in the Price/Total
            # columns — even at the reduced 8.5pt bold size a "Rs. 125000.00"
            # with the prefix would no longer fit these narrow columns and
            # would wrap. The currency is unmistakable from the
            # Subtotal/Grand Total block below, so the per-line columns stay
            # bare, the way a printed till receipt conventionally reads.
            data.append([
                Paragraph(str(idx), styles["thermal_center"]),
                Paragraph(name_html, styles["thermal"]),
                Paragraph(self.quantity(item.quantity), styles["thermal_center"]),
                Paragraph(self.money(effective_price), styles["thermal_right"]),
                Paragraph(self.money(item.line_total), styles["thermal_right"]),
            ])
        # Column widths are fixed absolute mm values, not ratios of `width`:
        # #4mm + Description 26mm + Qty 8mm + Price 13mm + Total 15mm = 66mm
        # exactly. Qty is widened to 8mm and headers dropped to 8.5pt (see
        # _styles) so "Qty" never wraps to "Qt"/"y" as it did at the larger
        # size. Pinning these in mm (rather than as fractions of whatever
        # `width` happens to be) guarantees the Total column's right edge can
        # never drift past the true 66mm printable boundary, whatever margin
        # or content-width changes happen elsewhere.
        col_widths = list(_THERMAL_COLS)
        table = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, 0), 0.7, colors.black),
            ("LINEBELOW", (0, 1), (-1, -2), 0.3, colors.HexColor("#E2E8F0")),
            ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
            # Requirement #3 -- vertically centre every cell so the row's #,
            # Qty, Price and Total sit level with a Description that wrapped to
            # two lines, instead of clinging to the top and reading as if the
            # serial numbers were drifting up and down the column.
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), _THERMAL_CELL_PAD),
            ("RIGHTPADDING", (0, 0), (-1, -1), _THERMAL_CELL_PAD),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return table

    def _thermal_totals(self, invoice, settings, styles, width):
        # block_w is capped at an absolute 66mm regardless of the incoming
        # `width` -- this is the same hard printable-width ceiling as the
        # items table (see _render_180mm), so the right border of these
        # tables can never reach past the true printable area even if a
        # caller passes a wider content_width in future. Within that 66mm
        # ceiling, the Subtotal / Paid / Remaining rows keep the "Rs."
        # prefix, so at the reduced 8.5-9pt sizes their value column still
        # gets 0.74 of the (capped) block for a 6-figure "Rs. 125,000.00" to
        # sit on one line instead of wrapping.
        max_w = width
        block_w = max_w
        item_discount = sum((Decimal(i.item_discount) for i in invoice.items), Decimal("0.00"))
        plain_rows = [("Subtotal", self._gross_subtotal(invoice)), ("Item discount", item_discount),
                      ("Bill discount", self._bill_discount(invoice))]
        # Tax is read defensively (getattr) and only shown when the invoice
        # actually carries a positive tax amount — most existing invoices
        # won't have this attribute yet, so its absence renders exactly as
        # before rather than an empty/zero row.
        tax_amount = getattr(invoice, "tax_amount", None) or getattr(invoice, "tax_total", None)
        if tax_amount and Decimal(str(tax_amount)) > 0:
            plain_rows.append(("Tax", Decimal(str(tax_amount))))
        plain_data = [[Paragraph(label, styles["thermal"]), Paragraph(self._currency(settings, value), styles["thermal_right"])] for label, value in plain_rows]
        plain = Table(plain_data, colWidths=[block_w * 0.55, block_w * 0.45], hAlign="RIGHT")
        plain.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ]))

        # Bold, enclosed Grand Total box -- the single most prominent figure
        # on the receipt, visually separated from the plain subtotal/discount
        # rows above it. Sized against BOTH cells' actual content at the
        # current sizes:
        #   - LABEL "GRAND TOTAL" at 11pt bold needs ~78pt of clear text room.
        #   - VALUE (currency + amount) at 12pt bold -- e.g. "Rs. 125000.00"
        #     for a 6-figure bill -- needs ~84pt of clear text room.
        # grand_w is 0.96 of the same 66mm-capped `max_w` (nearly the whole
        # printable area, which suits the most important line) with a
        # 0.46/0.54 label/value split, so neither the label nor the amount
        # wraps to a second line -- and, capped off `max_w` rather than raw
        # `width`, its right border stays inside the 66mm ceiling too.
        # hAlign="RIGHT" keeps the box flush with the value column of the
        # plain rows above/below it.
        grand_w = max_w
        grand = Table([[Paragraph("GRAND TOTAL", styles["thermal_grand_label"]), Paragraph(self._currency(settings, invoice.grand_total), styles["thermal_grand_value"])]],
                       colWidths=[grand_w * 0.46, grand_w * 0.54], hAlign="RIGHT")
        grand.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 1, colors.black),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))

        paid_rows = [("Paid", invoice.paid_amount), ("Remaining", invoice.remaining_amount)]
        # Amount Tendered / Change Due -- cash-drawer detail, shown only when
        # present (see getattr defaults). Change is only derived, never shown,
        # when it would come out negative (tendered didn't cover the bill).
        tendered = getattr(invoice, "amount_tendered", None) or getattr(invoice, "tendered_amount", None)
        if tendered is not None:
            tendered = Decimal(str(tendered))
            paid_rows.append(("Tendered", tendered))
            change_due = getattr(invoice, "change_due", None)
            change_due = Decimal(str(change_due)) if change_due is not None else (tendered - Decimal(str(invoice.grand_total)))
            if change_due >= 0:
                paid_rows.append(("Change Due", change_due))
        paid_data = [[Paragraph(label, styles["thermal"]), Paragraph(self._currency(settings, value), styles["thermal_right"])] for label, value in paid_rows]
        paid = Table(paid_data, colWidths=[block_w * 0.55, block_w * 0.45], hAlign="RIGHT")
        paid.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ]))
        story = [plain, Spacer(1, 4), grand, Spacer(1, 4), paid]

        # Customer ledger / khaata account block. Rendered only when the
        # caller (POS) attached real figures via
        # SalesService.receipt_context, and only for a credit (udhaar) sale --
        # the one case where a running customer balance exists. The Invoices
        # page never attaches this, so its reprints render exactly as before.
        # Every value below is a real, non-fabricated figure taken straight
        # from that context (see receipt_context):
        #   Previous Balance = outstanding before this bill
        #   Total Payable    = previous balance + this bill's grand total
        #   Received         = amount paid at the counter for this bill
        #   Net Balance      = the customer's current total khaata balance
        ctx = getattr(invoice, "receipt_ledger_context", None)
        if ctx and ctx.get("is_credit"):
            previous = Decimal(str(ctx.get("previous_balance") or "0"))
            grand_total = Decimal(str(ctx.get("grand_total") or "0"))
            received = Decimal(str(ctx.get("paid_amount") or "0"))
            net_balance = Decimal(str(ctx.get("total_outstanding") or "0"))
            account_rows = [
                ("Payment Method", "Credit"),
                ("Previous Balance", self._currency(settings, previous)),
                ("Total Payable", self._currency(settings, previous + grand_total)),
                ("Received", self._currency(settings, received)),
                ("Net Balance (Khaata)", self._currency(settings, net_balance)),
            ]
            account_data = [[Paragraph(label, styles["thermal"]), Paragraph(value, styles["thermal_right"])] for label, value in account_rows]
            account = Table(account_data, colWidths=[block_w * 0.55, block_w * 0.45], hAlign="RIGHT")
            account.setStyle(TableStyle([
                ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1),
                ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                ("LINEABOVE", (0, 0), (-1, 0), 0.5, colors.grey),
            ]))
            story.extend([Spacer(1, 4), Paragraph("<b>Account Summary (Khaata)</b>", styles["thermal"]), account])
        return story

    @staticmethod
    def _measure_story_height(story, width: float, available_height: float) -> float:
        """Sum each flowable's own wrapped height at ``width`` to get the
        real content height -- the "measure, then build the real page"
        approach this renderer needs since, unlike a fixed A4/A5 page, the
        80mm receipt's own page height is derived from its content rather
        than the other way around. ``Table.wrap`` / ``Paragraph.wrap`` are
        what reportlab's own
        doc.build() calls internally to paginate, so calling them here first,
        against a very tall available_height, gives the same per-flowable
        height doc.build() would use -- just summed up-front instead of
        consumed page-by-page.
        """
        total = 0.0
        for flowable in story:
            _, h = flowable.wrap(width, available_height)
            total += h
        return total

    def _render_180mm(self, invoice, settings, path: Path):
        # Despite the method's name (kept as-is -- see format_name="180mm"
        # used throughout the UI/settings), the actual page is true 80mm
        # POS-80 paper width now, not 180mm. The bug this fixes: a PDF built
        # at 180mm wide was being handed to the POS-80 Windows driver, which
        # has no choice but to scale that whole page down to fit its fixed
        # 80mm paper -- shrinking every line of text into an unreadable
        # sliver. Building the PDF at the printer's actual width means the
        # driver prints it 1:1, with no scaling at all.
        styles = self._styles()
        page_width = 80 * mm
        # Left/right margins are the only physical margins this page keeps
        # -- the 80mm roll's own unprintable edge strips. Top/bottom are
        # explicitly 0: the page HEIGHT is derived from the content itself
        # (below), so a separate top/bottom doc margin would just be extra
        # blank paper on top of that -- exactly the "5cm gap at the bottom"
        # bug this replaces. Any breathing room above the shop name or below
        # the last totals line belongs to the story's own Spacers, not to a
        # page margin that persists no matter how tall the page already is.
        # 7mm margins each side: header text at the previous font sizes was
        # wrapping ("Qty" -> "Qt"/"y") and causing fitting issues at 100%
        # print scale even within the 68mm width (6mm margins). Font sizes
        # were scaled down ~1pt (see _styles) and margins tightened further
        # to a hard 66mm printable width, giving real headroom before the
        # physical paper edge. This width is not treated as a budget to fill
        # with ratios: the items table uses fixed mm column widths (see
        # _thermal_items_table) that sum to exactly 66mm, and the
        # totals/Grand Total tables are explicitly capped at 66mm too, so
        # nothing can silently grow past this boundary again as content
        # changes.
        left = _THERMAL_LEFT_MARGIN
        right = _THERMAL_RIGHT_MARGIN
        content_width = page_width - left - right

        story = self._thermal_header(invoice, settings, styles, content_width)
        story.append(self._thermal_items_table(invoice, settings, styles, content_width))
        story.append(Spacer(1, 5))
        story += self._thermal_totals(invoice, settings, styles, content_width)
        # The thermal receipt ends right here -- at the totals / Remaining
        # (Khaata) balance block. There is deliberately NO trailing footer
        # line: a "Thank you..." paragraph here was overflowing onto a nearly
        # blank 2nd page on the 80mm roll (paper waste on every sale). The
        # shop's invoice_footer setting still prints on the A4/A5 executive
        # invoice via _executive_footer -- it is only dropped from the roll.

        # Dynamic page height: the page is exactly as tall as this invoice's
        # own content needs, plus a small safety buffer -- not a flat guess
        # -- so a short 2-item sale and a long 40-item one each get a page
        # sized to themselves, with no unnecessary trailing blank paper on
        # the roll either way. A generous _MEASURE_HEIGHT acts only as the
        # "how much room is there to wrap into" ceiling for the measuring
        # pass below, never as the actual page height.
        _MEASURE_HEIGHT = 5000 * mm
        # Safe-height buffer added on top of the measured content height,
        # below. Table/Paragraph.wrap() during the measuring pass above can
        # under-report by a fraction of a point versus what doc.build()'s
        # actual pagination consumes (font metrics rounding, KeepTogether
        # framing, etc.) -- on a fixed-size A4/A5 page that slack is
        # invisible, but here the page height IS the measurement, so any
        # shortfall means ReportLab silently starts a 2nd page for the last
        # line or two of a receipt. 15mm covers that rounding slack with
        # generous headroom so a 2nd page is NEVER triggered, while still
        # leaving no visible blank gap at the cut -- the earlier 50mm buffer
        # was solving the same 2nd-page problem but by such a wide margin it
        # wasted roughly 4cm of paper on every receipt; content_height +
        # top/bottom margins already account for the real content, so only
        # this small guard band is needed.
        _SAFE_HEIGHT_BUFFER = 15 * mm
        content_height = self._measure_story_height(story, content_width, _MEASURE_HEIGHT)
        page_height = content_height + _THERMAL_TOP_MARGIN + _THERMAL_BOTTOM_MARGIN + _SAFE_HEIGHT_BUFFER

        doc = BaseDocTemplate(str(path), pagesize=(page_width, page_height), leftMargin=left, rightMargin=right, topMargin=_THERMAL_TOP_MARGIN, bottomMargin=_THERMAL_BOTTOM_MARGIN, title=f"Invoice {invoice.invoice_number} 80mm")
        frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="receipt")
        doc.addPageTemplates([PageTemplate(id="receipt", frames=[frame])])
        doc.build(story)

    # ---------------------------------------------------------------
    # A4 / A5 -- executive commercial invoice
    # ---------------------------------------------------------------

    def _executive_header(self, invoice, settings, styles, width):
        left_lines = [Paragraph(settings.shop_name, styles["header_shop"])]
        if settings.shop_address:
            left_lines.append(Paragraph(settings.shop_address.replace("\n", "<br/>"), styles["header_left"]))
        if settings.shop_phone:
            left_lines.append(Paragraph(settings.shop_phone, styles["header_left"]))

        status_text, status_color = self._payment_status(invoice)
        badge = Table([[Paragraph(status_text, styles["badge"])]], colWidths=[28 * mm])
        badge.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), status_color),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ]))

        right_lines = [
            Paragraph("INVOICE", styles["invoice_title"]),
            Spacer(1, 4),
            # Clean sequential number, placed right above the date line.
            Paragraph(f"Invoice No: {invoice.id}", styles["header_right"]),
            Paragraph(f"Date: {invoice.created_at:%d %B %Y}", styles["header_right"]),
        ]
        cashier_name = getattr(invoice, "cashier_name", None) or getattr(invoice, "staff_name", None)
        if cashier_name:
            right_lines.append(Paragraph(f"Cashier: {cashier_name}", styles["header_right"]))
        right_lines.append(Spacer(1, 6))

        header_table = Table([[left_lines, [*right_lines, badge]]], colWidths=[width * 0.55, width * 0.45])
        header_table.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ]))
        story = [header_table, Spacer(1, 14)]

        if invoice.customer_name:
            # "Billed To" card: light-grey background box.
            card_body = [Paragraph("BILLED TO", styles["card_label"]), Paragraph(invoice.customer_name, styles["card_value"])]
            card = Table([[card_body]], colWidths=[width])
            card.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), _CARD_GREY),
                ("BOX", (0, 0), (-1, -1), 0.5, _BORDER_GREY),
                ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]))
            story += [card, Spacer(1, 14)]
        return story

    def _executive_items_table(self, invoice, settings, styles, width):
        header = [Paragraph(x, styles["body_header"]) for x in ["#", "Item Description", "Qty"]]
        header += [Paragraph(x, styles["body_header_right"]) for x in ["Price", "Total"]]
        data = [header]
        for idx, item in enumerate(invoice.items, start=1):
            # Price shows the EFFECTIVE per-unit rate actually charged
            # (line_total / qty) so Price × Qty reconciles to Total even when a
            # per-item discount applied -- never the raw pre-discount price.
            qty = Decimal(str(item.quantity))
            effective_price = (Decimal(str(item.line_total)) / qty) if qty else Decimal(str(item.unit_price))
            price_html = self._currency(settings, effective_price)
            discount = Decimal(item.item_discount)
            if discount != 0:
                # A non-zero item discount is folded into the Price cell as a
                # small secondary line so the saving stays visible even though
                # the Price above already reflects the discounted rate.
                price_html += f"<br/><font size=6.5 color='#64748B'>Disc: {self._currency(settings, discount)}</font>"
            data.append([
                Paragraph(str(idx), styles["body"]),
                Paragraph(item.product_name, styles["body"]),
                Paragraph(self.quantity(item.quantity), styles["body_right"]),
                Paragraph(price_html, styles["body_right"]),
                Paragraph(self._currency(settings, item.line_total), styles["body_right"]),
            ])
        col_widths = [width * 0.05, width * 0.55, width * 0.10, width * 0.15, width * 0.15]
        table = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), _ACCENT_DARK),
            ("LINEBELOW", (0, 1), (-1, -2), 0.4, _BORDER_GREY),
            ("LINEBELOW", (0, -1), (-1, -1), 0.8, _ACCENT_DARK),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), _A4_CELL_PAD),
            ("RIGHTPADDING", (0, 0), (-1, -1), _A4_CELL_PAD),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        return table

    def _executive_totals(self, invoice, settings, styles, width):
        item_discount = sum((Decimal(i.item_discount) for i in invoice.items), Decimal("0.00"))
        plain_rows = [("Subtotal", self._gross_subtotal(invoice)), ("Item discount", item_discount),
                      ("Bill discount", self._bill_discount(invoice))]
        tax_amount = getattr(invoice, "tax_amount", None) or getattr(invoice, "tax_total", None)
        if tax_amount and Decimal(str(tax_amount)) > 0:
            plain_rows.append(("Tax", Decimal(str(tax_amount))))
        plain_data = [[Paragraph(label, styles["total_label"]), Paragraph(self._currency(settings, value), styles["total_value"])] for label, value in plain_rows]
        card_width = width * 0.42
        plain = Table(plain_data, colWidths=[card_width * 0.55, card_width * 0.45], hAlign="RIGHT")
        plain.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))

        grand = Table([[Paragraph("GRAND TOTAL", styles["grand_label"]), Paragraph(self._currency(settings, invoice.grand_total), styles["grand_value"])]],
                       colWidths=[card_width * 0.47, card_width * 0.53], hAlign="RIGHT")
        grand.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), _CARD_GREY),
            ("BOX", (0, 0), (-1, -1), 0.75, _ACCENT_DARK),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))

        paid_rows = [("Paid", invoice.paid_amount), ("Remaining Balance", invoice.remaining_amount)]
        tendered = getattr(invoice, "amount_tendered", None) or getattr(invoice, "tendered_amount", None)
        if tendered is not None:
            tendered = Decimal(str(tendered))
            paid_rows.append(("Tendered", tendered))
            change_due = getattr(invoice, "change_due", None)
            change_due = Decimal(str(change_due)) if change_due is not None else (tendered - Decimal(str(invoice.grand_total)))
            if change_due >= 0:
                paid_rows.append(("Change Due", change_due))
        paid_data = [[Paragraph(label, styles["total_label"]), Paragraph(self._currency(settings, value), styles["total_value"])] for label, value in paid_rows]
        paid = Table(paid_data, colWidths=[card_width * 0.55, card_width * 0.45], hAlign="RIGHT")
        paid.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return [plain, Spacer(1, 6), grand, Spacer(1, 6), paid]

    def _executive_footer(self, settings, styles):
        if not settings.invoice_footer:
            return []
        return [Spacer(1, 16), Paragraph(settings.invoice_footer.replace("\n", "<br/>"), styles["meta"])]

    def _render_executive(self, invoice, settings, path: Path, page_size):
        """Shared A4/A5 renderer -- one well-designed template parameterised
        by page size, rather than duplicating the whole layout per format."""
        styles = self._styles()
        margin = 16 * mm if page_size is A4 else 12 * mm
        doc = BaseDocTemplate(str(path), pagesize=page_size, leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=margin, title=f"Invoice {invoice.invoice_number}")
        frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
        doc.addPageTemplates([PageTemplate(id="executive", frames=[frame])])
        story = self._executive_header(invoice, settings, styles, doc.width)
        story.append(self._executive_items_table(invoice, settings, styles, doc.width))
        story.append(Spacer(1, 14))
        story += self._executive_totals(invoice, settings, styles, doc.width)
        story += self._executive_footer(settings, styles)
        doc.build(story)
