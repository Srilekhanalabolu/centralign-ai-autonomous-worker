"""Finance-system tools. They call the sandbox finance app over HTTP and verify what was stored."""
from __future__ import annotations

import re
from typing import Any, Union

import httpx
from pydantic import BaseModel, Field

from app.agent.state import AgentState, now_iso


# ----------------------------------------------------------------- helpers
def normalize_amount(value: Any) -> float | None:
    """'₹48,500' -> 48500.0 ; 48500 -> 48500.0 ; garbage -> None"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d.]", "", str(value))
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def format_inr(value: float | None) -> str:
    """Indian digit grouping: 7500000 -> ₹75,00,000"""
    if value is None:
        return "?"
    s = str(int(round(value)))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return "₹" + s


def _plain(value: float | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


# -------------------------------------------------------------- arguments
class EnterFinanceRecordArgs(BaseModel):
    invoice_number: str
    company: str
    amount: Union[int, float, str] = Field(..., description="Invoice amount as accepted by the finance system")
    due_date: str = Field(..., description="YYYY-MM-DD")
    currency: str = "INR"
    source_invoice_id: str | None = None


class GetFinanceRecordArgs(BaseModel):
    record_id: int | None = Field(None, description="Finance record id returned on creation")
    invoice_number: str | None = Field(None, description="Alternative lookup by invoice number")


# ------------------------------------------------------------------ client
class FinanceClient:
    """Thin HTTP client for the internal finance app. `http` is any httpx-compatible client."""

    def __init__(self, http: httpx.Client) -> None:
        self.http = http

    def enter(self, args: EnterFinanceRecordArgs) -> dict[str, Any]:
        try:
            resp = self.http.post("/finance/invoices", json=args.model_dump())
        except httpx.HTTPError as exc:
            return {"success": False, "error": f"Finance system unreachable: {exc}", "hint": "Retry later"}
        body = _json(resp)
        body["http_status"] = resp.status_code
        if body.get("success"):
            body["summary"] = f"Finance record #{body['record_id']} created (HTTP {resp.status_code})"
        else:
            body["summary"] = f"HTTP {resp.status_code}: {body.get('error', 'request rejected')}"
        return body

    def get(self, args: GetFinanceRecordArgs) -> dict[str, Any]:
        try:
            if args.record_id is not None:
                resp = self.http.get(f"/finance/invoices/{args.record_id}")
                body = _json(resp)
            elif args.invoice_number:
                resp = self.http.get("/finance/invoices", params={"invoice_number": args.invoice_number})
                listing = _json(resp)
                records = listing.get("records", [])
                body = (
                    {"success": True, "record": records[0]}
                    if records
                    else {"success": False, "error": f"No record for {args.invoice_number}"}
                )
            else:
                return {"success": False, "error": "Provide record_id or invoice_number"}
        except httpx.HTTPError as exc:
            return {"success": False, "error": f"Finance system unreachable: {exc}"}
        body["http_status"] = resp.status_code
        if body.get("success"):
            r = body["record"]
            body["summary"] = f"Retrieved record #{r['id']}: {r['invoice_number']}, amount {r['amount']}, due {r['due_date']}"
        else:
            body.setdefault("summary", body.get("error", "lookup failed"))
        return body


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {"success": False, "error": "Unexpected response"}
    except ValueError:
        return {"success": False, "error": f"Non-JSON response (HTTP {resp.status_code})"}


# ------------------------------------------------------------ verification
def verify_record(source: dict[str, Any] | None, record: dict[str, Any] | None) -> dict[str, Any]:
    """Independent comparison of the stored record against the source document."""
    if not source or not record:
        return {
            "verified": False,
            "summary": "Cannot verify: source invoice or stored record is missing",
            "fields": [],
        }
    src_amount = normalize_amount(source.get("amount"))
    rec_amount = normalize_amount(record.get("amount"))
    checks = [
        ("invoice_number", source.get("invoice_number"), record.get("invoice_number"),
         str(source.get("invoice_number", "")).strip() == str(record.get("invoice_number", "")).strip()),
        ("company", source.get("company"), record.get("company"),
         str(source.get("company", "")).strip().casefold() == str(record.get("company", "")).strip().casefold()),
        ("amount", source.get("amount"), record.get("amount"),
         src_amount is not None and rec_amount is not None and abs(src_amount - rec_amount) < 0.005),
        ("due_date", source.get("due_date"), record.get("due_date"),
         str(source.get("due_date")) == str(record.get("due_date"))),
    ]
    fields = [{"field": f, "source": s, "stored": r, "match": m} for f, s, r, m in checks]
    ok = all(c["match"] for c in fields)
    mismatched = [c["field"] for c in fields if not c["match"]]
    return {
        "verified": ok,
        "record_id": record.get("id"),
        "fields": fields,
        "checked_at": now_iso(),
        "summary": (
            f"All {len(fields)} fields match between source invoice and finance record #{record.get('id')}"
            if ok
            else f"Mismatch in: {', '.join(mismatched)}"
        ),
    }


# --------------------------------------------------------------- state hooks
def on_enter_result(state: AgentState, args: EnterFinanceRecordArgs, result: dict[str, Any]) -> None:
    d = state.discovered_information
    if result.get("success"):
        d["finance_record_id"] = result["record_id"]
        d["submitted_amount"] = _plain(normalize_amount(args.amount))
    elif result.get("existing_record_id"):
        d["finance_record_id"] = result["existing_record_id"]
        d["duplicate_detected"] = True


def on_get_result(state: AgentState, args: GetFinanceRecordArgs, result: dict[str, Any]) -> None:
    if not result.get("success"):
        return
    record = result["record"]
    state.discovered_information["finance_record"] = record
    source = state.discovered_information.get("source_invoice")
    verification = verify_record(source, record)
    state.verification_result = verification
    result["verification"] = {"verified": verification["verified"], "summary": verification["summary"]}
    if verification["verified"]:
        state.evidence = {
            "finance_record_id": record["id"],
            "invoice_number": record["invoice_number"],
            "company": record["company"],
            "amount": record["amount"],
            "amount_display": format_inr(normalize_amount(record["amount"])),
            "due_date": record["due_date"],
            "source_document": (source or {}).get("invoice_id"),
            "comparison": verification["fields"],
            "method": "Source invoice + finance record field-by-field comparison",
        }


def make_approval_check(threshold: float):
    def check(args: EnterFinanceRecordArgs) -> tuple[str, str] | None:
        value = normalize_amount(args.amount)
        if value is not None and value > threshold:
            return (
                f"finance_record:{args.invoice_number}",
                f"Human approval required: invoice amount is {format_inr(value)}. Submit this record?",
            )
        return None

    return check
