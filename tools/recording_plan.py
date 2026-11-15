"""Guided shelf recordings for tools/record_sequence.py, for the ACTIVE product set (products/active.json).

Labels are kept per PRODUCT, never per place: where a product stands does not affect its identity, recognition or
count. Any arrangement on the shelf works; the left-to-right order in the set file (`recording.arrangement_example`)
is only an example for the setup text, and nothing asks to confirm or configure positions. (The data keeps the key
"slots" for the label keys, which are product identities here.)

Each step is one action the person does and marks with Enter at the moment they do it; the mark is stored on the
recording clock (the same clock as the frame times in meta.jsonl). Steps change the contents through explicit
operations, starting from the unit counts the person types before the recording (never from configured target
stock). Label values per product: its identity (the ground truth, also while its flavour text is turned away),
"empty" (none of it on the shelf) or "obstructed" (a hand covers it); the unfamiliar item is "unknown" and a removed
product such as the Salted Caramel shake is "not-inventory", each under its own key. Counts are units (bottles,
cartons), never packs. Reference captures teach a model; evaluation captures are only scored. Purchasing is a
separate question: the signed rule set decides it and shelf events stay observe-only, so no recording can lead to an
order.

The props set of 14 Nov had its own three-slot plan; that set is archived unchanged in
products/archive/kitchen-20261114-b.json and its recordings keep their identities.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

SET_FILE = Path(__file__).resolve().parents[1] / "products" / "active.json"
PRODUCTS = json.loads(SET_FILE.read_text())
SET_ID = PRODUCTS["set_id"]
_REC = PRODUCTS.get("recording") or {}
_BY = {p["identity"]: p for p in PRODUCTS["products"]}
KEYS = [p["identity"] for p in PRODUCTS["products"]]  # one label key per active product, in the set file's order
SLOTS = KEYS  # name kept for tools/record_sequence.py: the keys the person types counts for
IDENTITY = {k: k for k in KEYS}
LABEL = {k: _BY[k].get("short_label") or _BY[k]["label"] for k in KEYS}
UNIT = {k: _BY[k].get("count_unit") or "item" for k in KEYS}
UNFAMILIAR = _REC["unfamiliar"]["identity"]
UNFAMILIAR_LABEL = _REC["unfamiliar"]["label"]
REMOVED = (_REC.get("removed_flavour") or {}).get("identity")
REMOVED_LABEL = (_REC.get("removed_flavour") or {}).get("label")
EXTRA = {UNFAMILIAR: "unknown", **({REMOVED: "not-inventory"} if REMOVED else {})}  # label of an extra item present
NO_REQUESTS = {k: 0 for k in KEYS}  # observe-only: no recording may lead to an order
EXAMPLE = _REC.get("arrangement_example") or "any arrangement"
SHAKES = [k for k in KEYS if _BY[k].get("family") and (PRODUCTS["families"].get(_BY[k]["family"]) or {}).get("flavour_required")]
BONNE, AITO = ("aimo:bonne-orange-juice" if "aimo:bonne-orange-juice" in _BY else None,
               "aimo:aito-barista-oat" if "aimo:aito-barista-oat" in _BY else None)
STOCKED = f"every product on the shelf in any arrangement ({EXAMPLE})"


def _poses(k: str) -> list[dict]:
    """Orientations for the reference gallery, most of them with the barcode hidden (routine recognition must
    work without a visible barcode). Start: front facing the camera, barcode hidden."""
    what = LABEL[k]
    poses = [{"at": 5, "do": f"turn one {what} to its side (barcode hidden)", "ops": [("pose", k, "side", False)]},
             {"at": 10, "do": f"turn it so its BARCODE faces the camera", "ops": [("pose", k, "barcode-side", True)]},
             {"at": 15, "do": f"turn it to an angle or upside down (barcode hidden)", "ops": [("pose", k, "angled", False)]}]
    if k in SHAKES:  # the flavour text decides the product: also learn the view where it cannot be read
        poses.append({"at": 20, "do": f"turn it so its FLAVOUR TEXT faces away", "ops": [("pose", k, "flavour-hidden", False)]})
    return poses


def _ref(seconds, lighting, setup, steps=(), start="full"):
    return {"role": "reference", "seconds": seconds, "lighting": lighting, "setup": setup, "steps": list(steps),
            "start": start}


def _eval(seconds, lighting, setup, steps, scenario, events):
    return {"role": "evaluation", "seconds": seconds, "lighting": lighting, "setup": setup, "steps": steps,
            "start": "full", "scenario": scenario, "expected_requests": NO_REQUESTS, "expected_stock_events": events}


def _slug(k: str) -> str:
    return k.split(":", 1)[-1]


def _build() -> dict[str, dict]:
    n = len(KEYS)
    plan: dict[str, dict] = {
        # Reference captures: a model may learn from these; never scored. The unfamiliar and removed items never
        # appear here.
        "ref-empty-day": _ref(10, "day", "the shelf empty", start="empty"),
        "ref-full-day": _ref(10, "day", f"{STOCKED}, fronts facing the camera"),
        **{f"ref-{_slug(k)}-poses-day": _ref(25 if k in SHAKES else 20, "day", f"{STOCKED}; one {LABEL[k]} facing the camera",
                                            _poses(k)) for k in KEYS},
        "ref-counts-day": _ref(5 * n + 5, "day", STOCKED, [
            {"at": 5 * (i + 1), "do": f"take ONE {UNIT[k]} of {LABEL[k]} away", "ops": [("take", k, 1)]} for i, k in enumerate(KEYS)]),
        "ref-rearranged-day": _ref(15, "day", f"{STOCKED}", [
            {"at": 5, "do": "rearrange the products into a different order (nothing taken away)",
             "ops": [("cover", k) for k in KEYS]},
            {"at": 9, "do": "hands away", "ops": [("uncover", k) for k in KEYS]}]),
        "ref-full-dim": _ref(10, "dim", f"{STOCKED}, main light off or lamp moved"),
        "ref-empty-dim": _ref(10, "dim", "the shelf empty, main light off or lamp moved", start="empty"),
        # Evaluation recordings: scored only. Record each twice, NAME-a and NAME-b, putting items back by hand.
        "eval-identity-day": _eval(15, "day", f"{STOCKED}, untouched", [], "identity", {}),
        "eval-identity-dim": _eval(15, "dim", f"{STOCKED}, untouched, dim", [], "identity", {}),
        "eval-rearranged-day": _eval(20, "day", STOCKED, [
            {"at": 5, "do": "hands in: rearrange the products into a different order (nothing taken away)",
             "ops": [("cover", k) for k in KEYS]},
            {"at": 10, "do": "hands away: done", "ops": [("uncover", k) for k in KEYS]}],
            "arrangement", {k: "no change: identity and count do not depend on the place" for k in KEYS}),
        "eval-hand-day": _eval(5 * n + 5, "day", STOCKED, [
            step for i, k in enumerate(KEYS) for step in (
                {"at": 1 + 5 * i, "do": f"hand over the {LABEL[k]}", "ops": [("cover", k)]},
                {"at": 4 + 5 * i, "do": f"hand away from the {LABEL[k]}", "ops": [("uncover", k)]})],
            "obstruction", {k: "obstructed only" for k in KEYS}),
        "eval-barcode-hidden-day": _eval(20, "day", f"{STOCKED}, every barcode hidden from the camera", [
            {"at": 5, "do": "turn every item to its side (barcodes stay hidden)", "ops": [("pose", k, "side", False) for k in KEYS]},
            {"at": 10, "do": "turn every item to an angle or upside down (barcodes stay hidden)",
             "ops": [("pose", k, "angled", False) for k in KEYS]}], "orientation", {}),
        "eval-light-change": _eval(20, "day-dim-day", f"{STOCKED}, day light", [
            {"at": 5, "do": "light off", "ops": []}, {"at": 12, "do": "light back on", "ops": []}], "light", {}),
        "eval-unfamiliar-day": _eval(20, "day", f"{STOCKED}; the {UNFAMILIAR_LABEL} ready", [
            {"at": 5, "do": f"put the {UNFAMILIAR_LABEL} on the shelf, anywhere", "ops": [("put", UNFAMILIAR, 1)]}],
            "unfamiliar", {UNFAMILIAR: "unknown item: no identity, no automatic stock change"}),
    }
    if SHAKES:
        plan["eval-flavour-hidden-day"] = _eval(20, "day", f"{STOCKED}, flavour text facing the camera", [
            {"at": 5, "do": "turn every PROfeel shake so its FLAVOUR TEXT faces away",
             "ops": [("pose", k, "flavour-hidden", False) for k in SHAKES]},
            {"at": 12, "do": "turn them back, flavour text facing the camera", "ops": [("pose", k, "front", False) for k in SHAKES]}],
            "flavour", {k: "flavour unreadable while turned: expected 'flavour uncertain', never a guessed flavour" for k in SHAKES})
        if REMOVED:
            plan["eval-removed-flavour-day"] = _eval(20, "day", f"{STOCKED}; a {REMOVED_LABEL} ready (only if you have one)", [
                {"at": 5, "do": f"put the {REMOVED_LABEL} on the shelf, anywhere", "ops": [("put", REMOVED, 1)]}],
                "removed-product", {REMOVED: "not inventory: never counted as " + " or ".join(LABEL[k] for k in SHAKES)})
    if BONNE:
        plan["eval-carton-removal-day"] = _eval(20, "day", STOCKED, [
            {"at": 5, "do": f"take ONE carton of {LABEL[BONNE]} away", "ops": [("take", BONNE, 1)]}],
            "removal", {BONNE: "count decreases by 1 carton (the case of 18 is the ordering unit, not the count)"})
    if AITO:
        plan["eval-removal-all-day"] = _eval(20, "day", STOCKED, [
            {"at": 5, "do": f"take ALL {LABEL[AITO]} away", "ops": [("take", AITO, "all")]}], "removal", {AITO: "empty"})
    return plan


PLAN: dict[str, dict] = _build()

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


def _extras(plan: dict) -> list[str]:
    return [op[1] for st in plan["steps"] for op in st["ops"] if op[0] == "put"]


def start_content(plan: dict, counts: dict[str, int]) -> dict:
    """Contents per product at t = 0 from the counts the person typed (empty plans start at zero). Extra items the
    plan puts on the shelf (unfamiliar, removed flavour) start absent."""
    out = {}
    for k in KEYS:
        n = 0 if plan["start"] == "empty" else int(counts[k])
        out[k] = {"identity": k if n > 0 else None, "count": n, "covered": False,
                  "orientation": "front" if n > 0 else None, "barcode_visible": False if n > 0 else None}
    for k in _extras(plan):
        out[k] = {"identity": None, "count": 0, "covered": False, "orientation": None, "barcode_visible": None}
    return out


def apply_ops(content: dict, ops: list) -> dict:
    c = copy.deepcopy(content)
    for op in ops:
        kind = op[0]
        if kind == "pose":
            c[op[1]]["orientation"], c[op[1]]["barcode_visible"] = op[2], op[3]
        elif kind == "take":
            k, n = op[1], op[2]
            c[k]["count"] = 0 if n == "all" else max(0, c[k]["count"] - n)
        elif kind == "put":
            k, n = op[1], op[2]
            c[k]["identity"], c[k]["count"] = k, c[k]["count"] + n
            c[k]["orientation"], c[k]["barcode_visible"] = "as placed", None
        elif kind in ("cover", "uncover"):
            c[op[1]]["covered"] = kind == "cover"
        for k in c:
            if c[k]["count"] == 0:
                c[k]["identity"], c[k]["orientation"], c[k]["barcode_visible"] = None, None, None
    return c


def label_value(content: dict, key: str | None = None) -> str:
    if content["covered"]:
        return "obstructed"
    if content["count"] == 0:
        return "empty"
    ident = content["identity"]
    return EXTRA.get(ident, ident) if ident not in _BY else ident


def label_windows(content: dict, steps: list[dict], marks: list[float], end: float) -> tuple[list[dict], dict]:
    """Per-product windows [from_t, to_t) between marks with label, count and identity; the transition around
    each mark stays unlabelled. Returns (windows, final contents)."""
    windows = []
    since = {k: 0.0 for k in content}
    for step, t in zip(steps, marks):
        new = apply_ops(content, step["ops"])
        for k in content:
            if new[k] != content[k]:
                if since[k] < t - TRANSITION_BEFORE:
                    windows.append(_window(since[k], t - TRANSITION_BEFORE, k, content[k]))
                since[k] = t + TRANSITION_AFTER
        content = new
    for k in content:
        if since[k] < end:
            windows.append(_window(since[k], end, k, content[k]))
    return sorted(windows, key=lambda w: (w["from_t"], list(w["slots"]))), content


def _window(a: float, b: float, k: str, c: dict) -> dict:
    return {"from_t": round(a, 3), "to_t": round(b, 3), "slots": {k: label_value(c)},
            "counts": {k: None if c["covered"] else c["count"]}, "identities": {k: c["identity"]},
            "orientation": {k: c["orientation"]}, "barcode_visible": {k: c["barcode_visible"]}, "validated": True}
