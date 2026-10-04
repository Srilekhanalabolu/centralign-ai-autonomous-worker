"""Invoice repository tools: search and read documents in the sandboxed document store."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.agent.state import AgentState

_SAFE_ID = re.compile(r"^[a-z0-9_\-]{1,80}$")
_LATEST_WORDS = {"latest", "newest", "most recent", "recent", "last"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


class SearchInvoicesArgs(BaseModel):
    company: str = Field(..., description="Company / vendor name to search for, e.g. 'Acme Corp'")
    query: str = Field("", description="Free-text intent, e.g. 'latest invoice'")


class ReadInvoiceArgs(BaseModel):
    invoice_id: str = Field(..., description="invoice_id returned by search_invoices")


class InvoiceRepository:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def _load_all(self) -> list[dict[str, Any]]:
        docs = []
        for path in sorted(self.root.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            doc["invoice_id"] = path.stem
            docs.append(doc)
        return docs

    def list_all(self) -> list[dict[str, Any]]:
        return sorted(self._load_all(), key=lambda d: d.get("issue_date", ""), reverse=True)

    # ---------------------------------------------------------------- tools
    def search(self, args: SearchInvoicesArgs) -> dict[str, Any]:
        target = _norm(args.company)
        if not target:
            return {"success": False, "error": "company must not be empty", "results": []}
        matches = []
        for doc in self._load_all():
            company = _norm(doc.get("company", ""))
            if target in company or company in target:
                matches.append(doc)
        matches.sort(key=lambda d: d.get("issue_date", ""), reverse=True)
        results = [
            {
                "invoice_id": d["invoice_id"],
                "invoice_number": d.get("invoice_number"),
                "company": d.get("company"),
                "issue_date": d.get("issue_date"),
                "status": d.get("status"),
                "is_latest": i == 0,
            }
            for i, d in enumerate(matches)
        ]
        if not results:
            return {
                "success": True,
                "count": 0,
                "results": [],
                "summary": f"No invoices found for '{args.company}'",
            }
        latest = results[0]
        wants_latest = any(w in args.query.lower() for w in _LATEST_WORDS)
        summary = f"Found {len(results)} invoice(s) for {latest['company']}"
        if wants_latest or len(results) > 1:
            summary += f"; latest is {latest['invoice_number']} (issued {latest['issue_date']})"
        return {"success": True, "count": len(results), "results": results, "summary": summary}

    def read(self, args: ReadInvoiceArgs) -> dict[str, Any]:
        invoice_id = args.invoice_id.strip().removesuffix(".json")
        if not _SAFE_ID.match(invoice_id):
            return {"success": False, "error": "Invalid invoice_id", "hint": "Use an invoice_id from search_invoices"}
        path = (self.root / f"{invoice_id}.json").resolve()
        if path.parent != self.root or not path.exists():  # sandbox: no path traversal
            return {"success": False, "error": f"Invoice '{invoice_id}' not found", "hint": "Search again"}
        doc = json.loads(path.read_text(encoding="utf-8"))
        extracted = {
            "invoice_id": invoice_id,
            "invoice_number": doc.get("invoice_number"),
            "company": doc.get("company"),
            "amount": doc.get("amount"),
            "currency": doc.get("currency", "INR"),
            "issue_date": doc.get("issue_date"),
            "due_date": doc.get("due_date"),
            "purchase_order": doc.get("purchase_order"),
            "line_items": doc.get("line_items", []),
        }
        return {
            "success": True,
            "invoice": extracted,
            "summary": (
                f"Extracted {extracted['invoice_number']}: amount {extracted['amount']}, "
                f"due {extracted['due_date']}"
            ),
        }


# ---------------------------------------------------------------- state hooks
def on_search_result(state: AgentState, args: SearchInvoicesArgs, result: dict[str, Any]) -> None:
    if result.get("success"):
        state.discovered_information["invoice_candidates"] = result.get("results", [])


def on_read_result(state: AgentState, args: ReadInvoiceArgs, result: dict[str, Any]) -> None:
    if result.get("success"):
        state.discovered_information["source_invoice"] = result["invoice"]
