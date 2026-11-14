"""The stock timeline: how many units a shelf slot held over time, and what that says about the next order.

A reading is a count of one slot at one moment, from the shelf camera, a distance sensor or a person.
The timeline answers three questions for the person who approves an order:

    how many units are on the shelf now, and how old is that count
    how fast the kitchen uses the product
    when the shelf runs out, and whether that is before a delivery can arrive

It decides nothing. The rule check and the approver decide. A count from the camera is an estimate
from a fill level, so every answer names the source of its readings.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

SOURCES = ("camera", "sensor", "person")
MIN_SPAN = timedelta(hours=1)   # a shorter history says nothing about daily use
RETURN_WITHIN = timedelta(hours=2)  # units that come back this soon were taken out and put back, not used
STALE_AFTER = timedelta(hours=12)   # an older count is shown as old
MAX_AGE = timedelta(days=14)    # older readings do not describe the kitchen of today


@dataclass
class Reading:
    slot: int
    time: datetime
    units: int
    source: str


@dataclass
class Outlook:
    """What the readings of one slot say. A value is None when the readings cannot answer."""
    slot: int
    units: int | None             # the latest count
    counted_at: str | None
    source: str | None            # where the latest count comes from
    readings: int                 # how many readings the use rate rests on
    span_hours: float | None      # the time between the first and the last of them
    used: int | None              # units that left the shelf in that time
    use_per_day: float | None
    age_hours: float | None       # the age of the latest count; None when no time was given to compare with
    stale: bool                   # the latest count is older than STALE_AFTER
    runs_out_at: str | None       # at the measured rate, with no delivery
    days_of_use_when_full: float | None  # how long a full shelf lasts: a large number is stock that sits

    def to_dict(self) -> dict:
        return asdict(self)


def units_used(rs: list[Reading]) -> int:
    """Units that left the shelf for good. A drop counts as use. A rise is a delivery or a refill, not negative use.

    One exception: units that come back within RETURN_WITHIN of a drop were taken out and put back. They cancel
    that drop. A rise larger than the drop is a delivery and cancels nothing. Without this, a crate that somebody lifts out for a moment reads as use.
    """
    used, last_drop, last_drop_at = 0, 0, None
    for a, b in zip(rs, rs[1:]):
        change = b.units - a.units
        if change < 0:
            used, last_drop, last_drop_at = used - change, -change, b.time
        elif 0 < change <= last_drop and b.time - last_drop_at <= RETURN_WITHIN:
            used, last_drop = used - change, last_drop - change  # more than left the shelf is a delivery, not a return
    return used


def outlook(slot: int, readings: list[Reading], target_units: int | None = None, now: datetime | None = None) -> Outlook:
    """What the readings of one slot say about its stock, its use and its run-out time."""
    rs = sorted((r for r in readings if r.slot == slot), key=lambda r: r.time)
    if now is not None:
        rs = [r for r in rs if now - r.time <= MAX_AGE]
    if not rs:
        return Outlook(slot, None, None, None, 0, None, None, None, None, False, None, None)
    last, span = rs[-1], rs[-1].time - rs[0].time
    used = units_used(rs)
    age = now - last.time if now is not None else None
    rate = used / (span / timedelta(days=1)) if span >= MIN_SPAN and used > 0 else None
    runs_out = last.time + timedelta(days=last.units / rate) if rate else None
    return Outlook(
        slot=slot, units=last.units, counted_at=last.time.isoformat(timespec="seconds"), source=last.source,
        readings=len(rs), span_hours=round(span / timedelta(hours=1), 1), used=used,
        use_per_day=round(rate, 1) if rate else None,
        age_hours=round(age / timedelta(hours=1), 1) if age is not None else None,
        stale=bool(age is not None and age > STALE_AFTER),
        runs_out_at=runs_out.isoformat(timespec="seconds") if runs_out else None,
        days_of_use_when_full=round(target_units / rate, 1) if rate and target_units else None)


def against_delivery(o: Outlook, delivery: str | None, quantity: int | None) -> dict:
    """Compare the outlook with one order option: does the shelf run out before the delivery, and how long does the order last?

    `delivery` is the option's delivery date or time as the wholesaler gave it. A date with no time counts as
    the start of that day, so "runs out before" is never claimed for the delivery day itself.
    """
    out = {"runs_out_before_delivery": None, "order_lasts_days": None}
    if o.use_per_day and quantity:
        out["order_lasts_days"] = round(quantity / o.use_per_day, 1)
    if o.runs_out_at and delivery:
        try:
            arrives = datetime.fromisoformat(delivery)
        except ValueError:
            return out
        runs_out = datetime.fromisoformat(o.runs_out_at)
        if len(delivery) == 10:  # a date only
            arrives = arrives.replace(tzinfo=runs_out.tzinfo)
        out["runs_out_before_delivery"] = runs_out < arrives
    return out


class StockHistory:
    """Readings in their own table of the audit database. They are not audit entries: a shelf is read every few seconds."""

    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS stock_readings "
                            "(id INTEGER PRIMARY KEY, slot INTEGER NOT NULL, time TEXT NOT NULL, units INTEGER NOT NULL, source TEXT NOT NULL)")
            self.db.commit()

    def record(self, slot: int, units: int, source: str, when: datetime) -> bool:
        """Keep a reading if the count differs from the last one of that slot. Returns True if it was kept."""
        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}")
        with self.lock:
            last = self.db.execute("SELECT units FROM stock_readings WHERE slot = ? ORDER BY id DESC LIMIT 1", (slot,)).fetchone()
            if last is not None and last[0] == units:
                return False
            self.db.execute("INSERT INTO stock_readings (slot, time, units, source) VALUES (?,?,?,?)",
                            (slot, when.isoformat(timespec="seconds"), units, source))
            self.db.commit()
        return True

    def readings(self, slot: int | None = None, limit: int = 2000) -> list[Reading]:
        with self.lock:
            rows = self.db.execute("SELECT slot, time, units, source FROM stock_readings "
                                   + ("WHERE slot = ? " if slot is not None else "") + "ORDER BY id DESC LIMIT ?",
                                   ((slot, limit) if slot is not None else (limit,))).fetchall()
        return [Reading(s, datetime.fromisoformat(t), u, src) for s, t, u, src in rows][::-1]
