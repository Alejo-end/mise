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
