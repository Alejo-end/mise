"""What the shelf camera sees, shared between the box and the demo page: the backend keeps the latest observation
apart from stock and purchasing, and passes the demo page's request to the box through its command poll."""

import importlib

import pytest
from fastapi.testclient import TestClient

ITEMS = {"items": [{"kind": "product", "identity": "aimo:bonne-orange-juice", "text": "Bonne orange juice — 1 carton",
                    "quantity": 1, "unit": "carton"}], "uncertain": False}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DB", str(tmp_path / "a.sqlite"))
    monkeypatch.setenv("CATALOG", "local")
    monkeypatch.setenv("GATEWAY_KEY", str(tmp_path / "key.pem"))
    import backend.app as m
    return importlib.reload(m)


def snap(status, rid, current=None, error=None):
    return {"status": status, "pending": {"request_id": rid, "requested_by": "demo page"} if status == "pending" else None,
            "current": current, "last_error": error, "stale": False}


def test_the_demo_page_asks_and_the_box_takes_the_request_up_once(app):
    c = TestClient(app.app)
    assert "recognize" not in c.get("/device/commands").json()
    r = c.post("/observations/recognize").json()
    rid = r["requested"]["request_id"]
    assert r["status"] == "requested" and c.post("/observations/recognize").json()["status"] == "already requested"
    assert c.get("/device/commands").json()["recognize"] == {"request_id": rid, "requested_by": "demo page"}
    c.post("/observations/visible", json=snap("pending", rid))  # the box started it
    assert "recognize" not in c.get("/device/commands").json()
    done = {"request_id": rid, "requested_by": "demo page", "frame_id": 7, "captured_at": "2026-11-15T04:00:00+02:00",
            "visible_items": ITEMS}
    c.post("/observations/visible", json=snap("ok", rid, current=done))
    v = c.get("/observations/visible").json()
    assert v["snapshot"]["current"]["visible_items"]["items"][0]["text"] == "Bonne orange juice — 1 carton"
    assert v["requested"] is None and "not stock" in v["note"]


def test_a_failed_recognition_keeps_the_previous_result_visible(app):
    c = TestClient(app.app)
    prev = {"request_id": "b1", "captured_at": "2026-11-15T04:00:00+02:00", "visible_items": ITEMS}
    c.post("/observations/visible", json=snap("failed", "d9", current=prev,
                                             error={"request_id": "d9", "error": "recognition service not reachable"}))
    s = c.get("/observations/visible").json()["snapshot"]
    assert s["status"] == "failed" and s["current"]["visible_items"] == ITEMS and "not reachable" in s["last_error"]["error"]


def test_observations_never_become_stock_audit_or_orders(app):
    c = TestClient(app.app)
    before = len(app.log.entries(1000))
    c.post("/observations/recognize")
    c.post("/observations/visible", json=snap("ok", "b2", current={"request_id": "b2", "visible_items": ITEMS}))
    c.get("/observations/visible")
    assert len(app.log.entries(1000)) == before
    assert c.get("/stock").status_code == 200 and all(s.get("source") != "camera" for s in c.get("/stock").json())
    assert c.get("/approvals/open").json() == []


def test_a_status_with_the_slot_detector_off_clears_old_slot_readings(app):
    c = TestClient(app.app)
    app.device["slots"] = {"1": {"state": "low", "units": 2}}
    c.post("/events", json={"type": "status", "health": {"camera_ok": True, "slots": {}, "slot_detector": "off"}})
    assert app.device["slots"] == {}


def test_the_picture_is_served_only_for_its_own_result(app):
    import base64
    c = TestClient(app.app)
    jpeg = b"\xff\xd8\xff\xe0 test picture \xff\xd9"
    cur = {"request_id": "b7", "captured_at": "2026-11-15T05:00:00+02:00", "visible_items": ITEMS}
    c.post("/observations/visible", json={**snap("ok", "b7", current=cur), "frame": base64.b64encode(jpeg).decode()})
    v = c.get("/observations/visible").json()
    assert v["frame_request_id"] == "b7" and "frame" not in v["snapshot"]
    r = c.get("/observations/frame/b7")
    assert r.status_code == 200 and r.content == jpeg and r.headers["content-type"] == "image/jpeg"
    assert c.get("/observations/frame/b6").status_code == 404  # never another result's picture
    c.post("/observations/visible", json=snap("pending", "b8", current=cur))  # a status without a picture keeps it
    assert c.get("/observations/frame/b7").status_code == 200


def test_both_pages_get_the_same_drawing_code(app):
    c = TestClient(app.app)
    js = c.get("/visible-view.js")
    assert js.status_code == 200 and "placeBadges" in js.text and "javascript" in js.headers["content-type"]
    assert js.text == (app.DEVICE_DIR / "assets" / "visible-view.js").read_text()
    assert "assets/visible-view.js" in {f["path"] for f in c.get("/device/files").json()}


def test_continuous_start_and_stop_reach_the_box_once_and_grant_nothing(app, monkeypatch):
    c = TestClient(app.app)
    before = len(app.log.entries(1000))
    assert "continuous" not in c.get("/device/commands").json()
    r = c.post("/observations/continuous", json={"action": "start", "pause_s": 5}).json()
    cid = r["control"]["command_id"]
    cmd = c.get("/device/commands").json()["continuous"]
    assert cmd == {"command_id": cid, "action": "start", "pause_s": 5, "requested_by": "demo page"}
    assert c.get("/observations/visible").json()["control"]["command_id"] == cid
    # the box took it up: its own continuous state names the command; the backend stops repeating it
    c.post("/observations/visible", json={**snap("pending", "c1-1"), "continuous": {"running": True, "phase": "analysing locally",
                                                                                     "last_command_id": cid}})
    assert "continuous" not in c.get("/device/commands").json() and c.get("/observations/visible").json()["control"] is None
    stop = c.post("/observations/continuous", json={"action": "stop"}).json()["control"]
    assert c.get("/device/commands").json()["continuous"]["action"] == "stop" and stop["pause_s"] is None
    assert c.post("/observations/continuous", json={"action": "start", "pause_s": 0}).status_code == 422  # 1 to 600 s
    assert c.post("/observations/continuous", json={"action": "start", "pause_s": 601}).status_code == 422
    assert len(app.log.entries(1000)) == before  # no audit entry, no stock, no order: an observation mode only
    # a command the box never takes up lapses (the page says so) and is no longer sent
    t = app.time.time()
    monkeypatch.setattr(app.time, "time", lambda: t + app.OBSERVATION_REQUEST_S + 1)
    assert "continuous" not in c.get("/device/commands").json()
    assert c.get("/observations/visible").json()["control"]["lapsed"] is True


def test_a_request_that_joined_the_running_scan_counts_as_taken(app):
    c = TestClient(app.app)
    rid = c.post("/observations/recognize").json()["requested"]["request_id"]
    running = {**snap("pending", "c1-3"), "pending": {"request_id": "c1-3", "requested_by": "continuous", "joined_ids": [rid]}}
    c.post("/observations/visible", json=running)
    assert c.get("/observations/visible").json()["requested"] is None and "recognize" not in c.get("/device/commands").json()


def test_without_a_recognition_service_the_pass_on_route_says_so(app):
    r = TestClient(app.app).post("/recognition/recognize/hybrid", json={})
    assert r.status_code == 404 and "RECOGNITION_URL" in r.json()["detail"]


def test_the_box_calls_on_a_hosted_backend_pass_on_unchanged(app, monkeypatch):
    import httpx
    seen = {}

    def answer(request):
        seen.update(url=str(request.url), body=request.content)
        return httpx.Response(200, json={"objects": []})

    real = httpx.AsyncClient
    monkeypatch.setattr(app, "RECOGNITION_URL", "https://recognition.test")
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
    r = TestClient(app.app).post("/recognition/recognize/hybrid", json={"image_b64": "x"})
    assert r.status_code == 200 and r.json() == {"objects": []}
    assert seen == {"url": "https://recognition.test/recognize/hybrid", "body": b'{"image_b64":"x"}'}
