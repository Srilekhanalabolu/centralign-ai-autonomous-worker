from app.config import INVOICES_DIR
from app.tools.files import InvoiceRepository, ReadInvoiceArgs, SearchInvoicesArgs
from app.tools.finance import format_inr, normalize_amount, verify_record

repo = InvoiceRepository(INVOICES_DIR)


def test_search_returns_latest_acme_first():
    res = repo.search(SearchInvoicesArgs(company="Acme Corp", query="latest invoice"))
    assert res["success"] and res["count"] == 2
    assert res["results"][0]["invoice_number"] == "INV-2026-104"
    assert res["results"][0]["is_latest"] is True


def test_search_fuzzy_company_and_no_results():
    assert repo.search(SearchInvoicesArgs(company="globex"))["count"] == 2
    assert repo.search(SearchInvoicesArgs(company="Umbrella"))["count"] == 0


def test_read_invoice_extracts_fields():
    res = repo.read(ReadInvoiceArgs(invoice_id="acme_invoice_oct_2026"))
    inv = res["invoice"]
    assert (inv["invoice_number"], inv["amount"], inv["due_date"]) == ("INV-2026-104", "₹48,500", "2026-10-15")


def test_read_invoice_blocks_path_traversal():
    assert repo.read(ReadInvoiceArgs(invoice_id="../../config"))["success"] is False


def test_amount_helpers():
    assert normalize_amount("₹48,500") == 48500
    assert format_inr(7500000) == "₹75,00,000"


def test_verification_detects_mismatch():
    src = {"invoice_number": "A", "company": "X", "amount": "₹100", "due_date": "2026-01-01"}
    good = {"id": 1, "invoice_number": "A", "company": "X", "amount": 100, "due_date": "2026-01-01"}
    assert verify_record(src, good)["verified"] is True
    assert verify_record(src, {**good, "amount": 101})["verified"] is False
