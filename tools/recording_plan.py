"""Guided shelf recordings for tools/record_sequence.py, for the products in tools/recording_products.json.

Each step is one action the person does and marks with Enter at the moment they do it; the mark is stored on
the recording clock (the same clock as the frame times in meta.jsonl). Steps change the slot contents through
explicit operations, starting from the item counts the person types before the recording (never from the
configured target stock). Label values per slot: the observed identity of tools/recording_products.json,
"empty", "unknown" (the unfamiliar item, never in the reference gallery) or "obstructed" (a hand covers it).
Reference captures teach a model; evaluation captures are only scored. Purchasing is a separate question: the
signed rule set decides it (every slot of the current set is unmapped, so no recording can lead to an order).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

PRODUCTS = json.loads((Path(__file__).resolve().parent / "recording_products.json").read_text())
SET_ID = PRODUCTS["set_id"]
SLOTS = sorted(PRODUCTS["slots"])
IDENTITY = {s: PRODUCTS["slots"][s]["identity"] for s in SLOTS}
LABEL = {s: PRODUCTS["slots"][s]["label"] for s in SLOTS}
UNFAMILIAR = PRODUCTS["unfamiliar"]["identity"]
NO_REQUESTS = {s: 0 for s in SLOTS}  # every slot is unmapped for purchasing: no recording may lead to an order


def _poses(slot: str) -> list[dict]:
    """Orientations for the reference gallery, most of them with the barcode hidden (routine recognition must
    work without a visible barcode). Start: front facing the camera, barcode hidden."""
    what = LABEL[slot]
    return [{"at": 5, "do": f"turn the {what} in slot {slot} to its side (barcode hidden)",
             "ops": [("pose", slot, "side", False)]},
            {"at": 10, "do": f"turn the {what} in slot {slot} so its BARCODE faces the camera",
             "ops": [("pose", slot, "barcode-side", True)]},
            {"at": 15, "do": f"turn the {what} in slot {slot} to an angle or upside down (barcode hidden)",
             "ops": [("pose", slot, "angled", False)]}]


def _ref(seconds, lighting, setup, steps=(), start="full"):
    return {"role": "reference", "seconds": seconds, "lighting": lighting, "setup": setup, "steps": list(steps),
            "start": start}


def _eval(seconds, lighting, setup, steps, scenario, events):
    return {"role": "evaluation", "seconds": seconds, "lighting": lighting, "setup": setup, "steps": steps,
            "start": "full", "scenario": scenario, "expected_requests": NO_REQUESTS, "expected_stock_events": events}


L1, L2, L3 = LABEL["1"], LABEL["2"], LABEL["3"]
PLAN: dict[str, dict] = {
    # Reference captures: a model may learn from these; never scored. The unfamiliar item never appears here.
    "ref-empty-day": _ref(10, "day", "all three slots empty", start="empty"),
    "ref-full-day": _ref(10, "day", "every item you have in its slot, fronts facing the camera"),
    "ref-s1-poses-day": _ref(20, "day", f"all slots stocked; the {L1} in slot 1 facing the camera", _poses("1")),
    "ref-s2-poses-day": _ref(20, "day", f"all slots stocked; the {L2} in slot 2 facing the camera", _poses("2")),
    "ref-s3-poses-day": _ref(20, "day", f"all slots stocked; the {L3} in slot 3 facing the camera", _poses("3")),
    "ref-counts-day": _ref(20, "day", "all slots stocked", [
        {"at": 5, "do": f"take ONE {L1} out of slot 1 and keep it away", "ops": [("take", "1", 1)]},
        {"at": 10, "do": f"take ONE {L2} out of slot 2 and keep it away", "ops": [("take", "2", 1)]},
        {"at": 15, "do": f"take ONE {L3} out of slot 3 and keep it away", "ops": [("take", "3", 1)]}]),
    "ref-full-dim": _ref(10, "dim", "all slots stocked, main light off or lamp moved"),
    "ref-empty-dim": _ref(10, "dim", "all three slots empty, main light off or lamp moved", start="empty"),
    # Evaluation recordings: scored only. Record each twice, NAME-a and NAME-b, putting items back by hand.
    "eval-identity-day": _eval(15, "day", "all slots stocked, untouched", [], "identity", {}),
    "eval-identity-dim": _eval(15, "dim", "all slots stocked, untouched, dim", [], "identity", {}),
    "eval-removal-one-s3-day": _eval(20, "day", "all slots stocked", [
        {"at": 5, "do": f"take ONE {L3} out of slot 3 and keep it away", "ops": [("take", "3", 1)]}],
        "removal", {"3": "count decreases by 1"}),
    "eval-removal-all-s3-day": _eval(20, "day", "all slots stocked", [
        {"at": 5, "do": f"take ALL {L3} out of slot 3 and keep them away", "ops": [("take", "3", "all")]}],
        "removal", {"3": "empty"}),
    "eval-swap-s2-s3-day": _eval(20, "day", "all slots stocked", [
        {"at": 5, "do": f"hands in: start swapping the {L2} (slot 2) and the {L3} (slot 3)",
         "ops": [("cover", "2"), ("cover", "3")]},
        {"at": 8, "do": "hands away: swap done", "ops": [("swap", "2", "3"), ("uncover", "2"), ("uncover", "3")]}],
        "moved", {"2": "identity changes, nothing consumed", "3": "identity changes, nothing consumed"}),
    "eval-move-s3-to-s2-day": _eval(20, "day", "all slots stocked", [
        {"at": 5, "do": f"take ALL {L2} out of slot 2 and keep them away", "ops": [("take", "2", "all")]},
        {"at": 8, "do": f"move ONE {L3} from slot 3 into slot 2", "ops": [("move", "3", "2", 1)]}],
        "moved", {"2": "empty, then holds a moved item", "3": "count decreases by 1 (moved, not consumed)"}),
    "eval-unfamiliar-s3-day": _eval(20, "day", "all slots stocked; the fitness bar ready", [
        {"at": 5, "do": f"take ALL {L3} out of slot 3 and put the fitness bar there",
         "ops": [("take", "3", "all"), ("put", "3", UNFAMILIAR, 1)]}],
        "unfamiliar", {"3": "unknown item: no identity, no automatic stock change"}),
    "eval-hand-day": _eval(20, "day", "all slots stocked", [
        {"at": 3, "do": "hand over slot 1", "ops": [("cover", "1")]},
        {"at": 6, "do": "hand away from slot 1", "ops": [("uncover", "1")]},
        {"at": 9, "do": "hand over slot 2", "ops": [("cover", "2")]},
        {"at": 12, "do": "hand away from slot 2", "ops": [("uncover", "2")]},
        {"at": 15, "do": "hand over slot 3", "ops": [("cover", "3")]},
        {"at": 18, "do": "hand away from slot 3", "ops": [("uncover", "3")]}],
        "obstruction", {"1": "obstructed only", "2": "obstructed only", "3": "obstructed only"}),
    "eval-barcode-hidden-day": _eval(20, "day", "all slots stocked, every barcode hidden from the camera", [
        {"at": 5, "do": "turn every item to its side (barcodes stay hidden)",
         "ops": [("pose", s, "side", False) for s in SLOTS]},
        {"at": 10, "do": "turn every item to an angle or upside down (barcodes stay hidden)",
         "ops": [("pose", s, "angled", False) for s in SLOTS]}],
        "orientation", {}),
    "eval-light-change": _eval(20, "day-dim-day", "all slots stocked, day light", [
        {"at": 5, "do": "light off", "ops": []}, {"at": 12, "do": "light back on", "ops": []}],
        "light", {}),
}

TRANSITION_BEFORE, TRANSITION_AFTER = 0.5, 1.5  # seconds around each mark left unlabelled (hands moving)


def plan_for(name: str) -> dict | None:
    """The plan of a recording name; evaluation names carry an occasion suffix (-a, -b, ...)."""
    base, occasion = name, None
    tail = name.rsplit("-", 1)[-1]
    if name.startswith("eval-") and len(tail) == 1 and tail in "abcdef":
        base, occasion = name.rsplit("-", 1)
    p = PLAN.get(base)
    if p is None:
        return None
    if p["role"] == "evaluation" and not occasion:
        raise SystemExit(f"evaluation recordings are made twice: name it {name}-a or {name}-b")
    return {**copy.deepcopy(p), "base": base, "occasion": occasion}


def start_content(plan: dict, counts: dict[str, int]) -> dict:
    """Slot contents at t = 0 from the counts the person typed (empty plans start at zero)."""
    out = {}
    for s in SLOTS:
        n = 0 if plan["start"] == "empty" else int(counts[s])
        out[s] = {"identity": IDENTITY[s] if n > 0 else None, "count": n, "covered": False,
                  "orientation": "front" if n > 0 else None, "barcode_visible": False if n > 0 else None}
    return out


def apply_ops(content: dict, ops: list) -> dict:
    c = copy.deepcopy(content)
    for op in ops:
        kind = op[0]
        if kind == "pose":
            c[op[1]]["orientation"], c[op[1]]["barcode_visible"] = op[2], op[3]
        elif kind == "take":
            s, n = op[1], op[2]
            c[s]["count"] = 0 if n == "all" else max(0, c[s]["count"] - n)
        elif kind == "put":
            s, identity, n = op[1], op[2], op[3]
            c[s]["identity"], c[s]["count"] = identity, c[s]["count"] + n
            c[s]["orientation"], c[s]["barcode_visible"] = "as placed", None
        elif kind == "move":
            a, b, n = op[1], op[2], op[3]
            n = min(n, c[a]["count"])
            c[b]["identity"] = c[a]["identity"]
            c[a]["count"] -= n
            c[b]["count"] += n
            c[b]["orientation"], c[b]["barcode_visible"] = "as placed", None
        elif kind == "swap":
            a, b = op[1], op[2]
            c[a], c[b] = c[b], c[a]
            c[a]["covered"], c[b]["covered"] = c[b]["covered"], c[a]["covered"]
            for x in (a, b):
                c[x]["orientation"], c[x]["barcode_visible"] = "as placed", None
        elif kind in ("cover", "uncover"):
            c[op[1]]["covered"] = kind == "cover"
        for s in c:
            if c[s]["count"] == 0:
                c[s]["identity"], c[s]["orientation"], c[s]["barcode_visible"] = None, None, None
    return c


def label_value(slot_content: dict) -> str:
    if slot_content["covered"]:
        return "obstructed"
    if slot_content["count"] == 0:
        return "empty"
    return "unknown" if slot_content["identity"] == UNFAMILIAR else slot_content["identity"]


def label_windows(content: dict, steps: list[dict], marks: list[float], end: float) -> tuple[list[dict], dict]:
    """Per-slot windows [from_t, to_t) between marks with label, count and identity; the transition around
    each mark stays unlabelled. Returns (windows, final contents)."""
    windows = []
    since = {s: 0.0 for s in content}
    for step, t in zip(steps, marks):
        new = apply_ops(content, step["ops"])
        for s in content:
            if new[s] != content[s]:
                if since[s] < t - TRANSITION_BEFORE:
                    windows.append(_window(since[s], t - TRANSITION_BEFORE, s, content[s]))
                since[s] = t + TRANSITION_AFTER
        content = new
    for s in content:
        if since[s] < end:
            windows.append(_window(since[s], end, s, content[s]))
    return sorted(windows, key=lambda w: (w["from_t"], list(w["slots"]))), content


def _window(a: float, b: float, s: str, c: dict) -> dict:
    return {"from_t": round(a, 3), "to_t": round(b, 3), "slots": {s: label_value(c)},
            "counts": {s: None if c["covered"] else c["count"]}, "identities": {s: c["identity"]},
            "orientation": {s: c["orientation"]}, "barcode_visible": {s: c["barcode_visible"]}, "validated": True}
