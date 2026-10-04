PAYLOAD = {"invoice_number": "INV-2026-104", "company": "Acme Corp", "due_date": "2026-10-15"}


def test_formatted_amount_is_rejected(client):
    r = client.post("/finance/invoices", json={**PAYLOAD, "amount": "₹48,500"})
    assert r.status_code == 400
    assert r.json() == {"success": False, "error": "Amount must be numeric",
                        "hint": "Remove currency symbols and separators", "field": "amount", "received": "₹48,500"}


def test_retry_with_numeric_amount_succeeds_and_is_retrievable(client):
    assert client.post("/finance/invoices", json={**PAYLOAD, "amount": "₹48,500"}).status_code == 400
    r = client.post("/finance/invoices", json={**PAYLOAD, "amount": 48500})
    assert r.status_code == 201
    rid = r.json()["record_id"]
    assert rid == 1842
    got = client.get(f"/finance/invoices/{rid}").json()["record"]
    assert got["amount"] == 48500 and got["due_date"] == "2026-10-15"
    assert client.get("/finance/invoices").json()["count"] == 1


def test_duplicate_is_rejected(client):
    client.post("/finance/invoices", json={**PAYLOAD, "amount": 48500})
    r = client.post("/finance/invoices", json={**PAYLOAD, "amount": 48500})
    assert r.status_code == 409 and r.json()["existing_record_id"] == 1842
