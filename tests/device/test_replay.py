"""Replay tests for the shelf detection, on recorded and synthetic camera sequences.

Recorded: steady-01 (made with the baseline board code). Synthetic: the recorded frame as
background, with slots painted from their own calibration photos, so a slot reads exactly
"empty", "full" or in between. The separated rectangles below replace the recorded ones,
which overlap (see test_the_validator_flags_the_recorded_overlap).
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest
from PIL import Image

from backend import agent as A
from backend.audit import AuditLog
from backend.rules import load_rules
from backend.valio import LocalCatalog

from .replay import Frame, Sequence, paint_reference, replay_baseline, replay_board, sd

STEADY = "steady-01"
APART = [[0.05, 0.30, 0.28, 0.45], [0.36, 0.30, 0.28, 0.45], [0.67, 0.30, 0.28, 0.45]]


@pytest.fixture(scope="module")
def steady():
    return Sequence(STEADY)


@pytest.fixture(scope="module")
def apart(steady):
    """Config overrides: the same slots with non-overlapping rectangles."""
    return {"slots": [dict(s, rect=r) for s, r in zip(steady.config["slots"], APART)]}


def shelf_frame(seq, overrides, look: dict[int, str]) -> Image.Image:
    """A frame where each slot shows "empty", "full" or "half" from its calibration photos."""
    img = seq.frames[3].image
    for s in overrides["slots"]:
        e, f = seq.refs[s["slot"]]
        ref = {"empty": e, "full": f, "half": (e + f) / 2}[look.get(s["slot"], "full")]
        img = paint_reference(img, s["rect"], ref)
    return img


def timeline(seq, overrides, looks: list[dict[int, str]], fps: float = 1.0) -> list[Frame]:
    """One frame per entry of looks, at fps (1 fps is what the board got from the browser tab)."""
    return [Frame(round(i / fps, 3), shelf_frame(seq, overrides, look)) for i, look in enumerate(looks)]


def agent_for(tmp_path):
    return A.Agent(LocalCatalog(), load_rules(), AuditLog(tmp_path / "a.sqlite"))


def feed(agent, events):
    for e in events:
        agent.handle_event({"type": "slot_state", "slot": e.slot, "state": e.state, "estimated_units": e.units,
                            "auto_limit_eur": 40})


def requests_for(agent, slot):
    return ([o for o in agent.orders.values() if o["slot"] == slot]
            + [a for a in agent.open_approvals() if a["slot"] == slot])


FULL = {1: "full", 2: "full", 3: "full"}
MILK_GONE = {1: "full", 2: "full", 3: "empty"}
YOGURT_GONE = {1: "full", 2: "empty", 3: "full"}


# The baseline, kept as evidence ------------------------------------------------------------

def test_baseline_replay_matches_the_boards_own_readings(steady):
    _, trackers = replay_baseline(steady.frames, steady)
    board = steady.readings[-1]["slots"]
    for slot, tr in trackers.items():
        assert tr["state"] == board[str(slot)]["state"]
        assert abs(tr["fill"] - board[str(slot)]["fill"]) <= 0.02


def test_baseline_confirmed_states_from_one_frame_seen_twice(steady):
    """The failure that this worktree fixes, as it happened on the board on 14 Nov."""
    events, _ = replay_baseline(steady.frames, steady)
    assert events and all(e.confirmed_by_one_frame for e in events)


# Stale frames -------------------------------------------------------------------------------

def test_the_board_never_confirms_a_state_from_one_frame(steady, apart):
    looks = [FULL] * 3 + [MILK_GONE] * 6
    events, _ = replay_board(timeline(steady, apart, looks), steady, apart)
    assert events and not [e for e in events if len(set(e.frame_ids)) < sd.DEFAULTS["confirm_frames"]]


def test_a_stale_glitch_frame_cannot_change_a_slot(steady, apart):
    frames = timeline(steady, apart, [FULL] * 6)
    frames.append(Frame(frames[-1].t + 1, shelf_frame(steady, apart, YOGURT_GONE), "glitch"))  # then the camera stops
    events, trackers = replay_board(frames, steady, apart)
    assert [e for e in events if e.slot == 2 and e.state != "full"] == []
    assert trackers[2]["uncertain"] == "no camera frame"


def test_a_stale_glitch_frame_cannot_trigger_a_purchase(steady, apart, tmp_path):
    frames = timeline(steady, apart, [FULL] * 6)
    frames.append(Frame(frames[-1].t + 1, shelf_frame(steady, apart, YOGURT_GONE), "glitch"))
    events, _ = replay_board(frames, steady, apart)
    agent = agent_for(tmp_path)
    feed(agent, events)
    assert requests_for(agent, 2) == []


def test_one_glitch_frame_among_fresh_frames_is_ignored(steady, apart):
    looks = [FULL] * 5 + [YOGURT_GONE] + [FULL] * 5
    events, _ = replay_board(timeline(steady, apart, looks, fps=10), steady, apart, interval=0.1)
    assert [e for e in events if e.slot == 2 and e.state != "full"] == []


def test_a_hand_over_a_slot_for_two_seconds_is_ignored(steady, apart):
    looks = [FULL] * 4 + [MILK_GONE] * 2 + [FULL] * 4
    events, _ = replay_board(timeline(steady, apart, looks), steady, apart)
    assert [e for e in events if e.slot == 3 and e.state != "full"] == []


# Uncertain views ----------------------------------------------------------------------------

def test_a_view_that_does_not_match_the_calibration_is_uncertain(steady):
    """steady-01: the camera moved after calibration and shows a cup and a keyboard."""
    events, trackers = replay_board(steady.frames, steady, end=steady.frames[-1].t)  # while frames arrive
    assert events == []
    assert {tr["uncertain"] for tr in trackers.values()} == {"view does not match the photos"}


def test_a_covered_lens_is_uncertain(steady, apart):
    dark = Image.fromarray(np.clip(np.random.default_rng(0).normal(14, 3, (360, 480, 3)), 0, 255).astype(np.uint8))
    events, trackers = replay_board([Frame(float(i), dark) for i in range(8)], steady, apart, end=7.0)
    assert events == []
    assert {tr["uncertain"] for tr in trackers.values()} == {"too dark or covered"}


def test_half_full_reads_low_not_uncertain(steady, apart):
    s = apart["slots"][1]
    e, f = steady.refs[2]
    st, fill, why = sd.classify(sd.crop(shelf_frame(steady, apart, {2: "half"}), s["rect"]), e, f, steady.config)
    assert (st, why) == ("low", None) and 0.2 <= fill < 0.66


# Demo step 5 must still work -----------------------------------------------------------------

def test_taking_the_milk_off_triggers_exactly_one_purchase(steady, apart, tmp_path):
    looks = [FULL] * 3 + [MILK_GONE] * 10  # the milk stays off; the camera keeps reporting it
    events, _ = replay_board(timeline(steady, apart, looks), steady, apart)
    milk = [e for e in events if e.slot == 3 and e.state == "empty"]
    assert len(milk) == 1 and milk[0].t - 3 <= sd.DEFAULTS["confirm_seconds"] + 1.5  # within ~4.5 s
    agent = agent_for(tmp_path)
    feed(agent, events)
    assert len(requests_for(agent, 3)) == 1


# Calibration and configuration -----------------------------------------------------------------

def test_the_calibration_quiet_period_blocks_stock_changes():
    cur = sd.new_tracker()
    for i in range(10):
        assert not sd.observe(cur, "empty", 0.0, 24, i, float(i), {}, quiet=True)
    assert cur["state"] is None and cur["uncertain"] == "calibrating"


def test_the_validator_flags_the_recorded_overlap(steady, apart):
    assert sd.overlapping_slots(steady.config["slots"]) == [(1, 2), (1, 3), (2, 3)]
    assert sd.overlapping_slots(apart["slots"]) == []


def test_old_configs_without_the_new_keys_still_work(steady):
    assert all(k not in steady.config for k in sd.DEFAULTS)  # recorded before the keys existed
    assert sd.setting(steady.config, "confirm_frames") == 3
