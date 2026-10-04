"""FastAPI entrypoint: task API, sandbox finance app, and the dashboard UI."""
from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agent.manager import TaskManager
from app.config import STATIC_DIR, Settings
from app.finance_app.api import create_finance_router
from app.finance_app.store import FinanceStore
from app.models.schemas import ApprovalRequest, CreateTaskRequest, CreateTaskResponse
from app.tools import build_registry
from app.tools.files import InvoiceRepository

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="CentrAlign AI — Autonomous Company Task Worker", version="1.0.0")

    store = FinanceStore(settings.finance_db_path)
    app.include_router(create_finance_router(store))

    # The agent reaches the finance system over HTTP, like any internal app.
    # Tests swap `app.state.finance_http` for an in-process client.
    app.state.finance_http = httpx.Client(base_url=settings.finance_api_url, timeout=10)

    def registry_factory():
        return build_registry(settings.invoices_dir, app.state.finance_http, settings.approval_threshold)

    manager = TaskManager(settings, registry_factory)
    app.state.settings = settings
    app.state.store = store
    app.state.manager = manager
    repo = InvoiceRepository(settings.invoices_dir)

    # ------------------------------------------------------------ task API
    @app.post("/api/tasks", response_model=CreateTaskResponse, status_code=202)
    def create_task(req: CreateTaskRequest) -> CreateTaskResponse:
        record = manager.start(req.goal, req.mode)
        return CreateTaskResponse(task_id=record.state.task_id, status=record.state.status, mode=record.state.mode)

    @app.get("/api/tasks")
    def list_tasks() -> dict[str, Any]:
        return {"tasks": manager.list()}

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> dict[str, Any]:
        record = manager.get(task_id)
        if record is None:
            raise HTTPException(404, "Task not found")
        return record.snapshot()

    @app.get("/api/tasks/{task_id}/events")
    def get_events(task_id: str, since: int = 0) -> dict[str, Any]:
        record = manager.get(task_id)
        if record is None:
            raise HTTPException(404, "Task not found")
        events = record.snapshot()["events"]
        return {"task_id": task_id, "status": record.state.status, "events": [e for e in events if e["id"] > since]}

    @app.post("/api/tasks/{task_id}/approve")
    def approve(task_id: str, req: ApprovalRequest | None = None) -> dict[str, Any]:
        req = req or ApprovalRequest()
        if not manager.decide_approval(task_id, req.approved, req.note):
            raise HTTPException(409, "No approval is pending for this task")
        return {"task_id": task_id, "approved": req.approved}

    @app.post("/api/tasks/{task_id}/reject")
    def reject(task_id: str) -> dict[str, Any]:
        if not manager.decide_approval(task_id, False, "Rejected from dashboard"):
            raise HTTPException(409, "No approval is pending for this task")
        return {"task_id": task_id, "approved": False}

    # ------------------------------------------------------ sandbox helpers
    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return {
            "mode": "llm" if settings.llm_enabled else "demo",
            "model": settings.llm_model if settings.llm_enabled else "deterministic-demo-policy",
            "approval_threshold": settings.approval_threshold,
        }

    @app.get("/api/invoices")
    def invoices() -> dict[str, Any]:
        docs = repo.list_all()
        return {"invoices": [{k: d.get(k) for k in ("invoice_id", "invoice_number", "company", "amount",
                                                      "issue_date", "due_date", "status")} for d in docs]}

    @app.post("/api/sandbox/reset")
    def reset_sandbox() -> dict[str, Any]:
        store.reset()
        return {"success": True, "message": "Finance sandbox cleared"}

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # ------------------------------------------------------------- UI
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
