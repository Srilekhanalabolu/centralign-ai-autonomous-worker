import threading
import time

ACME = ("Find the latest invoice from Acme Corp, extract the invoice number, amount and due date, enter it into "
        "the internal finance system, verify it was saved correctly, and tell me when it is done.")


def titles(record):
    return [e.title for e in record.events]


def test_agent_completes_with_recovery_and_verification(app):
    record = app.state.manager.run_sync(ACME, mode="demo")
    st = record.state
    assert st.status == "COMPLETED"
    assert st.verification_result["verified"] is True
    assert st.evidence["finance_record_id"] == 1842 and st.evidence["amount"] == 48500
    # the observed failure and the adapted retry are both in the trace
    enters = [o for o in st.observations if o.action == "enter_finance_record"]
    assert [o.success for o in enters] == [False, True]
    assert enters[0].arguments["amount"] == "₹48,500" and enters[1].arguments["amount"] == 48500
    assert st.retry_count == 1
    assert "Agent analyzed failure" in titles(record)
    assert any(e.status == "RECOVERED" for e in record.events)
    assert any(e.status == "VERIFIED" for e in record.events)
    # the record really exists in the finance DB
    assert app.state.store.get(1842)["invoice_number"] == "INV-2026-104"


def test_rerun_detects_duplicate_and_verifies_existing(app):
    app.state.manager.run_sync(ACME, mode="demo")
    second = app.state.manager.run_sync(ACME, mode="demo")
    assert second.state.status == "COMPLETED"
    assert second.state.discovered_information["duplicate_detected"] is True
    assert len(app.state.store.list()) == 1


def test_unknown_company_fails_honestly(app):
    record = app.state.manager.run_sync("Find the latest invoice from Umbrella Corp and add it to finance.", mode="demo")
    assert record.state.status == "FAILED"
    assert record.state.final_result["verified"] is False


def test_high_value_invoice_requires_approval(app):
    manager = app.state.manager
    result = {}
    t = threading.Thread(target=lambda: result.update(r=manager.run_sync(
        "Find the latest invoice from Globex and add it to finance.", mode="demo")))
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        pending = [r for r in manager.tasks.values() if r.state.approval_status == "pending"]
        if pending:
            break
        time.sleep(0.02)
    assert pending, "agent should pause for approval"
    assert "₹75,000" in pending[0].state.approval_request["message"]
    assert app.state.store.list() == []  # nothing written before approval
    assert manager.decide_approval(pending[0].state.task_id, True)
    t.join(5)
    st = result["r"].state
    assert st.status == "COMPLETED" and st.evidence["amount"] == 75000


def test_rejected_approval_stops_without_writing(app):
    manager = app.state.manager
    result = {}
    t = threading.Thread(target=lambda: result.update(r=manager.run_sync(
        "Find the latest invoice from Globex and add it to finance.", mode="demo")))
    t.start()
    for _ in range(250):
        pending = [r for r in manager.tasks.values() if r.state.approval_status == "pending"]
        if pending:
            break
        time.sleep(0.02)
    manager.decide_approval(pending[0].state.task_id, False)
    t.join(5)
    assert result["r"].state.status == "FAILED"
    assert app.state.store.list() == []


def test_api_endpoints(app):
    c = app.state.finance_http
    r = c.post("/api/tasks", json={"goal": ACME, "mode": "demo"})
    assert r.status_code == 202
    tid = r.json()["task_id"]
    for _ in range(200):
        state = c.get(f"/api/tasks/{tid}").json()["state"]
        if state["status"] in ("COMPLETED", "FAILED"):
            break
        time.sleep(0.05)
    assert state["status"] == "COMPLETED"
    events = c.get(f"/api/tasks/{tid}/events").json()["events"]
    assert {"timestamp", "type", "action", "status", "summary"} <= set(events[0])


def test_llm_unavailable_falls_back_but_still_executes(app, settings):
    settings.llm_api_key = "invalid-key-for-test"
    settings.llm_base_url = "http://127.0.0.1:9"  # nothing listens here -> connection error
    record = app.state.manager.run_sync(ACME, mode="llm")
    assert record.state.mode == "llm"
    assert record.state.status == "COMPLETED"
    assert any(e.title == "Model fallback" for e in record.events)
    assert any(e["type"] == "model" for e in record.state.errors)
