"""Tool wiring. Domain logic lives in the tool modules; the controller only sees the registry."""
from __future__ import annotations

from pathlib import Path

import httpx

from .files import InvoiceRepository, ReadInvoiceArgs, SearchInvoicesArgs, on_read_result, on_search_result
from .finance import (
    EnterFinanceRecordArgs,
    FinanceClient,
    GetFinanceRecordArgs,
    make_approval_check,
    on_enter_result,
    on_get_result,
)
from .registry import ToolRegistry, ToolSpec


def build_registry(invoices_dir: Path, finance_http: httpx.Client, approval_threshold: float) -> ToolRegistry:
    repo = InvoiceRepository(invoices_dir)
    finance = FinanceClient(finance_http)
    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="search_invoices",
            description="Search the company invoice repository by company/vendor name. Results are sorted newest first (issue_date).",
            args_model=SearchInvoicesArgs,
            func=repo.search,
            running_title="Searching invoice repository",
            on_result=on_search_result,
        )
    )
    reg.register(
        ToolSpec(
            name="read_invoice",
            description="Open an invoice document by invoice_id and extract invoice_number, company, amount, due_date, issue_date and line items.",
            args_model=ReadInvoiceArgs,
            func=repo.read,
            running_title="Reading invoice",
            on_result=on_read_result,
        )
    )
    reg.register(
        ToolSpec(
            name="enter_finance_record",
            description=(
                "Create an accounts-payable record in the internal finance system (POST /finance/invoices). "
                "Returns success=false with error/hint when the system rejects the input."
            ),
            args_model=EnterFinanceRecordArgs,
            func=finance.enter,
            running_title="Submitting record to finance system",
            on_result=on_enter_result,
            approval_check=make_approval_check(approval_threshold),
        )
    )
    reg.register(
        ToolSpec(
            name="get_finance_record",
            description=(
                "Retrieve a stored finance record by record_id (or invoice_number). The runtime then compares it "
                "with the source invoice and reports verification_result."
            ),
            args_model=GetFinanceRecordArgs,
            func=finance.get,
            running_title="Retrieving finance record for verification",
            on_result=on_get_result,
        )
    )
    return reg
