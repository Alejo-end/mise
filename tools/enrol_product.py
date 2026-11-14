"""Enrol a physical product: link it once to its exact catalogue item, with the owner's confirmation.

Enrolment is separate from runtime recognition: the camera never needs the barcode later. Three methods,
all ending in an explicit owner confirmation; nothing is assigned because it merely looks similar:

  uv run python tools/enrol_product.py 3 --gtin 6408430000296        # barcode digits: exact GTIN match only
  uv run python tools/enrol_product.py 1 --search "taffel sips"      # owner picks from a name search
  uv run python tools/enrol_product.py 2 --from-orders                # owner picks from previous orders
  uv run python tools/enrol_product.py unfamiliar --none             # record: no exact catalogue item

SLOT is 1, 2, 3 or "unfamiliar". The result goes into tools/recording_products.json as catalogue_match. The
observed identity (exp:...) and the purchasing mapping (signed rule set, unmapped) do not change here.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SET_FILE = HERE / "recording_products.json"
CATALOGUE = Path(os.getenv("VALIO_PRODUCT_DATA", "/Users/bigboss/Ai/Claudecode/valio-source-data/2026-11-14/"
                                                   "valio_aimo_junction26_product_data.json"))


def gtin_ok(digits: str) -> bool:
    """GTIN-8/12/13/14 check digit."""
    if not re.fullmatch(r"\d{8}|\d{12,14}", digits):
        return False
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


def name_of(p: dict, lang: str = "fi") -> str:
    names = (p.get("synkkaData") or {}).get("names") or []
    return next((n["value"] for n in names if n.get("language") == lang), "") or \
        next((n["value"] for n in names), "") or p["id"]


def products() -> list[dict]:
    d = json.loads(CATALOGUE.read_text())
    items = d if isinstance(d, list) else next(v for v in d.values() if isinstance(v, list))
    return [p for p in items if not p.get("deleted")]


def gtin_matches(digits: str, items: list[dict]) -> list[dict]:
    """Exact GTIN equality at any unit level (consumer item, sales unit, pack, pallet). Leading zeros of
    GTIN-14 are ignored. Never a name or similarity match."""
    want = digits.lstrip("0")
    out = []
    for p in items:
        levels = [("sales unit " + str(p.get("salesUnit")), p.get("salesUnitGtin")),
                  ("item (Synkka)", (p.get("synkkaData") or {}).get("gtin"))]
        levels += [(f"unit {u.get('unitId')}", u.get("gtin")) for u in p.get("units") or []]
        hit = [level for level, g in levels if g and str(g).lstrip("0") == want]
        if hit:
            out.append({"code": p["id"], "name": name_of(p), "level": "GTIN at " + ", ".join(hit), "gtin": digits})
    return out


def pick(options: list[dict], what: str) -> dict | None:
    if not options:
        print(f"No {what}.")
        return None
    for i, o in enumerate(options, 1):
        print(f"  {i:>2}. {o['code']} | {o['name'][:70]} | {o.get('level', o.get('source', ''))}")
    raw = input(f"Which one is EXACTLY your product? Number, or 0 if none is exactly it: ").strip()
    return options[int(raw) - 1] if raw.isdigit() and 0 < int(raw) <= len(options) else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("slot", help="1, 2, 3 or unfamiliar")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--gtin")
    g.add_argument("--search")
    g.add_argument("--from-orders", action="store_true")
    g.add_argument("--none", action="store_true")
    ap.add_argument("--backend", default="http://localhost:8000")
    a = ap.parse_args()
    ps = json.loads(SET_FILE.read_text())
    entry = ps["unfamiliar"] if a.slot == "unfamiliar" else ps["slots"][a.slot]
    print(f"Enrolling {a.slot}: {entry['label']} (observed identity {entry['identity']})")

    chosen, method, extra = None, None, {}
    if a.none:
        method = "none"
    elif a.gtin:
        digits = re.sub(r"\D", "", a.gtin)
        if not gtin_ok(digits):
            raise SystemExit(f"{a.gtin}: not a valid GTIN (wrong length or check digit); please re-read the digits")
        matches = gtin_matches(digits, products())
        extra = {"scanned_gtin": digits}
        if not matches:
            print(f"GTIN {digits} is not in the catalogue: no exact match. Nothing similar is assigned.")
            method = "barcode-no-match"
        else:
            chosen, method = pick(matches, "exact GTIN match"), "barcode"
    elif a.search:
        words = a.search.lower().split()
        hits = [{"code": p["id"], "name": name_of(p), "level": "name search (not a match by itself)"}
                for p in products() if all(w in (name_of(p, "fi") + " " + name_of(p, "en")).lower() for w in words)]
        print(f"{len(hits)} catalogue products contain those words; first 20:")
        chosen, method = pick(hits[:20], "name search result"), "owner-selected"
    else:
        orders = json.load(urllib.request.urlopen(a.backend.rstrip("/") + "/orders", timeout=10))  # read-only
        seen = {}
        for o in orders:
            for line in o.get("lines", []):
                seen.setdefault(line["product_id"], {"code": line["product_id"], "name": line.get("name", ""),
                                                     "source": f"order {o.get('order_id')} ({o.get('time', '')[:10]})"})
        chosen, method = pick(list(seen.values()), "previous order"), "previous-order"

    if chosen is not None:
        if input(f"Confirm: your physical {entry['label']} is exactly {chosen['code']} {chosen['name']}? [y/N] ").strip().lower() != "y":
            chosen = None
    entry["catalogue_match"] = None if chosen is None else {
        "code": chosen["code"], "name": chosen["name"], "method": method, **extra,
        "matched_level": chosen.get("level") or chosen.get("source"), "confirmed_by": "owner",
        "confirmed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    entry.setdefault("enrolment_log", []).append({"method": method, **extra, "result": chosen["code"] if chosen else None,
                                                  "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    if a.gtin and "scanned_gtin" in extra:
        entry["gtin"] = extra["scanned_gtin"]
    SET_FILE.write_text(json.dumps(ps, indent=2, ensure_ascii=False) + "\n")
    print("Saved:", "exact catalogue item " + chosen["code"] if chosen else "no catalogue item (stays experimental)",
          "| purchasing stays unmapped until the rule set is re-signed for it")


if __name__ == "__main__":
    main()
