from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pypdf import PdfReader

from app.services.invoice_output_service import InvoiceOutputError, InvoiceOutputService
from app.services.settings_service import DEFAULT_SETTINGS


@dataclass
class Item:
    product_name: str = "Historical Wire"
    unit: str = "meter"
    quantity: Decimal = Decimal("5.250000")
    unit_price: Decimal = Decimal("10.00")
    item_discount: Decimal = Decimal("1.00")
    line_total: Decimal = Decimal("51.50")


@dataclass
class Invoice:
    id: int = 1
    invoice_number: str = "INV-20260817-0001"
    created_at: datetime = datetime(2026, 8, 17, 12, 34, 56)
    customer_name: str | None = "Historical Customer"
    payment_type: str = "credit"
    subtotal: Decimal = Decimal("51.50")
    discount_total: Decimal = Decimal("3.50")
    grand_total: Decimal = Decimal("48.00")
    paid_amount: Decimal = Decimal("20.00")
    remaining_amount: Decimal = Decimal("28.00")
    items: list = None

    def __post_init__(self):
        if self.items is None:
            self.items = [Item()]


def settings():
    return DEFAULT_SETTINGS


def text_from_pdf(path: Path) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def test_a4_pdf_generation_succeeds_and_contains_core_content(tmp_path):
    service = InvoiceOutputService(tmp_path / "out")
    path = service.generate(Invoice(), settings(), tmp_path / "invoice.pdf", "A4")
    text = text_from_pdf(path)
    assert path.exists()
    assert "INV-20260817-0001" in text
    assert "Historical Customer" in text
    assert "51.50" in text
    assert "48.00" in text
    assert "20.00" in text
    assert "28.00" in text


def test_a4_pdf_uses_historical_name_and_selling_price(tmp_path):
    invoice = Invoice(items=[Item(product_name="Old Wire", unit_price=Decimal("12.34"))])
    path = InvoiceOutputService(tmp_path / "out").generate(invoice, settings(), tmp_path / "invoice.pdf", "A4")
    text = text_from_pdf(path)
    assert "Old Wire" in text
    assert "12.34" in text


def test_empty_customer_name_is_not_rendered_as_walk_in(tmp_path):
    invoice = Invoice(customer_name=None)
    path = InvoiceOutputService(tmp_path / "out").generate(invoice, settings(), tmp_path / "walkin.pdf", "A4")
    text = text_from_pdf(path)
    assert "Walk-in" not in text
    assert "Customer:" not in text


def test_cash_customer_name_is_rendered_in_pdf(tmp_path):
    invoice = Invoice(customer_name="Ahmed Khan", payment_type="cash")
    path = InvoiceOutputService(tmp_path / "out").generate(invoice, settings(), tmp_path / "cash.pdf", "A4")
    text = text_from_pdf(path)
    assert "Payment: Cash" in text
    assert "Customer: Ahmed Khan" in text


def test_decimal_quantity_and_money_formatting_are_human_readable(tmp_path):
    invoice = Invoice(items=[Item(quantity=Decimal("5.250000"), unit_price=Decimal("10"), item_discount=Decimal("0"), line_total=Decimal("52.50"))])
    path = InvoiceOutputService(tmp_path / "out").generate(invoice, settings(), tmp_path / "invoice.pdf", "A4")
    text = text_from_pdf(path)
    assert "5.25" in text
    assert "5.250000" not in text
    assert "10.00" in text
    assert "52.50" in text


@pytest.mark.parametrize("format_name", ["180mm"])
def test_thermal_receipt_generation_succeeds(tmp_path, format_name):
    path = InvoiceOutputService(tmp_path / "out").generate(Invoice(), settings(), tmp_path / f"receipt_{format_name}.pdf", format_name)
    assert path.exists()
    text = text_from_pdf(path)
    assert "INV-20260817-0001" in text
    assert "Historical Customer" in text
    assert "48.00" in text


def test_long_product_name_does_not_fail(tmp_path):
    invoice = Invoice(items=[Item(product_name="Very long historical product name " * 12)])
    for fmt in ("A4", "180mm"):
        path = InvoiceOutputService(tmp_path / "out").generate(invoice, settings(), tmp_path / f"{fmt}.pdf", fmt)
        assert path.exists()


def test_many_items_generate_multi_page_a4(tmp_path):
    items = [Item(product_name=f"Product {i} with a deliberately long historical name") for i in range(120)]
    invoice = Invoice(items=items, subtotal=Decimal("6180.00"), discount_total=Decimal("120.00"), grand_total=Decimal("6060.00"))
    path = InvoiceOutputService(tmp_path / "out").generate(invoice, settings(), tmp_path / "many.pdf", "A4")
    reader = PdfReader(str(path))
    assert len(reader.pages) > 1
    text = text_from_pdf(path)
    assert "Product 0" in text
    assert "Product 119" in text
    assert "6060.00" in text


def test_pdf_generation_does_not_modify_invoice(tmp_path):
    invoice = Invoice()
    before = invoice.__dict__.copy()
    InvoiceOutputService(tmp_path / "out").generate(invoice, settings(), tmp_path / "invoice.pdf", "A4")
    assert invoice.__dict__ == before


def test_print_failure_does_not_modify_invoice_records(tmp_path, monkeypatch):
    invoice = Invoice()
    service = InvoiceOutputService(tmp_path / "out")
    pdf = service.generate(invoice, settings(), tmp_path / "invoice.pdf", "A4")

    # print_pdf now sends the PDF silently to the OS default printer (no
    # startfile / browser handoff). Simulate the "no default printer
    # configured" failure: it's deterministic and headless-safe, and makes
    # print_pdf raise before it ever builds a real QPrinter (constructing one
    # bound to a live printer would block on the print spooler under a
    # headless test run).
    class _NoDefaultPrinter:
        @staticmethod
        def defaultPrinter():
            class _NullPrinterInfo:
                def isNull(self):
                    return True

            return _NullPrinterInfo()

    monkeypatch.setattr("app.services.invoice_output_service.QPrinterInfo", _NoDefaultPrinter)
    with pytest.raises(InvoiceOutputError):
        service.print_pdf(pdf)
    assert invoice.invoice_number == "INV-20260817-0001"
    assert invoice.grand_total == Decimal("48.00")
    assert invoice.paid_amount == Decimal("20.00")


def test_default_paths_do_not_silently_overwrite(tmp_path):
    service = InvoiceOutputService(tmp_path / "out")
    invoice = Invoice()
    first = service.default_path(invoice, "A4")
    first.write_bytes(b"existing")
    second = service.default_path(invoice, "A4")
    assert first != second
    assert second.name.endswith("_2.pdf")


def test_output_reconciles_gross_subtotal_item_and_bill_discounts(tmp_path):
    invoice = Invoice(
        items=[Item(quantity=Decimal("2"), unit_price=Decimal("10"), item_discount=Decimal("2"), line_total=Decimal("18"))],
        subtotal=Decimal("18.00"),  # old stored convention; output derives gross from snapshots
        discount_total=Decimal("3.00"),
        grand_total=Decimal("17.00"),
    )
    path = InvoiceOutputService(tmp_path / "out").generate(invoice, settings(), tmp_path / "invoice.pdf", "A4")
    text = text_from_pdf(path)
    assert "20.00" in text
    assert "2.00" in text
    assert "1.00" in text
    assert "17.00" in text
