"""Load the example cases of the demo into a running backend.

    uv run python tools/demo_cases.py                 # backend on http://localhost:8000, the default database
    uv run python tools/demo_cases.py --url http://localhost:8021 --db /path/to/audit.sqlite

The products are real entries of the Valio challenge catalog. Everything else is made up for the demo and
says so: the counts of the last two days, the dates on the packs and the name "demo case" in the log.
Run it once on a fresh database. It orders nothing.

The cases, and what each one shows:

  1  Milk runs short        slot 3 was counted for two days. A request for milk then shows the count, the use
                            per day and the hours with an empty shelf before the delivery.
  2  Chicken near its date  a pack of chicken with tomorrow's date: "use it first", and "read the label".
  3  Yoghurt left over      a date in two days and a slow use: the list says how many will be left.
  4  Soup past its date     yesterday's date: "take it off the shelf and record it".
  5  Nuts at the door       a pack with nuts is scanned: not for this kitchen, with the reason.
  6  Sesame at the door     the same for a crispbread with sesame.
  7  Frozen in a warm room  a frozen product against the air temperature: "check before use".
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.rules import TZ  # noqa: E402
from backend.stock import StockHistory  # noqa: E402

WHO = "demo case"
# Counts of the last two days for the three shelf slots: (hours ago, units). Made up.
COUNTS = {
    3: [(50, 24), (44, 19), (38, 13), (30, 8), (26, 24), (20, 18), (12, 11)],   # milk: about 16 a day, one delivery
    2: [(48, 12), (30, 11), (10, 9)],                                            # yoghurt: slow
    1: [(46, 30), (28, 22), (6, 14)],                                            # rye bread
}
# Real catalog products by the number under their barcode. The dates are made up: days from today.
PACKS = [
    ("2377221800008", 1, "chicken breast fillet: tomorrow"),
    ("6408430026333", 2, "organic natural yoghurt: in two days"),
    ("6412000031795", -1, "chicken soup: yesterday"),
    ("6408430000296", 6, "lactose-free milk drink: in six days"),
]
REFUSED = [("4008400935225", "a chocolate bar with nuts"), ("6410500046516", "a crispbread with sesame")]
FROZEN = "6430104952570"  # diced root vegetables, keep at -18 °C


def post(url: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as f:
            return f.status, json.load(f)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--db", default=os.getenv("AUDIT_DB", str(ROOT / "data" / "audit.sqlite")))
    a = ap.parse_args()
    now, history = datetime.now(TZ), StockHistory(a.db)
    for slot, rows in COUNTS.items():
        kept = sum(history.record(slot, units, "person", now - timedelta(hours=h)) for h, units in rows)
        print(f"case 1: slot {slot}: {kept} made-up counts of the last two days")
    today = now.date()
    for code, days, what in PACKS:
        status, r = post(a.url + "/scan", {"code": code, "where": "door", "scanned_by": WHO,
                                           "best_before": (today + timedelta(days=days)).isoformat()})
        name = r["matches"][0]["product"]["name"] if status == 200 else r.get("detail")
        print(f"cases 2 to 4: {what}: {status} {name}")
    for code, what in REFUSED:
        status, r = post(a.url + "/scan", {"code": code, "where": "door", "scanned_by": WHO})
        print(f"cases 5 and 6: {what}: {status} {r['matches'][0]['verdict'] if status == 200 else r.get('detail')}")
    status, r = post(a.url + "/scan", {"code": FROZEN, "where": "shelf", "scanned_by": WHO})
    if status == 200:
        m = r["matches"][0]
        print(f"case 7: frozen product, keep at {m['keep_at_or_below_c']} °C: the cold room is {m['cold_room']['state']}")
    print("Now, in the Kitchen role, send: 24 lactose-free milk. The ticket of the head chef shows case 1.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
