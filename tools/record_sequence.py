"""Record what the board's camera sees, for repeatable replay tests.

Saves, into tests/device/sequences/<name>/:
  frames/000001.jpg ...  the preview frames the board page receives (480 x 360 JPEG)
  meta.jsonl             one line per frame and per board reading (time, file, slot states)
  calibration/           the board's slot photos (slotN_empty.npy, slotN_full.npy)
  config.json            the board's config at recording time

Only the integration session runs this: it needs the live board.

  cd valio-challenge
  uv run --with "python-socketio[client]" python tools/record_sequence.py NAME --seconds 15 --note "hand over slot 2 at 5 s"
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import threading
import time
from pathlib import Path

import socketio

ROOT = Path(__file__).resolve().parents[1]
ADB = Path.home() / "Library/Arduino15/packages/arduino/tools/adb/32.0.0/adb"
APP_DATA = "/home/arduino/ArduinoApps/safe-kitchen-test/data"


def pull_board_files(out: Path, serial: str) -> list[str]:
    """Copy config.json and the calibration photos from the board over adb."""
    cal = out / "calibration"
    cal.mkdir(parents=True, exist_ok=True)
    names = subprocess.run([str(ADB), "-s", serial, "shell", f"ls {APP_DATA}"], capture_output=True, text=True,
                           check=True).stdout.split()
    got = []
    for n in names:
        dest = out / "config.json" if n == "config.json" else cal / n
        if n == "config.json" or n.endswith(".npy"):
            data = subprocess.run([str(ADB), "-s", serial, "exec-out", f"cat {APP_DATA}/{n}"], capture_output=True,
                                  check=True).stdout
            dest.write_bytes(data)
            got.append(n)
    return got


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("name")
    p.add_argument("--seconds", type=float, default=15)
    p.add_argument("--board", default="172.20.10.2")
    p.add_argument("--serial", default="251305272", help="adb serial of the board")
    p.add_argument("--note", default="", help="what happens in the sequence, for the README")
    a = p.parse_args()

    out = ROOT / "tests" / "device" / "sequences" / a.name
    if out.exists():
        raise SystemExit(f"{out} exists; pick a new name")
    (out / "frames").mkdir(parents=True)
    pulled = pull_board_files(out, a.serial)

    meta = (out / "meta.jsonl").open("w")
    lock = threading.Lock()
    n = {"frames": 0, "readings": 0}
    t0 = time.time()

    sio = socketio.Client(reconnection=False)

    @sio.on("frame")
    def on_frame(m):
        with lock:
            n["frames"] += 1
            name = f"{n['frames']:06d}.jpg"
            (out / "frames" / name).write_bytes(base64.b64decode(m["image"]))
            meta.write(json.dumps({"kind": "frame", "t": round(time.time() - t0, 3), "file": name,
                                   "board_time": m.get("time")}) + "\n")

    @sio.on("sensors")
    def on_sensors(s):
        with lock:
            n["readings"] += 1
            meta.write(json.dumps({"kind": "reading", "t": round(time.time() - t0, 3), "slots": s.get("slots"),
                                   "camera_ok": s.get("camera_ok"), "buzzer": s.get("buzzer")}) + "\n")

    sio.connect(f"http://{a.board}:7000", transports=["websocket", "polling"])
    time.sleep(a.seconds)
    sio.disconnect()
    meta.close()

    (out / "README.md").write_text(
        f"# Sequence {a.name}\n\nRecorded {time.strftime('%Y-%m-%d %H:%M')} from the board at {a.board}, "
        f"{a.seconds:.0f} s, {n['frames']} frames, {n['readings']} board readings.\n\n"
        f"What happens: {a.note or 'not described'}\n\nBoard files copied: {', '.join(pulled)}\n")
    print(f"saved {n['frames']} frames and {n['readings']} readings to {out}")


if __name__ == "__main__":
    main()
