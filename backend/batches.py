"""Best-before dates: which packs to use first, before they become waste.

The Valio product file has no shelf life. The date is on the pack, so a person gives it when the pack
arrives: typed, or read from the barcode of the case. A case label in the GS1-128 form carries the
product number, the date and the lot in one code, for example (01)06408430000296(15)261120(10)L42.

An insight is a sentence for the kitchen: use this first, plan it into the menu or offer it, or take it
off the shelf. With a count and a use rate for the product's shelf slot, it also says how many units
will be left on the date. It decides nothing and orders nothing.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

USE_FIRST_DAYS = 2  # a pack with this many days left, or fewer, is named "use first"
SOON_DAYS = 5       # a pack with this many days left, or fewer, is listed as "coming up"


def parse_gs1(text: str) -> dict:
    """Read a GS1-128 code: (01) product number, (15) best before or (17) expiry as YYMMDD, (10) lot.

    Accepts the form with brackets and the plain form 01<14 digits>15<6 digits>10<lot>. Returns what it finds.
    A plain run of digits is taken as a product number alone.
    """
    t = text.strip()
    out: dict = {}
    if "(" in t:
        parts = dict(re.findall(r"\((\d{2})\)([^()]+)", t))
    else:
        parts, m = {}, re.fullmatch(r"01(\d{14})(?:(15|17)(\d{6}))?(?:10(.+))?", t)
        if m:
            parts["01"] = m.group(1)
            if m.group(2):
                parts[m.group(2)] = m.group(3)
            if m.group(4):
                parts["10"] = m.group(4)
        elif t.isdigit():
            parts["01"] = t
    if "01" in parts:
        out["code"] = parts["01"].strip()
    for ai in ("15", "17"):
        if ai in parts and re.fullmatch(r"\d{6}", parts[ai].strip()):
            yy, mm, dd = int(parts[ai][:2]), int(parts[ai][2:4]), int(parts[ai][4:6])
            try:
                out["best_before"] = date(2000 + yy, mm, dd or 1).isoformat()
                out["date_kind"] = "best before" if ai == "15" else "use by"
            except ValueError:
                pass
            break
    if "10" in parts:
        out["lot"] = parts["10"].strip()
    return out


@dataclass
class Batch:
    id: int
    product_id: str
    name: str
    best_before: str   # a date, YYYY-MM-DD
    lot: str | None
    registered_at: str
    registered_by: str | None
    done: str | None   # "used" or "removed", set by a person; an open batch has None

    def to_dict(self) -> dict:
        return asdict(self)


def insight(b: Batch, today: date, slot_units: int | None = None, use_per_day: float | None = None) -> dict:
    """One sentence about one open batch. `slot_units` and `use_per_day` come from the stock timeline, if the product has a slot."""
    days = (date.fromisoformat(b.best_before) - today).days
    level = "past" if days < 0 else "use_first" if days <= USE_FIRST_DAYS else "soon" if days <= SOON_DAYS else "fine"
    left = None
    if slot_units is not None and use_per_day and days >= 0:
        left = max(0, round(slot_units - use_per_day * days))
    when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
    if level == "past":
        text = f"{b.name} is {-days} day{'s' if days != -1 else ''} past its date. Take it off the shelf and record it."
    elif level == "fine":
        text = f"{b.name} is good until {b.best_before}."
    elif left:
        text = (f"{b.name} reaches its date {when}. At the present use, about {left} of {slot_units} will be left. "
                "Plan them into the menu or offer them before that day.")
    elif left == 0:
        text = f"{b.name} reaches its date {when}. At the present use it is gone before that day."
    else:
        text = f"{b.name} reaches its date {when}. Use it first, or plan it into the menu."
    return b.to_dict() | {"days_left": days, "level": level, "units_left_on_date": left, "text": text}


class Batches:
    """Registered packs in their own table of the audit database."""

    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS batches (id INTEGER PRIMARY KEY, product_id TEXT NOT NULL, name TEXT NOT NULL, "
                            "best_before TEXT NOT NULL, lot TEXT, registered_at TEXT NOT NULL, registered_by TEXT, done TEXT)")
            self.db.commit()

    def add(self, product_id: str, name: str, best_before: str, lot: str | None, by: str | None, when: datetime) -> Batch:
        date.fromisoformat(best_before)  # a wrong date is an error, not a batch
        with self.lock:
            cur = self.db.execute("INSERT INTO batches (product_id, name, best_before, lot, registered_at, registered_by) VALUES (?,?,?,?,?,?)",
                                  (str(product_id), name, best_before, lot, when.isoformat(timespec="seconds"), by))
            self.db.commit()
            return Batch(cur.lastrowid, str(product_id), name, best_before, lot, when.isoformat(timespec="seconds"), by, None)

    def close(self, batch_id: int, how: str) -> bool:
        if how not in ("used", "removed"):
            raise ValueError("how must be used or removed")
        with self.lock:
            n = self.db.execute("UPDATE batches SET done = ? WHERE id = ? AND done IS NULL", (how, batch_id)).rowcount
            self.db.commit()
        return n == 1

    def open(self) -> list[Batch]:
        with self.lock:
            rows = self.db.execute("SELECT id, product_id, name, best_before, lot, registered_at, registered_by, done "
                                   "FROM batches WHERE done IS NULL ORDER BY best_before, id").fetchall()
        return [Batch(*r) for r in rows]
