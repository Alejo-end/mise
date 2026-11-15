"""Downstream check: feed recognition predictions through the board loop model and the buying agent.

Read-only imports, as tests/device/test_replay.py does: tests/device/replay.py (Sequence, Frame, the
board loop model and shelf_detect as sd), backend.agent.Agent with the stand-in catalogue and rule set.
Run from valio-challenge/ with its environment, for example:
  uv run python -m experiments.recognition.recog.downstream --help   (see main() below)

replay_gated() is replay_board() (tests/device/replay.py) with one change: before observe(), every slot
reading of a frame passes through gate.gate_frame() with the model's labels for that frame. With no
predictions it must give exactly the events of replay_board(); tests/test_recognition.py checks that.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from tests.device.replay import CAMERA_GRACE_S, Event, Frame, Sequence, replay_board, sd  # noqa: F401

from .gate import gate_frame


def replay_gated(frames: list[Frame], seq: Sequence, preds: dict[tuple[str, int], str], assigned: dict[int, str],
                 overrides: dict | None = None, interval: float | None = None, end: float | None = None):
    """preds maps (frame label, slot) to a model label. Returns (events, trackers, gated readings)."""
    cfg = {**seq.config, **(overrides or {})}
    interval = interval or float(cfg.get("frame_interval_s", 1.0))
    trackers = {s["slot"]: sd.new_tracker() for s in cfg["slots"]}
    counted: dict[int, list[int]] = {s["slot"]: [] for s in cfg["slots"]}
    events: list[Event] = []
    vetoes: list[dict] = []
    frames = sorted(frames, key=lambda f: f.t)
    if not frames:
        return events, trackers, vetoes
    end = end if end is not None else frames[-1].t + CAMERA_GRACE_S + interval
    last_checked = None
    tick = frames[0].t
    while tick <= end:
        newest = max((i for i, f in enumerate(frames) if f.t <= tick), default=None)
        if newest is not None and tick - frames[newest].t <= CAMERA_GRACE_S:
            if newest != last_checked:
                last_checked = newest
                fr = frames[newest]
                img, t = fr.image, fr.t
                base = {}
                for s in cfg["slots"]:
                    empty_ref, full_ref = seq.refs[s["slot"]]
                    base[s["slot"]] = sd.classify(sd.crop(img, s["rect"]), empty_ref, full_ref, cfg)
                frame_preds = {n: preds[(fr.label, n)] for n in base if (fr.label, n) in preds}
                gated = gate_frame(base, assigned, frame_preds)
                for s in cfg["slots"]:
                    n, cur = s["slot"], trackers[s["slot"]]
                    st, fill, why = gated[n]
                    if gated[n] != base[n]:
                        vetoes.append({"t": t, "frame": fr.label, "slot": n, "baseline": base[n][0], "why": why})
                    changed = sd.observe(cur, st, fill, s["target_units"], newest, t, cfg, why=why)
                    counted[n] = (counted[n] + [newest])[-cur["count"]:] if cur["count"] else []
                    if changed:
                        events.append(Event(round(tick, 3), n, cur["state"], cur["units"], cur["fill"], list(counted[n])))
        else:
            for s in cfg["slots"]:
                sd.observe(trackers[s["slot"]], "uncertain", None, s["target_units"], None, tick, cfg, why="no camera frame")
                counted[s["slot"]] = []
        tick += interval
    return events, trackers, vetoes


def requests_from_events(events: list[Event], db_dir: Path | None = None) -> dict[int, int]:
    """Purchase requests (orders + open approvals) per slot, from the agent fed these events (as test_replay)."""
    from backend import agent as A
    from backend.audit import AuditLog
    from backend.rules import load_rules
    from backend.valio import LocalCatalog

    with tempfile.TemporaryDirectory(dir=db_dir) as tmp:
        agent = A.Agent(LocalCatalog(), load_rules(), AuditLog(Path(tmp) / "a.sqlite"))
        for e in events:
            agent.handle_event({"type": "slot_state", "slot": e.slot, "state": e.state, "estimated_units": e.units,
                                "auto_limit_eur": 40})
        out: dict[int, int] = {}
        for o in list(agent.orders.values()) + list(agent.open_approvals()):
            out[o["slot"]] = out.get(o["slot"], 0) + 1
        return out
