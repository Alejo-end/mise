"""Check the running backend before the demo. It reads state and runs the Driving Test. It places no order.

  uv run python tools/demo_preflight.py [http://localhost:8001]

Each line is PASS, WARN or FAIL. FAIL means that a step of DEMO_SCRIPT.md cannot work. WARN means that the
step works but the audience will see something to explain. The exit code is 1 if any line is FAIL.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.buyer_agent import _setting  # noqa: E402

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8001").rstrip("/")
worst = 0


def line(level: str, text: str):
    global worst
    worst = max(worst, {"PASS": 0, "WARN": 1, "FAIL": 2}[level])
    print(f"{level}  {text}")


def get(path: str):
    return httpx.get(BASE + path, timeout=20).json()


try:
    dev = get("/device")
except Exception as e:
    print(f"FAIL  The backend does not answer at {BASE}: {e}")
    sys.exit(1)

live = "stand-in" not in dev["catalog"]
line("PASS" if live else "WARN", f"Catalog: {dev['catalog']}." + ("" if live else " The demo then shows no real Valio products."))
line("PASS", f"Rule set {dev['rule_set_version']}, signed by {dev['signed_by']}.")
line("PASS" if dev["pins_set"] else "FAIL", "An approver PIN is set." if dev["pins_set"] else "No approver PIN. Nobody can approve or grant a licence.")
line("PASS" if _setting("VERDA_INFERENCE_API_KEY") else "WARN",
     "The Verda key is set, so the buying agent uses the model." if _setting("VERDA_INFERENCE_API_KEY")
     else "No Verda key. The buying agent runs on fixed rules, and a picture of a pack cannot be read.")

test = httpx.post(BASE + "/sandbox/test", json={"agent": "safe-kitchen"}, timeout=120).json()
results = test.get("results", [])
passed = sum(1 for s in results if s.get("passed"))
line("PASS" if test.get("passed") and results else "FAIL", f"Driving Test: {passed} of {len(results)} scenarios pass.")

hints = get("/hints")
verdicts = {c["verdict"] for c in hints["codes"]}
line("PASS" if {"allowed", "forbidden"} <= verdicts else "FAIL", f"Example products: {len(hints['codes'])}, with verdicts {sorted(verdicts)}.")
forbidden = next((c for c in hints["codes"] if c["verdict"] == "forbidden"), None)
if forbidden:
    answer = get(f"/scan/{forbidden['gtin']}")["matches"][0]
    line("PASS" if answer["verdict"] == "forbidden" else "FAIL", f"The forbidden example ({forbidden['name'][:40]}) is refused: {answer['reasons'][0][-70:]}")

line("PASS" if dev["online"] else "WARN", f"The box is online, {dev['temperature_c']} °C, limit of the knob €{dev['auto_limit_eur']}." if dev["online"]
     else "The box is offline. The page shows no temperature and the screen cannot judge a delivery.")
camera = (dev.get("health") or {}).get("camera_ok")
line("PASS" if camera else "WARN", "The box's camera gives a picture." if camera else "The box's camera gives no picture. Do not show the shelf.")

screen = get("/box/screen")
if screen["delivery"]:
    more = screen["delivery"]["more"]
    line("WARN" if more > 2 else "PASS", f"The touch screen shows order {screen['delivery']['order_id']}, with {more} more behind it."
         + (" Old test orders will appear one after another. Start with a fresh AUDIT_DB." if more > 2 else ""))
else:
    line("PASS", "The touch screen shows nothing at the door. A new order will appear on it.")

waiting = get("/approvals/open")
line("WARN" if waiting else "PASS", f"{len(waiting)} request(s) already wait for the head chef. Decide them before the demo." if waiting
     else "No request waits for the head chef.")

print({0: "Ready.", 1: "Ready, with points to explain.", 2: "Not ready."}[worst])
sys.exit(1 if worst == 2 else 0)
