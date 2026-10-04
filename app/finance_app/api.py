"""HTTP API of the sandbox finance system. The agent talks to it over HTTP, like a real internal app."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

from .store import FinanceStore


def create_finance_router(store: FinanceStore) -> APIRouter:
    router = APIRouter(prefix="/finance", tags=["finance-sandbox"])

    @router.post("/invoices")
    def create_invoice(payload: dict[str, Any] = Body(...)) -> JSONResponse:
        status, body = store.create(payload)
        return JSONResponse(status_code=status, content=body)

    @router.get("/invoices")
    def list_invoices(invoice_number: str | None = None) -> dict[str, Any]:
        records = store.list(invoice_number)
        return {"success": True, "count": len(records), "records": records}

    @router.get("/invoices/{record_id}")
    def get_invoice(record_id: int) -> JSONResponse:
        record = store.get(record_id)
        if record is None:
            return JSONResponse(
                status_code=404, content={"success": False, "error": f"Finance record #{record_id} not found"}
            )
        return JSONResponse(content={"success": True, "record": record})

    return router
