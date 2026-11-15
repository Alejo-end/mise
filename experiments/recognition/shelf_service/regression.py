"""Replay the labelled regression frames through the vision mode, several times each (repeatability).

  uv run --project shelf_service python -m shelf_service.regression --repeats 3      (from experiments/recognition)

Needs VERDA_INFERENCE_API_KEY. Compares the visible units of each inventory item with the owner's labels; writes
regression/results/<time>.json. Observational only. The cases are of 14 Nov and are labelled with the props set of
that day, so they are replayed against that ARCHIVED set (its original identities), never the active set:
SHELF_ARCHIVED_SET, else products/archive/kitchen-20261114-b.json, else the integration copy.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from . import product_set as PS
from . import vision
from .storage import INTEGRATION

HERE = Path(__file__).resolve().parent


def archived_set():
    import os
    for c in (os.environ.get("SHELF_ARCHIVED_SET"), HERE.parents[2] / "products/archive/kitchen-20261114-b.json",
              INTEGRATION / "tools/recording_products.json"):
        if c and Path(c).exists():
            ps = PS.read(c)
            if ps.set_id == "kitchen-20261114-b":
                return ps
    raise SystemExit("archived set kitchen-20261114-b not found: set SHELF_ARCHIVED_SET")


async def main(repeats: int) -> None:
    pset = archived_set()
    rows = []
    for case in sorted((HERE / "regression").glob("*/*/labels.json")):
        lab = json.loads(case.read_text()); jpeg = (case.parent / "frame.jpg").read_bytes()
        for k in range(repeats):
            t = time.perf_counter()
            try:
                r = await vision.recognize(jpeg, [0, 0, 1, 1], lab["captured_at"], HERE / "store" / "vision_runs",
                                           save=False, pset=pset)
                got = {i: r["visible_units"].get(i, 0) for i in lab["visible_counts"]}
                # an unfamiliar item present must never be counted as an inventory product (checked through the counts:
                # a bar called milk would make milk 1 where the label says 0)
                rows.append({"case": case.parent.name, "repeat": k + 1, "expected": lab["visible_counts"], "got": got,
                             "exact": got == lab["visible_counts"], "statuses": [f"{o['label']}/{o['status']}/{o['visibility']}/{o.get('match_level')}" for o in r["objects"]],
                             "count_status": {i: c["status"] for i, c in r["inventory_counts"].items()},
                             "unfamiliar_present": lab.get("unfamiliar_present", {}),
                             "seconds": round(time.perf_counter() - t, 1), "timing_ms": r["timing_ms"]})
            except RuntimeError as e:
                rows.append({"case": case.parent.name, "repeat": k + 1, "error": str(e)[:200]})
            print(json.dumps(rows[-1], ensure_ascii=False)[:400], flush=True)
    out = HERE / "regression" / "results"; out.mkdir(parents=True, exist_ok=True)
    (out / f"{time.strftime('%Y%m%d-%H%M%S')}.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False))
    ok = [r for r in rows if r.get("exact")]
    print(f"exact count match {len(ok)}/{len(rows)}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--repeats", type=int, default=3)
    asyncio.run(main(ap.parse_args().repeats))
