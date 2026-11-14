"""The gateway calls of an outside buying agent: licence, lookup, quote, order, state and audit."""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DB", str(tmp_path / "a.sqlite"))
    monkeypatch.setenv("APPROVER_PINS", "4821:Head chef")
    monkeypatch.setenv("CATALOG", "local")
    import backend.app as m
    return TestClient(importlib.reload(m).app)


@pytest.fixture
def auth(client):
    licence = client.post("/sandbox/test", json={"agent": "safe-kitchen"}).json()["licence"]
    return {"Authorization": f"Bearer {licence['token']}"}


def order(client, auth, product_id, quantity, key="k1"):
    quote = client.post("/gateway/quotes", json={"product_id": product_id, "quantity": quantity}, headers=auth)
    assert quote.status_code == 200, quote.text
    return quote.json(), client.post("/gateway/orders", json={"quote_id": quote.json()["quote_id"],
                                                               "idempotency_key": key}, headers=auth)


def test_every_gateway_call_needs_a_licence(client):
    assert client.get("/gateway/products?q=milk").status_code == 401
    assert client.get("/gateway/products?q=milk", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/gateway/quotes", json={"product_id": "MOCK-YOG-NAT-1KG", "quantity": 1}).status_code == 401


def test_lookup_shows_products_with_their_source_and_hides_stock(client, auth):
    r = client.get("/gateway/products?q=lactose-free milk", headers=auth).json()
    assert "MOCK-MILK-LF-1L" in {p["id"] for p in r["products"]}
    assert all("stock" not in p and "lead_days" not in p for p in r["products"])
    assert r["source"]["sha256"] and r["source"]["checked_at"]


def test_an_order_within_the_mandate_is_placed_and_logged_under_the_agents_name(client, auth):
    quote, r = order(client, auth, "MOCK-YOG-NAT-1KG", 6)
    assert quote["decision"]["result"] == "allowed" and quote["total_eur"] == 17.7
    assert r.status_code == 201 and r.json()["status"] == "ordered"
    run = r.json()["run_id"]
    steps = client.get(f"/gateway/audit?run_id={run}", headers=auth).json()
    assert [e["step"] for e in steps] == ["trigger", "rule_check", "recheck", "order"]
    assert steps[-1]["actor"] == "Safe Kitchen Agent"
    assert client.get(f"/gateway/orders/{run}", headers=auth).json()["status"] == "ordered"


def test_the_same_key_never_orders_twice(client, auth):
    quote, first = order(client, auth, "MOCK-YOG-NAT-1KG", 6, key="same")
    again = client.post("/gateway/orders", json={"quote_id": quote["quote_id"], "idempotency_key": "same"}, headers=auth)
    assert again.json() == first.json() and len(client.get("/orders").json()) == 1


def test_an_order_over_the_mandate_waits_for_the_chef(client, auth):
    quote, r = order(client, auth, "MOCK-YOG-NAT-1KG", 60)  # 177 euros, over the 150 euro mandate
    assert quote["decision"]["result"] == "needs_approval"
    assert r.status_code == 202 and r.json()["status"] == "needs_approval"
    run, approval = r.json()["run_id"], r.json()["approval_id"]
    assert client.get(f"/gateway/orders/{run}", headers=auth).json()["status"] == "waiting_for_approval"
    assert approval in {a["id"] for a in client.get("/approvals/open").json()}
    assert client.post(f"/approvals/{approval}", json={"action": "approve", "pin": "4821"}).json()["status"] == "ordered"
    assert client.get(f"/gateway/orders/{run}", headers=auth).json()["status"] == "ordered"


def test_a_banned_allergen_is_refused_with_the_reason(client, auth):
    quote, r = order(client, auth, "MOCK-ALMOND-1L", 5)
    assert quote["decision"]["result"] == "forbidden"
    assert r.status_code == 403 and "nuts" in r.json()["reasons"][0] and r.json()["rule_set_version"] == 3


def test_unknown_products_quotes_and_runs_are_not_found(client, auth):
    assert client.post("/gateway/quotes", json={"product_id": "NOPE", "quantity": 1}, headers=auth).status_code == 404
    assert client.post("/gateway/orders", json={"quote_id": "Q-NOPE", "idempotency_key": "x"}, headers=auth).status_code == 404
    assert client.get("/gateway/orders/nope", headers=auth).status_code == 404
