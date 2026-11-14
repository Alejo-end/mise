"""Shelf detection for the Safe Kitchen device. Pure functions, no Arduino imports.

The board (main.py) and the laptop replay tests (tests/device/) import this
module, so a recorded camera sequence gives the same decisions on both.

Algorithm (baseline, unchanged from the first device version):
1. crop():     cut the slot rectangle (fractions of the frame), grayscale,
               resize to 48 x 48, subtract the mean brightness.
2. classify(): mean absolute difference to the stored "empty" and "full"
               photos of the slot. fill = d_empty / (d_empty + d_full).
               fill >= full_at -> "full", fill < empty_below -> "empty",
               otherwise "low". Without a "full" photo: "full" if the
               difference to "empty" is above present_diff, else "empty".
3. observe():  a new state counts only after it shows in two observations
               in a row. Returns True when the confirmed state changes.

Interface contract (see valio-challenge/docs/PARALLEL_WORK.md):
- A slot config is {"slot": int, "product_hint": str, "target_units": int,
  "rect": [x, y, w, h]} with fractions of the frame.
- A tracker is the dict main.py keeps per slot: {"state", "fill", "units",
  "candidate", "count"}. The page and the backend read state, fill, units.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageOps

PATCH = 48


def crop(img: Image.Image, rect) -> np.ndarray:
    w, h = img.size
    x, y, cw, ch = rect
    box = (int(x * w), int(y * h), int((x + cw) * w), int((y + ch) * h))
    g = ImageOps.grayscale(img.crop(box)).resize((PATCH, PATCH))
    a = np.asarray(g, dtype=np.float32) / 255.0
    return a - a.mean()  # remove overall brightness so room light matters less


def diff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a - b).mean())


def classify(patch: np.ndarray, empty_ref: np.ndarray | None, full_ref: np.ndarray | None, cfg: dict):
    """Return (state, fill). state is None when the slot has no "empty" photo yet."""
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


def new_tracker() -> dict:
    return {"state": None, "fill": None, "units": None, "candidate": None, "count": 0}


def observe(cur: dict, st: str | None, fill: float | None, target_units: int) -> bool:
    """Update one slot tracker with one observation. True when the confirmed state changed."""
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
