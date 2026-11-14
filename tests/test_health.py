"""Board health in the status event (docs/PARALLEL_WORK.md, interface 7) and the settings the app reads.

Owned by worktree 1: the Status event model and the health part of GET /device in backend/app.py,
backend/settings.py. The device side (shelf_detect.build_health) is tested in tests/device/.
"""

import importlib
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "device" / "python"))
import shelf_detect as sd  # noqa: E402



def load_app(tmp_path, monkeypatch, **env):
    monkeypatch.setenv("AUDIT_DB", str(tmp_path / "a.sqlite"))
    monkeypatch.setenv("APPROVER_PINS", "4821:Head chef")
    monkeypatch.setenv("CATALOG", "local")  # never the real catalog or the live API in tests
    monkeypatch.setenv("GATEWAY_KEY", str(tmp_path / "key.pem"))
    # Policy values set to the committed defaults, so a local .env cannot change them for this test.
    for key, value in {"RESTOCK_MIN_S": "120", "RESTOCK_CONFIRM_S": "60", "OFFLINE_AFTER_S": "30",
                       "HEALTH_STALE_S": "30", "FRAME_STALE_S": "10", **env}.items():
        monkeypatch.setenv(key, value)
    import backend.app as m
    return importlib.reload(m)


@pytest.fixture
def app(tmp_path, monkeypatch):
    return load_app(tmp_path, monkeypatch)


@pytest.fixture
def client(app):
    return TestClient(app.app)


HEALTH = {"camera_ok": True, "frame_age_s": 0.4, "config_valid": True, "config_errors": [],
          "slots": {"2": {"state": "low", "uncertain": "view does not match the photos", "fill": 0.43,
                          "calibration": "ok", "assignment_id": "v4:464316"}}}


def test_status_with_health_is_kept_with_its_age(client):
    assert client.post("/events", json={"type": "status", "health": HEALTH}).status_code == 200
    d = client.get("/device").json()
    assert d["health"]["slots"]["2"]["uncertain"] == "view does not match the photos"
    assert d["health"]["slots"]["2"]["assignment_id"] == "v4:464316"
    assert 0 <= d["health_age_s"] < 5 and d["health_known"] is True


def test_status_without_health_still_works(client):
    assert client.post("/events", json={"type": "status"}).status_code == 200
    d = client.get("/device").json()
    assert d["online"] and d["health"] is None and d["health_age_s"] is None and d["health_known"] is False


def test_old_health_counts_as_unknown(app, client):
    client.post("/events", json={"type": "status", "health": HEALTH})
    app.agent.last_health["received"] -= timedelta(seconds=31)
    d = client.get("/device").json()
    assert d["health"] is not None and d["health_age_s"] >= 31 and d["health_known"] is False


@pytest.mark.parametrize("bad", [
    {"slots": {"2": {"fill": 1.5}}},
    {"slots": {"2": {"state": "uncertain"}}},  # uncertain is a reason, never a state
    {"slots": {"2": {"calibration": "maybe"}}},
    {"camera_ok": "yes please"},
    {"frame_age_s": -1},
])
def test_malformed_health_is_rejected(client, bad):
    assert client.post("/events", json={"type": "status", "health": bad}).status_code == 422


def test_fields_the_backend_does_not_know_are_kept(client):
    h = HEALTH | {"assignment_check": "unknown", "new_thing": 1}
    assert client.post("/events", json={"type": "status", "health": h}).status_code == 200
    assert client.get("/device").json()["health"]["assignment_check"] == "unknown"


def test_the_health_the_board_builds_is_accepted(client):
    trackers = {1: sd.new_tracker(), 2: sd.new_tracker() | {"state": "full", "fill": 0.91}, 3: sd.new_tracker()}
    trackers[3]["uncertain"] = sd.STALE_WHY
    h = sd.build_health(trackers, [1, 2, 3], True, 0.25, [], {1: "missing", 2: "ok", 3: "stale"},
                        {2: "v4:464316", 3: "v3:MOCK-MILK-LF-1L"}, "ok")
    assert client.post("/events", json={"type": "status", "health": h}).status_code == 200
    slots = client.get("/device").json()["health"]["slots"]
    assert slots["2"] == {"state": "full", "fill": 0.91, "calibration": "ok", "assignment_id": "v4:464316"}
    assert slots["3"]["calibration"] == "stale"


def test_health_never_orders(client):
    empty = {n: {"state": "empty", "fill": 0.0, "calibration": "ok"} for n in ("1", "2", "3")}
    for _ in range(3):
        client.post("/events", json={"type": "status", "auto_limit_eur": 40, "health": HEALTH | {"slots": empty}})
    assert client.get("/orders").json() == [] and client.get("/approvals/open").json() == []


def test_health_never_closes_a_request(client):
    client.post("/events", json={"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0,
                                 "auto_limit_eur": 40})
    full = {"3": {"state": "full", "fill": 1.0, "calibration": "ok"}}
    for _ in range(3):
        client.post("/events", json={"type": "status", "health": HEALTH | {"slots": full}})
    assert len(client.get("/approvals/open").json()) == 1


# Settings the app reads -----------------------------------------------------------------------

def test_the_app_runs_the_committed_defaults(app):
    assert app.agent.settings == app.SETTINGS and not app.SETTINGS.overrides()
    assert app.OFFLINE_AFTER_S == 30
    assert not [e for e in app.log.entries(1000) if e["step"] == "notice" and "Settings differ" in e["detail"]["summary"]]


def test_an_override_reaches_the_agent_and_is_logged(tmp_path, monkeypatch):
    m = load_app(tmp_path, monkeypatch, RESTOCK_MIN_S="0", RESTOCK_CONFIRM_S="0")
    assert (m.agent.settings.restock_min_s, m.agent.settings.restock_confirm_s) == (0, 0)
    notes = [e["detail"]["summary"] for e in m.log.entries(1000) if e["step"] == "notice"]
    assert any("RESTOCK_MIN_S=0" in n and "RESTOCK_CONFIRM_S=0" in n for n in notes)


def test_the_immediate_policy_through_http_and_health(tmp_path, monkeypatch):
    """With the window and the hold off, the board's health alone ends the order, as Alejo's spec says."""
    c = TestClient(load_app(tmp_path, monkeypatch, RESTOCK_MIN_S="0", RESTOCK_CONFIRM_S="0").app)
    empty = {"type": "slot_state", "slot": 2, "state": "empty", "estimated_units": 0, "auto_limit_eur": 40}
    assert c.post("/events", json=empty).json()["status"] == "ordered"
    c.post("/events", json={"type": "status", "health": HEALTH | {"slots": {"2": {"state": "full", "fill": 1.0}}}})
    assert c.post("/events", json=empty).json()["status"] == "ordered"
    assert len(c.get("/orders").json()) == 2
