"""Read the ordering facts of the active products' selected catalogue entries into products/active.json.

For each product the owner selected by EAN, find the record of the challenge product file whose unit, product or
unit-conversion GTIN equals that EAN exactly, and copy its catalogue id, names, sales unit, units, the consumer
unit the camera counts, how many of those one sales unit holds, the consumer-unit size and the lot size. An EAN
that is not in the file is recorded as not found: nothing similar is looked up or taken instead. The live API is
not called (it has no product or search endpoint); the file is read locally and never copied into git.

  uv run python tools/catalogue_units.py --data-dir ../../valio-source-data/2026-11-14          # show
  uv run python tools/catalogue_units.py --data-dir ../../valio-source-data/2026-11-14 --write  # update the set
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import products as P  # noqa: E402
from backend.valio import PRODUCT_FILE  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True, help="folder with the challenge product file (never in git)")
    ap.add_argument("--set", default=str(P.ACTIVE))
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args(argv)
    path = Path(a.data_dir) / PRODUCT_FILE
    blob = path.read_bytes()
    records = json.loads(blob)
    source = {"file": PRODUCT_FILE, "sha256": hashlib.sha256(blob).hexdigest(), "records": len(records),
              "read_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "note": "challenge product file (sponsor data, read locally, not in git)"}
    manifest = Path(a.data_dir) / "MANIFEST.txt"
    if manifest.exists():
        source["downloaded"] = manifest.read_text().splitlines()[0].lstrip("# ").strip()[:160]
    data = P.load(a.set)
    for p in data["products"]:
        hits = P.find_by_gtin(records, p["ean"])
        if len(hits) == 1:
            raw, unit = hits[0]
            p["catalogue"] = P.catalogue_entry(raw, p["ean"], unit, source)
        else:
            p["catalogue"] = {
                "status": "not found" if not hits else "more than one record",
                "product_id": None, "selected_ean": p["ean"],
                "records": [str(r["id"]) for r, _ in hits],
                "searched": "every unit GTIN, product GTIN and unit-conversion GTIN of the product file, exact digits",
                "consequence": "catalogue id, sales unit, pack conversion and lot size unknown; recognition still uses "
                               "the product, no ordering facts are derived from other entries",
                "source": source}
        c = p["catalogue"]
        print(f"{p['ean']} {p['label']}: " + (
            f"{c['product_id']} '{c['name_en']}', sells per {c['sales_unit']} = {c['count_units_per_sales_unit']} "
            f"x {c['count_unit_code']}, lot {c['lot_size_sales_units']}, EAN is the {c['selected_ean_is_unit']} unit"
            if c["status"] == "found" else c["status"].upper()))
    if a.write:
        P.validate(data)
        Path(a.set).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("written:", a.set)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
