"""Task manager: creates tasks, picks the decision policy, runs each task on a background thread."""
from __future__ import annotations

import threading
import uuid
from typing import Callable

from app.config import Settings
from app.tools.registry import ToolRegistry

from .controller import AgentController, TaskRecord
from .planner import DemoPolicy, LLMPolicy
from .state import AgentState


class TaskManager:
    def __init__(self, settings: Settings, registry_factory: Callable[[], ToolRegistry]) -> None:
        self.settings = settings
        self.registry_factory = registry_factory
        self.tasks: dict[str, TaskRecord] = {}
        self._lock = threading.Lock()

    def _policy(self, mode: str):
        use_llm = mode == "llm" or (mode == "auto" and self.settings.llm_enabled)
        if use_llm and self.settings.llm_api_key:
            s = self.settings
            return LLMPolicy(s.llm_api_key, s.llm_model, s.llm_base_url, s.approval_threshold)
        return DemoPolicy()

    def _new(self, goal: str, mode: str) -> tuple[TaskRecord, AgentController]:
        policy = self._policy(mode)
        state = AgentState(
            task_id=uuid.uuid4().hex[:8],
            original_goal=goal.strip(),
            mode=policy.name,
            model=self.settings.llm_model if policy.name == "llm" else "deterministic-demo-policy",
        )
        record = TaskRecord(state)
        with self._lock:
            self.tasks[state.task_id] = record
        return record, AgentController(record, policy, self.registry_factory(), self.settings)

    def start(self, goal: str, mode: str = "auto") -> TaskRecord:
        record, controller = self._new(goal, mode)
        threading.Thread(target=controller.run, name=f"task-{record.state.task_id}", daemon=True).start()
        return record

    def run_sync(self, goal: str, mode: str = "auto") -> TaskRecord:
        record, controller = self._new(goal, mode)
        controller.run()
        return record

    def get(self, task_id: str) -> TaskRecord | None:
        return self.tasks.get(task_id)

    def decide_approval(self, task_id: str, approved: bool, note: str = "") -> bool:
        record = self.get(task_id)
        if record is None or record.state.approval_status != "pending":
            return False
        record.approval_decision = approved
        record.approval_note = note
        record.approval_signal.set()
        return True

    def list(self) -> list[dict]:
        out = []
        for r in list(self.tasks.values())[::-1]:
            s = r.state
            out.append({"task_id": s.task_id, "goal": s.original_goal, "status": s.status,
                        "mode": s.mode, "created_at": s.created_at})
        return out
