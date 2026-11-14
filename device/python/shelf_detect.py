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

Health and calibration validity (interfaces 7 and 9):
5. calibration_status(): "missing" without an empty photo, "stale" when the photos were taken for
                        another assignment than the backend's GET /shelf names, else "ok". When
                        either side is unknown (no /shelf yet, photos from before 14 Nov) it is
                        "ok": unknown is never stale. gate_calibration() makes a stale slot uncertain.
6. config_errors():     what is wrong with the box configuration (overlaps, rectangles, thresholds).
7. build_health():      the "health" dict of the status event the board sends every 10 s.
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
    # Candidate rule (off at 0 until real recordings set it; see BETWEEN_WHY): a real shelf view lies between
    # its empty and full photo, so (d_empty + d_full) / d(empty, full) stays near 1; a hand, a partial cover or
    # an unrelated view goes above. max_shift_px makes the distances tolerate a product put back a few pixels off.
    "between_ratio": 0.0,
    "max_shift_px": 0,
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


def shift_diff(a: np.ndarray, b: np.ndarray, m: int) -> float:
    """The smallest diff over shifts of up to m pixels, compared on the overlapping part."""
    if m <= 0:
        return diff(a, b)
    n0, n1 = a.shape
    best = float("inf")
    for dy in range(-m, m + 1):
        for dx in range(-m, m + 1):
            A = a[max(0, dy):n0 + min(0, dy), max(0, dx):n1 + min(0, dx)]
            B = b[max(0, -dy):n0 + min(0, -dy), max(0, -dx):n1 + min(0, -dx)]
            best = min(best, float(np.abs(A - B).mean()))
    return best


BETWEEN_WHY = "view is not between the two photos (covered, or something else is there)"


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


def _state_from_fill(fill: float, cfg: dict):
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
        ratio = float(setting(cfg, "between_ratio") or 0)
        if ratio > 0:  # candidate rule, off by default
            m = int(setting(cfg, "max_shift_px") or 0)
            de, df, sp = shift_diff(patch, empty_ref, m), shift_diff(patch, full_ref, m), shift_diff(empty_ref, full_ref, m)
            if sp > 0 and (de + df) / sp > ratio:
                return "uncertain", de / (de + df + 1e-6), BETWEEN_WHY
            if m > 0:  # read the fill with the same shift tolerance, so a product a few pixels off is not "low"
                st2, fill2 = _state_from_fill(de / (de + df + 1e-6), cfg)
                return st2, fill2, None
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


def shortage_confirmed_now(cur: dict, now: float, cfg: dict) -> bool:
    """True when the newest readings confirm a shortage at this moment, not only an older confirmed state.

    A shortage waits a grace period before it can start an order. During that time the camera can be covered,
    stop, or be set up again. Such a reading is "uncertain": it clears the candidate and keeps the last
    confirmed state, which then is stale. So a shortage is released only when the readings since the last
    uncertainty again show low or empty, for the same number of frames and seconds that confirm any state.
    """
    return (cur.get("state") in ("low", "empty")
            and cur.get("uncertain") is None
            and cur.get("candidate") in ("low", "empty")
            and cur.get("count", 0) >= setting(cfg, "confirm_frames")
            and cur.get("since") is not None
            and now - cur["since"] >= setting(cfg, "confirm_seconds"))


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


# Calibration validity (interface 9) ---------------------------------------------------------

STALE_WHY = "photos are for another product; save the empty and full photos again"


def assignments_from_shelf(payload) -> dict[int, str]:
    """slot -> assignment_id from the backend's GET /shelf answer (a list of slots, or {"slots": [...]})."""
    rows = payload.get("slots") if isinstance(payload, dict) else payload
    out = {}
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("slot") is not None and row.get("assignment_id"):
            out[int(row["slot"])] = str(row["assignment_id"])
    return out


def calibration_meta(meta: dict | None, kind: str, assignment_id: str | None, when: str) -> dict:
    """A slot's calibration record (data/slotN_meta.json) after saving its kind ("empty"/"full") photo."""
    out = dict(meta or {})
    out[kind] = {"assignment_id": assignment_id, "saved": when}
    return out


def calibrated_for(meta: dict | None, has_full: bool) -> str | None:
    """The assignment a slot's photos show: the full photo's (it shows the product), else the empty photo's.
    None when unknown, for example photos saved before the record existed."""
    entry = (meta or {}).get("full" if has_full else "empty")
    return entry.get("assignment_id") if isinstance(entry, dict) else None


def calibration_status(has_empty: bool, calibrated: str | None, expected: str | None) -> str:
    """"missing", "stale" or "ok". Unknown on either side (None) is "ok", never "stale"."""
    if not has_empty:
        return "missing"
    if calibrated is None or expected is None:
        return "ok"
    return "ok" if calibrated == expected else "stale"


def gate_calibration(reading: tuple, status: str) -> tuple:
    """classify()'s (state, fill, why), made uncertain when the slot's calibration is stale."""
    st, fill, why = reading
    if status == "stale" and st is not None:
        return "uncertain", fill, STALE_WHY
    return st, fill, why


# Configuration and health (interface 7) -----------------------------------------------------

def config_errors(cfg: dict) -> list[str]:
    """What is wrong with a box configuration, in words. An empty list means valid."""
    errors = []

    def num(key, low, high=None, integer=False):
        value = cfg.get(key, DEFAULTS.get(key))
        if value is None:
            return
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value >= low
        ok = ok and (high is None or value <= high) and (not integer or float(value).is_integer())
        if not ok:
            errors.append(f"{key} must be {'a whole number ' if integer else ''}at least {low}"
                          + (f" and at most {high}" if high is not None else "") + f", not {value!r}")

    slots = cfg.get("slots")
    if not isinstance(slots, list) or not slots:
        return ["no shelf slots are configured"]
    good, seen = [], set()
    for s in slots:
        n = s.get("slot") if isinstance(s, dict) else None
        rect = s.get("rect") if isinstance(s, dict) else None
        if not isinstance(n, int) or isinstance(n, bool):
            errors.append(f"a slot has no number: {s!r}")
            continue
        if n in seen:
            errors.append(f"slot {n} appears twice")
        seen.add(n)
        if not (isinstance(rect, (list, tuple)) and len(rect) == 4
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in rect)):
            errors.append(f"slot {n} needs a rectangle of four numbers")
            continue
        x, y, w, h = rect
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > 1.0001 or y + h > 1.0001:
            errors.append(f"slot {n} rectangle {list(rect)} is not inside the picture")
            continue
        good.append(s)
    errors += [f"slots {a} and {b} overlap" for a, b in overlapping_slots(good)]
    for key in ("full_at", "empty_below", "present_diff"):
        num(key, 0, 1)
    full_at, empty_below = cfg.get("full_at"), cfg.get("empty_below")
    if isinstance(full_at, (int, float)) and isinstance(empty_below, (int, float)) and empty_below >= full_at:
        errors.append(f"empty_below ({empty_below}) must be below full_at ({full_at})")
    num("confirm_frames", 1, integer=True)
    for key in ("confirm_seconds", "min_detail", "calibration_quiet_s"):
        num(key, 0)
    num("mismatch_ratio", 0.01)
    num("between_ratio", 0)
    num("max_shift_px", 0, 12, integer=True)
    num("frame_interval_s", 0.05)
    return errors


def build_health(trackers: dict, slot_ids: list[int], camera_ok: bool, frame_age_s: float | None,
                 errors: list[str], calibration: dict[int, str], calibrated: dict[int, str | None],
                 assignment_check: str) -> dict:
    """The "health" of the status event (docs/PARALLEL_WORK.md, interface 7).

    trackers: slot -> observe() tracker. calibration: slot -> calibration_status(). calibrated:
    slot -> calibrated_for(). assignment_check: "ok" when the backend's GET /shelf answered at least
    once, else "unknown" (then no slot can be stale).
    """
    slots = {}
    for n in slot_ids:
        cur = trackers.get(n) or {}
        fill = cur.get("fill")
        slots[str(n)] = {"state": cur.get("state"), "uncertain": cur.get("uncertain"),
                         "fill": None if fill is None else round(min(1.0, max(0.0, float(fill))), 2),
                         "calibration": calibration.get(n, "missing"), "assignment_id": calibrated.get(n)}
    return {"camera_ok": bool(camera_ok),
            "frame_age_s": None if frame_age_s is None else round(max(0.0, float(frame_age_s)), 1),
            "config_valid": not errors, "config_errors": list(errors), "assignment_check": assignment_check,
            "slots": slots}


# Layout editing (14 Nov, after the "boxes overlap, cannot repair one at a time" blocker) ----------------
#
# The page keeps box moves in a local draft and sends the complete layout once ("Save boxes").
# apply_layout() validates that complete layout and never changes the active one when it fails.
# While the person edits, and while the saved layout is invalid, no slot may become a stock change.
# A moved box needs new empty and full photos before its slot counts again.

SETUP_WHY = "setting up the boxes; purchasing is paused"
LAYOUT_WHY = "the saved boxes are not valid; edit the boxes, move them apart and save"
RECAL_WHY = "this box moved; save the empty photo and the full photo again"


def moved_slots(old_slots: list[dict] | None, new_slots: list[dict] | None, tol: float = 1e-3) -> list[int]:
    """Slots whose rectangle is new or changed (by more than tol in any coordinate)."""
    old = {s.get("slot"): s.get("rect") for s in old_slots or [] if isinstance(s, dict)}
    out = []
    for s in new_slots or []:
        if not isinstance(s, dict):
            continue
        r0, r1 = old.get(s.get("slot")), s.get("rect")
        if (not isinstance(r0, (list, tuple)) or not isinstance(r1, (list, tuple)) or len(r0) != 4 or len(r1) != 4
                or any(abs(float(a) - float(b)) > tol for a, b in zip(r0, r1))):
            out.append(s.get("slot"))
    return sorted(n for n in out if n is not None)


def apply_layout(cfg: dict, new_slots: list[dict]) -> dict:
    """Validate a complete box layout. Returns {"ok", "config", "moved", "errors", "conflicts"}.
    "config" is the new active configuration when ok, else the unchanged cfg (never a partial layout)."""
    candidate = {**cfg, "slots": new_slots}
    errors = config_errors(candidate)
    shaped = [s for s in new_slots or [] if isinstance(s, dict) and isinstance(s.get("slot"), int)
              and isinstance(s.get("rect"), (list, tuple)) and len(s["rect"]) == 4]
    conflicts = [[a, b] for a, b in overlapping_slots(shaped)]
    if errors:
        return {"ok": False, "config": cfg, "moved": [], "errors": errors, "conflicts": conflicts}
    return {"ok": True, "config": candidate, "moved": moved_slots(cfg.get("slots"), new_slots),
            "errors": [], "conflicts": []}


def recal_after_move(recal: dict | None, moved: list[int]) -> dict:
    """Mark moved slots as needing new photos: slot -> kinds saved since the move (none yet)."""
    out = {str(k): list(v) for k, v in (recal or {}).items()}
    for n in moved:
        out[str(n)] = []
    return out


def recal_note_photo(recal: dict | None, slot: int, kind: str) -> dict:
    """Record a saved photo; a slot is done once both its empty and full photos are new."""
    out = {str(k): list(v) for k, v in (recal or {}).items()}
    key = str(slot)
    if key in out:
        kinds = sorted(set(out[key]) | {kind})
        if {"empty", "full"} <= set(kinds):
            out.pop(key)
        else:
            out[key] = kinds
    return out


def needs_recal(recal: dict | None, slot: int) -> bool:
    return str(slot) in (recal or {})


def gate_layout(reading: tuple, layout_ok: bool, recal: bool, setup: bool) -> tuple:
    """classify()'s (state, fill, why), made uncertain while editing, with an invalid saved layout, or
    for a moved box without new photos. Uncertain never becomes a stock change."""
    st, fill, why = reading
    if st is None:
        return reading
    if setup:
        return "uncertain", fill, SETUP_WHY
    if not layout_ok:
        return "uncertain", fill, LAYOUT_WHY
    if recal:
        return "uncertain", fill, RECAL_WHY
    return reading
