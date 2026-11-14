"""The box layout editor: repair an overlapping layout, save it atomically, never buy from an invalid one.

Blocker of 14 Nov: the saved boxes overlapped. Each drag saved the whole layout, the board refused it
because the other boxes still overlapped, and the page restored the old position, so the layout could
not be repaired one box at a time. Now moves stay in the page's draft and "Save boxes" sends the complete
layout once; the board validates it with shelf_detect.apply_layout (tested here; main.on_save_config
calls it) and gates readings with shelf_detect.gate_layout (mirrored in replay_board).
"""

from __future__ import annotations

import copy
import json

from backend import agent as A
from backend.audit import AuditLog
from backend.rules import load_rules
from backend.valio import LocalCatalog

from .replay import Frame, Sequence, paint_reference, replay_board, sd

APART = [[0.05, 0.30, 0.28, 0.45], [0.36, 0.30, 0.28, 0.45], [0.67, 0.30, 0.28, 0.45]]


def recorded_overlapping_config():
    """The board's real saved layout of 14 Nov: three nearly identical rectangles."""
    cfg = copy.deepcopy(Sequence("steady-01").config)
    assert sd.overlapping_slots(cfg["slots"]) == [(1, 2), (1, 3), (2, 3)]
    return cfg


def move(draft: list[dict], slot: int, rect: list[float]) -> list[dict]:
    """One drag in the page: changes only the local draft, saves nothing."""
    out = copy.deepcopy(draft)
    next(s for s in out if s["slot"] == slot)["rect"] = rect
    return out


def test_three_overlapping_boxes_moved_apart_one_by_one_then_saved_once(tmp_path):
    cfg = recorded_overlapping_config()
    draft = copy.deepcopy(cfg["slots"])
    # Sequential moves: after the first and the second move the draft still overlaps. That is fine,
    # because nothing is saved per move (the old flow refused exactly these intermediate layouts).
    draft = move(draft, 1, APART[0])
    assert sd.apply_layout(cfg, draft)["ok"] is False
    draft = move(draft, 3, APART[2])
    assert sd.overlapping_slots(draft) == []
    draft = move(draft, 2, APART[1])

    result = sd.apply_layout(cfg, draft)  # "Save boxes": the complete layout, once
    assert result["ok"] and result["errors"] == [] and result["moved"] == [1, 2, 3]
    assert [s["rect"] for s in result["config"]["slots"]] == APART

    # Persistence: the board writes the active config to data/config.json (main.save_config) and reads it
    # back at start (main.load_config). Product names and targets survive the move.
    path = tmp_path / "config.json"
    path.write_text(json.dumps(result["config"], indent=2))
    reloaded = json.loads(path.read_text())
    assert [s["rect"] for s in reloaded["slots"]] == APART
    assert [(s["slot"], s["product_hint"], s["target_units"]) for s in reloaded["slots"]] == \
           [(s["slot"], s["product_hint"], s["target_units"]) for s in cfg["slots"]]
    assert sd.config_errors(reloaded) == []


def test_a_refused_layout_never_becomes_active_and_names_the_conflicts():
    cfg = recorded_overlapping_config()
    apart = dict(cfg, slots=[dict(s, rect=r) for s, r in zip(cfg["slots"], APART)])
    draft = move(apart["slots"], 2, APART[0])  # box 2 dragged onto box 1
    result = sd.apply_layout(apart, draft)
    assert result["ok"] is False
    assert result["conflicts"] == [[1, 2]]
    assert result["config"] is apart and [s["rect"] for s in apart["slots"]] == APART  # active unchanged


def test_an_invalid_saved_layout_produces_no_stock_change_and_no_purchase(tmp_path):
    seq = Sequence("steady-01")
    cfg = recorded_overlapping_config()
    # Slot 3 shows its own "empty" photo for 6 s: with valid boxes this would be an empty-shelf event.
    e3, _ = seq.refs[3]
    frames = [Frame(float(i), paint_reference(seq.frames[3].image, cfg["slots"][2]["rect"], e3)) for i in range(7)]
    events, trackers = replay_board(frames, seq, end=6.0)
    assert events == []
    assert trackers[3]["uncertain"] == sd.LAYOUT_WHY
    agent = A.Agent(LocalCatalog(), load_rules(), AuditLog(tmp_path / "a.sqlite"))
    for e in events:
        agent.handle_event({"type": "slot_state", "slot": e.slot, "state": e.state, "estimated_units": e.units})
    assert agent.orders == {} and agent.open_approvals() == []


def test_while_editing_nothing_becomes_a_stock_change():
    seq = Sequence("steady-01")
    apart = {"slots": [dict(s, rect=r) for s, r in zip(seq.config["slots"], APART)]}
    e3, _ = seq.refs[3]
    frames = [Frame(float(i), paint_reference(seq.frames[3].image, APART[2], e3)) for i in range(7)]
    events, trackers = replay_board(frames, seq, apart, end=6.0, setup=True)
    assert events == [] and trackers[3]["uncertain"] == sd.SETUP_WHY


def test_a_moved_box_orders_only_after_both_new_photos():
    seq = Sequence("steady-01")
    apart = {"slots": [dict(s, rect=r) for s, r in zip(seq.config["slots"], APART)]}
    e3, f3 = seq.refs[3]
    frames = [Frame(float(i), paint_reference(seq.frames[3].image, APART[2], e3)) for i in range(7)]
    recal = sd.recal_after_move({}, [3])
    events, trackers = replay_board(frames, seq, apart, end=6.0, recalibrate=recal)
    assert [e for e in events if e.slot == 3] == [] and trackers[3]["uncertain"] == sd.RECAL_WHY

    recal = sd.recal_note_photo(recal, 3, "empty")  # only the empty photo: still paused
    assert sd.needs_recal(recal, 3)
    recal = sd.recal_note_photo(recal, 3, "full")  # both photos: the slot counts again
    assert not sd.needs_recal(recal, 3)
    events, _ = replay_board(frames, seq, apart, end=6.0, recalibrate=recal)
    assert [e.state for e in events if e.slot == 3] == ["empty"]


def test_unmoved_boxes_keep_their_calibration():
    cfg = {"slots": [{"slot": n, "rect": r} for n, r in zip((1, 2, 3), APART)]}
    draft = move(cfg["slots"], 2, [0.36, 0.05, 0.28, 0.20])
    assert sd.apply_layout(cfg, draft)["moved"] == [2]
    assert sd.recal_after_move({}, [2]) == {"2": []}
