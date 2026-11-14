"""The stock timeline: use rate, run-out time and the comparison with a delivery. Every reading here is made up."""

import importlib
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.rules import TZ
from backend.stock import Reading, StockHistory, against_delivery, outlook

T0 = datetime(2026, 11, 9, 8, 0, tzinfo=TZ)


def r(hours, units, source="sensor", slot=3):
    return Reading(slot, T0 + timedelta(hours=hours), units, source)


def test_use_counts_drops_only_and_a_delivery_is_not_negative_use():
    # 24 at the start, 12 used in a day, a delivery of 18, then 6 more used in half a day.
    o = outlook(3, [r(0, 24), r(12, 18), r(24, 12), r(25, 30), r(36, 24)], target_units=24)
    assert (o.units, o.source, o.used) == (24, "sensor", 18)
    assert o.use_per_day == 12.0                       # 18 units in 36 hours
    assert o.runs_out_at == (T0 + timedelta(hours=36 + 48)).isoformat(timespec="seconds")
    assert o.days_of_use_when_full == 2.0


def test_units_taken_out_and_put_back_soon_are_not_use():
    """The replenishment simulator's lesson: ten units leave for an hour and return. That is not demand."""
    o = outlook(3, [r(0, 24), r(10, 14), r(11, 24), r(24, 18)])
    assert (o.used, o.use_per_day) == (6, 6.0)
    # A refill a day later is a delivery. The drop before it still counts.
    assert outlook(3, [r(0, 24), r(10, 14), r(34, 24)]).used == 10
    # Only what came back cancels: ten out, four back.
    assert outlook(3, [r(0, 24), r(10, 14), r(11, 18), r(24, 18)]).used == 6


def test_an_old_count_is_marked_as_old():
    fresh = outlook(3, [r(0, 24), r(24, 12)], now=T0 + timedelta(hours=25))
    old = outlook(3, [r(0, 24), r(24, 12)], now=T0 + timedelta(hours=48))
    assert (fresh.age_hours, fresh.stale) == (1.0, False) and (old.age_hours, old.stale) == (24.0, True)


def test_no_rate_from_one_reading_a_short_history_or_no_use():
    assert outlook(3, [r(0, 10)]).use_per_day is None
    assert outlook(3, [r(0, 10), r(0.5, 4)]).use_per_day is None      # 30 minutes say nothing about a day
    assert outlook(3, [r(0, 10), r(30, 10)]).use_per_day is None      # nothing left the shelf
    empty = outlook(3, [])
    assert (empty.units, empty.readings, empty.runs_out_at) == (None, 0, None)


def test_old_readings_and_other_slots_are_left_out():
    now = T0 + timedelta(days=20)
    o = outlook(3, [r(0, 24), r(24, 12), r(24 * 19, 10), r(24 * 20, 4), r(1, 99, slot=1)], now=now)
    assert (o.readings, o.used, o.use_per_day) == (2, 6, 6.0)


def test_the_outlook_says_if_the_shelf_runs_out_before_a_delivery():
    o = outlook(3, [r(0, 24), r(24, 12)])                              # 12 a day, 12 left, out after one more day
    late = (T0 + timedelta(days=3)).isoformat()
    soon = (T0 + timedelta(hours=30)).isoformat()
    assert against_delivery(o, late, 24) == {"runs_out_before_delivery": True, "order_lasts_days": 2.0}
    assert against_delivery(o, soon, 24)["runs_out_before_delivery"] is False
    # A date with no time counts as the start of that day, so the run-out day itself is not "before".
    assert against_delivery(o, (T0 + timedelta(days=3)).date().isoformat(), 6)["runs_out_before_delivery"] is True
    assert against_delivery(o, (T0 + timedelta(days=2)).date().isoformat(), 6)["runs_out_before_delivery"] is False
    assert against_delivery(outlook(3, [r(0, 5)]), late, 24) == {"runs_out_before_delivery": None, "order_lasts_days": None}


def test_the_history_keeps_changes_only_and_survives_a_restart(tmp_path):
    h = StockHistory(tmp_path / "a.sqlite")
    assert h.record(3, 24, "sensor", T0) and not h.record(3, 24, "sensor", T0 + timedelta(minutes=1))
    assert h.record(3, 20, "person", T0 + timedelta(hours=2))
    again = StockHistory(tmp_path / "a.sqlite")
    assert [(x.units, x.source) for x in again.readings(3)] == [(24, "sensor"), (20, "person")]
    with pytest.raises(ValueError):
        h.record(3, 1, "guess", T0)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DB", str(tmp_path / "a.sqlite"))
    monkeypatch.setenv("APPROVER_PINS", "4821:Head chef")
    monkeypatch.setenv("GATEWAY_KEY", str(tmp_path / "key.pem"))
    import backend.app as m
    return TestClient(importlib.reload(m).app)


def test_a_person_counts_a_slot_and_the_stock_view_shows_it(client):
    assert client.post("/stock/3/count", json={"units": 7, "counted_by": "Aino"}).status_code == 200
    assert client.post("/stock/99/count", json={"units": 7}).status_code == 404
    slot = next(s for s in client.get("/stock").json() if s["slot"] == 3)
    assert (slot["units"], slot["source"], slot["target_units"]) == (7, "person", 24)
    assert slot["use_per_day"] is None and slot["history"] == [{"time": slot["counted_at"], "units": 7, "source": "person"}]


def test_the_stock_view_says_what_is_already_on_order(client):
    """Stock on the way is stock. Without it a person orders twice, and the second order sits on the shelf."""
    client.post("/events", json={"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0})
    slot = next(s for s in client.get("/stock").json() if s["slot"] == 3)
    assert slot["on_order"] == 24 and slot["request_open"] is False
    quiet = next(s for s in client.get("/stock").json() if s["slot"] == 1)
    assert quiet["on_order"] is None and quiet["request_open"] is False


def test_a_camera_reading_goes_into_the_timeline(client):
    client.post("/events", json={"type": "slot_state", "slot": 2, "state": "full", "estimated_units": 9})
    slot = next(s for s in client.get("/stock").json() if s["slot"] == 2)
    assert (slot["units"], slot["source"]) == (9, "camera")
