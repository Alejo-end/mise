"""Backend endpoints. Run on the laptop:

    uv run uvicorn backend.app:app --host 0.0.0.0 --port 8000

Approver PINs come from APPROVER_PINS in .env, for example "4821:Head chef,1937:Sous chef".
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, Union

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from .agent import Agent
from .audit import AuditLog, story
from . import rules as R
from .gateway import make_router, public
from .rules import TZ
from .allergens import unscanned
from .sandbox import AGENTS, DrivingTest
from .settings import Settings
from .shelf import Shelf, ShelfError, versions_dir_for
from .signing import Signer
from .cold import BREACH, DEFAULT_ALLOWED_MIN, ColdLog, exposure
from .stock import StockHistory, against_delivery, outlook
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
SETTINGS = Settings.from_env()  # committed defaults in backend/settings.py; .env.example lists every key
# Shelf recording session (tools/record_sequence.py): device events update the device view and are logged,
# but never reach the agent, so calibration and evaluation actions cannot create orders or approvals and
# cannot end a pending order. Empty (the default) is normal operation.
RECORDING_SESSION = os.getenv("RECORDING_SESSION", "").strip()
if os.getenv("ANTHROPIC_API_KEY"):  # a real model can take the Driving Test
    from .model_agent import ModelUnderTest
    AGENTS["claude"] = ModelUnderTest

# The signed rule file, then every shelf change signed after it (backend/shelf.py). The last version is active.
shelf = Shelf(os.getenv("RULES_FILE", ROOT / "rules" / "school_kitchen.yaml"),
              versions_dir_for(os.getenv("AUDIT_DB", DATA / "audit.sqlite")))
ruleset = shelf.rules
catalog = make_client()
log = AuditLog(os.getenv("AUDIT_DB", DATA / "audit.sqlite"))
stock_history = StockHistory(os.getenv("AUDIT_DB", DATA / "audit.sqlite"))
cold_log = ColdLog(os.getenv("AUDIT_DB", DATA / "audit.sqlite"))


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
agent = Agent(catalog, ruleset, log, settings=SETTINGS)
if SETTINGS.overrides():
    log.write("notice", {"summary": "Settings differ from the committed defaults in backend/settings.py: "
                         + ", ".join(f"{k}={v:g}" for k, v in SETTINGS.overrides().items()) + "."},
              "agent", ruleset["version"])
driving_test = DrivingTest(ruleset, log)
PINS = {p.split(":", 1)[0].strip(): p.split(":", 1)[1].strip()
        for p in os.getenv("APPROVER_PINS", "").split(",") if ":" in p}
SHOW_PINS = os.getenv("SHOW_PINS", "").strip() == "1"  # demo only: a PIN on the page protects nothing
OFFLINE_AFTER_S = SETTINGS.offline_after_s
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
    product_hint: str | None = None  # a label only; the backend's shelf configuration names the product
    # Version 2 (interface 1), all optional. With fill, units come from the backend's target, not the device's.
    fill: float | None = Field(default=None, ge=0, le=1)
    frame_id: int | None = Field(default=None, ge=0)
    frame_time: str | None = None


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


class SlotHealth(BaseModel):
    """One slot in the board's health (interface 7). Fields it does not know are kept."""
    model_config = ConfigDict(extra="allow")
    state: Literal["full", "low", "empty"] | None = None  # the board's last confirmed state
    uncertain: str | None = None  # why the newest reading is unsure; never a stock change
    fill: float | None = Field(default=None, ge=0, le=1)
    calibration: Literal["ok", "missing", "stale"] | None = None
    assignment_id: str | None = None  # what the slot's photos were taken for (interface 9)


class Health(BaseModel):
    model_config = ConfigDict(extra="allow")
    camera_ok: bool | None = None
    frame_age_s: float | None = Field(default=None, ge=0)
    config_valid: bool | None = None
    config_errors: list[str] = []
    slots: dict[str, SlotHealth] = {}


class Status(_Base):
    type: Literal["status"]
    # Optional: old boards omit it. Health never orders and never cancels (docs/PARALLEL_WORK.md, interface 7).
    health: Health | None = None


Event = Annotated[Union[SlotState, ManualLow, TemperatureAlarm, DeliveryCheck, Status], Field(discriminator="type")]


class Decision(BaseModel):
    action: Literal["approve", "choose", "skip"] = "approve"
    option: int | None = None
    pin: str = ""


class TextRequest(BaseModel):
    text: str
    product_id: str | None = None
    asked_by: str | None = Field(default=None, max_length=60)  # the person who asks; the log and the approver see it


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


@app.get("/fonts/name.ttf")
def name_font():
    """The cursive font of the name on the case, so the page and the box carry the same name. SIL Open Font License."""
    return FileResponse(ROOT / "enclosure" / "fonts" / "Borel-Regular.ttf", media_type="font/ttf")


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
    if data.get("temperature_c") is not None and data["type"] != "delivery_check":
        cold_log.record(float(data["temperature_c"]), "sensor", datetime.now(TZ))  # the cold log; it only watches
    if data["type"] == "temperature_alarm":
        device["alarm"] = data["active"]
    elif "alarm" in data:
        device["alarm"] = data["alarm"]
    if data["type"] == "slot_state":
        data = shelf.observe(data)  # the observation; with fill, units = round(fill x the assignment's target)
        device["slots"][str(data["slot"])] = {"state": data["state"], "units": data.get("estimated_units")}
        if data.get("estimated_units") is not None and not data.get("uncertain"):
            # The stock timeline. It only watches: also in a recording session, and before any trigger.
            stock_history.record(int(data["slot"]), int(data["estimated_units"]), "camera", datetime.now(TZ))
    if RECORDING_SESSION:
        if data["type"] in ("slot_state", "manual_low"):
            log.write("recording", {"summary": f"Recording session {RECORDING_SESSION}: slot {data['slot']} reads "
                                    f"{data.get('state', 'low (knob)')}. Purchasing is isolated: no trigger.",
                                    "event": data}, "device", ruleset["version"])
        return {"status": "ignored", "reason": f"recording session {RECORDING_SESSION}: purchasing isolated"}
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
    """The open approval requests. Each one says who started it: a person, the shelf camera or an outside agent."""
    waiting = agent.open_approvals()
    runs = {a["run_id"] for a in waiting}
    started = {e["run_id"]: e for e in log.entries(2000) if e["step"] == "trigger" and e["run_id"] in runs}
    return [a | {"asked_by": started[a["run_id"]]["actor"] if a["run_id"] in started else None,
                 "request": started[a["run_id"]]["detail"].get("text") if a["run_id"] in started else None,
                 "stock": _stock_for(a)}
            for a in waiting]


def _slot_outlook(slot: int):
    target = next((t for t in ruleset["targets"] if t["slot"] == slot), None)
    return target, outlook(slot, stock_history.readings(slot), target.get("target_units") if target else None, datetime.now(TZ))


def _stock_for(approval: dict) -> dict | None:
    """What the stock timeline says about a request that a shelf slot started: the count, the use, and each option against it."""
    if approval.get("slot") is None:
        return None
    _, o = _slot_outlook(int(approval["slot"]))
    if o.units is None:
        return None
    options = []
    for opt in approval["options"]:
        first = opt["lines"][0] if opt.get("lines") else {}
        options.append(against_delivery(o, first.get("delivery"), sum(l["quantity"] for l in opt.get("lines", []))))
    return o.to_dict() | {"options": options}


class Count(BaseModel):
    units: int = Field(ge=0, le=100000)
    counted_by: str | None = Field(default=None, max_length=60)
    source: Literal["person", "sensor"] = "person"


@app.post("/stock/{slot}/count")
def stock_count(slot: int, c: Count):
    """A count of one slot by a person or a distance sensor. It goes into the timeline and starts no order."""
    if not any(t["slot"] == slot for t in ruleset["targets"]):
        raise HTTPException(404, f"slot {slot} is not in rule set v{ruleset['version']}")
    kept = stock_history.record(slot, c.units, c.source, datetime.now(TZ))
    return {"slot": slot, "units": c.units, "source": c.source, "kept": kept}


@app.get("/stock")
def stock():
    """Per slot: the latest count, the use per day, the run-out time and the last readings."""
    out = []
    for t in sorted(ruleset["targets"], key=lambda t: t["slot"]):
        _, o = _slot_outlook(t["slot"])
        recent = stock_history.readings(t["slot"], limit=60)
        waiting = t["slot"] in agent.pending_slots  # an order or a request for this slot is not settled yet
        out.append(o.to_dict() | {"product": t.get("product"), "target_units": t.get("target_units"),
                                  "reorder_below": t.get("reorder_below"),
                                  "on_order": agent.last_order_qty.get(t["slot"]) if waiting else None,
                                  "request_open": waiting and agent.last_order_qty.get(t["slot"]) is None,
                                  "history": [{"time": x.time.isoformat(timespec="seconds"), "units": x.units, "source": x.source}
                                              for x in recent]})
    return out


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
        "signed_by": ruleset.get("signed_by"), "pins_set": bool(PINS),
        # For a demo only: SHOW_PINS=1 puts the approver PINs on the page, so that a visitor can approve.
        "demo_pins": [{"pin": p, "name": n} for p, n in PINS.items()] if SHOW_PINS else [],
        "recording_session": RECORDING_SESSION or None} | device_health()


def device_health() -> dict:
    """The last health the board sent and its age. Older than HEALTH_STALE_S counts as unknown."""
    h = agent.last_health
    age = round((datetime.now(TZ) - h["received"]).total_seconds(), 1) if h else None
    return {"health": h["report"] if h else None, "health_age_s": age,
            "health_known": age is not None and age <= SETTINGS.health_stale_s}


@app.get("/rules")
def rules():
    return ruleset


# Shelf configuration (docs/PARALLEL_WORK.md, interfaces 4 and 9). The expectation comes from the
# signed rule set; the observation from the device stays apart from it.

class ShelfChange(BaseModel):
    pin: str = ""
    product_id: str | None = Field(default=None, min_length=1, max_length=40)
    unmapped: bool = False  # true: the slot holds `product` (a name), with no catalog product and no purchasing
    target_units: int | None = Field(default=None, ge=1, le=10000)
    reorder_below: int | None = Field(default=None, ge=0, le=10000)
    product: str | None = Field(default=None, min_length=1, max_length=80)  # name for search and pack size
    group: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,40}$")  # substitute group of the slot


def _approver(pin: str) -> str:
    """The approver a PIN names. Wrong PINs count toward the same lock as on approvals."""
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


def _slot_health(slot: int) -> dict | None:
    """The board's last health for one slot (interface 7), with its age. Older than HEALTH_STALE_S is not known."""
    d = device_health()
    h = ((d["health"] or {}).get("slots") or {}).get(str(slot))
    return None if h is None else dict(h) | {"age_s": d["health_age_s"], "known": d["health_known"]}


def _activate(new: dict) -> None:
    """Make a newly signed rule set version the one every part of the backend uses."""
    global ruleset
    ruleset = new
    agent.rules = new
    driving_test.rules = new


@app.get("/shelf")
def shelf_view():
    """Per slot: the expected assignment with its assignment_id, then the observation and any pending order."""
    return shelf.view(pending=agent.pending_for_slot, health=_slot_health)


@app.put("/shelf/{slot}")
def shelf_change(slot: int, c: ShelfChange):
    """Change what a slot holds. This signs and activates a new rule set version; it never edits one."""
    approver = _approver(c.pin)
    try:
        return shelf.change(slot, c.model_dump(exclude={"pin"}), approver, catalog, log,
                            pending=agent.pending_for_slot, activate=_activate)
    except ShelfError as e:
        raise HTTPException(e.status, str(e)) from e


@app.get("/log")
def get_log(format: str = "story", limit: int = 200):
    entries = log.entries(limit)
    return {"story": story(entries)} if format == "story" else entries


@app.post("/request")
def text_request(r: TextRequest):
    return agent.handle_request(r.text, r.product_id, (r.asked_by or "").strip() or None)


# A scanned product code, looked up in the wholesaler's catalog -------------------------------------

def _code(text: str) -> str:
    """A product code as people and scanners give it: digits only, with no leading zeros."""
    return "".join(ch for ch in str(text) if ch.isdigit()).lstrip("0")


def _by_code(code: str) -> list[dict]:
    """The catalog products that carry this GTIN (the number under the barcode). A few codes name two products."""
    wanted = _code(code)
    return [p for p in catalog.products.values() if wanted and _code(p.get("gtin") or "") == wanted]


def _scan_answer(product: dict) -> dict:
    """What the catalog and this kitchen's rules say about one product, for a person who holds the pack."""
    forbidden, person = R.safety(product, ruleset)
    verdict = "forbidden" if forbidden else "read_label" if person else "allowed"
    limit = product.get("max_temp_c")
    now = datetime.now(TZ)
    air = exposure(cold_log.readings(now - timedelta(hours=24)), limit, _allowed_minutes(), None, now)
    slot = next((t["slot"] for t in ruleset["targets"] if str(t.get("product_id")) == str(product["id"])), None)
    return {"product": public(product), "verdict": verdict, "reasons": forbidden or person,
            "rule_set_version": ruleset["version"], "keep_at_or_below_c": limit, "cold_room": air.to_dict(),
            "shelf_slot": slot}


class Scan(BaseModel):
    code: str = Field(min_length=4, max_length=40)
    where: Literal["door", "shelf"] = "door"
    scanned_by: str | None = Field(default=None, max_length=60)


@app.get("/scan/{code}")
def scan_lookup(code: str):
    """Look a product code up in the catalog. Nothing is recorded."""
    found = _by_code(code)
    if not found:
        raise HTTPException(404, "This code is not in the wholesaler's catalog.")
    return {"code": _code(code), "matches": [_scan_answer(p) for p in found]}


@app.post("/scan")
def scan_record(s: Scan):
    """Record that a person or the box scanned a pack at the door or on the shelf. The audit log keeps the product,
    what this kitchen's rules say about it, and its storage limit."""
    found = _by_code(s.code)
    if not found:
        log.write("scan", {"summary": f"A scanned code is not in the catalog: {_code(s.code)}.", "code": _code(s.code),
                           "where": s.where}, s.scanned_by or "kitchen", ruleset["version"])
        raise HTTPException(404, "This code is not in the wholesaler's catalog.")
    answers = [_scan_answer(p) for p in found]
    words = {"allowed": "allowed in this kitchen", "read_label": "a person must read the label", "forbidden": "forbidden in this kitchen"}
    for a in answers:
        limit = a["keep_at_or_below_c"]
        log.write("scan", {"summary": f"{a['product']['name']} scanned at the {s.where}: {words[a['verdict']]}"
                           + (f" ({'; '.join(a['reasons'])})" if a["reasons"] else "")
                           + (f". Keep at {limit:g} °C or colder." if limit is not None else ". No storage limit in the catalog."),
                           "code": _code(s.code), "product_id": a["product"]["id"], "where": s.where,
                           "verdict": a["verdict"]}, s.scanned_by or "kitchen", ruleset["version"])
    return {"code": _code(s.code), "where": s.where, "matches": answers}


# The cold log and the way of an order ------------------------------------------------------------

cold_alerted: set[tuple[int, str]] = set()  # (slot, start of the breach): one audit entry for each breach


def _allowed_minutes() -> int:
    return int((ruleset.get("cold_chain") or {}).get("max_minutes_above", DEFAULT_ALLOWED_MIN))


def _cold_products(since: datetime | None = None) -> list[dict]:
    """Each shelf product against its own storage limit from the catalog."""
    readings, now, out = cold_log.readings(since), datetime.now(TZ), []
    for t in sorted(ruleset["targets"], key=lambda t: t["slot"]):
        product = (catalog.product(t["product_id"]).value if t.get("product_id") else None) or {}
        e = exposure(readings, product.get("max_temp_c"), _allowed_minutes(), since, now)
        out.append(e.to_dict() | {"slot": t["slot"], "product": t.get("product")})
    return out


class TempReading(BaseModel):
    temperature_c: float = Field(ge=-60, le=80)
    measured_by: str | None = Field(default=None, max_length=60)


@app.post("/cold/reading")
def cold_reading(t: TempReading):
    """A temperature that a person measured, for example with a probe thermometer. It goes into the cold log."""
    cold_log.record(t.temperature_c, "person", datetime.now(TZ))
    return {"temperature_c": t.temperature_c, "source": "person"}


@app.get("/cold")
def cold():
    """The air temperature where the food is kept, and each shelf product against its own limit over the last day."""
    now = datetime.now(TZ)
    since = now - timedelta(hours=24)
    products, readings = _cold_products(since), cold_log.readings(since)
    for p in products:
        if p["state"] == BREACH:
            started = (now - timedelta(minutes=p["minutes_above_now"])).strftime("%Y-%m-%dT%H")
            if (p["slot"], started) not in cold_alerted:
                cold_alerted.add((p["slot"], started))
                log.write("cold_breach", {"summary": f"{p['product']} was above its {p['limit_c']:g} °C limit for "
                                          f"{p['minutes_above_now']} minutes (air {p['temperature_c']:.1f} °C). "
                                          "A person must check the product before use.", "slot": p["slot"],
                                          "limit_c": p["limit_c"], "minutes": p["minutes_above_now"]},
                          "cold log", ruleset["version"])
    last = readings[-1] if readings else None
    return {"temperature_c": last.temperature_c if last else None,
            "measured_at": last.time.isoformat(timespec="seconds") if last else None,
            "source": last.source if last else None, "allowed_minutes": _allowed_minutes(), "products": products,
            "history": [{"time": x.time.isoformat(timespec="seconds"), "temperature_c": x.temperature_c} for x in readings[-120:]]}


@app.get("/orders/{order_id}/chain")
def order_chain(order_id: str):
    """The way of one order: who asked, what the rule check said, the order, the delivery at the door, the storage.

    Every step comes from the audit log or the cold log. A step that did not happen yet has done = false.
    """
    order = agent.orders.get(order_id)
    if order is None:
        raise HTTPException(404, "No such order.")
    entries = [e for e in log.entries(100000) if e["run_id"] == order.get("run_id")]
    first = lambda step: next((e for e in entries if e["step"] == step), None)  # noqa: E731
    asked, ordered = first("trigger"), next((e for e in entries if e["step"] == "order"), None)
    checks = [e for e in entries if e["step"] == "rule_check"]
    decided = next((e for e in entries if e["step"] == "approval" and e["detail"].get("status") not in ("open", "expired")), None)
    delivery = next((d for d in agent.deliveries if d["order_id"] == order_id), None)
    delivered_at = next((e["time"] for e in entries if e["step"] == "delivery_check"), None)
    steps = [
        {"step": "asked", "done": asked is not None, "time": asked and asked["time"], "who": asked and asked["actor"],
         "text": asked["detail"].get("summary", "") if asked else "No request in the log."},
        {"step": "checked", "done": bool(checks), "time": checks[-1]["time"] if checks else None,
         "who": decided["actor"] if decided else "rule check",
         "text": (f"Rule set {order.get('rule_set_version', ruleset['version'])}: " + checks[-1]["detail"]["result"].replace("_", " ")
                  + (f". {decided['actor']} approved." if decided else ". No person was needed.")) if checks else "No rule check in the log."},
        {"step": "ordered", "done": True, "time": order["time"], "who": ordered["actor"] if ordered else None,
         "text": ", ".join(f"{l['quantity']} × {l['name']}" for l in order["lines"]) + f", €{order['total_eur']:.2f}"},
        {"step": "delivered", "done": delivery is not None, "time": delivered_at, "who": "at the door",
         "text": delivery["summary"] + (f" Air at the door: {delivery['temperature_c']:.1f} °C." if delivery and delivery.get("temperature_c") is not None else "")
                 if delivery else "Not checked at the door yet."},
    ]
    stored = {"step": "stored", "done": False, "time": None, "who": "cold log", "text": "The storage record starts after the delivery check."}
    if delivery is not None and delivered_at:
        since = datetime.fromisoformat(delivered_at)
        limits = [(catalog.product(l["product_id"]).value or {}).get("max_temp_c") for l in order["lines"]]
        limit = min((x for x in limits if x is not None), default=None)
        e = exposure(cold_log.readings(since), limit, _allowed_minutes(), since, datetime.now(TZ))
        stored = {"step": "stored", "done": e.state != "unknown", "time": e.measured_at, "who": "cold log", "state": e.state,
                  "text": "No temperature reading since the delivery." if e.temperature_c is None else
                          "No storage limit in the product data." if limit is None else
                          f"Since the delivery: {e.minutes_above_total} minutes above {limit:g} °C, peak {e.peak_c:.1f} °C. Now {e.state}."}
    return {"order_id": order_id, "steps": steps + [stored]}


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
