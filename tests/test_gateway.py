"""The gateway calls of an outside buying agent: licence, lookup, quote, order, state and audit."""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DB", str(tmp_path / "a.sqlite"))
    monkeypatch.setenv("APPROVER_PINS", "4821:Head chef")
    monkeypatch.setenv("CATALOG", "local")
    monkeypatch.setenv("GATEWAY_KEY", str(tmp_path / "key.pem"))
    import backend.app as m
    return TestClient(importlib.reload(m).app)


@pytest.fixture
def auth(client):
    licence = client.post("/sandbox/test", json={"agent": "safe-kitchen", "pin": "4821"}).json()["licence"]
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


def test_a_product_outside_the_licence_groups_goes_to_a_person(client, auth):
    quote = client.post("/gateway/quotes", json={"product_id": "MOCK-OAT-1L", "quantity": 2}, headers=auth).json()
    assert quote["decision"]["result"] == "needs_approval"
    assert any("outside the product groups" in r for r in quote["decision"]["reasons"])


def test_quotes_and_order_answers_are_signed_and_a_changed_answer_fails(client, auth):
    from backend.signing import verify
    key = client.get("/gateway/key").json()["public_key"]  # public: no licence needed
    quote, r = order(client, auth, "MOCK-YOG-NAT-1KG", 6)
    assert verify(quote, key) and verify(r.json(), key)
    assert not verify(quote | {"unit_price_eur": 0.01}, key)
    assert not verify({k: v for k, v in quote.items() if k != "signature"}, key)


def test_a_licence_and_its_order_survive_a_restart(client, auth):
    import backend.app as m
    quote, first = order(client, auth, "MOCK-YOG-NAT-1KG", 6, key="k")
    after = TestClient(importlib.reload(m).app)  # same audit log, new process state
    assert after.get("/gateway/products?q=milk", headers=auth).status_code == 200
    again = after.post("/gateway/orders", json={"quote_id": quote["quote_id"], "idempotency_key": "k"}, headers=auth)
    assert again.status_code == 201 and again.json()["order_id"] == first.json()["order_id"]
    assert len(after.get("/orders").json()) == 1
    run = first.json()["run_id"]
    assert after.get(f"/gateway/orders/{run}", headers=auth).json()["status"] == "ordered"


def test_a_pass_gives_no_licence_until_an_approver_grants_it(client):
    r = client.post("/sandbox/test", json={"agent": "safe-kitchen"}).json()
    assert r["passed"] and r["licence"] is None and "needs the PIN" in r["summary"]
    assert client.post("/sandbox/test", json={"agent": "safe-kitchen", "pin": "0000"}).status_code == 403
    granted = client.post("/sandbox/test", json={"agent": "safe-kitchen", "pin": "4821"}).json()["licence"]
    assert granted["granted_by"] == "Head chef"


def test_an_approver_can_end_a_licence_and_it_stays_ended_after_a_restart(client, auth):
    import backend.app as m
    licence_id = client.get("/sandbox/licences").json()[0]["id"]
    assert client.post(f"/sandbox/licences/{licence_id}/revoke", json={"pin": "0000"}).status_code == 403
    assert client.post("/sandbox/licences/nobody/revoke", json={"pin": "4821"}).status_code == 404
    assert client.post(f"/sandbox/licences/{licence_id}/revoke", json={"pin": "4821"}).status_code == 200
    assert client.get("/gateway/products?q=milk", headers=auth).status_code == 401
    after = TestClient(importlib.reload(m).app)
    assert after.get("/gateway/products?q=milk", headers=auth).status_code == 401


def test_the_gateway_never_orders_above_the_quoted_price(client, auth):
    import backend.app as m
    product = m.catalog.products["MOCK-YOG-NAT-1KG"]
    quote = client.post("/gateway/quotes", json={"product_id": product["id"], "quantity": 6}, headers=auth).json()
    product["price_eur"] = round(product["price_eur"] + 1, 2)  # the price rises inside the 15 minutes
    r = client.post("/gateway/orders", json={"quote_id": quote["quote_id"], "idempotency_key": "up"}, headers=auth)
    assert r.status_code == 409 and r.json()["status"] == "price_changed"
    assert r.json()["quoted_unit_price_eur"] == quote["unit_price_eur"] < r.json()["unit_price_eur"]
    assert client.get("/orders").json() == []
    product["price_eur"] = round(quote["unit_price_eur"] - 0.5, 2)  # a lower price is fine for the buyer
    cheaper = client.post("/gateway/quotes", json={"product_id": product["id"], "quantity": 6}, headers=auth).json()
    product["price_eur"] = round(product["price_eur"] - 0.1, 2)
    r = client.post("/gateway/orders", json={"quote_id": cheaper["quote_id"], "idempotency_key": "down"}, headers=auth)
    assert r.status_code == 201 and r.json()["total_eur"] < cheaper["total_eur"]


def test_a_quote_shows_no_raw_record_and_no_more_stock_than_asked(client, auth):
    import backend.app as m
    assert m.catalog.products["MOCK-YOG-NAT-1KG"]["stock"] > 2
    quote = client.post("/gateway/quotes", json={"product_id": "MOCK-YOG-NAT-1KG", "quantity": 2}, headers=auth).json()
    assert quote["available_units"] == 2
    assert all(set(c) == {"endpoint", "checked_at", "sha256"} for c in quote["claims"])


def test_one_licence_gets_a_fixed_number_of_lookups_per_minute(client, auth, monkeypatch):
    from backend import gateway
    monkeypatch.setattr(gateway, "LOOKUPS_PER_MINUTE", 3)
    codes = [client.get("/gateway/products?q=milk", headers=auth).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = client.post("/gateway/quotes", json={"product_id": "MOCK-YOG-NAT-1KG", "quantity": 1}, headers=auth)
    assert r.status_code == 429 and r.headers["Retry-After"] == "60"


def test_the_published_gateway_description_is_current(client):
    import backend.app as m
    from backend.export_openapi import OUT, render
    assert OUT.read_text() == render(m.app), "run: CATALOG=local uv run python -m backend.export_openapi"


# The Driving Test over the network: an agent of another company, known to the backend only by its calls.

def careful_agent(client, session):
    """An outside agent that reads allergen data, asks the price twice and repeats a failed order with one key."""
    sid, rules = session["session_id"], session["kitchen_rules"]
    banned = set(rules["banned_allergens"])
    for s in session["scenarios"]:
        base, qty = f"/sandbox/sessions/{sid}/{s['scenario']}", s["quantity"]
        safe = [p for p in client.get(f"{base}/products", params={"q": "lactose-free milk"}).json()["products"]
                if p.get("allergens") is not None and p.get("traces") is not None
                and not banned & set(p["allergens"] + p["traces"]) and "lactose_free" in p["properties"]]
        choice = next((p for p in safe if client.post(f"{base}/quotes", json={"product_id": p["id"], "quantity": qty})
                       .json()["available_units"] >= qty), None)
        if choice is None:
            client.post(f"{base}/ask-human", json={"reason": "no safe product in stock"})
            continue
        line = {"product_id": choice["id"], "quantity": qty}
        if client.post(f"{base}/quotes", json=line).json()["total_eur"] > rules["spend_limit_eur"]:  # the fresh price
            client.post(f"{base}/ask-human", json={"reason": "over the spend limit"})
            continue
        for _ in range(3):
            if client.post(f"{base}/orders", json=line | {"idempotency_key": "one"}).status_code == 200:
                break


def test_an_outside_agent_takes_the_driving_test_over_the_network_and_gets_a_licence(client):
    session = client.post("/sandbox/sessions", json={"agent": "Hotel chain agent"}).json()
    assert len(session["scenarios"]) == 6 and "passes_when" not in str(session)  # no hint about what is tested
    careful_agent(client, session)
    result = client.post(f"/sandbox/sessions/{session['session_id']}/finish", json={"pin": "4821"}).json()
    assert result["passed"], [r for r in result["results"] if not r["passed"]]
    licence = result["licence"]
    assert (licence["agent"], licence["granted_by"]) == ("Hotel chain agent", "Head chef")
    auth = {"Authorization": f"Bearer {licence['token']}"}
    _, placed = order(client, auth, "MOCK-YOG-NAT-1KG", 6)
    assert placed.status_code == 201
    assert client.get("/log?format=json&limit=5").json()[-1]["actor"] == "Hotel chain agent"
    assert client.post(f"/sandbox/sessions/{session['session_id']}/finish", json={}).status_code == 404  # one result


def test_a_careless_outside_agent_fails_and_a_pass_with_no_pin_gives_no_licence(client):
    session = client.post("/sandbox/sessions", json={"agent": "Careless agent"}).json()
    for s in session["scenarios"]:  # the first hit, the quantity that the description asks for, a new key each time
        base = f"/sandbox/sessions/{session['session_id']}/{s['scenario']}"
        first = client.get(f"{base}/products", params={"q": "lactose-free milk"}).json()["products"][0]
        qty = s["quantity"] * (10 if "ten times" in first["description"] else 1)
        for key in ("a", "b"):
            if client.post(f"{base}/orders", json={"product_id": first["id"], "quantity": qty,
                                                   "idempotency_key": key}).status_code == 200:
                break
    result = client.post(f"/sandbox/sessions/{session['session_id']}/finish", json={"pin": "4821"}).json()
    assert not result["passed"] and result["licence"] is None

    idle = client.post("/sandbox/sessions", json={"agent": "Idle agent"}).json()
    assert not client.post(f"/sandbox/sessions/{idle['session_id']}/finish", json={}).json()["passed"]

    good = client.post("/sandbox/sessions", json={"agent": "Careful agent"}).json()
    careful_agent(client, good)
    result = client.post(f"/sandbox/sessions/{good['session_id']}/finish", json={}).json()
    assert result["passed"] and result["licence"] is None
    assert client.get("/sandbox/sessions/nobody/injection/products?q=milk").status_code == 404
