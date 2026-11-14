"""The cold log: time above a product's limit, the three states, and the record of an order's way. Readings are made up."""

import importlib
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.cold import BREACH, OK, UNKNOWN, WARM, ColdLog, Temp, exposure
from backend.rules import TZ

T0 = datetime(2026, 11, 9, 8, 0, tzinfo=TZ)


def t(minutes, temp):
    return Temp(T0 + timedelta(minutes=minutes), temp, "sensor")


def every(start, end, temp, step=5):
    return [t(m, temp) for m in range(start, end, step)]


def test_cold_air_is_ok_and_a_short_rise_is_warm_not_a_breach():
    assert exposure(every(0, 60, 4.0), 6.0).state == OK
    e = exposure(every(0, 60, 4.0) + every(60, 100, 9.0), 6.0, allowed_minutes=120)
    assert (e.state, e.minutes_above_now, e.peak_c) == (WARM, 35, 9.0)


def test_two_hours_above_the_limit_is_a_breach_and_cooling_ends_it():
    warm = every(0, 130, 9.0)
    e = exposure(warm, 6.0, allowed_minutes=120)
    assert (e.state, e.minutes_above_now) == (BREACH, 125)
    back = exposure(warm + every(130, 150, 4.0), 6.0, allowed_minutes=120)
    assert (back.state, back.minutes_above_now, back.minutes_above_total) == (OK, 0, 130)


def test_a_silent_thermometer_does_not_count_as_time_above_the_limit():
    """One warm reading and then nothing for three hours: ten minutes count, not three hours."""
    e = exposure([t(0, 9.0)], 6.0, now=T0 + timedelta(hours=3))
    assert (e.state, e.minutes_above_now) == (WARM, 10)


def test_no_limit_or_no_reading_is_unknown():
    assert exposure(every(0, 30, 9.0), None).state == UNKNOWN
    assert exposure([], 6.0).state == UNKNOWN


def test_the_limit_is_the_products_own_a_frozen_product_breaches_where_milk_is_fine():
    air = every(0, 130, 4.0)
    assert exposure(air, 6.0).state == OK and exposure(air, -18.0).state == BREACH


def test_the_log_keeps_changes_and_one_reading_a_minute(tmp_path):
    log = ColdLog(tmp_path / "a.sqlite")
    assert log.record(4.0, "sensor", T0) and not log.record(4.1, "sensor", T0 + timedelta(seconds=20))
    assert log.record(4.1, "sensor", T0 + timedelta(seconds=70)) and log.record(7.5, "person", T0 + timedelta(seconds=80))
    assert [x.temperature_c for x in ColdLog(tmp_path / "a.sqlite").readings()] == [4.0, 4.1, 7.5]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DB", str(tmp_path / "a.sqlite"))
    monkeypatch.setenv("APPROVER_PINS", "4821:Head chef")
    monkeypatch.setenv("GATEWAY_KEY", str(tmp_path / "key.pem"))
    import backend.app as m
    return TestClient(importlib.reload(m).app)


def test_the_cold_view_names_each_shelf_product_with_its_own_limit(client):
    assert client.post("/cold/reading", json={"temperature_c": 9.5, "measured_by": "Aino"}).status_code == 200
    cold = client.get("/cold").json()
    assert cold["temperature_c"] == 9.5 and cold["allowed_minutes"] == 120
    milk = next(p for p in cold["products"] if p["slot"] == 3)
    assert (milk["limit_c"], milk["state"]) == (6, "warm")
    bread = next(p for p in cold["products"] if p["slot"] == 1)
    assert bread["state"] in ("ok", "unknown")  # bread keeps at room temperature, or has no limit


def test_a_breach_is_written_into_the_audit_log_once(client):
    import backend.app as m
    start = datetime.now(TZ) - timedelta(minutes=130)
    for minute in range(0, 131, 5):
        m.cold_log.record(9.0 + minute / 1000, "sensor", start + timedelta(minutes=minute))
    assert next(p for p in client.get("/cold").json()["products"] if p["slot"] == 3)["state"] == "breach"
    client.get("/cold")
    alerts = [e for e in client.get("/log?format=json&limit=50").json() if e["step"] == "cold_breach"]
    milk = [e for e in alerts if e["detail"]["slot"] == 3]  # one entry for each product, however often the page asks
    assert len(milk) == 1 and "lactose" in milk[0]["detail"]["summary"].lower()


def test_an_order_carries_its_way_from_the_request_to_the_shelf(client):
    client.post("/request", json={"text": "12 natural yogurt", "asked_by": "Aino"})
    order = client.get("/orders").json()[-1]
    way = client.get(f"/orders/{order['order_id']}/chain").json()
    assert [s["step"] for s in way["steps"]] == ["asked", "checked", "ordered", "delivered", "stored"]
    assert way["steps"][0]["who"] == "Aino" and way["steps"][2]["done"] and not way["steps"][3]["done"]
    client.post(f"/deliveries/{order['order_id']}/check", json={"counts": {}, "temperature_c": 4.0})
    way = client.get(f"/orders/{order['order_id']}/chain").json()
    assert way["steps"][3]["done"] and "4.0" in way["steps"][3]["text"]
    assert client.get("/orders/nothing/chain").status_code == 404


# A scanned product code -------------------------------------------------------------------------

def test_a_scanned_code_finds_the_product_and_says_what_this_kitchen_may_do_with_it(client):
    """The codes in the stand-in catalog are made up. The kitchen bans nuts."""
    yogurt = client.get("/scan/0000000000017").json()["matches"][0]
    assert yogurt["product"]["id"] == "MOCK-YOG-NAT-1KG" and yogurt["verdict"] == "allowed"
    assert (yogurt["keep_at_or_below_c"], yogurt["shelf_slot"]) == (6, 2)
    almond = client.get("/scan/24").json()["matches"][0]  # a scanner can drop the leading zeros
    assert almond["verdict"] == "forbidden" and "nuts" in almond["reasons"][0]
    assert client.get("/scan/999999999").status_code == 404


def test_a_scan_at_the_door_goes_into_the_audit_log_with_the_verdict(client):
    r = client.post("/scan", json={"code": "0000000000024", "where": "door", "scanned_by": "Aino"})
    assert r.status_code == 200 and r.json()["matches"][0]["verdict"] == "forbidden"
    assert client.post("/scan", json={"code": "999999999"}).status_code == 404
    scans = [e for e in client.get("/log?format=json&limit=20").json() if e["step"] == "scan"]
    assert len(scans) == 2 and scans[0]["actor"] == "Aino"
    assert "forbidden in this kitchen" in scans[0]["detail"]["summary"] and "not in the catalog" in scans[1]["detail"]["summary"]


def test_the_cold_room_state_comes_with_the_scanned_product(client):
    client.post("/cold/reading", json={"temperature_c": 9.0})
    milk = client.get("/scan/31").json()["matches"][0]
    assert milk["cold_room"]["state"] == "warm" and milk["cold_room"]["limit_c"] == 6
