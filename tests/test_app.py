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


def test_the_screen_names_the_limit_it_judges_by_and_the_cold_room():
    from backend import app as A
    client = TestClient(A.app)
    client.post("/events", json={"type": "status", "temperature_c": 9.0, "auto_limit_eur": 150})
    client.post("/request", json={"text": "6 lactose-free milk", "asked_by": "Aino"})
    screen = client.get("/box/screen").json()
    door, room = screen["delivery"], screen["cold"]
    assert {"limit_of", "minutes_above", "allowed_minutes"} <= door.keys()
    assert (door["limit_of"] is None) == (door["limit_c"] is None)
    assert door["limit_of"] is None or (door["limit_of"].isascii() and len(door["limit_of"]) <= 22)
    assert room["state"] in ("ok", "warm", "breach", "unknown") and room["allowed_minutes"] > 0
    assert room["limit_c"] is None or room["state"] != "unknown"


def test_the_screen_cannot_decide_an_order_that_does_not_exist():
    from backend import app as A
    r = TestClient(A.app).post("/events", json={"type": "delivery_decision", "accepted": True, "order_id": "ORD-NOPE", "source": "screen"})
    assert r.status_code == 404


def test_the_box_update_offers_every_python_file_of_the_box_app():
    from backend import app as A
    offered = {f["path"] for f in TestClient(A.app).get("/device/files").json()}
    on_disk = {f"python/{p.name}" for p in (A.DEVICE_DIR / "python").glob("*.py")}
    on_disk |= {f"sketch/{p.name}" for p in (A.DEVICE_DIR / "sketch").iterdir() if p.suffix in (".ino", ".h", ".cpp")}
    assert on_disk <= offered, on_disk - offered


def test_the_screen_gets_a_cold_chart_and_the_stock():
    from backend import app as A
    client = TestClient(A.app)
    client.post("/events", json={"type": "status", "temperature_c": 5.2, "auto_limit_eur": 150})
    screen = client.get("/box/screen").json()
    chart = screen["cold"]
    assert chart["span_min"] == 120 and len(chart["points"]) == 48 and chart["points"][-1] is not None  # an average of this bucket
    assert screen["view"] in ("door", "cold")
    rows = screen["stock"]
    assert [r["slot"] for r in rows] == sorted(t["slot"] for t in A.ruleset["targets"])
    for r in rows:
        assert r["name"].isascii() and len(r["name"]) <= 24 and isinstance(r["low"], bool)


def test_report_low_on_the_screen_names_the_screen_and_leaves_the_box_offline():
    from backend import app as A
    client = TestClient(A.app)
    A.device["online"], A.device["last_seen"] = False, None
    slot = A.ruleset["targets"][0]["slot"]
    A.agent.pending_slots.discard(slot)  # an earlier test can leave an order for this slot unsettled
    A.agent.pending.pop(slot, None)
    r = client.post("/events", json={"type": "manual_low", "slot": slot, "source": "screen"})
    assert r.status_code == 200 and r.json()["status"] != "ignored", r.json()
    assert A.device["online"] is False
    summaries = [e["detail"].get("summary", "") for e in A.log.entries(200) if e["step"] == "trigger"]
    assert any("by the touch screen" in t for t in summaries), (r.json(), summaries[:3])


def test_the_scorecard_counts_from_the_log():
    from backend import app as A
    client = TestClient(A.app)
    client.post("/sandbox/test", json={"agent": "safe-kitchen"})
    card = client.get("/scorecard").json()
    assert card["driving_test"]["scenarios"] == 6 and card["driving_test"]["passed"] == 6
    assert card["unsafe_blocked"] >= 0 and card["duplicates_prevented"] >= 0 and card["sent_to_a_person"] >= 0
    assert card["median_seconds_to_decision"] is None or card["median_seconds_to_decision"] >= 0


def test_the_camera_picture_reaches_the_screen_as_rgb565_and_stays_in_memory():
    import io
    from PIL import Image
    from backend import app as A
    client = TestClient(A.app)
    A.camera["jpeg"] = None
    assert client.get("/box/camera.rgb565").status_code == 204
    buf = io.BytesIO()
    Image.new("RGB", (64, 36), (255, 0, 0)).save(buf, format="JPEG")
    assert client.post("/box/camera", content=buf.getvalue(), headers={"Content-Type": "image/jpeg"}).json() == {"status": "ok"}
    r = client.get("/box/camera.rgb565?w=160&h=90")
    assert r.status_code == 200 and len(r.content) == 160 * 90 * 2 and r.headers["X-Frame-Width"] == "160"
    px = int.from_bytes(r.content[:2], "little")
    assert px >> 11 > 28 and (px & 0x1F) < 4          # red, in RGB565
    assert client.post("/box/camera", content=b"not a picture").status_code == 400


def test_a_distance_frame_reaches_the_screen():
    from backend import app as A
    client = TestClient(A.app)
    A.tof_frame["cm"] = None
    assert client.get("/box/tof").status_code == 204
    cm = [30.0] * 63 + [None]
    assert client.post("/box/tof", json={"cm": cm}).json() == {"status": "ok"}
    got = client.get("/box/tof").json()
    assert got["rows"] == got["cols"] == 8 and got["cm"][0] == 30.0 and got["cm"][63] is None
    assert client.post("/box/tof", json={"cm": [1.0] * 10}).status_code == 422
