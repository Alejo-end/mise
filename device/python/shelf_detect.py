"""Shelf detection for the Safe Kitchen device. Pure functions, no Arduino imports.

The board (main.py) and the laptop replay tests (tests/device/) import this module, so a
recorded camera sequence gives the same decisions on both. Contract: docs/PARALLEL_WORK.md,
interface 2.

The detector itself is the BASELINE two-photo comparison, unchanged:
1. crop():              cut the slot rectangle (fractions of the frame), grayscale, 48 x 48,
                        subtract the mean brightness.
2. classify_baseline(): mean absolute difference to the slot's "empty" and "full" photos.
                        fill = d_empty / (d_empty + d_full); fill >= full_at -> "full",
                        fill < empty_below -> "empty", otherwise "low".

Added around it (14 Nov), so a reading becomes a stock change only when it is trustworthy:
3. classify():          "uncertain" when the patch has too little detail (covered or dark lens)
                        or when it matches neither photo (camera moved, unknown object).
4. observe():           a frame counts once; a state counts after confirm_frames different frames
                        over at least confirm_seconds; uncertain and the calibration quiet period
                        reset the count.
observe_baseline() keeps the old two-check rule, so recordings made with the old board code
replay exactly.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageOps

PATCH = 48

DEFAULTS = {
    "confirm_frames": 3,         # different frames that must agree
    "confirm_seconds": 3.0,      # between the first and the last of them
    "min_detail": 0.03,          # patch contrast below this: covered or dark (real views >= 0.15)
    "mismatch_ratio": 0.85,      # nearer photo farther than this x the photos' distance: no match
    "calibration_quiet_s": 15.0, # no events this long after a calibration photo or a slot move
}


def setting(cfg: dict, key: str):
    return cfg.get(key, DEFAULTS[key])


def crop(img: Image.Image, rect) -> np.ndarray:
    w, h = img.size
    x, y, cw, ch = rect
    box = (int(x * w), int(y * h), int((x + cw) * w), int((y + ch) * h))
    g = ImageOps.grayscale(img.crop(box)).resize((PATCH, PATCH))
    a = np.asarray(g, dtype=np.float32) / 255.0
    return a - a.mean()  # remove overall brightness so room light matters less


def diff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a - b).mean())


def classify_baseline(patch: np.ndarray, empty_ref: np.ndarray | None, full_ref: np.ndarray | None, cfg: dict):
    """The original rule. Return (state, fill); state is None without an "empty" photo."""
    if empty_ref is None:
        return None, None
    d_empty = diff(patch, empty_ref)
    if full_ref is not None:
        d_full = diff(patch, full_ref)
        fill = d_empty / (d_empty + d_full + 1e-6)
    else:
        fill = 1.0 if d_empty > cfg["present_diff"] else 0.0
    if fill >= cfg["full_at"]:
        return "full", fill
    if fill < cfg["empty_below"]:
        return "empty", fill
    return "low", fill


def classify(patch: np.ndarray, empty_ref: np.ndarray | None, full_ref: np.ndarray | None, cfg: dict):
    """Return (state, fill, why). state may be "uncertain"; why says why, else None."""
    if empty_ref is None:
        return None, None, None
    if float(patch.std()) < setting(cfg, "min_detail"):
        return "uncertain", None, "too dark or covered"
    st, fill = classify_baseline(patch, empty_ref, full_ref, cfg)
    if full_ref is not None:
        span = diff(empty_ref, full_ref)
        if span > 0 and min(diff(patch, empty_ref), diff(patch, full_ref)) > setting(cfg, "mismatch_ratio") * span:
            return "uncertain", fill, "view does not match the photos"
    return st, fill, None


def new_tracker() -> dict:
    return {"state": None, "fill": None, "units": None, "candidate": None, "count": 0,
            "since": None, "last_frame": None, "uncertain": None}


def observe(cur: dict, st: str | None, fill: float | None, target_units: int, frame_id, t: float, cfg: dict,
            quiet: bool = False, why: str | None = None) -> bool:
    """One observation of one frame for one slot. True when the confirmed state changed.

    frame_id identifies the frame (None: no frame, for example the camera stopped). t is the
    frame's arrival time in seconds. quiet is true during the calibration quiet period.
    """
    if frame_id is not None and frame_id == cur.get("last_frame"):
        return False  # the same frame again: it already counted
    cur["last_frame"] = frame_id
    cur["fill"] = None if fill is None else round(fill, 2)
    cur["uncertain"] = why if st == "uncertain" else ("calibrating" if quiet else None)
    if st is None or st == "uncertain" or quiet:
        cur["candidate"], cur["count"], cur["since"] = None, 0, None
        return False
    if st == cur["candidate"]:
        cur["count"] += 1
    else:
        cur["candidate"], cur["count"], cur["since"] = st, 1, t
    changed = (cur["count"] >= setting(cfg, "confirm_frames")
               and t - cur["since"] >= setting(cfg, "confirm_seconds")
               and st != cur["state"])
    if changed:
        cur["state"] = st
        cur["units"] = 0 if st == "empty" else round(fill * target_units)
    return changed


def observe_baseline(cur: dict, st: str | None, fill: float | None, target_units: int) -> bool:
    """The original rule: the same state in two checks in a row, even of the same frame."""
    cur["fill"] = None if fill is None else round(fill, 2)
    if st is None:
        return False
    if st == cur["candidate"]:
        cur["count"] += 1
    else:
        cur["candidate"], cur["count"] = st, 1
    changed = cur["count"] >= 2 and st != cur["state"]
    if changed:
        cur["state"] = st
        cur["units"] = 0 if st == "empty" else round(fill * target_units)
    return changed


def overlapping_slots(slots: list[dict], max_share: float = 0.05) -> list[tuple[int, int]]:
    """Pairs of slots whose rectangles share more than max_share of the smaller one."""
    def area(r):
        return r[2] * r[3]

    def overlap(a, b):
        w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
        h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
        return max(0.0, w) * max(0.0, h)

    out = []
    for i, a in enumerate(slots):
        for b in slots[i + 1:]:
            if overlap(a["rect"], b["rect"]) > max_share * min(area(a["rect"]), area(b["rect"])):
                out.append((a["slot"], b["slot"]))
    return out
