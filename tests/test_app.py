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


def test_the_page_gets_the_pins_only_when_the_demo_switch_is_on(client, monkeypatch):
    assert client.get("/device").json()["demo_pins"] == []
    monkeypatch.setenv("SHOW_PINS", "1")
    import backend.app as m
    shown = TestClient(importlib.reload(m).app).get("/device").json()["demo_pins"]
    assert shown == [{"pin": "4821", "name": "Head chef"}]
