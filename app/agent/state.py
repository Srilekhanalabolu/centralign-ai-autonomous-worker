"""Structured execution state. The agent reasons over this state, not over a chat transcript."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

TaskStatus = Literal["RUNNING", "WAITING_FOR_APPROVAL", "COMPLETED", "FAILED"]
EventStatus = Literal["RUNNING", "SUCCESS", "FAILED", "RECOVERED", "WAITING_FOR_APPROVAL", "VERIFIED"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class PlanStep(BaseModel):
    description: str
    tool: str = ""
    done: bool = False


class AgentAction(BaseModel):
    """One decision of the agent: a single registered action + arguments + a short, auditable reason."""

    action: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class Understanding(BaseModel):
    interpreted_goal: str
    entities: dict[str, Any] = Field(default_factory=dict)
    success_criteria: list[str] = Field(default_factory=list)
    plan: list[PlanStep] = Field(default_factory=list)


class Observation(BaseModel):
    step: int
    action: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    success: bool
    result: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=now_iso)


class Event(BaseModel):
    id: int
    timestamp: str = Field(default_factory=now_iso)
    type: str  # goal | plan | decision | tool_call | recovery | approval | verification | complete | error
    action: str | None = None
    status: EventStatus = "SUCCESS"
    title: str
    summary: str = ""
    details: dict[str, Any] | None = None
    step: int | None = None


class AgentState(BaseModel):
    task_id: str
    mode: str = "demo"  # "llm" or "demo"
    model: str | None = None
    status: TaskStatus = "RUNNING"
    original_goal: str
    interpreted_goal: str = ""
    success_criteria: list[str] = Field(default_factory=list)
    plan: list[PlanStep] = Field(default_factory=list)
    current_step: int = 0
    completed_steps: list[str] = Field(default_factory=list)
    step_count: int = 0
    observations: list[Observation] = Field(default_factory=list)
    discovered_information: dict[str, Any] = Field(default_factory=dict)
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    retry_count: int = 0
    approval_required: bool = False
    approval_status: Literal["not_required", "pending", "approved", "rejected"] = "not_required"
    approval_request: dict[str, Any] | None = None
    approved_scopes: list[str] = Field(default_factory=list)
    verification_result: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    final_result: dict[str, Any] | None = None
    created_at: str = Field(default_factory=now_iso)
    finished_at: str | None = None
