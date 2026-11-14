"""Best-before dates and the "use first" insight. Every code, date and count here is made up."""

import importlib
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.batches import Batch, Batches, insight, parse_gs1
from backend.rules import TZ

TODAY = date(2026, 11, 14)


def batch(days, name="Test chicken 1 kg"):
    return Batch(1, "P1", name, (TODAY + timedelta(days=days)).isoformat(), "L42", "2026-11-13T08:00:00+02:00", "Aino", None)


def test_a_case_label_gives_the_product_the_date_and_the_lot():
    assert parse_gs1("(01)06408430000296(15)261120(10)L42") == {
        "code": "06408430000296", "best_before": "2026-11-20", "date_kind": "best before", "lot": "L42"}
    assert parse_gs1("010640843000029617261120") == {"code": "06408430000296", "best_before": "2026-11-20", "date_kind": "use by"}
    assert parse_gs1("6408430000296") == {"code": "6408430000296"}
    assert parse_gs1("(01)06408430000296(15)269940") == {"code": "06408430000296"}  # month 99 is no date
    assert parse_gs1("milk") == {}


def test_the_insight_names_the_packs_to_use_first():
    assert insight(batch(1), TODAY)["level"] == "use_first" and "tomorrow" in insight(batch(1), TODAY)["text"]
    assert insight(batch(4), TODAY)["level"] == "soon"
    assert insight(batch(20), TODAY)["level"] == "fine"
    late = insight(batch(-2), TODAY)
    assert late["level"] == "past" and "Take it off the shelf" in late["text"]


def test_with_a_count_and_a_use_rate_it_says_how_many_will_be_left():
    i = insight(batch(2), TODAY, slot_units=20, use_per_day=4.0)   # 8 used in two days, 12 left
    assert i["units_left_on_date"] == 12 and "about 12 of 20 will be left" in i["text"] and "offer them" in i["text"]
    gone = insight(batch(2), TODAY, slot_units=6, use_per_day=4.0)
    assert gone["units_left_on_date"] == 0 and "gone before that day" in gone["text"]


def test_a_batch_stays_until_a_person_closes_it(tmp_path):
    b = Batches(tmp_path / "a.sqlite")
    first = b.add("P1", "Test chicken 1 kg", "2026-11-16", "L42", "Aino", datetime.now(TZ))
    b.add("P2", "Test cream 1 l", "2026-11-15", None, None, datetime.now(TZ))
    assert [x.name for x in Batches(tmp_path / "a.sqlite").open()] == ["Test cream 1 l", "Test chicken 1 kg"]  # the nearest date first
    assert b.close(first.id, "used") and not b.close(first.id, "used")
    assert [x.product_id for x in b.open()] == ["P2"]
    with pytest.raises(ValueError):
        b.add("P3", "Bad date", "16.11.2026", None, None, datetime.now(TZ))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DB", str(tmp_path / "a.sqlite"))
    monkeypatch.setenv("APPROVER_PINS", "4821:Head chef")
    monkeypatch.setenv("GATEWAY_KEY", str(tmp_path / "key.pem"))
    import backend.app as m
    return TestClient(importlib.reload(m).app)


def test_a_scan_with_a_date_registers_the_pack_and_the_insight_shows_it(client):
    soon = (datetime.now(TZ).date() + timedelta(days=1)).isoformat()
    r = client.post("/scan", json={"code": "0000000000017", "where": "door", "best_before": soon, "scanned_by": "Aino"})
    assert r.status_code == 200 and r.json()["batches"][0]["best_before"] == soon
    (i,) = client.get("/insights").json()["use_first"]
    assert i["level"] == "use_first" and "yogurt" in i["text"].lower()
    assert client.post(f"/batches/{i['id']}/close", json={"how": "used", "by": "Aino"}).status_code == 200
    assert client.get("/insights").json()["use_first"] == []
    assert any(e["step"] == "batch" for e in client.get("/log?format=json&limit=20").json())


def test_a_case_label_in_the_scan_field_carries_its_own_date(client):
    yymmdd = (datetime.now(TZ).date() + timedelta(days=2)).strftime("%y%m%d")
    r = client.post("/scan", json={"code": f"(01)00000000000017(15){yymmdd}(10)L7", "where": "door"})
    assert r.status_code == 200 and r.json()["batches"][0]["lot"] == "L7"
    assert client.post("/scan", json={"code": "0000000000017", "best_before": "tomorrow"}).status_code == 422
