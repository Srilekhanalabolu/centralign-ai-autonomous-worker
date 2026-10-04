"""Request/response schemas for the public task API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CreateTaskRequest(BaseModel):
    goal: str = Field(..., min_length=5, max_length=2000)
    mode: Literal["auto", "llm", "demo"] = "auto"


class CreateTaskResponse(BaseModel):
    task_id: str
    status: str
    mode: str


class ApprovalRequest(BaseModel):
    approved: bool = True
    note: str = ""
