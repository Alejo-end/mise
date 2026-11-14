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
from arduino.app_peripherals.camera import Camera, WebSocketCamera
from arduino.app_utils import App, Bridge, Logger
from arduino.app_utils.image import numpy_to_pil

import shelf_detect
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
    "frame_interval_s": 1.0,  # how often the shelf is checked; the live preview runs faster, see PREVIEW_FPS
    # "usb": camera plugged into the board. "websocket": frames sent over Wi-Fi to port 8080,
    # for example by tools/laptop-camera.html. The app.yaml must then list "ports: [8080]".
    "camera_source": "usb",
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


def buzzer_worker():
    applied = None
    while True:
        try:
            if buzzer_wanted["confirm"]:
                buzzer_wanted["confirm"] = False
                Bridge.call("set_buzzer", BUZZER_MODES["confirm"])
                applied = None  # the confirm beep ends in "off" on the board, so resend the mode
                time.sleep(0.2)
            # The local temperature alarm wins over the approval beep.
            mode = "alarm" if state["alarm"] else buzzer_wanted["mode"]
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
            set_buzzer(r.json().get("buzzer", "off"))
        except Exception as e:
            state["backend_ok"], state["backend_error"] = False, str(e)[:200]
            set_buzzer("off")
        time.sleep(2)


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
        post_async({"type": "temperature_alarm", "active": False, "temperature_c": state["temperature_c"], "limit_c": limit})
    ui.send_message("sensors", sensors())


Bridge.provide("knob_limit", on_knob_limit)
Bridge.provide("knob_pressed", on_knob_pressed)
Bridge.provide("knob_long_press", on_knob_long_press)
Bridge.provide("thermo", on_thermo)


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
    for s in config["slots"]:
        slot = s["slot"]
        # A slot whose photos are for another product is uncertain until someone saves new photos.
        st, fill, why = shelf_detect.gate_calibration(classify(s, crop(img, s["rect"])), calibration_of(slot))
        with lock:
            cur = state["slots"].setdefault(slot, shelf_detect.new_tracker())
            changed = shelf_detect.observe(cur, st, fill, s["target_units"], frame_id, t, config, quiet, why)
        if changed:
            # Slot event version 2 (docs/PARALLEL_WORK.md, interface 1). The backend ignores fields it
            # does not know yet, and "uncertain" is never sent.
            post_async({"type": "slot_state", "slot": slot, "product_hint": s["product_hint"], "state": cur["state"],
                        "estimated_units": cur["units"], "fill": cur["fill"], "frame_id": frame_id,
                        "frame_time": datetime.fromtimestamp(t, TZ).isoformat(timespec="seconds")})


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
    """health(), or None after logging why: a bug in the health must not stop the page or the status ping."""
    try:
        return health()
    except Exception as e:
        logger.warning(f"health: {e}")
        return None


def sensors():
    return {
        "temperature_c": state["temperature_c"], "humidity": state["humidity"],
        "auto_limit_eur": state["auto_limit_eur"], "alarm": state["alarm"],
        "backend_ok": state["backend_ok"], "backend_error": state["backend_error"],
        "backend_url": config["backend_url"], "buzzer": state["buzzer"], "modulinos": state["modulinos"],
        "camera_ok": state["camera_ok"], "camera_error": state["camera_error"],
        "delivery_left_s": max(0, round(state["delivery"]["ends"] - time.time())) if state["delivery"] else None,
        "last_delivery": state["last_delivery"],
        "slots": {str(k): {kk: vv for kk, vv in v.items() if kk in ("state", "fill", "units", "uncertain")}
                  for k, v in state["slots"].items()},
        "config": {k: config[k] for k in ("slots", "temperature_limit_c", "manual_slot")},
        "calibrated": {str(s["slot"]): [k for k in ("empty", "full") if ref_path(s["slot"], k).exists()] for s in config["slots"]},
        "health": health_or_none(),
        "shelf_error": state["shelf_error"],
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
    # Calibrating means hands and products moving in front of the camera: no stock events for a while.
    start_quiet_period()
    logger.info(f"saved {kind} photo for slots {[s['slot'] for s in which]}")
    return {"ok": True, "slots": [s["slot"] for s in which], "kind": kind}


def on_save_config(_sid, data):
    if "slots" in data:
        bad = shelf_detect.overlapping_slots(data["slots"])
        if bad:
            pairs = ", ".join(f"{a} and {b}" for a, b in bad)
            return {"ok": False, "error": f"Spots {pairs} overlap, so one product would move both. "
                                          "Move them apart and save again."}
        start_quiet_period()
    for key in ("backend_url", "temperature_limit_c", "manual_slot", "slots"):
        if key in data:
            config[key] = data[key]
    save_config()
    ui.send_message("sensors", sensors())
    return {"ok": True}


def on_simulate(_sid, data):
    """Send a slot event by hand, for tests without the camera."""
    slot, st = int(data["slot"]), data["state"]
    s = next((x for x in config["slots"] if x["slot"] == slot), {"product_hint": None, "target_units": 0})
    units = {"empty": 0, "low": max(1, s["target_units"] // 6), "full": s["target_units"]}[st]
    return post_event({"type": "slot_state", "slot": slot, "product_hint": s["product_hint"], "state": st,
                       "estimated_units": units})


ui = WebUI()
ui.on_message("calibrate", on_calibrate)
ui.on_message("save_config", on_save_config)
ui.on_message("simulate", on_simulate)
ui.on_message("delivery_check", lambda _sid, _d: start_delivery_check())
ui.on_message("get_state", lambda sid, _d: ui.send_message("sensors", sensors(), sid))
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


def camera_worker():
    camera = None
    last_frame_at = 0.0
    while True:
        try:
            remote = config.get("camera_source") == "websocket"
            if camera is None:
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
                if time.time() - last_frame_at > 2.0:
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
    if img is not None and state["camera_ok"]:
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
threading.Thread(target=camera_worker, daemon=True).start()
threading.Thread(target=preview_worker, daemon=True).start()
App.run(user_loop=loop)
