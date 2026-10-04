"""The agent controller: Goal → Understand → Plan → (Decide → Execute → Observe → Update)* → Verify → Complete.

The controller is task-agnostic. It knows nothing about invoices: it asks a policy for ONE next
action, executes it through the tool registry, records the observation, and enforces runtime
guarantees that must not depend on the model:

  * only registered tools can run, with schema-validated arguments
  * human approval is enforced by the runtime (approval_check guardrail), not left to the model
  * retries are bounded
  * "complete" is refused unless an independent verification has passed
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from pydantic import ValidationError

from app.config import Settings
from app.tools.registry import ToolRegistry

from .planner import SPECIAL_ACTIONS, DemoPolicy
from .state import AgentAction, AgentState, Event, Observation, now_iso

log = logging.getLogger("centralign.agent")


class TaskRecord:
    """A running task: state + event log + approval channel. All mutations happen under `lock`."""

    def __init__(self, state: AgentState) -> None:
        self.state = state
        self.events: list[Event] = []
        self.lock = threading.RLock()
        self.approval_signal = threading.Event()
        self.approval_decision: bool | None = None
        self.approval_note: str = ""

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "state": self.state.model_dump(mode="json"),
                "events": [e.model_dump(mode="json") for e in self.events],
            }


class AgentController:
    def __init__(self, task: TaskRecord, policy: Any, registry: ToolRegistry, settings: Settings) -> None:
        self.t = task
        self.policy = policy
        self.fallback = DemoPolicy()
        self.registry = registry
        self.settings = settings
        self.catalog = registry.catalog()

    # ------------------------------------------------------------- events
    def emit(self, type_: str, title: str, status: str = "SUCCESS", summary: str = "",
             action: str | None = None, details: dict[str, Any] | None = None) -> Event:
        with self.t.lock:
            ev = Event(id=len(self.t.events) + 1, type=type_, title=title, status=status, summary=summary,
                       action=action, details=details, step=self.t.state.step_count or None)
            self.t.events.append(ev)
        log.info("[%s] %s %s — %s", self.t.state.task_id, status, title, summary)
        return ev

    def update(self, ev: Event, **fields: Any) -> None:
        with self.t.lock:
            for k, v in fields.items():
                setattr(ev, k, v)
            ev.timestamp = now_iso()

    def _pause(self) -> None:
        if self.settings.step_delay > 0:
            time.sleep(self.settings.step_delay)

    # ---------------------------------------------------------- main loop
    def run(self) -> AgentState:
        st = self.t.state
        try:
            self._understand()
            while st.status in ("RUNNING", "WAITING_FOR_APPROVAL"):
                if st.step_count >= self.settings.max_steps:
                    self._finish(False, f"Step budget of {self.settings.max_steps} actions exhausted without verified completion.")
                    break
                with self.t.lock:
                    st.step_count += 1
                action = self._decide()
                self._act(action)
                self._pause()
        except Exception as exc:  # never leave a task hanging in RUNNING
            log.exception("Agent crashed")
            with self.t.lock:
                st.errors.append({"type": "internal", "error": str(exc), "at": now_iso()})
            self._finish(False, f"Internal error: {exc}")
        return st

    # ------------------------------------------------------------ phases
    def _call_policy(self, method: str, *args: Any) -> Any:
        try:
            return getattr(self.policy, method)(*args)
        except Exception as exc:
            if self.policy is self.fallback:
                raise
            with self.t.lock:
                self.t.state.errors.append({"type": "model", "error": str(exc)[:300], "at": now_iso()})
            self.emit("decision", "Model fallback", "RECOVERED",
                      f"LLM call failed ({type(exc).__name__}); using deterministic policy for this step.")
            return getattr(self.fallback, method)(*args)

    def _understand(self) -> None:
        st = self.t.state
        self.emit("goal", "Goal received", "SUCCESS", st.original_goal)
        self._pause()
        understanding = self._call_policy("understand", st.original_goal, self.catalog)
        with self.t.lock:
            st.interpreted_goal = understanding.interpreted_goal
            st.success_criteria = understanding.success_criteria
            st.plan = understanding.plan
            st.discovered_information["goal_entities"] = understanding.entities
        self.emit("goal", "Goal understood", "SUCCESS", understanding.interpreted_goal,
                  details={"entities": understanding.entities, "success_criteria": understanding.success_criteria})
        self._pause()
        self.emit("plan", "Plan created", "SUCCESS",
                  f"{len(understanding.plan)}-step plan; each next action is decided from live observations.",
                  details={"plan": [p.model_dump() for p in understanding.plan]})
        self._pause()

    def _decide(self) -> AgentAction:
        st = self.t.state
        action = self._call_policy("decide", st, self.catalog)
        last = st.observations[-1] if st.observations else None
        if last is not None and not last.success and action.action not in ("fail",):
            self.emit("recovery", "Agent analyzed failure", "SUCCESS", action.reason, action=action.action,
                      details={"failed_action": last.action, "observed_error": last.result.get("error"),
                               "hint": last.result.get("hint"), "next_action": action.model_dump()})
            self._pause()
        return action

    def _observe(self, action: str, arguments: dict[str, Any], result: dict[str, Any]) -> Observation:
        st = self.t.state
        success = bool(result.get("success", False))
        obs = Observation(step=st.step_count, action=action, arguments=arguments, success=success, result=result)
        with self.t.lock:
            st.observations.append(obs)
            st.tool_results.append({"step": st.step_count, "action": action, "success": success,
                                    "summary": result.get("summary") or result.get("error")})
            if not success:
                st.errors.append({"step": st.step_count, "action": action, "error": result.get("error"),
                                  "hint": result.get("hint"), "at": obs.timestamp})
            else:
                self._mark_plan(action)
        return obs

    def _mark_plan(self, action: str) -> None:
        st = self.t.state
        for step in st.plan:
            if not step.done and step.tool == action:
                step.done = True
                st.completed_steps.append(step.description)
                break
        st.current_step = next((i for i, s in enumerate(st.plan) if not s.done), len(st.plan))

    # ------------------------------------------------------------ actions
    def _act(self, action: AgentAction) -> None:
        name = action.action
        if name == "complete":
            return self._complete(action)
        if name == "fail":
            summary = str(action.arguments.get("summary") or action.reason or "Agent stopped.")
            self.emit("complete", "Agent stopped: task cannot be completed", "FAILED", summary, action="fail")
            return self._finish(False, summary)
        if name == "request_human_approval":
            reason = str(action.arguments.get("reason") or action.reason)
            approved = self._request_approval("*", reason, action.arguments)
            self._observe(name, action.arguments, {"success": approved, "approved": approved,
                                                   "note": self.t.approval_note,
                                                   "error": None if approved else "Rejected by human reviewer"})
            return

        spec = self.registry.get(name)
        if spec is None or name in SPECIAL_ACTIONS:
            self.emit("tool_call", f"Rejected unknown action '{name}'", "FAILED",
                      "Only registered tools may be executed.", action=name)
            self._observe(name, action.arguments, {"success": False, "error": f"Unknown action '{name}'",
                                                   "hint": f"Use one of: {', '.join(self.registry.names() + SPECIAL_ACTIONS)}"})
            return self._check_retry_budget(name)

        try:
            args = spec.args_model.model_validate(action.arguments)
        except ValidationError as exc:
            self.emit("tool_call", f"Invalid arguments for {name}", "FAILED", str(exc.errors()[:2]), action=name)
            self._observe(name, action.arguments, {"success": False, "error": "Invalid arguments",
                                                   "details": exc.errors()[:3]})
            return self._check_retry_budget(name)

        prior = [o for o in self.t.state.observations if o.action == name]
        is_retry = bool(prior) and not prior[-1].success
        title = ("Retrying: " if is_retry else "") + spec.running_title
        ev = self.emit("tool_call", title, "RUNNING", action.reason, action=name,
                       details={"arguments": args.model_dump()})

        # Runtime guardrail: human approval is enforced here, regardless of what the policy decided.
        if spec.approval_check:
            check = spec.approval_check(args)
            approved_scopes = self.t.state.approved_scopes
            if check and check[0] not in approved_scopes and "*" not in approved_scopes:
                self.update(ev, status="WAITING_FOR_APPROVAL", summary=f"Paused by policy guardrail: {check[1]}")
                if not self._request_approval(check[0], check[1], args.model_dump()):
                    result = {"success": False, "error": "Human reviewer rejected this action",
                              "approval_rejected": True, "summary": "Not submitted: approval rejected"}
                    self.update(ev, status="FAILED", title=f"{spec.running_title} — blocked", summary=result["summary"])
                    self._observe(name, args.model_dump(), result)
                    return
                self.update(ev, status="RUNNING", summary=action.reason)

        self._pause()
        try:
            result = spec.func(args)
        except Exception as exc:  # tool crash becomes an observation, not a controller crash
            result = {"success": False, "error": f"Tool error: {exc}"}
        with self.t.lock:
            if spec.on_result:
                spec.on_result(self.t.state, args, result)
            if is_retry:
                self.t.state.retry_count += 1
        obs = self._observe(name, args.model_dump(), result)

        status = ("RECOVERED" if is_retry else "SUCCESS") if obs.success else "FAILED"
        new_title = title if obs.success else f"{spec.running_title} — rejected"
        self.update(ev, status=status, title=new_title,
                    summary=result.get("summary") or result.get("error") or "",
                    details={"arguments": args.model_dump(), "result": _compact(result), "reason": action.reason})

        if "verification" in result:
            vr = self.t.state.verification_result or {}
            self.emit("verification", "Verification completed" if vr.get("verified") else "Verification failed",
                      "VERIFIED" if vr.get("verified") else "FAILED", vr.get("summary", ""),
                      action=name, details={"fields": vr.get("fields", [])})
        if not obs.success:
            self._check_retry_budget(name)

    def _check_retry_budget(self, name: str) -> None:
        trailing = 0
        for o in reversed(self.t.state.observations):
            if o.action != name or o.success:
                break
            trailing += 1
        if trailing > self.settings.max_retries:
            self.emit("complete", "Retry budget exhausted", "FAILED",
                      f"{name} failed {trailing} times in a row; escalating to a human.", action=name)
            self._finish(False, f"{name} kept failing after {self.settings.max_retries} retries.")

    def _request_approval(self, scope: str, message: str, details: dict[str, Any]) -> bool:
        st = self.t.state
        self.t.approval_signal.clear()
        self.t.approval_decision = None
        with self.t.lock:
            st.status = "WAITING_FOR_APPROVAL"
            st.approval_required = True
            st.approval_status = "pending"
            st.approval_request = {"scope": scope, "message": message, "details": details, "requested_at": now_iso()}
        ev = self.emit("approval", "Waiting for human approval", "WAITING_FOR_APPROVAL", message,
                       action="request_human_approval", details=details)
        got = self.t.approval_signal.wait(timeout=self.settings.approval_timeout)
        approved = bool(got and self.t.approval_decision)
        with self.t.lock:
            st.status = "RUNNING"
            st.approval_status = "approved" if approved else "rejected"
            if approved:
                st.approved_scopes.append(scope)
        self.update(ev, status="SUCCESS" if approved else "FAILED",
                    title="Approved by human reviewer" if approved else
                    ("Rejected by human reviewer" if got else "Approval timed out"),
                    summary=message + (f" — note: {self.t.approval_note}" if self.t.approval_note else ""))
        return approved

    def _complete(self, action: AgentAction) -> None:
        st = self.t.state
        vr = st.verification_result
        if not vr or not vr.get("verified"):
            self.emit("verification", "Completion blocked: result not verified", "FAILED",
                      "The runtime refuses to report success without verified evidence.", action="complete")
            self._observe("complete", action.arguments, {
                "success": False, "error": "Completion blocked: no verified evidence yet",
                "hint": "Retrieve the stored record (get_finance_record) so it can be verified against the source"})
            return self._check_retry_budget("complete")
        with self.t.lock:
            self._mark_plan("complete")
        summary = str(action.arguments.get("summary") or action.reason or "Task completed.")
        self.emit("complete", "Task completed", "VERIFIED", summary, action="complete",
                  details={"evidence": st.evidence})
        self._finish(True, summary)

    def _finish(self, success: bool, summary: str) -> None:
        st = self.t.state
        with self.t.lock:
            st.status = "COMPLETED" if success else "FAILED"
            st.finished_at = now_iso()
            st.final_result = {
                "status": "TASK COMPLETED" if success else "TASK FAILED",
                "summary": summary,
                "verified": bool(success and st.verification_result and st.verification_result.get("verified")),
                "evidence": st.evidence if success else None,
                "steps_taken": st.step_count,
                "retries": st.retry_count,
                "errors_recovered": len([e for e in st.errors if e.get("action")]) if success else None,
            }


def _compact(result: dict[str, Any]) -> dict[str, Any]:
    out = dict(result)
    if isinstance(out.get("invoice"), dict):
        out["invoice"] = {k: v for k, v in out["invoice"].items() if k != "line_items"}
    return out
