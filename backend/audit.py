"""Append-only audit log in SQLite. Triggers refuse UPDATE and DELETE."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .rules import TZ

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time TEXT NOT NULL,
    step TEXT NOT NULL,
    detail TEXT NOT NULL,
    rule_set_version INTEGER,
    actor TEXT NOT NULL,
    order_id TEXT,
    run_id TEXT
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""


class AuditLog:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    def write(self, step: str, detail: dict, actor: str, rule_set_version=None, order_id=None, run_id=None) -> dict:
        entry = {
            "time": datetime.now(TZ).isoformat(timespec="seconds"),
            "step": step,
            "detail": detail,
            "rule_set_version": rule_set_version,
            "actor": actor,
            "order_id": order_id,
            "run_id": run_id,
        }
        with self.lock:
            cur = self.db.execute(
                "INSERT INTO audit (time, step, detail, rule_set_version, actor, order_id, run_id) VALUES (?,?,?,?,?,?,?)",
                (entry["time"], step, json.dumps(detail, default=str), rule_set_version, actor, order_id, run_id),
            )
            self.db.commit()
        entry["id"] = cur.lastrowid
        return entry

    def entries(self, limit: int = 200) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = [dict(r) | {"detail": json.loads(r["detail"])} for r in rows]
        return out[::-1]


def story(entries: list[dict]) -> list[str]:
    """Turn log entries into short sentences for the chef."""
    lines = []
    for e in entries:
        d, t = e["detail"], e["time"][11:16]
        s = e["step"]
        if s == "trigger":
            lines.append(f"{t} {d.get('summary', 'Trigger received.')}")
        elif s == "search":
            lines.append(f"{t} Searched the catalog for \"{d['query']}\" and found {d['count']} products (checked {d['checked_at'][11:16]}).")
        elif s == "candidates":
            lines.append(f"{t} Asked price and stock for {len(d['candidates'])} products.")
        elif s == "rule_check":
            lines.append(f"{t} Rule check v{e['rule_set_version']}: {d['subject']} is {d['result'].replace('_', ' ')}. {'; '.join(d['reasons'])}.")
        elif s == "approval":
            if d.get("status") == "open":
                lines.append(f"{t} Asked {d.get('approver', 'the chef')} to approve: {len(d['options'])} options.")
            elif d.get("status") == "expired":
                lines.append(f"{t} The approval request expired after 2 hours. A new quote is needed.")
            else:
                opt = f" (option {d['option'] + 1})" if d.get("option") is not None else ""
                lines.append(f"{t} {e['actor']} {d['status']} the request{opt}.")
        elif s == "recheck":
            lines.append(f"{t} Checked price and stock again just before ordering: {d['summary']}")
        elif s == "order":
            lines.append(f"{t} Order {e['order_id']} placed: {d['summary']}")
        elif s == "blocked":
            lines.append(f"{t} Blocked: {d['summary']}")
        elif s == "alarm":
            if d.get("active", True):
                lines.append(f"{t} Cold room alarm: {d['temperature_c']:.1f} °C. Auto-orders wait for the chef.")
            else:
                lines.append(f"{t} Cold room back to {d['temperature_c']:.1f} °C.")
        elif s in ("restock", "error", "delivery_check", "driving_test", "device"):
            prefix = {"error": "Error: ", "driving_test": "Driving test: "}.get(s, "")
            lines.append(f"{t} {prefix}{d.get('summary', '')}")
        else:
            lines.append(f"{t} {s}: {d.get('summary', '')}")
    return lines
