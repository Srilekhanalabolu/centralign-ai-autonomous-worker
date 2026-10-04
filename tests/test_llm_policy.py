"""Exercise the LLM code path end-to-end with a fake OpenAI-compatible endpoint.

The fake model answers with the deterministic policy's decision, serialised as JSON in a chat
completion (wrapped in a ```json fence, as real models often do). This checks request shape,
JSON parsing and that LLM decisions drive the very same controller and real tools."""
import json as jsonlib

import httpx

from app.agent import planner
from app.agent.planner import DemoPolicy

ACME = "Find the latest invoice from Acme Corp, enter it into the finance system and verify it."


def test_llm_policy_drives_controller(app, settings, monkeypatch):
    settings.llm_api_key = "sk-test"
    demo = DemoPolicy()
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        assert url.endswith("/chat/completions") and headers["Authorization"] == "Bearer sk-test"
        system, user = json["messages"][0]["content"], json["messages"][1]["content"]
        if "planning module" in system:
            content = demo.understand(ACME, []).model_dump_json()
        else:
            assert "enter_finance_record" in system  # tool catalog is provided to the model
            state_view = jsonlib.loads(user.split("\n", 1)[1].rsplit("\n\nChoose", 1)[0])
            assert "recent_observations" in state_view
            state = app.state.manager.tasks[next(reversed(app.state.manager.tasks))].state
            content = "```json\n" + demo.decide(state, []).model_dump_json() + "\n```"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(planner.httpx, "post", fake_post)
    record = app.state.manager.run_sync(ACME, mode="llm")
    assert record.state.mode == "llm"
    assert record.state.status == "COMPLETED"
    assert not any(e.title == "Model fallback" for e in record.events)
    assert len(calls) >= 6
