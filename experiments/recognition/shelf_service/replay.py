"""Replay saved raw camera frames through the vision recognition (real model calls), for checking decisions.

  uv run --project shelf_service python -m shelf_service.replay store/vision_runs/20261115-050648.jpg ...

Each frame is the exact JPEG the box sent (store/vision_runs/<time>.jpg, no drawing on it); its capture time comes
from the run's .json next to it. The active product set (SHELF_PRODUCTS) and the labelled reference photos of the
enrolment store are used as in the service; replayed frames are never added to the references. Results go to
store/replays/<frame>-<time>.json. Needs VERDA_INFERENCE_API_KEY. Observational only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from . import product_set as PS
from . import vision

HERE = Path(__file__).resolve().parent


async def replay(frame: Path, local_json: Path | None = None) -> dict:
    meta = frame.with_suffix(".json")
    captured = json.loads(meta.read_text()).get("captured_at") if meta.exists() else None
    ps = PS.active()
    if local_json:  # the hybrid flow: that frame's local model answer (saved by the caller), then the whole-frame check
        from . import hybrid
        local = json.loads(local_json.read_text())
        async def no_save(*a, **kw):
            return await vision.recognize(*a, save=False, **kw)
        r = await hybrid.run(frame.read_bytes(), captured or "unknown", local, "whole-frame", None, ps,
                             HERE / "store" / "replays", recognize=no_save)
    else:
        r = await vision.recognize(frame.read_bytes(), [0, 0, 1, 1], captured or "unknown", HERE / "store" / "replays", save=False, pset=ps)
    out = HERE / "store" / "replays"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{frame.stem}-{time.strftime('%H%M%S')}.json").write_text(json.dumps(r, indent=1, ensure_ascii=False))
    print(f"\n== {frame.name} (captured {captured}), product set {ps.set_id}, {r['timing_ms']}")
    print("references sent:", ", ".join(f"{x['reference']} {ps.display(x['identity'])} {x['path'].split('/')[-1]}" for x in r["references_used"]) or "none")
    for i, o in enumerate(r["objects"], 1):
        who = o["identity"] or o.get("removed_identity") or o.get("family") or "-"
        src = o.get("source")
        if src:
            L = src.get("local") or {}
            print(f"    source: identity by {src['identity_by']}, box by {src['box_by']}"
                  + (f", local class {L.get('class_id')} score {L.get('score')}" if L else ""))
        print(f" {i}. {o['status']:<17} {ps.display(who) if who != '-' else '-':<24} by {o.get('match_by')} [{o.get('match_level')}]"
              f" could_be={o.get('could_be')} | read: {o.get('brand')} / {o.get('variant')} / {o.get('readable_text')}"
              f" | {str(o.get('match_reason'))[:140]}")
    print("aggregate:", {ps.display(k): f"{v['status']} {v['visible']}" for k, v in r["inventory_counts"].items()})
    print("visible items:", [(i["marks"], i["status"], i["text"]) for i in r["visible_items"]["items"]])
    return r


async def main(frames, local_json=None):
    for f in frames:
        r = await replay(Path(f), Path(local_json) if local_json else None)
        if r.get("hybrid"):
            h = r["hybrid"]
            print("hybrid:", {k: h[k] for k in ("matches", "conflicts", "kimi_only", "local_only", "crop_checks", "dismissed_local_boxes")},
                  "cloud", h["cloud"], "local", h["local"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("frames", nargs="+")
    ap.add_argument("--local-json", help="the local model's answer for the (single) frame: hybrid replay")
    a = ap.parse_args()
    asyncio.run(main(a.frames, a.local_json))
