"""Device health and calibration validity (docs/PARALLEL_WORK.md, interfaces 7 and 9), on the laptop.

Only the pure parts in shelf_detect.py are tested here. main.py (the GET /shelf poll, the
slotN_meta.json files, the status post) runs only on the board and is not tested on the laptop.
"""

from __future__ import annotations

import pytest

from backend import agent as A
from backend.audit import AuditLog
from backend.rules import load_rules
from backend.valio import LocalCatalog

from .replay import Sequence, replay_board, sd
from .test_replay import APART, FULL, MILK_GONE, STEADY, timeline


@pytest.fixture(scope="module")
def steady():
    return Sequence(STEADY)


@pytest.fixture(scope="module")
def apart(steady):
    return {"slots": [dict(s, rect=r) for s, r in zip(steady.config["slots"], APART)]}


# Calibration validity --------------------------------------------------------------------------

@pytest.mark.parametrize("has_empty,calibrated,expected,status", [
    (False, None, None, "missing"),
    (False, "v4:1", "v4:1", "missing"),
    (True, None, None, "ok"),           # no /shelf and no record: unknown, never stale
    (True, "v4:1", None, "ok"),         # the backend has no /shelf yet: unknown
    (True, None, "v4:1", "ok"),         # photos saved before 14 Nov: unknown
    (True, "v4:1", "v4:1", "ok"),
    (True, "v4:1", "v4:2", "stale"),    # the slot holds another product now
    (True, "v4:1", "v5:1", "stale"),    # same product, new rule set version: recalibrate
])
def test_calibration_status(has_empty, calibrated, expected, status):
    assert sd.calibration_status(has_empty, calibrated, expected) == status


def test_the_calibration_record_follows_the_full_photo():
    meta = sd.calibration_meta(None, "empty", "v4:1", "2026-11-14T06:00:00+02:00")
    assert sd.calibrated_for(meta, has_full=False) == "v4:1"
    assert sd.calibrated_for(meta, has_full=True) is None  # a full photo with no record: unknown
    meta = sd.calibration_meta(meta, "full", "v4:2", "2026-11-14T06:01:00+02:00")
    assert sd.calibrated_for(meta, has_full=True) == "v4:2"
    assert meta["empty"] == {"assignment_id": "v4:1", "saved": "2026-11-14T06:00:00+02:00"}
    assert sd.calibrated_for({}, True) is None and sd.calibrated_for(None, False) is None


@pytest.mark.parametrize("payload,want", [
    ([{"slot": 1, "assignment_id": "v4:477032"}, {"slot": "2", "assignment_id": "v4:464316"}],
     {1: "v4:477032", 2: "v4:464316"}),
    ({"slots": [{"slot": 3, "assignment_id": "v4:9"}]}, {3: "v4:9"}),
    ([{"slot": 1, "product_id": "477032"}], {}),  # interface 4 without interface 9: nothing to compare
    ({"detail": "Not Found"}, {}),
    (None, {}),
    (["junk", {"assignment_id": "v4:1"}], {}),
])
def test_assignments_from_the_shelf_answer(payload, want):
    assert sd.assignments_from_shelf(payload) == want


def test_a_stale_calibration_makes_the_reading_uncertain():
    assert sd.gate_calibration(("empty", 0.02, None), "stale") == ("uncertain", 0.02, sd.STALE_WHY)
    assert sd.gate_calibration(("empty", 0.02, None), "ok") == ("empty", 0.02, None)
    assert sd.gate_calibration((None, None, None), "stale") == (None, None, None)  # no photo: no reading anyway


def test_a_stale_slot_never_changes_and_never_buys(steady, apart, tmp_path):
    looks = [FULL] * 3 + [MILK_GONE] * 10
    events, trackers = replay_board(timeline(steady, apart, looks), steady, apart, calibration={3: "stale"},
                                    end=12.0)
    assert [e for e in events if e.slot == 3] == []
    assert trackers[3]["uncertain"] == sd.STALE_WHY
    agent = A.Agent(LocalCatalog(), load_rules(), AuditLog(tmp_path / "a.sqlite"))
    for e in events:
        agent.handle_event({"type": "slot_state", "slot": e.slot, "state": e.state, "estimated_units": e.units})
    assert agent.orders == {} and agent.open_approvals() == []


def test_the_same_milk_with_a_valid_calibration_still_triggers(steady, apart):
    looks = [FULL] * 3 + [MILK_GONE] * 10
    events, _ = replay_board(timeline(steady, apart, looks), steady, apart, calibration={3: "ok"})
    assert [e.state for e in events if e.slot == 3] == ["empty"]


# Configuration validity -------------------------------------------------------------------------

def test_the_recorded_board_config_is_invalid_because_of_its_overlaps(steady, apart):
    assert sd.config_errors(steady.config) == ["slots 1 and 2 overlap", "slots 1 and 3 overlap", "slots 2 and 3 overlap"]
    assert sd.config_errors({**steady.config, **apart}) == []


@pytest.mark.parametrize("change,word", [
    ({"slots": []}, "no shelf slots"),
    ({"slots": [{"slot": 1, "rect": [0.8, 0.1, 0.4, 0.2]}]}, "not inside the picture"),
    ({"slots": [{"slot": 1, "rect": [0.1, 0.1]}]}, "four numbers"),
    ({"slots": [{"slot": 1, "rect": [0.0, 0.0, 0.2, 0.2]}, {"slot": 1, "rect": [0.5, 0.5, 0.2, 0.2]}]}, "twice"),
    ({"full_at": 0.2, "empty_below": 0.5}, "below full_at"),
    ({"confirm_frames": 0}, "confirm_frames"),
    ({"confirm_frames": 2.5}, "whole number"),
    ({"mismatch_ratio": 0}, "mismatch_ratio"),
    ({"min_detail": "high"}, "min_detail"),
])
def test_config_errors_name_the_problem(steady, apart, change, word):
    errors = sd.config_errors({**steady.config, **apart, **change})
    assert errors and any(word in e for e in errors)


# The health dict -----------------------------------------------------------------------------------

def test_build_health_shape():
    cur = sd.new_tracker() | {"state": "low", "fill": 1.2, "uncertain": "view does not match the photos"}
    h = sd.build_health({2: cur}, [1, 2], True, 0.44, ["slots 1 and 2 overlap"], {2: "ok"}, {2: "v4:464316"}, "unknown")
    assert h == {"camera_ok": True, "frame_age_s": 0.4, "config_valid": False,
                 "config_errors": ["slots 1 and 2 overlap"], "assignment_check": "unknown",
                 "slots": {"1": {"state": None, "uncertain": None, "fill": None, "calibration": "missing",
                                 "assignment_id": None},
                           "2": {"state": "low", "uncertain": "view does not match the photos", "fill": 1.0,
                                 "calibration": "ok", "assignment_id": "v4:464316"}}}


def test_build_health_without_a_frame():
    h = sd.build_health({}, [3], False, None, [], {}, {}, "ok")
    assert h["camera_ok"] is False and h["frame_age_s"] is None and h["config_valid"] is True
