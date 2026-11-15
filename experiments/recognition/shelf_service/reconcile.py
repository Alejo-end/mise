"""Automatic cloud fallback for the local model's detections (detection/repeat mode). Observational only.

The page sends each frame's local detections together with that frame. Overlapping boxes are one object (a box of
one product on top of a box of another is a conflict, never two units). An object is counted from the local model
only when all of these hold: its class is a product of the active set, its score is ABOVE that product's routing
threshold (kimi_at_or_below in the shared product set; separate from the minimum score for showing a box), its
boxes agree on the class, and, for a flavour family, no other flavour's class score is within flavour_margin.
Every other object is resolved by the cloud model from a crop plus context of the same frame
(vision.recognize(region=...) then vision.resolve_selected): only items inside the selected box count, items around
it are never added.

Cloud requests: one in flight, with a timeout; others wait their turn (shown as queued). An answer is reused only
while the object's box and its crop stay the same; it never changes an observation already returned, and a late
answer is dropped if a newer answer for the same object exists. A failure or timeout leaves the object uncertain,
never zero, and is retried after a pause. Objects the local model did not detect are not here: the whole-picture
check ("Recognize shelf") stays the way to find missed objects. Nothing here creates stock events or orders.
"""
from __future__ import annotations

import asyncio
import io
import time

import numpy as np
from PIL import Image

from . import product_set as PS
from .vision import order_units

RETRY_AFTER_S = 30.0
THUMB = 16
WAITING = ("asking cloud", "queued for cloud", "awaiting cloud")


class Rejected(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _area(b):
    return max(0.0, b[2]) * max(0.0, b[3])


def _inter(a, b):
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    return ix * iy


def iou(a, b) -> float:
    i = _inter(a, b)
    u = _area(a) + _area(b) - i
    return i / u if u > 0 else 0.0


def overlap_of_smaller(a, b) -> float:
    s = min(_area(a), _area(b))
    return _inter(a, b) / s if s > 0 else 0.0


def thumb(img: Image.Image, box, margin: float = 0.25) -> np.ndarray:
    """A small grey picture of the box and its margin: the object's appearance, to tell whether the scene changed."""
    W, H = img.size
    x, y, w, h = box
    x0, y0 = max(0.0, x - margin * w), max(0.0, y - margin * h)
    x1, y1 = min(1.0, x + w + margin * w), min(1.0, y + h + margin * h)
    crop = img.crop((round(x0 * W), round(y0 * H), max(round(x1 * W), round(x0 * W) + 1), max(round(y1 * H), round(y0 * H) + 1)))
    return np.asarray(crop.convert("L").resize((THUMB, THUMB), Image.BILINEAR), dtype=np.float32)


class Reconciler:
    def __init__(self, ask, pset=None, clock=time.monotonic):
        """ask: async (frame_jpeg, box, captured_at) -> a resolution (vision.resolve_selected's shape)."""
        self.ask = ask
        self._pset = pset
        self.clock = clock
        self.cache: list[dict] = []
        self.failures: list[dict] = []
        self.in_flight: dict | None = None
        self.task: asyncio.Task | None = None
        self.last_seq: dict[str, int] = {}  # per page session: a reloaded page starts its frame numbers again
        self.stats = {"observations": 0, "asked": 0, "answered": 0, "failed": 0, "timed_out": 0, "late_dropped": 0,
                      "reused": 0, "rejected_stale": 0}

    def pset(self):
        return self._pset() if callable(self._pset) else (self._pset or PS.active())

    # objects and routing --------------------------------------------------------------------------------------

    @staticmethod
    def group(dets: list[dict], ps) -> list[dict]:
        thr = float(ps.routing.get("conflict_overlap", 0.5))
        objs: list[dict] = []
        for d in sorted(dets, key=lambda d: -float(d.get("score", 0))):
            for o in objs:
                if overlap_of_smaller(o["box"], d["box"]) >= thr:
                    o["members"].append(d)
                    break
            else:
                objs.append({"box": list(d["box"]), "members": [d]})
        return objs

    @staticmethod
    def escalation(obj: dict, ps, cov: dict | None = None) -> list[str]:
        """Why this object goes to the cloud model; empty when the local answer is counted. cov: the model's coverage
        map (ProductSet.coverage): a product it does not cover locally always goes to the cloud."""
        top = obj["members"][0]
        ident, score = top.get("identity"), float(top.get("score", 0))
        prod = ps.by_identity.get(ident)
        why = []
        if prod is not None and cov is not None and not (cov["products"].get(ident) or {}).get("local"):
            why.append(f"{prod['label']} is cloud-covered ({cov['products'].get(ident, {}).get('why', 'coverage map')})")
        idents = sorted({m.get("identity") or f"class {m.get('class_id')}" for m in obj["members"]})
        if len(idents) > 1:
            why.append("conflicting classes: " + " / ".join(ps.label(i) for i in idents))
        if prod is None:
            removed = ps.excluded_by_identity.get(ident)
            why.append(f"class {top.get('class_id')} is {removed['label']}, removed from the inventory: never counted directly"
                       if removed else f"class {top.get('class_id')} has no verified meaning (class order not in the model's "
                                       f"manifest): the cloud check establishes the identity" if not ident
                       else f"class {top.get('class_id')} is not a product of the active set")
            return why
        thr = float(prod["kimi_at_or_below"])
        if score <= thr:
            why.append(f"score {score:.3f} at or below {thr:.2f} ({prod['label']})")
        fam = prod.get("family")
        if fam and (ps.families.get(fam) or {}).get("flavour_required"):
            cs = top.get("class_scores") or {}
            mine = cs.get(ident)
            # every flavour of the family the model knows, removed ones included (a close Salted Caramel is a doubt too)
            others = [(i, float(s)) for i, s in cs.items() if i != ident and ps.family_of(i) == fam]
            margin = float(ps.routing.get("flavour_margin", 0.25))
            if mine is None or not others:
                why.append("flavour scores not reported by the local model: flavour not established")
            else:
                oi, os_ = max(others, key=lambda x: x[1])
                if float(mine) - os_ <= margin:
                    other = ps.by_identity.get(oi) or ps.excluded_by_identity.get(oi) or {}
                    why.append(f"flavour uncertain: {prod.get('short_label') or prod['label']} {float(mine):.2f} "
                               f"vs {other.get('short_label') or ps.label(oi)} {os_:.2f} (margin {margin})")
        return why

    # scene memory ---------------------------------------------------------------------------------------------

    def _same(self, entry: dict, box, th, ps) -> bool:
        r = ps.routing
        return (iou(entry["box"], box) >= float(r.get("scene_unchanged_box_iou", 0.7))
                and float(np.abs(entry["thumb"] - th).mean()) <= float(r.get("scene_unchanged_mean_abs_diff", 8.0)))

    def _find(self, entries, box, th, ps):
        hits = [e for e in entries if self._same(e, box, th, ps)]
        return max(hits, key=lambda e: e["seq"]) if hits else None

    def _expire(self, now, ps):
        max_age = float(ps.routing.get("cloud_answer_max_age_s", 600))
        self.cache = [e for e in self.cache if now - e["answered"] <= max_age]
        self.failures = [f for f in self.failures if now - f["at"] < RETRY_AFTER_S]

    def _store(self, job: dict, resolution: dict, ps) -> None:
        newer = [e for e in self.cache if self._same(e, job["box"], job["thumb"], ps) and e["seq"] > job["seq"]]
        if newer:  # a later frame of the same object was answered already: the late answer changes nothing
            self.stats["late_dropped"] += 1
            return
        self.cache = [e for e in self.cache if not self._same(e, job["box"], job["thumb"], ps)]
        self.cache.append({"box": job["box"], "thumb": job["thumb"], "seq": job["seq"], "captured_at": job["captured_at"],
                           "answered": self.clock(), "resolution": resolution})

    async def _run(self, job: dict, timeout_s: float, ps) -> None:
        try:
            res = await asyncio.wait_for(self.ask(job["frame"], job["box"], job["captured_at"]), timeout=timeout_s)
            self.stats["answered"] += 1
            self._store(job, res, ps)
        except asyncio.TimeoutError:
            self.stats["timed_out"] += 1
            self.failures.append({"box": job["box"], "thumb": job["thumb"], "seq": job["seq"], "at": self.clock(),
                                  "error": f"no answer within {timeout_s:.0f} s"})
        except Exception as e:  # noqa: BLE001 - any failure leaves the object uncertain
            self.stats["failed"] += 1
            self.failures.append({"box": job["box"], "thumb": job["thumb"], "seq": job["seq"], "at": self.clock(),
                                  "error": f"{type(e).__name__}: {str(e)[:160]}"})
        finally:
            self.in_flight = None

    def _start(self, frame, captured_at, seq, box, th, ps) -> None:
        timeout_s = float(ps.routing.get("cloud_timeout_s", 60))
        job = {"frame": frame, "captured_at": captured_at, "seq": seq, "box": box, "thumb": th, "started": self.clock()}
        self.in_flight = {k: job[k] for k in ("seq", "box", "thumb", "started", "captured_at")}
        self.stats["asked"] += 1
        self.task = asyncio.get_running_loop().create_task(self._run(job, timeout_s, ps))

    # one frame ------------------------------------------------------------------------------------------------

    def observe(self, frame_jpeg: bytes, captured_at: str, seq: int, dets: list[dict], model: dict,
                session: str = "default") -> dict:
        """One observation from one frame. Never waits for the cloud model; must run on the event loop."""
        ps = self.pset()
        cov = ps.coverage(model)  # partial coverage is normal: unmapped classes and cloud-covered products escalate
        last = self.last_seq.get(session)
        if last is not None and seq <= last:
            self.stats["rejected_stale"] += 1
            raise Rejected(409, f"frame {seq} is not newer than frame {last}, already observed")
        self.last_seq[session] = seq
        if len(self.last_seq) > 50:
            self.last_seq.pop(next(iter(self.last_seq)))
        self.stats["observations"] += 1
        now = self.clock()
        self._expire(now, ps)
        img = Image.open(io.BytesIO(frame_jpeg))
        img.load()
        objects, pending = [], []
        for k, o in enumerate(self.group(dets, ps)):
            th = thumb(img, o["box"])
            top = o["members"][0]
            e = {"object": k + 1, "box": [round(float(v), 4) for v in o["box"]],
                 "local": [{"identity": m.get("identity"), "label": ps.label(m.get("identity")) if m.get("identity") else f"class {m.get('class_id')}",
                            "score": round(float(m.get("score", 0)), 4)} for m in o["members"]],
                 "units": {}, "family_units": {}}
            why = self.escalation(o, ps, cov)
            if not why:
                e.update(route="local", status="counted", units={top["identity"]: 1},
                         reason=f"local score {float(top['score']):.3f} above {ps.by_identity[top['identity']]['kimi_at_or_below']:.2f}")
            else:
                e.update(route="cloud", escalation=why)
                hit = self._find(self.cache, o["box"], th, ps)
                fail = self._find(self.failures, o["box"], th, ps)
                if hit:
                    r = hit["resolution"]
                    e.update(status=r["status"], units=dict(r.get("units") or {}), family_units=dict(r.get("family_units") or {}),
                             reason=r.get("reason"), cloud={"answer_for_frame": hit["seq"], "answer_captured_at": hit["captured_at"],
                                                            "reused": hit["seq"] != seq, "age_s": round(now - hit["answered"], 1),
                                                            "items_around_ignored": r.get("items_around_ignored", 0)})
                    if hit["seq"] != seq:
                        self.stats["reused"] += 1
                elif self.in_flight and self._same(self.in_flight, o["box"], th, ps):
                    e.update(status="awaiting cloud", reason=f"asked for frame {self.in_flight['seq']}, "
                                                             f"{now - self.in_flight['started']:.0f} s ago")
                elif fail:
                    e.update(status="uncertain", reason=f"cloud check failed ({fail['error']}); retried after "
                                                         f"{RETRY_AFTER_S:.0f} s; not counted as zero")
                else:
                    e.update(status="queued for cloud", reason="one cloud request at a time")
                    prio = (0 if any(w.startswith("conflicting") for w in why) else
                            1 if any(w.startswith("flavour") for w in why) else 2, float(top.get("score", 0)))
                    pending.append((prio, k, e, th))
            objects.append(e)
        if pending and self.in_flight is None:
            _, _, e, th = min(pending, key=lambda p: (p[0], p[1]))
            self._start(frame_jpeg, captured_at, seq, e["box"], th, ps)
            e.update(status="asking cloud", reason="sent with a crop and the whole frame as context")
        out = self._summary(ps, objects, seq, captured_at, now)
        out["coverage"] = cov
        return out

    def _summary(self, ps, objects, seq, captured_at, now) -> dict:
        counts = {p["identity"]: {"label": p["label"], "counted": 0, "from_local": 0, "from_cloud": 0,
                                  "count_unit": p.get("count_unit")} for p in ps.products}
        family_units: dict[str, int] = {}
        open_objects = 0
        for e in objects:
            for ident, n in e["units"].items():
                c = counts.setdefault(ident, {"label": ps.label(ident), "counted": 0, "from_local": 0, "from_cloud": 0})
                c["counted"] += n
                c["from_local" if e["route"] == "local" else "from_cloud"] += n
            for fam, n in e["family_units"].items():
                family_units[fam] = family_units.get(fam, 0) + n
            if e["status"] in WAITING or e["status"] == "uncertain":
                open_objects += 1
        for ident, c in counts.items():
            p = ps.by_identity.get(ident) or {}
            fam_open = bool(p.get("family") and family_units.get(p["family"]))
            lower = open_objects > 0 or fam_open
            c["status"] = ("at least" if lower else "counted") if c["counted"] else ("uncertain" if lower else "none detected")
            c["order_units"] = order_units(p, c["counted"]) if p else None
        return {"set_id": ps.set_id, "frame_seq": seq, "captured_at": captured_at, "objects": objects, "counts": counts,
                "family_units": {f: {"label": f"{ps.family_label(f)}, flavour uncertain", "units": n} for f, n in family_units.items()},
                "open_objects": open_objects, "complete": open_objects == 0 and not family_units,
                "cloud": {"in_flight": None if not self.in_flight else {"frame_seq": self.in_flight["seq"],
                                                                        "seconds": round(now - self.in_flight["started"], 1)},
                          "one_at_a_time": True, "timeout_s": float(ps.routing.get("cloud_timeout_s", 60)),
                          "cached_answers": len(self.cache), "stats": dict(self.stats)},
                "missed_objects_note": "Only objects the local model detected are here. Use \"Recognize shelf\" (the whole "
                                       "picture) for objects it missed.",
                "authority": "observational: no stock events, no orders"}

    def state(self) -> dict:
        now = self.clock()
        return {"last_frame_seq_by_session": dict(self.last_seq), "in_flight": None if not self.in_flight else
                {"frame_seq": self.in_flight["seq"], "seconds": round(now - self.in_flight["started"], 1)},
                "cached_answers": len(self.cache), "recent_failures": [{"error": f["error"], "frame_seq": f["seq"]} for f in self.failures],
                "stats": dict(self.stats)}
