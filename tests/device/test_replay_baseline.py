"""Replay tests for the shelf detection, on recorded camera sequences.

Tests marked xfail(strict=True) state a requirement that the BASELINE detector breaks,
with the evidence in the reason. When worktree 1 fixes one, the test passes, strict
xfail turns that into a failure, and the fix removes the mark in the same commit.
"""

from __future__ import annotations

import itertools

import pytest

from backend import agent as A
from backend.audit import AuditLog
from backend.rules import load_rules
from backend.valio import LocalCatalog

from .replay import Frame, Sequence, paint_reference, replay_baseline

STEADY = "steady-01"


@pytest.fixture(scope="module")
def steady():
    return Sequence(STEADY)


def glitch_then_silence(seq: Sequence, slot: int) -> list[Frame]:
    """Normal frames, then ONE frame where `slot` looks empty (a hand, a reflection), then the camera stops."""
    frames = list(seq.frames)
    last = frames[-1]
    e, _ = seq.refs[slot]
    frames.append(Frame(last.t + 1.0, paint_reference(last.image, seq.slot(slot)["rect"], e), "glitch"))
    return frames


def glitch_among_fresh_frames(seq: Sequence, slot: int) -> list[Frame]:
    """10 fps of real frames for 10 s, with ONE frame in the middle where `slot` looks empty."""
    imgs = itertools.cycle([f.image for f in seq.frames])
    frames = [Frame(round(i * 0.1, 1), next(imgs)) for i in range(100)]
    e, _ = seq.refs[slot]
    frames[45] = Frame(4.5, paint_reference(frames[45].image, seq.slot(slot)["rect"], e), "glitch")
    return frames


def later(events, after: float):
    return [e for e in events if e.t > after]


# Fidelity and steady behaviour (the baseline passes these) ------------------------------

def test_replay_matches_the_boards_own_readings(steady):
    _, trackers = replay_baseline(steady.frames, steady)
    board = steady.readings[-1]["slots"]
    for slot, tr in trackers.items():
        assert tr["state"] == board[str(slot)]["state"]
        assert abs(tr["fill"] - board[str(slot)]["fill"]) <= 0.02


def test_steady_shelf_changes_nothing_after_the_first_confirmation(steady):
    events, _ = replay_baseline(steady.frames, steady)
    first = min(e.t for e in events)
    assert later(events, first) == []


def test_one_glitch_frame_among_fresh_frames_is_ignored(steady):
    events, _ = replay_baseline(glitch_among_fresh_frames(steady, 2), steady)
    first = min(e.t for e in events)
    assert [e for e in later(events, first) if e.slot == 2] == []


# Requirements the baseline breaks ---------------------------------------------------------

@pytest.mark.xfail(strict=True, reason=(
    "baseline: the shelf check re-reads the newest frame every second, so one frame seen twice "
    "confirms a state. In steady-01 every first state came from frame 0 twice (the board got ~1 frame/s)."))
def test_a_state_needs_two_different_frames_in_the_recording(steady):
    events, _ = replay_baseline(steady.frames, steady)
    assert not [e for e in events if e.confirmed_by_one_frame]


@pytest.mark.xfail(strict=True, reason=(
    "baseline: after one glitch frame the camera stops; camera_ok stays true for 2 s, the check reads "
    "the same glitch frame twice and confirms 'empty'."))
def test_a_stale_glitch_frame_cannot_change_a_slot(steady):
    events, _ = replay_baseline(glitch_then_silence(steady, 2), steady)
    first = min(e.t for e in events)
    assert [e for e in later(events, first) if e.slot == 2] == []


@pytest.mark.xfail(strict=True, reason=(
    "baseline end to end: the stale glitch frame becomes slot_state empty, and the agent orders "
    "12 x yogurt for slot 2 although the shelf never changed."))
def test_a_stale_glitch_frame_cannot_trigger_a_purchase(steady, tmp_path):
    agent = A.Agent(LocalCatalog(), load_rules(), AuditLog(tmp_path / "a.sqlite"))
    events, _ = replay_baseline(glitch_then_silence(steady, 2), steady)
    for e in events:
        agent.handle_event({"type": "slot_state", "slot": e.slot, "state": e.state, "estimated_units": e.units,
                            "auto_limit_eur": 40})
    slot2 = [o for o in agent.orders.values() if o["slot"] == 2] + [a for a in agent.open_approvals() if a["slot"] == 2]
    assert slot2 == []


@pytest.mark.xfail(strict=True, reason=(
    "live config (calibrated 14 Nov 04:20): the three rectangles overlap almost fully, x = 0.339/0.349/0.342, "
    "so slots move together; painting slot 1 empty moves slot 2 from 0.37 to 0.22."))
def test_slot_rectangles_do_not_overlap(steady):
    def area(r):
        return r[2] * r[3]

    def overlap(a, b):
        w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
        h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
        return max(0, w) * max(0, h)

    rects = [s["rect"] for s in steady.config["slots"]]
    for a, b in itertools.combinations(rects, 2):
        assert overlap(a, b) <= 0.05 * min(area(a), area(b))
