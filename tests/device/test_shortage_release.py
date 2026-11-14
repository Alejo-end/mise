"""A camera shortage is released only when the camera confirms it at that moment (review finding, 14 November).

The grace period used to end on the last confirmed state alone. A covered or stopped camera keeps that state
at low or empty, so an uncertain reading could still start an order.
"""

from .replay import sd

CFG = {"confirm_frames": 3, "confirm_seconds": 2.0}


def tracker_with_confirmed_low(t0=100.0):
    cur = sd.new_tracker()
    for i in range(4):  # four frames, one second apart: low is confirmed
        sd.observe(cur, "low", 0.2, 24, i, t0 + i, CFG)
    assert cur["state"] == "low"
    return cur


def test_a_shortage_that_the_camera_still_sees_is_confirmed():
    cur = tracker_with_confirmed_low()
    assert sd.shortage_confirmed_now(cur, 110.0, CFG)


def test_a_covered_camera_keeps_the_old_state_but_does_not_confirm_it():
    cur = tracker_with_confirmed_low()
    sd.observe(cur, "uncertain", None, 24, 10, 105.0, CFG, why="too dark or covered")
    assert cur["state"] == "low"  # the stale state that the old worker released
    assert not sd.shortage_confirmed_now(cur, 400.0, CFG)


def test_a_stopped_camera_does_not_confirm_it():
    cur = tracker_with_confirmed_low()
    sd.observe(cur, None, None, 24, None, 105.0, CFG)  # no frame
    assert not sd.shortage_confirmed_now(cur, 400.0, CFG)


def test_one_clear_frame_after_the_uncertainty_is_not_enough():
    cur = tracker_with_confirmed_low()
    sd.observe(cur, "uncertain", None, 24, 10, 105.0, CFG, why="too dark or covered")
    sd.observe(cur, "low", 0.2, 24, 11, 300.0, CFG)
    assert not sd.shortage_confirmed_now(cur, 300.5, CFG)


def test_the_shortage_is_confirmed_again_after_enough_clear_frames_and_seconds():
    cur = tracker_with_confirmed_low()
    sd.observe(cur, "uncertain", None, 24, 10, 105.0, CFG, why="too dark or covered")
    for i in range(3):
        sd.observe(cur, "low", 0.2, 24, 11 + i, 300.0 + i, CFG)
    assert sd.shortage_confirmed_now(cur, 302.5, CFG)


def test_the_quiet_period_of_a_calibration_does_not_confirm_it():
    cur = tracker_with_confirmed_low()
    sd.observe(cur, "low", 0.2, 24, 10, 105.0, CFG, quiet=True)
    assert not sd.shortage_confirmed_now(cur, 400.0, CFG)


def test_a_full_shelf_is_never_a_shortage():
    cur = sd.new_tracker()
    for i in range(4):
        sd.observe(cur, "full", 0.9, 24, i, 100.0 + i, CFG)
    assert not sd.shortage_confirmed_now(cur, 110.0, CFG)
