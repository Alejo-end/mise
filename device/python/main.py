# Safe Kitchen Agent: Linux side of the UNO Q.
#
# Once a second (frame_interval_s) it takes the newest camera frame and compares each taped shelf slot
# with stored photos of the slot when empty and when full. When a slot changes
# state, it sends an event to the backend on the laptop. It also forwards the
# knob, the knob press and the thermo readings, and plays buzzer tones that the
# backend asks for.
#
# A web page on port 7000 of the board shows the camera, the slots and the
# sensors, and has the buttons to calibrate the slots.

import base64
import io
import json
import math
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import requests
from PIL import Image

from arduino.app_bricks.web_ui import WebUI
# USBCamera was removed from the Arduino bricks on 25 Sep 2026 (app-bricks-py #499).
# Camera returns numpy frames in BGR order; numpy_to_pil converts them to an RGB PIL image.
try:
    from arduino.app_peripherals.camera import Camera, WebSocketCamera
    CAMERA_LIBRARY_ERROR = ""
except ImportError as exc:
    # A camera is useful, but it must not prevent the knob, LED readout, web UI or
    # manual ordering from starting when App Lab provisions a camera-less container.
    CAMERA_LIBRARY_ERROR = str(exc)
    try:
        # The bricks image 0.5.0 on this board still ships USBCamera and not Camera.
        # USBCamera gives an RGB PIL image; the worker expects a BGR numpy frame, as Camera gives.
        from arduino.app_peripherals.usb_camera import USBCamera

        class Camera:
            def __init__(self, resolution=(640, 480), fps=15):
                self._camera = USBCamera(resolution=resolution, fps=fps)

            def start(self):
                self._camera.start()

            def stop(self):
                self._camera.stop()

            def capture(self):
                image = self._camera.capture()
                return None if image is None else np.asarray(image.convert("RGB"))[:, :, ::-1]

        def WebSocketCamera(**_kwargs):
            raise RuntimeError("the camera over WebSocket needs a newer Arduino bricks image")

        CAMERA_LIBRARY_ERROR = ""
    except ImportError:
        Camera = WebSocketCamera = None
from arduino.app_utils import App, Bridge, Logger
try:
    from arduino.app_utils.image import numpy_to_pil
except ImportError:
    def numpy_to_pil(frame):
        """Compatibility conversion for older App Lab image helpers."""
        if getattr(frame, "ndim", 0) == 3 and frame.shape[2] >= 3:
            return Image.fromarray(frame[:, :, :3][:, :, ::-1])
        return Image.fromarray(frame)

import scene
import shelf_detect
import visible
from shelf_detect import crop

logger = Logger("safe-kitchen")
TZ = ZoneInfo("Europe/Helsinki")
HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "data"
DATA.mkdir(exist_ok=True)
CONFIG_FILE = DATA / "config.json"

DEFAULT_CONFIG = {
    # The laptop that runs the backend. On a phone hotspot, use the laptop IP.
    "backend_url": "http://192.168.1.10:8000",
    "temperature_limit_c": 6.0,
    # A camera sighting starts a timer, not an order. Someone taking one product for use should not
    # make the kitchen buy immediately. A deliberate knob press remains immediate.
    "shortage_confirm_s": 300.0,
    "frame_interval_s": 1.0,  # how often the shelf is checked; the live preview runs faster, see PREVIEW_FPS
    # "usb": camera plugged into the board. "websocket": frames sent over Wi-Fi to port 8080,
    # for example by tools/laptop-camera.html. The app.yaml must then list "ports: [8080]".
    "camera_source": "usb",
    # The three-slot occupancy detector (empty/full photo comparison per box) is replaced by product recognition
    # (visible.py: what is on the shelf, by product). "off": it reads no frames, sends no slot events and no slot
    # health. Its saved calibration (data/slot*_*.npy, layout.json) is kept: set "on" to restore it.
    "slot_detector": "off",
    # The hybrid recognition action (visible.py): the board's local model (valio-local-detect, port 8200, outside
    # this app) runs first on the same frame, then the automatic whole-frame cloud check. "off": cloud only.
    # local_detect_url: "" = this app's own host (127.0.0.1, else the container's gateway, i.e. the board).
    "local_model": "on",
    "local_detect_url": "",
    "local_min_score": 0.3,
    "manual_slot": 3,
    # Slots as fractions of the frame: x, y, width, height.
    "slots": [
        {"slot": 1, "product_hint": "rye bread 500 g", "target_units": 30, "rect": [0.05, 0.30, 0.28, 0.45]},
        {"slot": 2, "product_hint": "natural yogurt 1 kg", "target_units": 12, "rect": [0.36, 0.30, 0.28, 0.45]},
        {"slot": 3, "product_hint": "lactose-free milk 1 L", "target_units": 24, "rect": [0.67, 0.30, 0.28, 0.45]},
    ],
    # Fill fraction thresholds: at or above "full" the slot is full, below "empty" it is empty, between is low.
    "full_at": 0.66,
    "empty_below": 0.20,
    # Without a "full" photo, a slot whose difference from the empty photo is above this is present.
    "present_diff": 0.10,
    # When a reading becomes a stock change: confirm_frames, confirm_seconds, min_detail,
    # mismatch_ratio, calibration_quiet_s. See shelf_detect.DEFAULTS and docs/PARALLEL_WORK.md.
    **shelf_detect.DEFAULTS,
}


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text()))
        except Exception as e:
            logger.warning(f"config.json unreadable, using defaults: {e}")
    return cfg


config = load_config()
lock = threading.Lock()
state = {
    "slots": {},  # slot -> {"state", "fill", "units", "candidate", "count"}
    "temperature_c": None,
    "humidity": None,
    "auto_limit_eur": 40,
    "alarm": False,
    "backend_ok": None,
    "backend_error": "",
    "buzzer": "off",
    "modulinos": None,
    "last_frame": None,
    "frame_id": 0,  # grows with every new camera frame, so a frame is checked only once
    "frame_time": None,  # arrival time of last_frame, in seconds
    "last_checked_frame": None,
    "quiet_until": 0.0,  # no stock events before this time (after a calibration or a slot move)
    "camera_ok": False,
    "camera_error": "",
    "delivery": None,  # {"ends": time, "temps": [...]} while a delivery check runs, else None
    "last_delivery": None,
    # Camera-confirmed low/empty readings waiting through the kitchen's grace period before they reach purchasing.
    "shortage_wait": {},  # slot -> {"due", "event"}
    # Interface 9: slot -> assignment_id from the backend's GET /shelf, polled every SHELF_POLL_S.
    # Kept after a failed poll; "unknown" until the first answer (then no slot can be stale).
    "assignments": {},
    "assignment_check": "unknown",
    "shelf_error": "",
}


def now_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")


def save_config():
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


# Backend -----------------------------------------------------------------

def post_event(event: dict):
    event.setdefault("time", now_iso())
    event.setdefault("temperature_c", state["temperature_c"])
    event.setdefault("auto_limit_eur", state["auto_limit_eur"])
    event.setdefault("alarm", state["alarm"])
    try:
        r = requests.post(config["backend_url"].rstrip("/") + "/events", json=event, timeout=5)
        r.raise_for_status()
        state["backend_ok"], state["backend_error"] = True, ""
        reply = r.json()
        logger.info(f"event {event['type']} -> {reply}")
        ui.send_message("event", {"event": event, "reply": reply})
        return reply
    except Exception as e:
        state["backend_ok"], state["backend_error"] = False, str(e)[:200]
        logger.warning(f"backend unreachable: {e}")
        ui.send_message("event", {"event": event, "reply": {"status": "backend unreachable"}})
        return None


def post_async(event: dict):
    threading.Thread(target=post_event, args=(event,), daemon=True).start()


def queue_camera_shortage(event: dict):
    """Hold a camera shortage before it can purchase; a human knob press is intentionally separate."""
    wait_s = max(0.0, float(config.get("shortage_confirm_s", 300.0)))
    if wait_s == 0:
        post_async(event)
        return
    slot, now = int(event["slot"]), time.time()
    with lock:
        queued = state["shortage_wait"].get(slot)
        if queued is None:
            state["shortage_wait"][slot] = {"due": now + wait_s, "event": event}
            logger.info(f"slot {slot} {event['state']}: waiting {wait_s:.0f} s before ordering")
        else:
            # Escalating low to empty keeps the original due time but sends the fresher count.
            queued["event"] = event
    ui.send_message("sensors", sensors())


def cancel_camera_shortage(slot: int):
    with lock:
        cancelled = state["shortage_wait"].pop(slot, None)
    if cancelled:
        logger.info(f"slot {slot} recovered before the order grace period ended")
        ui.send_message("sensors", sensors())


def shortage_worker():
    """Release only shortages that the camera confirms at the moment the grace period ends.

    The last confirmed state is not enough: a covered camera, a stopped camera or a new set-up leaves it at
    low or empty while nobody knows what the shelf holds. Such a shortage stays in the queue, marked as held,
    until fresh readings confirm it again or show that the shelf is full.
    """
    while True:
        ready = []
        now = time.time()
        with lock:
            for slot, queued in list(state["shortage_wait"].items()):
                current = state["slots"].get(slot, {})
                if current.get("state") not in ("low", "empty"):
                    state["shortage_wait"].pop(slot, None)
                elif now >= queued["due"]:
                    if shelf_detect.shortage_confirmed_now(current, now, config):
                        # The count of this moment, not the one from the start of the wait.
                        ready.append(queued["event"] | {"state": current["state"], "estimated_units": current["units"],
                                                        "fill": current["fill"]})
                        state["shortage_wait"].pop(slot, None)
                    elif not queued.get("held"):
                        queued["held"] = True
                        logger.info(f"slot {slot}: the wait ended, but the camera reading is uncertain "
                                    f"({current.get('uncertain') or 'not confirmed again yet'}). No order until it is clear.")
        for event in ready:
            logger.info(f"slot {event['slot']} still {event['state']}: order grace period ended")
            post_async(event)
        if ready:
            ui.send_message("sensors", sensors())
        time.sleep(0.5)


BUZZER_MODES = {"off": 0, "approval": 1, "alarm": 2, "confirm": 3}


buzzer_wanted = {"mode": "off", "confirm": False}


def set_buzzer(mode: str):
    """Record the wanted buzzer mode. buzzer_worker() sends it to the microcontroller.

    Bridge callbacks run on the Bridge read thread, and a Bridge.call from there
    waits for a reply that same thread must read, so it times out. Only the
    worker thread calls Bridge.
    """
    if mode == "confirm":
        buzzer_wanted["confirm"] = True
    else:
        buzzer_wanted["mode"] = mode


def matrix_status() -> int:
    """What the LED face shows besides the limit: bit 0 while a delivery waits for a decision at the door."""
    return 1 if state.get("delivery_waiting") else 0


def buzzer_worker():
    applied = None
    while True:
        try:
            if buzzer_wanted["confirm"]:
                buzzer_wanted["confirm"] = False
                Bridge.call("set_buzzer", BUZZER_MODES["confirm"])
                applied = None  # the confirm beep ends in "off" on the board, so resend the mode
                time.sleep(0.2)
            if time.time() - tof.get("logged", 0) > 10:   # the distance sensor's state, in the log once in 10 s
                tof["logged"] = time.time()
                v = int(Bridge.call("tof_info"))
                logger.info(f"tof: state {v // 10000000 - 10}, frames {(v // 100) % 100000}, valid zones {v % 100}, "
                            f"frames sent {tof.get('sent', 0)}")
            status = matrix_status()
            if status != state.get("status_sent"):
                Bridge.call("set_status", status)
                state["status_sent"] = status
            # The cold alarm wins over the approval beep. It is the backend's (each product's own limit); without the
            # backend, the box's own limit (temperature_limit_c) stands in.
            alarm = state["alarm"] if state.get("backend_alarm") is None else state["backend_alarm"]
            loud = alarm and time.time() >= state.get("alarm_quiet_until", 0)
            mode = "alarm" if loud else buzzer_wanted["mode"]
            if mode != applied:
                Bridge.call("set_buzzer", BUZZER_MODES[mode])
                applied = mode
                state["buzzer"] = mode
        except Exception as e:
            logger.warning(f"set_buzzer failed: {e}")
            applied = None
            time.sleep(1)
        time.sleep(0.2)


SHELF_POLL_S = 60


def poll_shelf():
    """Ask the backend every minute which product each slot should hold (GET /shelf, interface 9).

    Until the backend has /shelf the call fails. That is "unknown", not "stale": slots keep working.
    """
    while True:
        try:
            r = requests.get(config["backend_url"].rstrip("/") + "/shelf", timeout=5)
            r.raise_for_status()
            state["assignments"] = shelf_detect.assignments_from_shelf(r.json())
            state["assignment_check"], state["shelf_error"] = "ok", ""
        except Exception as e:
            state["shelf_error"] = str(e)[:200]
        time.sleep(SHELF_POLL_S)


def poll_commands():
    while True:
        try:
            r = requests.get(config["backend_url"].rstrip("/") + "/device/commands", timeout=3)
            r.raise_for_status()
            state["backend_ok"], state["backend_error"] = True, ""
            cmd = r.json()
            set_buzzer(cmd.get("buzzer", "off"))
            # The cold alarm follows each product's own limit, which the backend knows. A person who silenced it on
            # the page or the touch screen makes the buzzer quiet here too.
            state["backend_alarm"] = bool(cmd["cold_alarm"]) if "cold_alarm" in cmd else None
            if cmd.get("quiet_s"):
                state["alarm_quiet_until"] = max(state.get("alarm_quiet_until", 0), time.time() + float(cmd["quiet_s"]))
            ask = cmd.get("recognize")
            if isinstance(ask, dict) and ask.get("request_id"):  # the demo page asked through the backend
                shelf_view.request(str(ask.get("requested_by") or "demo page")[:40], str(ask["request_id"])[:40])
            ctl = cmd.get("continuous")
            if isinstance(ctl, dict) and ctl.get("command_id"):  # Start/Stop of continuous recognition (demo page)
                by, cid = str(ctl.get("requested_by") or "demo page")[:40], str(ctl["command_id"])[:40]
                if ctl.get("action") == "start":
                    shelf_view.start_continuous(ctl.get("pause_s"), by, cid)
                elif ctl.get("action") == "stop":
                    shelf_view.stop_continuous(by, cid)
            if time.time() - state.get("screen_at", 0) > 10:  # the LED face shows a parcel while a delivery waits
                state["screen_at"] = time.time()
                screen = requests.get(config["backend_url"].rstrip("/") + "/box/screen", timeout=3).json()
                state["delivery_waiting"] = bool(screen.get("delivery"))
        except Exception as e:
            state["backend_ok"], state["backend_error"] = False, str(e)[:200]
            state["backend_alarm"] = None
            set_buzzer("off")
        time.sleep(1)  # the demo page's "Recognize now" reaches the box within about a second


# Microcontroller callbacks ------------------------------------------------

def on_knob_limit(eur):
    state["auto_limit_eur"] = int(eur)
    ui.send_message("sensors", sensors())
    post_async({"type": "status"})


def on_knob_pressed():
    slot = int(config.get("manual_slot", 3))
    s = next((x for x in config["slots"] if x["slot"] == slot), None)
    set_buzzer("confirm")
    post_async({"type": "manual_low", "slot": slot, "product_hint": s["product_hint"] if s else None})


DELIVERY_SECONDS = 60


def start_delivery_check():
    """Measure the temperature for 60 s, then send the counts and the average to the agent."""
    if state["delivery"]:
        return {"ok": False, "error": "a delivery check is already running"}
    state["delivery"] = {"ends": time.time() + DELIVERY_SECONDS, "temps": []}
    set_buzzer("confirm")

    def finish():
        time.sleep(DELIVERY_SECONDS)
        d, state["delivery"] = state["delivery"], None
        temps = d["temps"] or ([state["temperature_c"]] if state["temperature_c"] is not None else [])
        counts = {str(k): v["units"] for k, v in state["slots"].items() if v.get("units") is not None}
        reply = post_event({"type": "delivery_check", "counts": counts,
                            "temperature_c": round(max(temps), 2) if temps else None,
                            "seconds_measured": DELIVERY_SECONDS})
        state["last_delivery"] = reply

    threading.Thread(target=finish, daemon=True).start()
    return {"ok": True}


def on_knob_long_press():
    start_delivery_check()


# The knob menu of the sketch: page number -> name. The sketch owns the pages and the matrix;
# this side gives each confirmed page its meaning.
MENU_PAGES = {0: "limit", 1: "delivery", 2: "cold", 3: "stock"}
ALARM_QUIET_S = 600  # after "I have seen the alarm" the buzzer stays quiet this long


def menu_choice(page: str, choice: int) -> str:
    """What the choice of a page means, in words for the page of the board."""
    if page == "delivery":
        return "accept" if choice == 0 else "reject"
    if page == "stock":
        slots = sorted(config["slots"], key=lambda s: s["slot"])
        return f"slot {slots[choice]['slot']}: {slots[choice]['product_hint']}" if choice < len(slots) else "no such slot"
    return "seen" if page == "cold" else ""


def on_knob_menu(page, choice):
    name = MENU_PAGES.get(int(page), "limit")
    state["menu"] = {"page": name, "choice": int(choice), "label": menu_choice(name, int(choice))}
    ui.send_message("sensors", sensors())


def on_knob_confirm(page, choice):
    """A hold on a menu page: the person decided. The box has no PIN, so nothing here orders or approves."""
    name, choice = MENU_PAGES.get(int(page)), int(choice)  # the sketch plays its own chime
    if name == "delivery":
        post_async({"type": "delivery_decision", "accepted": choice == 0})
    elif name == "cold":                                  # the sketch sends this only during an alarm
        state["alarm_quiet_until"] = time.time() + ALARM_QUIET_S
        post_async({"type": "alarm_seen"})
    elif name == "stock":
        slots = sorted(config["slots"], key=lambda s: s["slot"])
        if choice < len(slots):
            post_async({"type": "manual_low", "slot": slots[choice]["slot"], "product_hint": slots[choice]["product_hint"]})


# The distance sensor: eight rows of eight distances in mm (-1: no valid target). A full frame goes to the backend.
tof = {"rows": [None] * 8, "seen": 0}


def on_tof_row(row, *mm):
    try:
        _tof_row(int(row), mm)
    except Exception as e:
        logger.warning(f"tof row: {e}")


def _tof_row(row, mm):
    if 0 <= row < 8 and len(mm) == 8:
        tof["rows"][row] = [None if int(v) < 0 else round(int(v) / 10, 1) for v in mm]
        if row == 7 and all(r is not None for r in tof["rows"]):
            frame = [v for r in tof["rows"] for v in r]
            tof["seen"] = time.time()
            tof["sent"] = tof.get("sent", 0) + 1
            threading.Thread(target=post_tof, args=(frame,), daemon=True).start()


def post_tof(cm):
    try:
        requests.post(config["backend_url"].rstrip("/") + "/box/tof", json={"cm": cm}, timeout=2)
    except Exception as e:
        logger.debug(f"tof: {e}")


def finite(value):
    """The value as a float, or None when it is missing or not a real number (NaN, infinity)."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def on_thermo(celsius, humidity):
    # A Thermo that is unplugged or busy reads NaN. JSON cannot carry NaN, so one bad reading
    # in the state would stop every event to the agent and break the /state page.
    celsius, humidity = finite(celsius), finite(humidity)
    if celsius is None:
        if state["temperature_c"] is not None:
            logger.warning("thermo gave no valid reading; the temperature is unknown until it does")
        state["temperature_c"], state["humidity"] = None, None
        ui.send_message("sensors", sensors())
        return
    if state["delivery"]:
        state["delivery"]["temps"].append(celsius)
    state["temperature_c"] = round(celsius, 2)
    state["humidity"] = None if humidity is None else round(humidity, 1)
    limit = float(config["temperature_limit_c"])
    # 0.5 degree hysteresis so the alarm does not flicker at the limit.
    if not state["alarm"] and celsius > limit:
        state["alarm"] = True
        post_async({"type": "temperature_alarm", "active": True, "temperature_c": state["temperature_c"], "limit_c": limit})
    elif state["alarm"] and celsius < limit - 0.5:
        state["alarm"] = False
        state["alarm_quiet_until"] = 0  # a new alarm is loud again
        post_async({"type": "temperature_alarm", "active": False, "temperature_c": state["temperature_c"], "limit_c": limit})
    ui.send_message("sensors", sensors())


Bridge.provide("knob_limit", on_knob_limit)
Bridge.provide("knob_pressed", on_knob_pressed)
Bridge.provide("knob_long_press", on_knob_long_press)
Bridge.provide("knob_menu", on_knob_menu)
Bridge.provide("knob_confirm", on_knob_confirm)
Bridge.provide("thermo", on_thermo)
Bridge.provide("tof_row", on_tof_row)


# Shelf detection -----------------------------------------------------------

# The algorithm lives in shelf_detect.py, so laptop replay tests run the same code.

def ref_path(slot: int, kind: str) -> Path:
    return DATA / f"slot{slot}_{kind}.npy"


def load_ref(slot: int, kind: str):
    p = ref_path(slot, kind)
    return np.load(p) if p.exists() else None


def meta_path(slot: int) -> Path:
    return DATA / f"slot{slot}_meta.json"


def load_meta(slot: int) -> dict:
    """The slot's calibration record: for each photo, the assignment it was taken for and when."""
    try:
        return json.loads(meta_path(slot).read_text())
    except Exception:
        return {}  # photos saved before 14 Nov have no record: their assignment is unknown


calib_meta = {s["slot"]: load_meta(s["slot"]) for s in config["slots"]}

# Layout editing: which moved boxes still need new photos (kept across restarts), the setup pause,
# and the validity of the saved layout (an invalid saved layout never produces stock events).
LAYOUT_FILE = DATA / "layout.json"
SETUP_MAX_S = 1800  # the setup pause ends by itself after 30 minutes without a save


def load_layout_state() -> dict:
    try:
        return {"recalibrate": json.loads(LAYOUT_FILE.read_text()).get("recalibrate", {})}
    except Exception:
        return {"recalibrate": {}}


layout = load_layout_state()
state["setup_until"] = 0.0
state["layout_errors"] = shelf_detect.config_errors(config)


def save_layout_state():
    try:
        LAYOUT_FILE.write_text(json.dumps(layout, indent=2))
    except OSError as e:
        logger.warning(f"could not save the layout state: {e}")


def calibrated_for(slot: int):
    return shelf_detect.calibrated_for(calib_meta.get(slot), ref_path(slot, "full").exists())


def calibration_of(slot: int) -> str:
    """ok, missing or stale (interface 9)."""
    return shelf_detect.calibration_status(ref_path(slot, "empty").exists(), calibrated_for(slot),
                                           state["assignments"].get(slot))


def classify(slot_cfg: dict, patch: np.ndarray):
    """(state, fill, why). state may be "uncertain", which never becomes a stock change."""
    slot = slot_cfg["slot"]
    return shelf_detect.classify(patch, load_ref(slot, "empty"), load_ref(slot, "full"), config)


def process_frame(img: Image.Image, frame_id: int, t: float):
    quiet = time.time() < state["quiet_until"]
    setup = time.time() < state["setup_until"]
    layout_ok = not state["layout_errors"]
    for s in config["slots"]:
        slot = s["slot"]
        # A slot whose photos are for another product is uncertain until someone saves new photos.
        reading = shelf_detect.gate_calibration(classify(s, crop(img, s["rect"])), calibration_of(slot))
        # Setting up, an invalid saved layout, or a moved box without new photos: uncertain, no stock event.
        st, fill, why = shelf_detect.gate_layout(reading, layout_ok, shelf_detect.needs_recal(layout["recalibrate"], slot),
                                                 setup)
        with lock:
            cur = state["slots"].setdefault(slot, shelf_detect.new_tracker())
            changed = shelf_detect.observe(cur, st, fill, s["target_units"], frame_id, t, config, quiet, why)
        if changed:
            # Slot event version 2 (docs/PARALLEL_WORK.md, interface 1). The backend ignores fields it
            # does not know yet, and "uncertain" is never sent.
            event = {"type": "slot_state", "slot": slot, "product_hint": s["product_hint"], "state": cur["state"],
                     "estimated_units": cur["units"], "fill": cur["fill"], "frame_id": frame_id,
                     "frame_time": datetime.fromtimestamp(t, TZ).isoformat(timespec="seconds")}
            if cur["state"] in ("low", "empty"):
                queue_camera_shortage(event)
            else:
                cancel_camera_shortage(slot)
                post_async(event)


def mark_uncertain(why: str):
    """Every slot is uncertain, for example when no camera frame arrives."""
    with lock:
        for s in config["slots"]:
            cur = state["slots"].setdefault(s["slot"], shelf_detect.new_tracker())
            shelf_detect.observe(cur, "uncertain", None, s["target_units"], None, time.time(), config, why=why)


def start_quiet_period():
    state["quiet_until"] = time.time() + float(shelf_detect.setting(config, "calibration_quiet_s"))


def health():
    """The health of the status event (docs/PARALLEL_WORK.md, interface 7). Health never orders."""
    t = state["frame_time"]
    with lock:
        trackers = {k: dict(v) for k, v in state["slots"].items()}
    ids = [s["slot"] for s in config["slots"]]
    return shelf_detect.build_health(
        trackers, ids, state["camera_ok"], None if t is None else time.time() - t, shelf_detect.config_errors(config),
        {n: calibration_of(n) for n in ids}, {n: calibrated_for(n) for n in ids}, state["assignment_check"])


def health_or_none():
    """health(), or None after logging why: a bug in the health must not stop the page or the status ping.
    With the three-slot detector off, the health says so and carries no slot readings."""
    try:
        if config.get("slot_detector", "off") != "on":
            t = state["frame_time"]
            return {"camera_ok": bool(state["camera_ok"]), "frame_age_s": None if t is None else round(max(0.0, time.time() - t), 1),
                    "config_valid": True, "config_errors": [], "assignment_check": state["assignment_check"],
                    "slots": {}, "slot_detector": "off"}
        return health()
    except Exception as e:
        logger.warning(f"health: {e}")
        return None


def sensors():
    return {
        "temperature_c": state["temperature_c"], "humidity": state["humidity"],
        "auto_limit_eur": state["auto_limit_eur"], "alarm": state["alarm"],
        "menu": state.get("menu") or {"page": "limit", "choice": 0, "label": ""},
        "alarm_quiet_s": max(0, round(state.get("alarm_quiet_until", 0) - time.time())),
        "backend_ok": state["backend_ok"], "backend_error": state["backend_error"],
        "backend_url": config["backend_url"], "buzzer": state["buzzer"], "modulinos": state["modulinos"],
        "camera_ok": state["camera_ok"], "camera_error": state["camera_error"],
        "delivery_left_s": max(0, round(state["delivery"]["ends"] - time.time())) if state["delivery"] else None,
        "last_delivery": state["last_delivery"],
        "shortage_wait": [{"slot": slot, "seconds_left": max(0, round(item["due"] - time.time())),
                           "state": item["event"]["state"], "held": bool(item.get("held"))}
                          for slot, item in state["shortage_wait"].items()],
        "slots": {str(k): {kk: vv for kk, vv in v.items() if kk in ("state", "fill", "units", "uncertain")}
                  for k, v in state["slots"].items()},
        "config": {k: config[k] for k in ("slots", "temperature_limit_c", "shortage_confirm_s", "manual_slot")},
        "calibrated": {str(s["slot"]): [k for k in ("empty", "full") if ref_path(s["slot"], k).exists()] for s in config["slots"]},
        "health": health_or_none(),
        "shelf_error": state["shelf_error"],
        "setup": time.time() < state["setup_until"],
        "layout_errors": state["layout_errors"],
        "recalibrate": layout["recalibrate"],
    }


PREVIEW_FPS = 8
PREVIEW_SIZE = (480, 360)


def send_frame(img: Image.Image):
    buf = io.BytesIO()
    img.resize(PREVIEW_SIZE).save(buf, format="JPEG", quality=60)
    ui.send_message("frame", {"image": base64.b64encode(buf.getvalue()).decode(), "time": now_iso()})


# Web page callbacks --------------------------------------------------------

def on_calibrate(_sid, data):
    """data: {"slot": 3 or "all", "kind": "empty" | "full"}"""
    img = state["last_frame"]
    if img is None:
        return {"ok": False, "error": "no camera frame yet"}
    kind = data.get("kind")
    if kind not in ("empty", "full"):
        return {"ok": False, "error": "kind must be empty or full"}
    which = [s for s in config["slots"] if data.get("slot") in ("all", s["slot"], str(s["slot"]))]
    for s in which:
        np.save(ref_path(s["slot"], kind), crop(img, s["rect"]))
        # Interface 9: remember which assignment these photos show (None while the backend has no /shelf).
        calib_meta[s["slot"]] = shelf_detect.calibration_meta(
            calib_meta.get(s["slot"]), kind, state["assignments"].get(s["slot"]), now_iso())
        try:
            meta_path(s["slot"]).write_text(json.dumps(calib_meta[s["slot"]], indent=2))
        except OSError as e:
            logger.warning(f"could not save the calibration record of slot {s['slot']}: {e}")
        with lock:
            state["slots"].pop(s["slot"], None)
        layout["recalibrate"] = shelf_detect.recal_note_photo(layout["recalibrate"], s["slot"], kind)
    save_layout_state()
    # Calibrating means hands and products moving in front of the camera: no stock events for a while.
    start_quiet_period()
    logger.info(f"saved {kind} photo for slots {[s['slot'] for s in which]}")
    return {"ok": True, "slots": [s["slot"] for s in which], "kind": kind}


def on_save_config(_sid, data):
    """Settings and the box layout. A layout is validated complete and activated all at once; a refused
    layout leaves the active one untouched (the page keeps its draft). Saving names, targets or other
    settings with unchanged boxes is never blocked by the saved layout's validity."""
    moved = []
    if "slots" in data:
        new = data["slots"]
        geometry = (not isinstance(new, list) or len(new) != len(config["slots"])
                    or shelf_detect.moved_slots(config["slots"], new))
        if geometry:
            result = shelf_detect.apply_layout(config, new)
            if not result["ok"]:
                return {"ok": False, "conflicts": result["conflicts"], "errors": result["errors"],
                        "error": "Boxes not saved: " + "; ".join(result["errors"])
                                 + ". Your moves are kept: move the red boxes apart and save again."}
            moved = result["moved"]
            layout["recalibrate"] = shelf_detect.recal_after_move(layout["recalibrate"], moved)
            save_layout_state()
            with lock:
                for n in moved:
                    state["slots"].pop(n, None)
            state["setup_until"] = 0.0  # the new layout is saved; moved boxes stay paused until new photos
        start_quiet_period()
    for key in ("backend_url", "temperature_limit_c", "shortage_confirm_s", "manual_slot", "slots"):
        if key in data:
            config[key] = data[key]
    save_config()
    state["layout_errors"] = shelf_detect.config_errors(config)
    ui.send_message("sensors", sensors())
    return {"ok": True, "moved": moved, "recalibrate": sorted(int(k) for k in layout["recalibrate"])}


def on_setup(_sid, data):
    """The page's "Edit boxes" (on) and "Cancel" (off): pause stock events while the person edits."""
    on = bool((data or {}).get("on"))
    state["setup_until"] = time.time() + SETUP_MAX_S if on else 0.0
    logger.info(f"box setup {'started: stock events paused' if on else 'ended'}")
    ui.send_message("sensors", sensors())
    return {"ok": True, "setup": on}


def on_simulate(_sid, data):
    """Send a slot event by hand, for tests without the camera."""
    slot, st = int(data["slot"]), data["state"]
    s = next((x for x in config["slots"] if x["slot"] == slot), {"product_hint": None, "target_units": 0})
    units = {"empty": 0, "low": max(1, s["target_units"] // 6), "full": s["target_units"]}[st]
    return post_event({"type": "slot_state", "slot": slot, "product_hint": s["product_hint"], "state": st,
                       "estimated_units": units})


# Shelf recognition (observational) ------------------------------------------------------------------------------
# "Recognize shelf" from either page goes through one coordinator (visible.py): one recognition at a time on the
# newest camera frame, sent to the recognition service on the laptop (experiments/recognition/shelf_service, port
# 8100). The result goes to the setup pages and to the backend's /observations/visible, where the demo page reads
# it; it never becomes a stock event, an order or an approval. recognition_call() serves the enrolment photos.

def recognition_url() -> str:
    if config.get("recognition_url"):
        return str(config["recognition_url"]).rstrip("/")
    backend = str(config["backend_url"]).rstrip("/")
    if backend.startswith("https://"):  # a hosted backend (Modal) passes the calls on to its recognition service
        return backend + "/recognition"
    host = backend.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    return f"http://{host}:8100"


def surface_rect() -> list[float]:
    """Where to look: the configured surface, else the area around all slot boxes (a region, not a product)."""
    if config.get("surface"):
        return config["surface"]
    rects = [s["rect"] for s in config.get("slots", [])]
    if not rects:
        return [0.0, 0.0, 1.0, 1.0]
    pad = 0.03
    x0, y0 = max(0.0, min(r[0] for r in rects) - pad), max(0.0, min(r[1] for r in rects) - pad)
    x1, y1 = min(1.0, max(r[0] + r[2] for r in rects) + pad), min(1.0, max(r[1] + r[3] for r in rects) + pad)
    return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]


def recognition_call(sid, path: str, extra: dict, reply: str):
    """Send the newest frame (never older than CAMERA_GRACE_S) to the service; answer the page with the result."""
    with lock:
        img, t = state["last_frame"], state["frame_time"]
    if img is None or t is None or time.time() - t > CAMERA_GRACE_S:
        ui.send_message(reply, {"ok": False, "error": "no fresh camera picture (none, or older than 2 s)"}, sid)
        return
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    b64 = base64.b64encode(buf.getvalue()).decode()
    captured = datetime.fromtimestamp(t, TZ).isoformat(timespec="milliseconds")
    surface = extra.pop("surface", None) or surface_rect()
    body = {"image_b64": b64, "surface": surface, "captured_at": captured, **extra}

    def run():
        t0 = time.time()
        try:
            r = requests.post(recognition_url() + path, json=body, timeout=60)
            try:
                out = r.json()
            except ValueError:
                out = {"error": r.text[:200]}
            ok = r.status_code == 200
        except Exception as e:
            out, ok = {"error": f"recognition service not reachable at {recognition_url()}: {str(e)[:120]}"}, False
        out = dict(out) if isinstance(out, dict) else {"result": out}
        out.update(ok=ok and not out.get("error"), round_trip_ms=round((time.time() - t0) * 1000),
                   captured_at=captured, surface=surface)
        ui.send_message(reply, out, sid)

    threading.Thread(target=run, daemon=True).start()


def grab_frame():
    """The newest camera frame for recognition: (jpeg_b64, frame_id, captured_at) or None if none is fresh."""
    with lock:
        img, t, frame_id = state["last_frame"], state["frame_time"], state["frame_id"]
    if img is None or t is None or time.time() - t > CAMERA_GRACE_S:
        return None
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return (base64.b64encode(buf.getvalue()).decode(), frame_id,
            datetime.fromtimestamp(t, TZ).isoformat(timespec="milliseconds"))


def _gateway() -> str | None:
    """The default gateway of this app's network: the board itself when the app runs in a bridge container."""
    try:
        for line in Path("/proc/net/route").read_text().splitlines()[1:]:
            f = line.split()
            if f[1] == "00000000" and f[2] != "00000000":
                return ".".join(str(int(f[2][i:i + 2], 16)) for i in (6, 4, 2, 0))
    except Exception:
        return None
    return None


_local_url = {"url": None}


def local_detect(b64: str, rid: str) -> dict:
    """The board's local model on exactly this frame (POST /detect with the JPEG). Raises on any failure."""
    tried = []
    urls = [config["local_detect_url"].rstrip("/")] if config.get("local_detect_url") else \
        ([_local_url["url"]] if _local_url["url"] else []) + ["http://127.0.0.1:8200"] + \
        ([f"http://{_gateway()}:8200"] if _gateway() else [])
    for url in dict.fromkeys(u for u in urls if u):
        try:
            r = requests.post(f"{url}/detect", params={"threshold": config.get("local_min_score", 0.3), "rid": rid},
                              data=base64.b64decode(b64), headers={"Content-Type": "image/jpeg"}, timeout=8)
        except requests.ConnectionError as e:
            tried.append(f"{url}: {str(e)[:60]}")
            continue
        out = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"error": r.text[:160]}
        if r.status_code != 200:
            raise RuntimeError(f"local model at {url} answered HTTP {r.status_code}: {str(out.get('error'))[:120]}")
        _local_url["url"] = url
        return out
    raise RuntimeError("local model not reachable (" + "; ".join(tried)[:200] + ")")


def recognize_frame(b64: str, captured: str, **hybrid) -> dict:
    """The whole picture to the recognition service (the shared product set decides what it looks for). With the
    hybrid flow: that frame's local detections and the cloud mode (whole-frame or reuse) go along."""
    try:
        if hybrid:
            r = requests.post(recognition_url() + "/recognize/hybrid", json={"image_b64": b64, "captured_at": captured, **hybrid},
                              timeout=110)
        else:
            r = requests.post(recognition_url() + "/recognize", json={"image_b64": b64, "surface": [0, 0, 1, 1],
                                                                      "captured_at": captured}, timeout=70)
    except Exception as e:
        raise RuntimeError(f"recognition service not reachable at {recognition_url()}: {str(e)[:120]}")
    try:
        out = r.json()
    except ValueError:
        out = {"error": r.text[:200]}
    if r.status_code != 200:
        detail = out.get("detail") if isinstance(out, dict) else None
        raise RuntimeError(f"recognition service answered HTTP {r.status_code}: {str(detail or out)[:160]}")
    return out


def publish_view(snap: dict, frame_b64):
    """Every change to the open setup pages (with the recognised picture when new) and to the backend, where the
    demo page reads it. The backend keeps it apart from stock and purchasing."""
    ui.send_message("visible", {**snap, "frame": frame_b64})

    def post():
        try:
            body = visible.for_backend(snap)
            if frame_b64:  # the recognised picture goes with its own result (same request id), for the demo page
                body["frame"] = frame_b64
            requests.post(config["backend_url"].rstrip("/") + "/observations/visible", json=body, timeout=10)
        except Exception as e:
            logger.warning(f"observation not delivered to the backend: {str(e)[:120]}")
    threading.Thread(target=post, daemon=True).start()


shelf_view = visible.Coordinator(grab_frame, recognize_frame, publish_view, scene=scene,
                                 local_detect=local_detect if config.get("local_model", "on") != "off" else None)


def send_view(sid):
    snap = shelf_view.snapshot()
    ui.send_message("visible", {**snap, "frame": shelf_view.current_frame}, sid)


def recognition_status(sid):
    def run():
        try:
            out = requests.get(recognition_url() + "/enrolment", timeout=10).json()
            ui.send_message("recognition_status_response", {"ok": True, "enrolment": out, "url": recognition_url()}, sid)
        except Exception as e:
            ui.send_message("recognition_status_response", {"ok": False, "error": str(e)[:160], "url": recognition_url()}, sid)
    threading.Thread(target=run, daemon=True).start()


ui = WebUI()
ui.on_message("calibrate", on_calibrate)
ui.on_message("save_config", on_save_config)
ui.on_message("setup", on_setup)
ui.on_message("simulate", on_simulate)
ui.on_message("delivery_check", lambda _sid, _d: start_delivery_check())
ui.on_message("get_state", lambda sid, _d: ui.send_message("sensors", sensors(), sid))
ui.on_message("recognize", lambda sid, _d: shelf_view.request("camera setup page"))
ui.on_message("continuous", lambda sid, d: shelf_view.start_continuous((d or {}).get("pause_s"), "camera setup page")
              if (d or {}).get("action") == "start" else shelf_view.stop_continuous("camera setup page"))
ui.on_message("visible_status", lambda sid, _d: send_view(sid))
ui.on_message("recognition_background", lambda sid, d: recognition_call(
    sid, "/background", {"surface": (d or {}).get("surface")}, "recognition_background_response"))
ui.on_message("enrol_photo", lambda sid, d: recognition_call(
    sid, "/enrol/photo", {"identity": (d or {}).get("identity"), "role": (d or {}).get("role", "reference"),
                          "surface": (d or {}).get("surface")}, "enrol_photo_response"))
ui.on_message("recognition_status", lambda sid, _d: recognition_status(sid))
ui.expose_api("GET", "/state", sensors)


def diagnostics():
    """What the app container can see: video devices and USB devices. For setup checks over Wi-Fi."""
    import glob
    import os

    usb = []
    for d in glob.glob("/sys/bus/usb/devices/*/product"):
        try:
            usb.append(open(d).read().strip())
        except OSError:
            pass
    return {"video": sorted(glob.glob("/dev/video*")), "v4l_by_id": sorted(glob.glob("/dev/v4l/by-id/*")),
            "usb_products": sorted(usb), "dev_visible": os.path.isdir("/dev/v4l")}


ui.expose_api("GET", "/diag", diagnostics)


APP_DIR = HERE.parent


def on_update(_sid, _data):
    """Copy changed app files from the laptop. The page shows at once; Python and sketch changes need a power cycle."""
    import hashlib

    base = config["backend_url"].rstrip("/")
    try:
        files = requests.get(base + "/device/files", timeout=5).json()
        changed = []
        for f in files:
            local = APP_DIR / f["path"]
            if local.exists() and hashlib.sha256(local.read_bytes()).hexdigest() == f["sha256"]:
                continue
            body = requests.get(f"{base}/device/files/{f['path']}", timeout=10).content
            if hashlib.sha256(body).hexdigest() != f["sha256"]:
                return {"ok": False, "error": f"{f['path']} changed during the download, try again"}
            local.parent.mkdir(parents=True, exist_ok=True)   # a new file can sit in a new folder
            tmp = local.with_suffix(local.suffix + ".new")
            tmp.write_bytes(body)
            tmp.replace(local)
            changed.append(f["path"])
        restart = [c for c in changed if not c.startswith("assets/")]
        logger.info(f"update: {changed}")
        return {"ok": True, "changed": changed,
                "restart": bool(restart), "message": (
                    "Nothing to update." if not changed else
                    "Updated. Pull the power supply for a few seconds to start the new version." if restart else
                    "Updated. Reload the page.")}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


ui.on_message("update", on_update)


# Main loop -----------------------------------------------------------------
#
# Three loops run side by side so a slow one never holds up another:
#   camera_worker  reads frames as fast as the camera gives them and keeps the newest
#   preview_worker sends the newest frame to the page PREVIEW_FPS times a second
#   loop           checks the shelf once every frame_interval_s and talks to the agent

last_status = 0.0
# A frame older than this counts as no frame. camera_worker also reports the camera missing after this
# long without frames; the loop checks the age itself, because a capture() that hangs never reports.
# tests/device/replay.py models the same grace (CAMERA_GRACE_S).
CAMERA_GRACE_S = 2.0


def camera_worker():
    camera = None
    last_frame_at = 0.0
    while True:
        try:
            remote = config.get("camera_source") == "websocket"
            if camera is None:
                if Camera is None or WebSocketCamera is None:
                    raise RuntimeError(f"camera support is not installed: {CAMERA_LIBRARY_ERROR}")
                if remote:
                    camera = WebSocketCamera(port=8080, resolution=(640, 480), fps=15)
                else:
                    camera = Camera(resolution=(640, 480), fps=15)  # first plugged-in camera
                camera.start()
                logger.info(f"camera started ({'websocket on port 8080' if remote else 'usb'})")
            frame = camera.capture()
            if frame is None:
                # capture() gives None whenever no new frame arrived in time (the WebSocket camera
                # waits only 0.1 s). Report the camera as missing only after 2 s without any frame,
                # so the page does not flicker between the picture and "No camera picture".
                if time.time() - last_frame_at > CAMERA_GRACE_S:
                    state["camera_ok"] = False
                    state["camera_error"] = "waiting for frames on port 8080" if remote else "camera gave no picture"
                time.sleep(0.05)
                continue
            last_frame_at = time.time()
            img = numpy_to_pil(frame)
            with lock:  # the frame and its number change together
                state["last_frame"], state["frame_time"] = img, last_frame_at
                state["frame_id"] += 1
            state["camera_ok"], state["camera_error"] = True, ""
        except Exception as e:
            state["camera_ok"], state["camera_error"] = False, str(e)[:160]
            logger.warning(f"camera: {e}")
            try:
                camera.stop()
            except Exception:
                pass
            camera = None
            time.sleep(10)  # the app only sees cameras plugged in at start, so retrying fast does not help


CAMERA_PUSH_S = 0.25  # the touch screen shows the shelf: four pictures a second go to the backend


def camera_push_worker():
    """Send the newest camera picture to the backend about once a second, for the touch screen. A picture is
    sent once, only while it is fresh, and a failed send is skipped."""
    last_sent = None
    while True:
        img, t = state["last_frame"], state["frame_time"]
        if img is not None and img is not last_sent and t and time.time() - t < CAMERA_GRACE_S:
            try:
                buf = io.BytesIO()
                img.convert("RGB").resize((480, 360)).save(buf, format="JPEG", quality=75)   # the screen is 480 wide
                requests.post(config["backend_url"].rstrip("/") + "/box/camera", data=buf.getvalue(),
                              headers={"Content-Type": "image/jpeg"}, timeout=3)
                last_sent = img
            except Exception as e:
                logger.debug(f"camera push: {e}")
        time.sleep(CAMERA_PUSH_S)


def preview_worker():
    last_sent = None
    while True:
        img = state["last_frame"]
        if img is not None and img is not last_sent:
            try:
                send_frame(img)
                last_sent = img
            except Exception as e:
                logger.warning(f"preview: {e}")
        time.sleep(1 / PREVIEW_FPS)


def loop():
    global last_status
    if state["modulinos"] is None:
        try:
            bits = int(Bridge.call("modulino_status"))
            state["modulinos"] = {"knob": bool(bits & 1), "thermo": bool(bits & 2), "buzzer": bool(bits & 4)}
            logger.info(f"modulinos: {state['modulinos']}")
        except Exception as e:
            logger.warning(f"modulino_status failed: {e}")
    with lock:
        img, frame_id, t = state["last_frame"], state["frame_id"], state["frame_time"]
    if config.get("slot_detector", "off") != "on":
        pass  # the three-slot detector is off: no slot readings, no slot events (calibration kept in data/)
    elif img is not None and state["camera_ok"] and t is not None and time.time() - t <= CAMERA_GRACE_S:
        if frame_id != state["last_checked_frame"]:  # a frame is checked at most once
            state["last_checked_frame"] = frame_id
            process_frame(img, frame_id, t)
    else:
        mark_uncertain("no camera frame")
    ui.send_message("sensors", sensors())
    if time.time() - last_status > 10:
        last_status = time.time()
        h = health_or_none()
        post_async({"type": "status", "health": h} if h is not None else {"type": "status"})
    time.sleep(float(config["frame_interval_s"]))


threading.Thread(target=poll_commands, daemon=True).start()
threading.Thread(target=poll_shelf, daemon=True).start()
threading.Thread(target=buzzer_worker, daemon=True).start()
threading.Thread(target=shortage_worker, daemon=True).start()
threading.Thread(target=camera_worker, daemon=True).start()
threading.Thread(target=preview_worker, daemon=True).start()
threading.Thread(target=camera_push_worker, daemon=True).start()
App.run(user_loop=loop)
