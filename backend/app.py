"""Backend endpoints. Run on the laptop:

    uv run uvicorn backend.app:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from .agent import Agent
from .audit import AuditLog, story
from .rules import load_rules
from .valio import make_client

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

ruleset = load_rules(os.getenv("RULES_FILE", ROOT / "rules" / "school_kitchen.yaml"))
catalog = make_client()
log = AuditLog(os.getenv("AUDIT_DB", DATA / "audit.sqlite"))
agent = Agent(catalog, ruleset, log)
device = {"last_seen": None, "temperature_c": None, "auto_limit_eur": None, "alarm": False, "slots": {}}

app = FastAPI(title="Safe Kitchen Agent")


class Decision(BaseModel):
    action: str = "approve"  # approve, choose, skip
    option: int | None = None
    approver: str | None = None


class TextRequest(BaseModel):
    text: str
    product_id: str | None = None


@app.get("/")
def index():
    return RedirectResponse("/approve")


@app.get("/approve")
def approve_page():
    return FileResponse(ROOT / "web" / "approve.html")


@app.post("/events")
def events(ev: dict):
    from datetime import datetime

    device["last_seen"] = datetime.now().isoformat(timespec="seconds")
    for key in ("temperature_c", "auto_limit_eur"):
        if ev.get(key) is not None:
            device[key] = ev[key]
    if ev.get("type") == "temperature_alarm":
        device["alarm"] = bool(ev.get("active", True))
    elif "alarm" in ev:
        device["alarm"] = bool(ev["alarm"])
    if ev.get("type") == "slot_state":
        device["slots"][str(ev["slot"])] = {"state": ev["state"], "units": ev.get("estimated_units")}
    return agent.handle_event(ev)


@app.get("/approvals/open")
def approvals_open():
    return agent.open_approvals()


@app.post("/approvals/{approval_id}")
def approvals_decide(approval_id: str, d: Decision):
    if d.action not in ("approve", "choose", "skip"):
        raise HTTPException(400, "action must be approve, choose or skip")
    approver = d.approver or ruleset.get("approvers", {}).get("default", "Head chef")
    return agent.decide(approval_id, d.action, d.option, approver)


@app.get("/device/commands")
def device_commands():
    return {"buzzer": "approval" if agent.open_approvals() else "off"}


@app.get("/device")
def device_state():
    return device | {"weekly_spent_eur": agent.weekly_spent_eur, "catalog": catalog.name,
                     "rule_set_version": ruleset["version"], "signed_by": ruleset.get("signed_by")}


@app.get("/rules")
def rules():
    return ruleset


@app.get("/log")
def get_log(format: str = "story", limit: int = 200):
    entries = log.entries(limit)
    return {"story": story(entries)} if format == "story" else entries


@app.post("/request")
def text_request(r: TextRequest):
    return agent.handle_request(r.text, r.product_id)
