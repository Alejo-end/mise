"""The cold log: the temperature where the food is kept, over time, against the limit of each product.

A thermometer measures the air around the food, not the food. So the log does not say that a product is
spoiled. It says how long the air was above the storage limit that the manufacturer gave for the product
(`max_temp_c` in the catalog). The kitchen sets how long that may last before a person must look
(`cold_chain.max_minutes_above` in the signed rule set).

    ok       the air is at or below the product's limit
    warm     above the limit now, for less than the allowed time
    breach   above the limit for the allowed time or longer: a person must check the product
    unknown  no limit in the product data, or no reading

It decides nothing about an order. It alerts, and it records.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

OK, WARM, BREACH, UNKNOWN = "ok", "warm", "breach", "unknown"
DEFAULT_ALLOWED_MIN = 120          # our default for a demo, not a legal figure: the kitchen's own plan sets it
MAX_STEP = timedelta(minutes=10)   # a reading speaks for at most this long: a silent thermometer proves nothing
KEEP_EVERY = timedelta(minutes=1)  # an unchanged temperature is kept once a minute
SOURCES = ("sensor", "person")


@dataclass
class Temp:
    time: datetime
    temperature_c: float
    source: str


@dataclass
class Exposure:
    """How one product's limit compares with the readings since `since`."""
    state: str
    limit_c: float | None
    temperature_c: float | None      # the latest reading
    measured_at: str | None
    minutes_above_now: int           # the unbroken time above the limit up to the latest reading
    minutes_above_total: int         # all time above the limit since `since`
    peak_c: float | None
    allowed_minutes: int

    def to_dict(self) -> dict:
        return asdict(self)


def exposure(readings: list[Temp], limit_c: float | None, allowed_minutes: int = DEFAULT_ALLOWED_MIN,
             since: datetime | None = None, now: datetime | None = None) -> Exposure:
    """Each reading counts until the next one, and for at most MAX_STEP. Time with no reading counts as not measured."""
    rs = sorted((r for r in readings if since is None or r.time >= since), key=lambda r: r.time)
    if not rs or limit_c is None:
        last = rs[-1] if rs else None
        return Exposure(UNKNOWN, limit_c, last.temperature_c if last else None,
                        last.time.isoformat(timespec="seconds") if last else None, 0, 0,
                        max((r.temperature_c for r in rs), default=None), allowed_minutes)
    total, run = timedelta(), timedelta()
    ends = [r.time for r in rs[1:]] + [now or rs[-1].time]
    for r, end in zip(rs, ends):
        step = min(max(end - r.time, timedelta()), MAX_STEP)
        if r.temperature_c > limit_c:
            total, run = total + step, run + step
        else:
            run = timedelta()
    last = rs[-1]
    above = last.temperature_c > limit_c
    run_min, total_min = int(run / timedelta(minutes=1)), int(total / timedelta(minutes=1))
    state = OK if not above else BREACH if run_min >= allowed_minutes else WARM
    return Exposure(state, limit_c, last.temperature_c, last.time.isoformat(timespec="seconds"),
                    run_min if above else 0, total_min, max(r.temperature_c for r in rs), allowed_minutes)


class ColdLog:
    """Temperature readings in their own table of the audit database."""

    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.execute("CREATE TABLE IF NOT EXISTS cold_readings "
                            "(id INTEGER PRIMARY KEY, time TEXT NOT NULL, temperature_c REAL NOT NULL, source TEXT NOT NULL)")
            # The head chef's own limit per product. It wins over the catalog: the kitchen knows how it stores the food.
            self.db.execute("CREATE TABLE IF NOT EXISTS cold_limits "
                            "(product TEXT PRIMARY KEY, limit_c REAL NOT NULL, set_by TEXT NOT NULL, time TEXT NOT NULL)")
            self.db.commit()

    def set_limit(self, product: str, limit_c: float | None, set_by: str, when: datetime) -> None:
        """Keep the head chef's limit for one product. None removes it, so the catalog's limit counts again."""
        with self.lock:
            if limit_c is None:
                self.db.execute("DELETE FROM cold_limits WHERE product = ?", (product,))
            else:
                self.db.execute("INSERT OR REPLACE INTO cold_limits (product, limit_c, set_by, time) VALUES (?,?,?,?)",
                                (product, float(limit_c), set_by, when.isoformat(timespec="seconds")))
            self.db.commit()

    def limits(self) -> dict[str, dict]:
        """The head chef's limits: product -> {limit_c, set_by, time}."""
        with self.lock:
            rows = self.db.execute("SELECT product, limit_c, set_by, time FROM cold_limits").fetchall()
        return {p: {"limit_c": c, "set_by": by, "time": t} for p, c, by, t in rows}

    def record(self, temperature_c: float, source: str, when: datetime) -> bool:
        """Keep a reading if the temperature moved by 0.2 °C or the last one is a minute old. True if kept."""
        if source not in SOURCES:
            raise ValueError(f"source must be one of {SOURCES}")
        with self.lock:
            last = self.db.execute("SELECT time, temperature_c FROM cold_readings ORDER BY id DESC LIMIT 1").fetchone()
            if last is not None and abs(last[1] - temperature_c) < 0.2 and when - datetime.fromisoformat(last[0]) < KEEP_EVERY:
                return False
            self.db.execute("INSERT INTO cold_readings (time, temperature_c, source) VALUES (?,?,?)",
                            (when.isoformat(timespec="seconds"), float(temperature_c), source))
            self.db.commit()
        return True

    def readings(self, since: datetime | None = None, limit: int = 5000) -> list[Temp]:
        with self.lock:
            rows = self.db.execute("SELECT time, temperature_c, source FROM cold_readings ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = [Temp(datetime.fromisoformat(t), c, s) for t, c, s in rows][::-1]
        return [r for r in out if since is None or r.time >= since]
