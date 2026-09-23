from __future__ import annotations

from decimal import Decimal


def money(value) -> str:
    # On-screen money is shown as clean whole numbers — no trailing ".00"
    # clutter — with ',' thousands separators (Known Issue #6). A whole value
    # like 120 renders as "120" and 1500 as "1,500"; a genuinely fractional
    # value (e.g. 35.50) is preserved as "35.5" rather than being silently
    # rounded, so a real price is never misrepresented. This is the single
    # shared display formatter every page reuses, so the whole-integer rule
    # applies on every screen at once. (Formal printed invoices/receipts keep
    # two decimals via InvoiceOutputService.money — a separate concern.)
    d = Decimal(value)
    if d == d.to_integral_value():
        return f"{d:,.0f}"
    text = f"{d:,.2f}".rstrip("0").rstrip(".")
    return text


def money_input(value) -> str:
    # A clean, comma-free rendering for editable price fields. Commas would
    # break re-parsing by validate_money (Decimal("1,200") is invalid), so an
    # input default/existing value shows "120" (or "35.5"), never "120.00" or
    # "1,200".
    d = Decimal(value)
    if d == d.to_integral_value():
        return f"{d:f}".split(".")[0]
    return format(d, "f").rstrip("0").rstrip(".")


def quantity(value) -> str:
    text = format(Decimal(value), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_invoice_number(sequence_id: int, prefix: str = "INV") -> str:
    # The invoice_number stored on the row: a clean, gapless, always-unique
    # sequence derived straight from the invoice's own autoincrementing
    # primary key — "INV-0001", "INV-0002", "INV-0003"... — instead of the
    # old date-stamped scheme ("INV-20260908-0001") that reset its counter
    # every day and needed collision-retry handling. Zero-padded to 4 digits
    # so numbers sort and align cleanly; sequence 10000+ simply grows past
    # the padding rather than truncating.
    clean_prefix = (prefix or "INV").strip() or "INV"
    return f"{clean_prefix}-{int(sequence_id):04d}"


def invoice_display_number(invoice) -> str:
    # The prominent on-screen label — "Invoice #1", "Invoice #2"... — shown
    # large/bold in the preview modal and in the history table. This reads
    # straight from the invoice's own id rather than parsing invoice_number,
    # so it stays correct even if the stored prefix/padding format changes.
    return f"Invoice #{invoice.id}"
