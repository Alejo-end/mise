"""Replay recorded camera sequences through the board's shelf detection, on the laptop.

Two models of the board loop:

replay_baseline(): the board code up to commit ce9e52f, which recorded steady-01.
- the camera thread keeps only the newest frame;
- the shelf check runs every frame_interval_s on that newest frame, whether or not it
  is new, as long as some frame arrived in the last CAMERA_GRACE_S (camera_ok);
- observe_baseline() needs the same state in two checks in a row.
It must keep reproducing the board's own readings in recordings made with that code.

replay_board(): the board code now (device/python/main.py loop and process_frame).
- a frame is checked at most once (frame numbers);
- classify() may say "uncertain"; no camera frame for CAMERA_GRACE_S makes every slot uncertain;
- a slot whose calibration is "stale" (interface 9) is uncertain (gate_calibration);
- observe() needs confirm_frames different frames over confirm_seconds.
A commit that changes the loop on the board changes replay_board() too.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

DEVICE = Path(__file__).resolve().parents[2] / "device" / "python"
sys.path.insert(0, str(DEVICE))
import shelf_detect as sd  # noqa: E402

SEQUENCES = Path(__file__).parent / "sequences"
CAMERA_GRACE_S = 2.0  # main.py camera_worker: camera_ok stays true this long after the last frame


@dataclass
class Frame:
    t: float
    image: Image.Image
    label: str = ""


@dataclass
class Event:
    t: float
    slot: int
    state: str
    units: int
    fill: float
    frame_ids: list[int] = field(default_factory=list)  # frames behind the two confirming checks

    @property
    def confirmed_by_one_frame(self) -> bool:
        return len(set(self.frame_ids)) < 2


class Sequence:
    def __init__(self, name: str):
        self.dir = SEQUENCES / name
        self.config = json.loads((self.dir / "config.json").read_text())
        meta = [json.loads(line) for line in (self.dir / "meta.jsonl").read_text().splitlines() if line.strip()]
        self.frames = [Frame(m["t"], Image.open(self.dir / "frames" / m["file"]).convert("RGB"), m["file"])
                       for m in meta if m["kind"] == "frame"]
        self.readings = [m for m in meta if m["kind"] == "reading"]
        self.refs = {s["slot"]: (self._ref(s["slot"], "empty"), self._ref(s["slot"], "full"))
                     for s in self.config["slots"]}

    def _ref(self, slot: int, kind: str):
        p = self.dir / "calibration" / f"slot{slot}_{kind}.npy"
        return np.load(p) if p.exists() else None

    def slot(self, n: int) -> dict:
        return next(s for s in self.config["slots"] if s["slot"] == n)


def paint_reference(img: Image.Image, rect, ref: np.ndarray) -> Image.Image:
    """A copy of img whose slot rectangle shows the calibration photo ref, so the slot reads like ref."""
    out = img.copy()
    w, h = out.size
    x, y, cw, ch = rect
    box = (int(x * w), int(y * h), int((x + cw) * w), int((y + ch) * h))
    pix = np.clip(ref + 0.5, 0, 1) * 255
    patch = Image.fromarray(pix.astype(np.uint8), "L").resize((box[2] - box[0], box[3] - box[1])).convert("RGB")
    out.paste(patch, box[:2])
    return out


def replay_baseline(frames: list[Frame], seq: Sequence, interval: float | None = None,
                    end: float | None = None) -> tuple[list[Event], dict]:
    """Run the baseline board loop over frames. Returns (events, final trackers)."""
    cfg = seq.config
    interval = interval or float(cfg.get("frame_interval_s", 1.0))
    trackers = {s["slot"]: sd.new_tracker() for s in cfg["slots"]}
    seen: dict[int, list[int]] = {s["slot"]: [] for s in cfg["slots"]}  # frame id per check, per slot
    events: list[Event] = []
    frames = sorted(frames, key=lambda f: f.t)
    if not frames:
        return events, trackers
    end = end if end is not None else frames[-1].t + CAMERA_GRACE_S + interval
    tick = frames[0].t
    while tick <= end:
        newest = max((i for i, f in enumerate(frames) if f.t <= tick), default=None)
        if newest is not None and tick - frames[newest].t <= CAMERA_GRACE_S:
            img = frames[newest].image
            for s in cfg["slots"]:
                n = s["slot"]
                empty_ref, full_ref = seq.refs[n]
                st, fill = sd.classify_baseline(sd.crop(img, s["rect"]), empty_ref, full_ref, cfg)
                if st is not None:
                    seen[n].append(newest)
                if sd.observe_baseline(trackers[n], st, fill, s["target_units"]):
                    events.append(Event(round(tick, 3), n, trackers[n]["state"], trackers[n]["units"],
                                        trackers[n]["fill"], seen[n][-2:]))
        tick += interval
    return events, trackers


def replay_board(frames: list[Frame], seq: Sequence, overrides: dict | None = None, interval: float | None = None,
                 end: float | None = None, calibration: dict[int, str] | None = None,
                 recalibrate: dict | None = None, setup: bool = False) -> tuple[list[Event], dict]:
    """Run the current board loop over frames. overrides replaces config keys, for example the slots.
    calibration: slot -> "ok" / "stale" as main.calibration_of() would say (default "ok")."""
    calibration = calibration or {}
    cfg = {**seq.config, **(overrides or {})}
    layout_ok = not sd.config_errors(cfg)  # main.state["layout_errors"]
    interval = interval or float(cfg.get("frame_interval_s", 1.0))
    trackers = {s["slot"]: sd.new_tracker() for s in cfg["slots"]}
    counted: dict[int, list[int]] = {s["slot"]: [] for s in cfg["slots"]}  # frames behind each count
    events: list[Event] = []
    frames = sorted(frames, key=lambda f: f.t)
    if not frames:
        return events, trackers
    end = end if end is not None else frames[-1].t + CAMERA_GRACE_S + interval
    last_checked = None
    tick = frames[0].t
    while tick <= end:
        newest = max((i for i, f in enumerate(frames) if f.t <= tick), default=None)
        if newest is not None and tick - frames[newest].t <= CAMERA_GRACE_S:
            if newest != last_checked:  # main.loop: a frame is checked at most once
                last_checked = newest
                img, t = frames[newest].image, frames[newest].t
                for s in cfg["slots"]:
                    n, cur = s["slot"], trackers[s["slot"]]
                    empty_ref, full_ref = seq.refs[n]
                    reading = sd.gate_calibration(sd.classify(sd.crop(img, s["rect"]), empty_ref, full_ref, cfg),
                                                  calibration.get(n, "ok"))
                    # main.process_frame: setup pause, invalid saved layout, moved box without new photos.
                    st, fill, why = sd.gate_layout(reading, layout_ok, sd.needs_recal(recalibrate, n), setup)
                    changed = sd.observe(cur, st, fill, s["target_units"], newest, t, cfg, why=why)
                    counted[n] = (counted[n] + [newest])[-cur["count"]:] if cur["count"] else []
                    if changed:
                        events.append(Event(round(tick, 3), n, cur["state"], cur["units"], cur["fill"], list(counted[n])))
        else:  # main.mark_uncertain("no camera frame")
            for s in cfg["slots"]:
                sd.observe(trackers[s["slot"]], "uncertain", None, s["target_units"], None, tick, cfg, why="no camera frame")
                counted[s["slot"]] = []
        tick += interval
    return events, trackers
