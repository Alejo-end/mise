"""Hybrid recognition of one camera frame: the board's local model plus the automatic whole-frame cloud check,
reconciled into one object list (since 15 Nov 06:40). Observational only: no stock events, no orders.

The box sends one frame with its local model's detections of exactly that frame. Local boxes that overlap are one
object. A local object keeps its identity only when all of these hold: its class has a verified meaning (the model's
manifest, from the trainer's export) that is an active product the coverage map lets the model cover, its score is
above that product's threshold (kimi_at_or_below: 0.95 for the shakes, 0.85 for the others), its boxes agree and no
other flavour is close. Every other local object is escalated: unmapped classes (no trainer metadata), low scores,
conflicts, removed or old products.

The cloud side is the whole-frame check (vision.recognize with the labelled reference photos), done by the box on the
first observation and on every changed, settled scene, or the same check reused while the scene is unchanged. It
finds what the local model does not cover (Aito has no local class) or missed, and it resolves the escalated local
objects in the same request: a local object and a cloud object at the same place are one object, never two counts.
Only an escalated local object the whole-frame check did not cover gets a crop check of its own (at most CROP_LIMIT,
one request at a time; an answer is reused while that box and its picture stay the same).

Conflicts are explicit: a confident local identity and a different cloud identity at the same place make one
ambiguous object that could be either, never a count of both. A failed or skipped cloud check leaves every product
"uncertain" or "at least" (scene_unverified): neither a missing local box nor a cloud timeout means zero stock.
"""
from __future__ import annotations

import asyncio
import io
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import vision
from .reconcile import Reconciler, iou, overlap_of_smaller, thumb

MATCH_IOU = 0.25           # a local box and a cloud box at the same place: IoU at least this ...
MATCH_OVERLAP = 0.6        # ... or this share of the smaller box inside the other (a box on a carton's top)
CROP_LIMIT = 2             # crop checks per frame, for escalated local objects the whole-frame check did not cover
BUDGET_S = 60.0            # no new crop check starts after this long (each <= 35 s; the box waits up to 110 s)
CROP_TIMEOUT_S = 35.0
CROP_REUSE_S = 600.0
SOURCE_TEXT = {"local": "local vision", "kimi": "Kimi", "local+kimi": "local vision + Kimi",
               "conflict": "local vision vs Kimi", "pending": "identity pending"}
CLOUD_KEYS = ("status", "identity", "family", "label", "removed_identity", "could_be", "could_be_any", "description",
              "brand", "variant", "size", "readable_text", "evidence", "obstruction", "text_evidence", "visibility",
              "match_level", "match_by", "match_reason", "candidates", "visible_count", "box")


def _box(b):
    try:
        x, y, w, h = (float(v) for v in b)
    except (TypeError, ValueError):
        return None
    if not all(np.isfinite([x, y, w, h])) or w <= 0 or h <= 0:
        return None
    x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
    return [round(x, 4), round(y, 4), round(min(w, 1.0 - x), 4), round(min(h, 1.0 - y), 4)]


def local_objects(dets: list[dict], ps, cov: dict) -> list[dict]:
    """The local model's boxes of one frame as objects (overlapping boxes are one object), each with the identity its
    class verifiably has (or None) and why it is escalated (empty: the local identity stands)."""
    classes = cov["classes"]
    min_score = float(ps.routing.get("display_min_score", 0.3))
    clean = []
    for d in dets or []:
        b, k = _box(d.get("box")), d.get("class_id")
        if b is None or float(d.get("score", 0)) < min_score:
            continue
        c = classes[k] if isinstance(k, int) and 0 <= k < len(classes) else {"identity": None, "kind": "unmapped"}
        cs = d.get("class_scores")
        # class scores by identity only where the class order is verified (a list of numbers otherwise)
        if isinstance(cs, list) and cov["class_map"] == "verified":
            cs = {classes[i]["name"]: float(v) for i, v in enumerate(cs) if i < len(classes) and classes[i]["name"]}
        clean.append({"box": b, "score": float(d.get("score", 0)), "class_id": k, "identity": c["identity"],
                      "kind": c["kind"], "class_scores": cs if isinstance(cs, dict) else None})
    out = []
    for g in Reconciler.group(clean, ps):
        top = g["members"][0]
        why = Reconciler.escalation(g, ps, cov)
        out.append({"box": g["box"], "class_id": top["class_id"], "score": round(top["score"], 4), "identity": top["identity"],
                    "kind": top["kind"], "escalation": why,
                    "members": [{"class_id": m["class_id"], "score": round(m["score"], 4), "identity": m["identity"]} for m in g["members"]]})
    return out


def _match(lobjs: list[dict], cobjs: list[dict]):
    """Local object -> cloud object at the same place. A cloud object of n visible units takes at most n local boxes."""
    cands = []
    for i, L in enumerate(lobjs):
        for j, C in enumerate(cobjs):
            cb = _box(C.get("box"))
            if cb is None:
                continue
            u, ov = iou(L["box"], cb), overlap_of_smaller(L["box"], cb)
            if u >= MATCH_IOU or ov >= MATCH_OVERLAP:
                cands.append((u + ov, i, j))
    cap = {j: max(1, int(C.get("visible_count") or 1)) for j, C in enumerate(cobjs)}
    pairs, used = {}, {}
    for _, i, j in sorted(cands, reverse=True):
        if i in pairs or used.get(j, 0) >= cap[j]:
            continue
        pairs[i] = j
        used[j] = used.get(j, 0) + 1
    return pairs, used, cap


def _local_info(L: dict) -> dict:
    return {"class_id": L["class_id"], "score": L["score"], "identity": L["identity"], "class_kind": L["kind"],
            "escalation": L["escalation"], "boxes": len(L["members"])}


def _kimi_info(C: dict, j: int, cloud: dict) -> dict:
    return {"object": j + 1, "status": C.get("status"), "identity": C.get("identity") or C.get("removed_identity"),
            "match_by": C.get("match_by"), "reused_from": cloud.get("from_request") if cloud.get("status") == "reused" else None}


def _known(ps, ident, L, why: str) -> dict:
    return {"status": "known", "identity": ident, "family": None, "label": ps.label(ident), "removed_identity": None,
            "could_be": [], "could_be_any": False, "description": ps.display(ident), "match_reason": why}


def _open_scope(ps, L) -> tuple[list, bool]:
    """What an escalated local object nobody identified could be: its own mapped product (and its flavour family's
    other products), the family of a mapped removed flavour, or anything when its class has no verified meaning."""
    ident = L["identity"]
    fam = ps.family_of(ident) if ident else None
    could = [p["identity"] for p in ps.products if (ident and p["identity"] == ident) or (fam and p.get("family") == fam)]
    return could, not could


def merge(lobjs: list[dict], cloud: dict, ps) -> tuple[list[dict], dict]:
    """One object list from the local objects and the cloud objects of the same frame (see the module text)."""
    cobjs = cloud.get("objects") or []
    pairs, used, cap = _match(lobjs, cobjs)
    cloud_ran = cloud.get("status") in ("ok", "reused")
    merged, conflicts = [], []
    for i, L in enumerate(lobjs):
        loc = _local_info(L)
        if i in pairs:
            j = pairs[i]
            C = cobjs[j]
            kimi = _kimi_info(C, j, cloud)
            base = {k: C.get(k) for k in CLOUD_KEYS if k in C}
            if not L["escalation"]:  # a confident, verified, locally covered product
                if C.get("status") == "known" and C.get("identity") == L["identity"]:
                    o = {**base, **_known(ps, L["identity"], L, "local model and Kimi agree"), "description": C.get("description")}
                    by = "local+kimi"
                elif C.get("status") in ("known", "not-inventory"):
                    other = C.get("identity") or C.get("removed_identity")
                    could = [x for x in (L["identity"], C.get("identity")) if x in ps.by_identity]
                    why = (f"local model: {ps.display(L['identity'])} ({L['score']:.2f}); Kimi: "
                           f"{ps.display(other) if other else 'not inventory'}")
                    o = {**base, "status": "ambiguous", "identity": None, "family": None, "label": "ambiguous",
                         "removed_identity": None, "could_be": could, "could_be_any": False, "match_reason": why}
                    conflicts.append({"local": L["identity"], "kimi": other, "kimi_status": C.get("status"), "box": L["box"], "why": why})
                    by = "conflict"
                else:  # Kimi left it open: the confident local identity stands
                    o = {**base, **_known(ps, L["identity"], L, f"local model {L['score']:.2f}; Kimi undecided ({C.get('status')})")}
                    by = "local"
            else:
                o = dict(base)
                by = "kimi"
            # a local box on part of the package (a carton's top) is drawn as the cloud model's whole-package box
            cb = _box(C.get("box"))
            part = cb is not None and L["box"][2] * L["box"][3] < 0.5 * cb[2] * cb[3] and overlap_of_smaller(L["box"], cb) >= 0.8
            o.update(box=cb if part else L["box"], visible_count=1,
                     source={"identity_by": by, "box_by": "kimi (the local box covered part of it)" if part else "local",
                             "local": {**loc, "box": L["box"]}, "kimi": kimi})
        elif not L["escalation"]:
            o = {**_known(ps, L["identity"], L, f"local model {L['score']:.2f} above its threshold"), "box": L["box"],
                 "visible_count": 1, "source": {"identity_by": "local", "box_by": "local", "local": loc, "kimi": None}}
        else:
            could, anything = _open_scope(ps, L)
            what = f"class {L['class_id']}" + (f" = {ps.display(L['identity'])}" if L["identity"] else "")
            o = {"status": "unknown", "identity": None, "family": None, "label": "unknown", "removed_identity": None,
                 "could_be": could, "could_be_any": anything, "visible_count": 1, "box": L["box"],
                 "description": f"local detection ({what}, score {L['score']:.2f})",
                 "match_reason": ("Kimi's whole-frame check did not find an object here" if cloud_ran else
                                  "no cloud check of this frame yet" if cloud.get("status") == "none" else
                                  f"cloud check failed ({cloud.get('error', 'no answer')})"),
                 "needs_crop": cloud_ran,
                 "source": {"identity_by": "pending", "box_by": "local", "local": loc, "kimi": None}}
        merged.append(o)
    for j, C in enumerate(cobjs):
        n = int(C.get("visible_count") or 1)
        rest = n - used.get(j, 0)
        if rest <= 0:
            continue
        o = {k: C.get(k) for k in CLOUD_KEYS if k in C}
        o.update(visible_count=rest, source={"identity_by": "kimi", "box_by": "kimi", "local": None,
                                              "kimi": {**_kimi_info(C, j, cloud), "units_beside_local_boxes": rest if used.get(j) else None}})
        merged.append(o)
    merged.sort(key=lambda o: (o.get("box") is None, (o["box"][0] + o["box"][2] / 2) if o.get("box") else 0))
    info = {"matches": [{"local_object": i + 1, "kimi_object": j + 1} for i, j in sorted(pairs.items())],
            "conflicts": conflicts, "local_objects": len(lobjs), "kimi_objects": len(cobjs),
            "kimi_only": sum(1 for j in range(len(cobjs)) if not used.get(j)),
            "local_only": sum(1 for i in range(len(lobjs)) if i not in pairs)}
    return merged, info


class CropCache:
    """Crop-check answers, reused while the object's box and picture stay the same (as in reconcile.py)."""

    def __init__(self, clock=time.monotonic):
        self.clock, self.entries = clock, []

    def find(self, img, box, ps):
        now, r = self.clock(), ps.routing
        self.entries = [e for e in self.entries if now - e["at"] <= CROP_REUSE_S]
        th = thumb(img, box)
        for e in reversed(self.entries):
            if iou(e["box"], box) >= float(r.get("scene_unchanged_box_iou", 0.7)) and \
                    float(np.abs(e["thumb"] - th).mean()) <= float(r.get("scene_unchanged_mean_abs_diff", 8.0)):
                return e
        return None

    def store(self, img, box, answer):
        self.entries = (self.entries + [{"box": box, "thumb": thumb(img, box), "answer": answer, "at": self.clock()}])[-50:]


def _apply_crop(o: dict, r: dict, ps) -> dict | None:
    """The crop check's decision for one local object; None when it found no package in the box (dismissed)."""
    inside = [x for x in r.get("objects", []) if x.get("location") == vision.LOCATIONS[0]]
    src = {**o["source"], "identity_by": "kimi", "kimi": {"crop_check": True, "captured_at": r.get("captured_at")}}
    if not inside:
        return None
    if len(inside) == 1:
        x = inside[0]
        keep = {k: x.get(k) for k in CLOUD_KEYS if k in x and k not in ("box", "visible_count")}
        return {**o, **keep, "needs_crop": False, "source": src}
    could = sorted({x.get("identity") for x in inside if x.get("identity") in ps.by_identity})
    return {**o, "status": "ambiguous", "could_be": could, "could_be_any": not could, "needs_crop": False,
            "match_reason": f"the crop check found {len(inside)} packages in this box", "source": src}


VISION_REASON_PREFIXES = ("view obstructed", "image quality")


async def run(frame_jpeg: bytes, captured_at: str, local: dict | None, mode: str, reuse: dict | None, ps,
              runs_dir: Path, recognize=None, crops: CropCache | None = None, crop_checks: bool = True,
              store_root: Path | None = None, gate: asyncio.Lock | None = None) -> dict:
    """One hybrid observation. mode: "whole-frame" (ask the cloud model now), "reuse" (the box's cached whole-frame
    objects of the unchanged scene, in reuse["objects"]) or "none" (local only; every product stays uncertain)."""
    raw = recognize or vision.recognize
    gate = gate or asyncio.Lock()

    async def recognize(*a, **kw):  # one cloud model request at a time; merging never waits for it
        async with gate:
            return await raw(*a, **kw)
    started = time.perf_counter()
    local = local or {}
    model = local.get("model") or {}
    cov = ps.coverage(model)
    lobjs = [] if local.get("error") else local_objects(local.get("detections") or [], ps, cov)
    cloud: dict = {"mode": mode, "status": "none"}
    vr: dict = {}
    if mode == "whole-frame":
        t = time.perf_counter()
        try:
            vr = await recognize(frame_jpeg, [0, 0, 1, 1], captured_at, runs_dir, pset=ps, **({"store_root": store_root} if store_root else {}))
            cloud.update(status="ok", objects=vr.get("objects") or [])
        except Exception as e:  # noqa: BLE001 - a failed cloud check leaves everything uncertain, never zero
            cloud.update(status="failed", error=f"{type(e).__name__}: {str(e)[:200]}")
        cloud["ms"] = round((time.perf_counter() - t) * 1000)
    elif mode == "reuse" and reuse and isinstance(reuse.get("objects"), list):
        cloud.update(status="reused", objects=reuse["objects"], from_request=reuse.get("request_id"),
                     from_captured_at=reuse.get("captured_at"))
        vr = {k: reuse.get(k) for k in ("scene_obstruction", "image_quality", "references_used")}
    merged, info = merge(lobjs, cloud, ps)
    crop_log, dismissed = [], []
    want = [o for o in merged if o.get("needs_crop")]
    if want and crop_checks:
        img = Image.open(io.BytesIO(frame_jpeg)).convert("RGB")
        crops = crops if crops is not None else CropCache()
        for o in want:
            hit = crops.find(img, o["box"], ps)
            if hit is None:
                if len([c for c in crop_log if not c.get("reused")]) >= CROP_LIMIT or time.perf_counter() - started > BUDGET_S:
                    o["match_reason"] += "; crop check waits for the next recognition"
                    continue
                t = time.perf_counter()
                try:
                    r = await asyncio.wait_for(recognize(frame_jpeg, [0, 0, 1, 1], captured_at, runs_dir, pset=ps,
                                                         region=list(o["box"]), prompted_by={"hybrid": True}),
                                               timeout=CROP_TIMEOUT_S)
                except Exception as e:  # noqa: BLE001
                    crop_log.append({"box": o["box"], "error": f"{type(e).__name__}: {str(e)[:120]}",
                                     "ms": round((time.perf_counter() - t) * 1000)})
                    o["match_reason"] += "; crop check failed"
                    continue
                crops.store(img, o["box"], r)
                crop_log.append({"box": o["box"], "ms": round((time.perf_counter() - t) * 1000), "reused": False})
            else:
                r = hit["answer"]
                crop_log.append({"box": o["box"], "reused": True})
            new = _apply_crop(o, r, ps)
            if new is None:
                dismissed.append({"box": o["box"], "local": o["source"]["local"], "why": "the crop check found no package in this local box"})
                merged.remove(o)
            else:
                merged[merged.index(o)] = new
    for o in merged:
        o.pop("needs_crop", None)
        o.setdefault("obstruction", None)
        o.setdefault("visibility", "visible")
    result = {"captured_at": captured_at, "surface": [0, 0, 1, 1], "objects": merged, "reasons": [], "uncertain": False,
              "mode": "hybrid", "model": vr.get("model") or {"name": vision.MODEL, "device": "Verda API"},
              "provider": "board local model + Verda inference API", "note": vision.NOTE,
              "references_used": vr.get("references_used") or [], "scene_obstruction": vr.get("scene_obstruction"),
              "image_quality": vr.get("image_quality"), "product_set": {"set_id": ps.set_id, "sha256": ps.sha256},
              "timing_ms": {}}
    result["reasons"] += [x for x in (vr.get("reasons") or []) if "unavailable" in x]
    if cloud["status"] not in ("ok", "reused"):
        result["scene_unverified"] = ("no whole-frame Kimi check of this picture" +
                                      (f" ({cloud['error']})" if cloud.get("error") else "") +
                                      ": products without a local box are not counted yet; counts are lower bounds, never zero")
    if local.get("error"):
        result["reasons"].append(f"local model unavailable ({str(local['error'])[:120]}): Kimi only")
    vision.count_objects(result, ps)
    result["visible_items"] = vision.visible_items(result, ps)
    result["timing_ms"] = {"cloud": cloud.get("ms"), "local_board": (local.get("timing_ms") or {}).get("total"),
                           "crop_checks": sum(c.get("ms", 0) for c in crop_log), "total_service": round((time.perf_counter() - started) * 1000)}
    result["hybrid"] = {"coverage": cov, "local": {"model": model.get("name"), "model_sha256_16": model.get("sha256_16"),
                                                    "boxes": len(local.get("detections") or []), "objects": len(lobjs),
                                                    "escalated": sum(1 for L in lobjs if L["escalation"]),
                                                    "timing_ms": local.get("timing_ms"), "error": local.get("error")},
                        "cloud": {k: v for k, v in cloud.items() if k != "objects"}, **info,
                        "crop_checks": crop_log, "dismissed_local_boxes": dismissed,
                        # the whole-frame check's own objects, for the box to reuse while the scene stays unchanged
                        "cloud_objects": cloud.get("objects") if cloud["status"] in ("ok", "reused") else None,
                        "cloud_extra": {k: vr.get(k) for k in ("scene_obstruction", "image_quality", "references_used")}
                        if cloud["status"] in ("ok", "reused") else None}
    return result
