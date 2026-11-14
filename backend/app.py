"""Backend endpoints. Run on the laptop:

    uv run uvicorn backend.app:app --host 0.0.0.0 --port 8000

Approver PINs come from APPROVER_PINS in .env, for example "4821:Head chef,1937:Sous chef".
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Union

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from .agent import Agent
from .audit import AuditLog, story
from .gateway import make_router
from .rules import TZ, load_rules
from .allergens import unscanned
from .sandbox import AGENTS, DrivingTest
from .signing import Signer
from .valio import ValioCatalog, make_client

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)


def _load_env(path: Path) -> None:
    """Read KEY=value lines from .env without overriding the real environment."""
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_env(ROOT / ".env")
if os.getenv("ANTHROPIC_API_KEY"):  # a real model can take the Driving Test
    from .model_agent import ModelUnderTest
    AGENTS["claude"] = ModelUnderTest

ruleset = load_rules(os.getenv("RULES_FILE", ROOT / "rules" / "school_kitchen.yaml"))
catalog = make_client()
log = AuditLog(os.getenv("AUDIT_DB", DATA / "audit.sqlite"))


def _check_targets() -> None:
    """Refuse a rule set whose products the catalog does not have.

    The stand-in rule set names stand-in products and product groups. On the real Valio
    catalog its lactose rule would never match, so the app must not start that way.
    """
    missing = [t["product_id"] for t in ruleset["targets"]
               if t.get("product_id") and catalog.product(t["product_id"]).value is None]
    if missing:
        raise RuntimeError(
            f"Rule set v{ruleset['version']} names products that the catalog '{catalog.name}' does not have: "
            f"{', '.join(missing)}. Set RULES_FILE to a rule set written for this catalog, "
            "or set CATALOG=local to run on the stand-in catalog.")


_check_targets()
# The ingredient text scan knows only some allergens. For the others, only the allergen codes protect.
TEXT_SCAN_GAPS = unscanned(ruleset["banned_allergens"]) if isinstance(catalog, ValioCatalog) else []
if TEXT_SCAN_GAPS:
    log.write("notice", {"summary": "The ingredient text scan does not cover these banned allergens: "
                         f"{', '.join(TEXT_SCAN_GAPS)}. For them, only the allergen codes are checked."},
              "rule_check", ruleset["version"])
agent = Agent(catalog, ruleset, log)
driving_test = DrivingTest(ruleset, log)
PINS = {p.split(":", 1)[0].strip(): p.split(":", 1)[1].strip()
        for p in os.getenv("APPROVER_PINS", "").split(",") if ":" in p}
OFFLINE_AFTER_S = 30
# After 5 wrong PINs within a minute, nobody can approve for 30 seconds. A PIN has four digits.
WRONG_PINS, PIN_WINDOW_S, PIN_LOCK_S = 5, 60, 30
pin_guard = {"wrong": [], "locked_until": 0.0}
device = {"last_seen": None, "online": False, "temperature_c": None, "auto_limit_eur": None, "alarm": False,
          "slots": {}, "problem_until": 0.0}

app = FastAPI(title="Aimo Agent Gateway and Safe Kitchen Agent")


# Device events: one model per type. Unknown types and fields of the wrong type get a 422.

class _Base(BaseModel):
    time: str | None = None
    temperature_c: float | None = None
    auto_limit_eur: float | None = Field(default=None, ge=0, le=10000)
    alarm: bool | None = None


class SlotState(_Base):
    type: Literal["slot_state"]
    slot: int
    state: Literal["full", "low", "empty"]
    estimated_units: int | None = Field(default=None, ge=0)
    product_hint: str | None = None


class ManualLow(_Base):
    type: Literal["manual_low"]
    slot: int
    product_hint: str | None = None


class TemperatureAlarm(_Base):
    type: Literal["temperature_alarm"]
    temperature_c: float
    active: bool = True
    limit_c: float | None = None


class DeliveryCheck(_Base):
    type: Literal["delivery_check"]
    order_id: str | None = None
    counts: dict[str, int] = {}
    seconds_measured: float | None = None


class Status(_Base):
    type: Literal["status"]


Event = Annotated[Union[SlotState, ManualLow, TemperatureAlarm, DeliveryCheck, Status], Field(discriminator="type")]


class Decision(BaseModel):
    action: Literal["approve", "choose", "skip"] = "approve"
    option: int | None = None
    pin: str = ""


class TextRequest(BaseModel):
    text: str
    product_id: str | None = None


class DeliveryBody(BaseModel):
    counts: dict[str, int] = {}
    temperature_c: float | None = None
    seconds_measured: float | None = None


class SandboxRun(BaseModel):
    agent: str = "safe-kitchen"
    pin: str = ""  # the PIN of the approver who grants the licence; with no PIN the test runs and gives no licence


class Pin(BaseModel):
    pin: str = ""


class SessionStart(BaseModel):
    agent: str = Field(min_length=1, max_length=60)  # the name that the licence and the log will carry


class SandboxQuote(BaseModel):
    product_id: str
    quantity: int = Field(ge=1, le=100000)


class SandboxOrder(SandboxQuote):
    idempotency_key: str = Field(min_length=1, max_length=80)


class SandboxQuestion(BaseModel):
    reason: str = Field(default="", max_length=500)


@app.get("/")
def index():
    return RedirectResponse("/approve")


@app.get("/approve")
def approve_page():
    return FileResponse(ROOT / "web" / "approve.html")


@app.post("/events")
def events(ev: Event):
    data = ev.model_dump(exclude_none=True)
    if data["type"] in ("slot_state", "manual_low") and not any(t["slot"] == data["slot"] for t in ruleset["targets"]):
        raise HTTPException(400, f"slot {data['slot']} is not in rule set v{ruleset['version']}")
    if not device["online"] and device["last_seen"]:
        log.write("device", {"summary": "The shelf box is back online."}, "device", ruleset["version"])
    device["last_seen"] = time.time()
    device["online"] = True
    for key in ("temperature_c", "auto_limit_eur"):
        if data.get(key) is not None:
            device[key] = data[key]
    if data["type"] == "temperature_alarm":
        device["alarm"] = data["active"]
    elif "alarm" in data:
        device["alarm"] = data["alarm"]
    if data["type"] == "slot_state":
        device["slots"][str(data["slot"])] = {"state": data["state"], "units": data.get("estimated_units")}
    result = agent.handle_event(data)
    if data["type"] == "delivery_check" and result.get("status") == "problem":
        device["problem_until"] = time.time() + 6
    return result


def _watch_device():
    """Mark the box offline after 30 seconds without an event, and log it once."""
    while True:
        if device["online"] and device["last_seen"] and time.time() - device["last_seen"] > OFFLINE_AFTER_S:
            device["online"] = False
            log.write("device", {"summary": f"The shelf box sent nothing for {OFFLINE_AFTER_S} s and counts as "
                                 "offline. Camera triggers wait until it is back."}, "device", ruleset["version"])
        time.sleep(5)


threading.Thread(target=_watch_device, daemon=True).start()
app.include_router(make_router(agent, catalog, driving_test, log, Signer(os.getenv("GATEWAY_KEY", DATA / "gateway_key.pem"))))


@app.get("/approvals/open")
def approvals_open():
    return agent.open_approvals()


def approver_of(pin: str) -> str:
    """The approver behind a PIN. Wrong PINs lock every PIN check for a short time."""
    if not PINS:
        raise HTTPException(503, "No approver PINs are set. Put APPROVER_PINS in valio-challenge/.env.")
    now = time.time()
    if now < pin_guard["locked_until"]:
        raise HTTPException(429, f"Too many wrong PINs. Wait {PIN_LOCK_S} seconds.")
    approver = PINS.get(pin)
    if approver is None:
        pin_guard["wrong"] = [t for t in pin_guard["wrong"] if now - t < PIN_WINDOW_S] + [now]
        if len(pin_guard["wrong"]) >= WRONG_PINS:
            pin_guard["wrong"], pin_guard["locked_until"] = [], now + PIN_LOCK_S
        raise HTTPException(403, "Wrong PIN")
    return approver


@app.post("/approvals/{approval_id}")
def approvals_decide(approval_id: str, d: Decision):
    return agent.decide(approval_id, d.action, d.option, approver_of(d.pin))


@app.get("/device/commands")
def device_commands():
    if time.time() < device["problem_until"]:
        return {"buzzer": "alarm"}
    return {"buzzer": "approval" if agent.open_approvals() else "off"}


DEVICE_DIR = ROOT / "device"
DEVICE_FILES = ["python/main.py", "assets/index.html", "sketch/sketch.ino", "sketch/sketch.yaml", "app.yaml"]


@app.get("/device/files")
def device_files():
    """The box app files on this laptop, with hashes, so the box can update itself over Wi-Fi."""
    import hashlib

    return [{"path": f, "sha256": hashlib.sha256((DEVICE_DIR / f).read_bytes()).hexdigest()} for f in DEVICE_FILES]


@app.get("/device/files/{path:path}")
def device_file(path: str):
    if path not in DEVICE_FILES:
        raise HTTPException(404, "not a box app file")
    return FileResponse(DEVICE_DIR / path)


@app.get("/device")
def device_state():
    seen = device["last_seen"]
    return {k: v for k, v in device.items() if k not in ("last_seen", "problem_until")} | {
        "last_seen": datetime.fromtimestamp(seen, TZ).isoformat(timespec="seconds") if seen else None,
        "weekly_spent_eur": agent.weekly_spent_eur, "catalog": catalog.name, "text_scan_gaps": TEXT_SCAN_GAPS, "cold_alarm_c": agent.cold_alarm_c,
        "rule_set_version": ruleset["version"], "rule_set_sha256": ruleset["sha256"][:12],
        "signed_by": ruleset.get("signed_by"), "pins_set": bool(PINS)}


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


@app.get("/orders")
def orders():
    checked = {d["order_id"] for d in agent.deliveries}
    return [o | {"checked": o["order_id"] in checked} for o in sorted(agent.orders.values(), key=lambda o: o["time"])]


@app.post("/deliveries/{order_id}/check")
def delivery_check(order_id: str, b: DeliveryBody):
    r = agent.check_delivery(order_id, b.counts, b.temperature_c, b.seconds_measured)
    if r["status"] == "unknown_order":
        raise HTTPException(404, f"no order {order_id}")
    if r["status"] == "problem":
        device["problem_until"] = time.time() + 6
    return r


@app.get("/deliveries")
def deliveries():
    return agent.deliveries[::-1]


@app.get("/suppliers/reliability")
def suppliers_reliability():
    return sorted(agent.reliability().values(), key=lambda r: r["score"])


@app.post("/sandbox/test")
def sandbox_test(r: SandboxRun):
    """Run the Driving Test on one of the agents that this backend holds. A pass gives a licence only with
    the PIN of an approver: that person grants the agent its spend limit and product groups."""
    if r.agent not in AGENTS:
        raise HTTPException(400, f"agent must be one of {sorted(AGENTS)}")
    return driving_test.run(r.agent, granted_by=approver_of(r.pin) if r.pin else None)


# The Driving Test over the network. No licence is needed to take it: the sandbox holds only the stand-in catalog.

SANDBOX_PUBLIC = ("id", "name", "product_group", "properties", "allergens", "traces", "supplier", "origin", "description")


def _scenario(session_id: str, scenario: str):
    found = driving_test.scenario(session_id, scenario)
    if found is None:
        raise HTTPException(404, "No such session or scenario. A session lasts 30 minutes.")
    return found


@app.post("/sandbox/sessions", tags=["sandbox"])
def sandbox_open(r: SessionStart):
    """Start the Driving Test for an agent that runs somewhere else. The answer lists the scenarios and the
    kitchen's rules. It does not say what each scenario tests."""
    return driving_test.open_session(r.agent.strip()) | {
        "calls": "For each scenario: GET .../products?q=, POST .../quotes, POST .../orders, POST .../ask-human. "
                 "Then POST /sandbox/sessions/{session_id}/finish."}


@app.get("/sandbox/sessions/{session_id}/{scenario}/products", tags=["sandbox"])
def sandbox_products(session_id: str, scenario: str, q: str):
    cat, _ = _scenario(session_id, scenario)
    return {"products": [{k: p[k] for k in SANDBOX_PUBLIC if k in p} for p in cat.search(q).value]}


@app.post("/sandbox/sessions/{session_id}/{scenario}/quotes", tags=["sandbox"])
def sandbox_quote(session_id: str, scenario: str, r: SandboxQuote):
    cat, _ = _scenario(session_id, scenario)
    if r.product_id not in cat.products:
        raise HTTPException(404, "No such product.")
    a = cat.availability(r.product_id, r.quantity, datetime.now(TZ)).value
    return a | {"total_eur": round(a["unit_price_eur"] * r.quantity, 2)}


@app.post("/sandbox/sessions/{session_id}/{scenario}/orders", tags=["sandbox"])
def sandbox_order(session_id: str, scenario: str, r: SandboxOrder):
    cat, _ = _scenario(session_id, scenario)
    if r.product_id not in cat.products:
        raise HTTPException(404, "No such product.")
    try:
        return cat.place_order([{"product_id": r.product_id, "quantity": r.quantity}], r.idempotency_key).value
    except TimeoutError:
        raise HTTPException(504, "The order call timed out.")


@app.post("/sandbox/sessions/{session_id}/{scenario}/ask-human", tags=["sandbox"])
def sandbox_ask(session_id: str, scenario: str, r: SandboxQuestion):
    """The agent sends the request to a person and does not order."""
    _, asked = _scenario(session_id, scenario)
    asked.append(r.reason)
    return {"status": "sent to a person"}


@app.post("/sandbox/sessions/{session_id}/finish", tags=["sandbox"])
def sandbox_finish(session_id: str, p: Pin):
    """Judge the session by the calls that the agent made. With the PIN of an approver, a pass gives a licence."""
    result = driving_test.close_session(session_id, approver_of(p.pin) if p.pin else None)
    if result is None:
        raise HTTPException(404, "No such session. A session lasts 30 minutes.")
    return result


@app.post("/sandbox/licences/{licence_id}/revoke")
def sandbox_revoke(licence_id: str, p: Pin):
    """End a licence now, for example when an agent orders in a way that nobody expected."""
    lic = driving_test.revoke(licence_id, approver_of(p.pin))
    if lic is None:
        raise HTTPException(404, "No such licence.")
    return {"id": lic["id"], "agent": lic["agent"], "revoked": lic["revoked"]}


@app.get("/sandbox/licences")
def sandbox_licences():
    return [{k: v for k, v in l.items() if k not in ("token_sha256", "token_hint")} | {"token": l["token_hint"] + "…"}
            for l in driving_test.licences.values()]
