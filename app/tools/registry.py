"""Tool registry: the ONLY way the agent can affect the world.

Every tool has a typed argument schema (Pydantic), a callable, and optional hooks:
  * on_result      – records domain facts into the agent state (keeps the controller generic)
  * approval_check – runtime guardrail; returns (scope, message) if the call needs human approval
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel

from app.agent.state import AgentState

ToolFunc = Callable[[BaseModel], dict[str, Any]]
ResultHook = Callable[[AgentState, BaseModel, dict[str, Any]], None]
ApprovalCheck = Callable[[BaseModel], "tuple[str, str] | None"]


@dataclass
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    func: ToolFunc
    running_title: str
    on_result: ResultHook | None = None
    approval_check: ApprovalCheck | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def catalog(self) -> list[dict[str, Any]]:
        """Tool descriptions + JSON schemas, given to the LLM so it can only choose registered tools."""
        out = []
        for spec in self._tools.values():
            schema = spec.args_model.model_json_schema()
            out.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": {
                        "properties": schema.get("properties", {}),
                        "required": schema.get("required", []),
                    },
                }
            )
        return out
