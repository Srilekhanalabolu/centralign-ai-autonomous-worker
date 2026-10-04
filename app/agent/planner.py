"""Decision policies. Both implement the same interface and plug into the same controller:

    understand(goal, catalog) -> Understanding
    decide(state, catalog)    -> AgentAction   (exactly one next action)

* LLMPolicy   – OpenAI-compatible chat model with JSON output.
* DemoPolicy  – deterministic, state-driven decisions for reproducible demos without an API key.
               It is NOT a fixed script: every decision is derived from the current state and the
               latest observation (e.g. it only normalises the amount after the finance system
               rejects it, and only retrieves an existing record after a duplicate is reported).
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from app.tools.finance import normalize_amount

from .prompts import DECIDE_SYSTEM, DECIDE_USER, UNDERSTAND_SYSTEM
from .state import AgentAction, AgentState, PlanStep, Understanding

SPECIAL_ACTIONS = ["request_human_approval", "complete", "fail"]


# =========================================================== deterministic policy
class DemoPolicy:
    name = "demo"

    _COMPANY_RE = re.compile(r"\bfrom\s+([A-Z][\w&.\-]*(?:\s+(?:[A-Z][\w&.\-]*|&))*)")

    def understand(self, goal: str, catalog: list[dict[str, Any]]) -> Understanding:
        match = self._COMPANY_RE.search(goal)
        if not match:
            loose = re.search(r"\bfrom\s+([\w&.\-]+)", goal, re.IGNORECASE)
            company = loose.group(1).title() if loose else None
        else:
            company = match.group(1).strip(" .,")
        latest = bool(re.search(r"latest|newest|most recent|last", goal, re.IGNORECASE))
        who = company or "the requested company"
        return Understanding(
            interpreted_goal=(
                f"Locate the {'most recent ' if latest else ''}invoice from {who}, extract its invoice number, "
                f"amount and due date, record it in the internal finance system, and confirm the stored record "
                f"matches the source document."
            ),
            entities={"company": company, "document": "invoice", "selection": "latest" if latest else "any"},
            success_criteria=[
                "Correct (latest) source invoice identified",
                "Finance record created and accepted by the finance system",
                "Stored invoice number, company, amount and due date match the source",
            ],
            plan=[
                PlanStep(description=f"Search the invoice repository for {who}", tool="search_invoices"),
                PlanStep(description="Open the latest invoice and extract key fields", tool="read_invoice"),
                PlanStep(description="Enter the record into the finance system", tool="enter_finance_record"),
                PlanStep(description="Retrieve the stored record and verify it against the source", tool="get_finance_record"),
                PlanStep(description="Report completion with evidence", tool="complete"),
            ],
        )

    def decide(self, state: AgentState, catalog: list[dict[str, Any]]) -> AgentAction:
        d = state.discovered_information
        company = (d.get("goal_entities") or {}).get("company")
        last = state.observations[-1] if state.observations else None

        if not company:
            return AgentAction(action="fail", arguments={"summary": "Could not identify which company the request refers to."},
                               reason="The request does not name a company, so I cannot safely choose a document.")

        # 1. Locate candidate documents
        if "invoice_candidates" not in d:
            return AgentAction(action="search_invoices", arguments={"company": company, "query": "latest invoice"},
                               reason=f"Searching for the most recent invoice matching {company}.")
        candidates = d["invoice_candidates"]
        if not candidates:
            return AgentAction(action="fail", arguments={"summary": f"No invoices from {company} exist in the repository."},
                               reason=f"The repository returned no invoices for {company}; nothing to process.")

        # 2. Extract source data from the newest document
        source = d.get("source_invoice")
        if not source:
            latest = max(candidates, key=lambda c: c.get("issue_date") or "")
            return AgentAction(action="read_invoice", arguments={"invoice_id": latest["invoice_id"]},
                               reason=f"Opening {latest['invoice_number']} (issued {latest['issue_date']}), the newest of {len(candidates)} result(s).")

        if state.approval_status == "rejected":
            return AgentAction(action="fail", arguments={"summary": "A human reviewer rejected submission of this record."},
                               reason="Approval was rejected, so the record must not be submitted.")

        # 3. Write to the finance system, adapting to its feedback
        record_id = d.get("finance_record_id")
        if not record_id:
            enter_attempts = [o for o in state.observations if o.action == "enter_finance_record"]
            amount_rejected = any(
                not o.success and "numeric" in f"{o.result.get('error', '')} {o.result.get('hint', '')}".lower()
                for o in enter_attempts
            )
            if last and last.action == "enter_finance_record" and not last.success and not amount_rejected \
                    and not last.result.get("approval_required"):
                return AgentAction(action="fail", arguments={"summary": f"Finance system rejected the record: {last.result.get('error')}"},
                                   reason="The finance system returned an error I cannot correct automatically.")
            amount: Any = source["amount"]
            reason = f"Submitting {source['invoice_number']} to the finance system using the values extracted from the invoice."
            if amount_rejected:
                value = normalize_amount(source["amount"])
                amount = int(value) if value is not None and value.is_integer() else value
                reason = (f"Finance system rejected the currency-formatted amount ('{source['amount']}'). "
                          f"Retrying with normalized numeric value {amount}.")
            elif last and last.action == "request_human_approval" and last.success:
                reason = "Human approval received; submitting the record."
            return AgentAction(
                action="enter_finance_record",
                arguments={
                    "invoice_number": source["invoice_number"],
                    "company": source["company"],
                    "amount": amount,
                    "due_date": source["due_date"],
                    "currency": source.get("currency", "INR"),
                    "source_invoice_id": source.get("invoice_id"),
                },
                reason=reason,
            )

        # 4. Never trust the write: read it back and verify
        verification = state.verification_result
        if not verification:
            why = ("The finance system reports this invoice already exists; retrieving the existing record to verify it."
                   if d.get("duplicate_detected") else
                   "Record saved; retrieving it to compare stored values with the source invoice.")
            return AgentAction(action="get_finance_record", arguments={"record_id": record_id}, reason=why)

        if verification.get("verified"):
            r = d.get("finance_record", {})
            prefix = "Existing" if d.get("duplicate_detected") else "Finance"
            return AgentAction(
                action="complete",
                arguments={"summary": f"{prefix} record #{r.get('id')} for {r.get('invoice_number')} ({r.get('company')}) verified against the source invoice."},
                reason="All compared fields match; the task is complete.",
            )
        return AgentAction(action="fail", arguments={"summary": f"Verification failed: {verification.get('summary')}"},
                           reason="Stored record does not match the source invoice; escalating instead of reporting success.")


# =================================================================== LLM policy
def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Model response did not contain a JSON object")
    return json.loads(text[start : end + 1])


def _truncate(value: Any, limit: int = 1500) -> Any:
    raw = json.dumps(value, ensure_ascii=False, default=str)
    return value if len(raw) <= limit else raw[:limit] + "…"


def state_for_model(state: AgentState) -> dict[str, Any]:
    """Compact, structured view of the state that the model decides from."""
    return {
        "goal": state.original_goal,
        "interpreted_goal": state.interpreted_goal,
        "plan": [{"step": p.description, "tool": p.tool, "done": p.done} for p in state.plan],
        "step": state.step_count,
        "discovered_information": _truncate(state.discovered_information, 3000),
        "approval_status": state.approval_status,
        "verification_result": state.verification_result and {
            "verified": state.verification_result.get("verified"),
            "summary": state.verification_result.get("summary"),
        },
        "retry_count": state.retry_count,
        "recent_observations": [
            {"action": o.action, "arguments": o.arguments, "success": o.success, "result": _truncate(o.result)}
            for o in state.observations[-6:]
        ],
    }


class LLMPolicy:
    name = "llm"

    def __init__(self, api_key: str, model: str, base_url: str, threshold: float, timeout: float = 45.0) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.threshold = threshold
        self.timeout = timeout

    def _chat(self, system: str, user: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"{self.base_url}/chat/completions"
        resp = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
        if resp.status_code == 400:  # some compatible providers do not support response_format
            payload.pop("response_format")
            resp = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return _parse_json(content)

    def understand(self, goal: str, catalog: list[dict[str, Any]]) -> Understanding:
        names = ", ".join(t["name"] for t in catalog)
        data = self._chat(UNDERSTAND_SYSTEM.format(tool_names=names), f"Request: {goal}")
        return Understanding.model_validate(data)

    def decide(self, state: AgentState, catalog: list[dict[str, Any]]) -> AgentAction:
        system = DECIDE_SYSTEM.format(
            catalog=json.dumps(catalog, indent=1), threshold=f"INR {self.threshold:,.0f}"
        )
        user = DECIDE_USER.format(state_json=json.dumps(state_for_model(state), ensure_ascii=False, default=str))
        data = self._chat(system, user)
        if not isinstance(data.get("arguments"), dict):
            data["arguments"] = {}
        return AgentAction.model_validate(data)
