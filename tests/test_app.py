"""HTTP checks: PIN on approvals and event validation."""

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DB", str(tmp_path / "a.sqlite"))
    monkeypatch.setenv("APPROVER_PINS", "4821:Head chef")
    monkeypatch.setenv("CATALOG", "local")  # never the real catalog or the live API in tests
    monkeypatch.setenv("GATEWAY_KEY", str(tmp_path / "key.pem"))
    import backend.app as m
    m = importlib.reload(m)
    m.agent.auto_limit_eur = 40
    return TestClient(m.app)


def _open(client):
    client.post("/events", json={"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0})
    return client.get("/approvals/open").json()[0]["id"]


def test_approval_needs_the_right_pin(client):
    aid = _open(client)
    assert client.post(f"/approvals/{aid}", json={"action": "approve", "pin": "0000"}).status_code == 403
    r = client.post(f"/approvals/{aid}", json={"action": "approve", "pin": "4821"}).json()
    assert r["status"] == "ordered"
    assert client.get("/log?format=json").json()[-1]["actor"] == "Head chef"


def test_unknown_slot_is_rejected(client):
    assert client.post("/events", json={"type": "slot_state", "slot": 9, "state": "empty"}).status_code == 400


def test_bad_event_is_rejected(client):
    assert client.post("/events", json={"type": "slot_state", "slot": 3, "state": "gone"}).status_code == 422
    assert client.post("/events", json={"type": "teleport"}).status_code == 422


def test_five_wrong_pins_lock_approvals_for_a_while(client):
    aid = _open(client)
    for _ in range(5):
        assert client.post(f"/approvals/{aid}", json={"action": "approve", "pin": "0000"}).status_code == 403
    assert client.post(f"/approvals/{aid}", json={"action": "approve", "pin": "4821"}).status_code == 429


def test_a_recording_session_isolates_purchasing(tmp_path, monkeypatch):
    """Shelf recordings: taking the milk out, pressing the knob or a health report changes no order."""
    monkeypatch.setenv("AUDIT_DB", str(tmp_path / "r.sqlite"))
    monkeypatch.setenv("CATALOG", "local")
    monkeypatch.setenv("GATEWAY_KEY", str(tmp_path / "key.pem"))
    monkeypatch.setenv("RECORDING_SESSION", "eval-a-20261114")
    import backend.app as m
    m = importlib.reload(m)
    c = TestClient(m.app)
    for ev in ({"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0},
               {"type": "manual_low", "slot": 2}):
        r = c.post("/events", json=ev).json()
        assert r["status"] == "ignored" and "purchasing isolated" in r["reason"]
    assert c.get("/approvals/open").json() == [] and c.get("/orders").json() == []
    assert c.get("/device").json()["recording_session"] == "eval-a-20261114"
    assert c.get("/device").json()["slots"]["3"]["state"] == "empty"  # the device view still updates
    assert [e for e in c.get("/log?format=json").json() if e["step"] in ("trigger", "order", "approval")] == []
    monkeypatch.delenv("RECORDING_SESSION")
    importlib.reload(m)  # leave a normal app for later tests


def test_shelf_events_observe_keeps_the_gateway_buying(tmp_path, monkeypatch):
    """Props on the shelf: camera and knob never buy, while the kitchen request still goes through."""
    for k, v in {"AUDIT_DB": str(tmp_path / "o.sqlite"), "CATALOG": "local", "GATEWAY_KEY": str(tmp_path / "k.pem"),
                 "APPROVER_PINS": "4821:Head chef", "SHELF_EVENTS": "observe"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("RECORDING_SESSION", raising=False)
    import backend.app as m
    m = importlib.reload(m)
    c = TestClient(m.app)
    for ev in ({"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0}, {"type": "manual_low", "slot": 3}):
        r = c.post("/events", json=ev).json()
        assert r["status"] == "ignored" and "observe only" in r["reason"]
    assert c.get("/approvals/open").json() == [] and c.get("/orders").json() == []
    assert c.get("/device").json()["shelf_events"] == "observe"
    m.agent.auto_limit_eur = 40
    r = c.post("/request", json={"text": "lactose free milk", "product_id": "MOCK-MILK-LF-1L"}).json()
    assert r["status"] in ("ordered", "needs_approval")  # the kitchen request still buys
    monkeypatch.delenv("SHELF_EVENTS")
    importlib.reload(m)


def test_the_page_gets_the_pins_only_when_the_demo_switch_is_on(client, monkeypatch):
    assert client.get("/device").json()["demo_pins"] == []
    monkeypatch.setenv("SHOW_PINS", "1")
    import backend.app as m
    shown = TestClient(importlib.reload(m).app).get("/device").json()["demo_pins"]
    assert shown == [{"pin": "4821", "name": "Head chef"}]


def test_observed_prop_readings_never_enter_the_stock_timeline(tmp_path, monkeypatch):
    """Vision props on an observe-only shelf must not become stock history of a catalogue product."""
    for k, v in {"AUDIT_DB": str(tmp_path / "t.sqlite"), "CATALOG": "local", "GATEWAY_KEY": str(tmp_path / "k.pem"),
                 "APPROVER_PINS": "4821:Head chef", "SHELF_EVENTS": "observe"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("RECORDING_SESSION", raising=False)
    import backend.app as m
    m = importlib.reload(m)
    c = TestClient(m.app)
    c.post("/events", json={"type": "slot_state", "slot": 3, "state": "low", "estimated_units": 5})
    slot3 = next(s for s in c.get("/stock").json() if s["slot"] == 3)
    assert slot3["history"] == []  # nothing recorded for the catalogue product of slot 3
    assert any(e["step"] == "observation" for e in c.get("/log?format=json").json())  # kept apart, in the audit log
    monkeypatch.delenv("SHELF_EVENTS")
    importlib.reload(m)


def test_the_hints_name_requests_and_codes_that_this_catalog_answers():
    from backend import app as A
    client = TestClient(A.app)
    hints = client.get("/hints").json()
    assert len(hints["requests"]) == len(A.ruleset["targets"]) and hints["codes"]
    assert "forbidden" in {c["verdict"] for c in hints["codes"]}
    for c in hints["codes"]:
        assert client.get(f"/scan/{c['gtin']}").status_code == 200


def test_a_decision_at_the_box_is_logged_with_the_temperature_and_never_orders():
    from backend import app as A
    client = TestClient(A.app)
    orders = len(A.agent.orders)
    r = client.post("/events", json={"type": "delivery_decision", "accepted": False, "temperature_c": 9.4})
    assert r.status_code == 200 and r.json()["status"] == "logged" and r.json()["accepted"] is False
    r = client.post("/events", json={"type": "alarm_seen", "temperature_c": 9.4})
    assert r.json() == {"status": "logged"}
    story = " ".join(client.get("/log").json()["story"])
    assert "rejected a delivery at 9.4 °C" in story and "confirmed the cold alarm at 9.4 °C" in story
    assert len(A.agent.orders) == orders
    assert client.get("/device").json()["temperature_c"] == 9.4  # the reading still reaches the cold log


def test_a_delivery_decision_needs_a_yes_or_a_no():
    from backend import app as A
    assert TestClient(A.app).post("/events", json={"type": "delivery_decision"}).status_code == 422


def test_the_screen_shows_the_newest_undecided_order_and_a_decision_removes_it():
    from backend import app as A
    client = TestClient(A.app)
    client.post("/events", json={"type": "status", "temperature_c": 9.0, "auto_limit_eur": 150})
    placed = client.post("/request", json={"text": "6 lactose-free milk", "asked_by": "Aino"}).json()
    assert placed["status"] == "ordered", placed
    screen = client.get("/box/screen").json()
    assert screen["delivery"]["order_id"] == placed["order_id"] and screen["temperature_c"] == 9.0
    assert all(len(line) <= 40 and line.isascii() for line in screen["delivery"]["lines"])
    r = client.post("/events", json={"type": "delivery_decision", "accepted": False, "order_id": placed["order_id"], "source": "screen"})
    assert r.json()["order_id"] == placed["order_id"]
    after = client.get("/box/screen").json()
    assert after["delivery"] is None or after["delivery"]["order_id"] != placed["order_id"]
    chain = client.get(f"/orders/{placed['order_id']}/chain").json()
    delivered = next(s for s in chain["steps"] if s["step"] == "delivered")
    assert delivered["accepted"] is False and "on the touch screen rejected" in delivered["text"] and "9.0 °C" in delivered["text"]


def test_the_screen_cannot_decide_an_order_that_does_not_exist():
    from backend import app as A
    r = TestClient(A.app).post("/events", json={"type": "delivery_decision", "accepted": True, "order_id": "ORD-NOPE", "source": "screen"})
    assert r.status_code == 404


def test_the_box_update_offers_every_python_file_of_the_box_app():
    from backend import app as A
    offered = {f["path"] for f in TestClient(A.app).get("/device/files").json()}
    on_disk = {f"python/{p.name}" for p in (A.DEVICE_DIR / "python").glob("*.py")}
    assert on_disk <= offered, on_disk - offered
