"""Record what the board's camera sees, for repeatable replay tests.

Saves, into tests/device/sequences/<name>/:
  frames/000001.jpg ...  the preview frames the board page receives (480 x 360 JPEG)
  meta.jsonl             one line per frame and per board reading (time, file, slot states)
  calibration/           the board's slot photos (slotN_empty.npy, slotN_full.npy)
  config.json            the board's config at recording time

Only the integration session runs this: it needs the live board.

  cd valio-challenge
  uv run --with "python-socketio[client]" python tools/record_sequence.py NAME --seconds 15 --note "hand over slot 2 at 5 s"

Guided shelf recordings (names from tools/recording_plan.py, i.e. experiments/recognition/MISSING_DATA.md):

  uv run --with "python-socketio[client]" python tools/record_sequence.py eval-removal-s3-day-a

shows the steps, checks that the boxes are valid and calibrated, starts when you press Enter, and records a
mark each time you press Enter as you do a step (typed text + Enter = a free note). Marks use the recording
clock, the same clock as the frame times in meta.jsonl. At the end you confirm the marks, and labels.json is
written for the evaluation harness. Saved outside git, reference and evaluation apart:
  data/recordings/reference/<name>/   data/recordings/evaluation/<name>/
"""

from __future__ import annotations

import argparse
import base64
import json
import queue
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import socketio

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recording_plan as rp  # noqa: E402

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
    p.add_argument("--lookalike", action="append", default=[], help="look-alike product code (guided look-alike plans)")
    p.add_argument("--session", default="", help="recording session id (default: role, occasion and date)")
    p.add_argument("--labeller", default="person at the shelf (Enter marks, confirmed after capture)")
    a = p.parse_args()

    plan = rp.plan_for(a.name, a.lookalike)
    if plan:
        a.seconds = plan["seconds"] if "--seconds" not in sys.argv else a.seconds
        check_board_ready(a.board)
        show_plan(a.name, plan, a.seconds)
        input("Press Enter to START recording (then Enter at each step) ")
        out = ROOT / "data" / "recordings" / plan["role"] / a.name
    else:
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
            if meta.closed:
                return
            n["frames"] += 1
            name = f"{n['frames']:06d}.jpg"
            (out / "frames" / name).write_bytes(base64.b64decode(m["image"]))
            meta.write(json.dumps({"kind": "frame", "t": round(time.time() - t0, 3), "file": name,
                                   "board_time": m.get("time")}) + "\n")

    @sio.on("sensors")
    def on_sensors(s):
        with lock:
            if meta.closed:
                return
            n["readings"] += 1
            meta.write(json.dumps({"kind": "reading", "t": round(time.time() - t0, 3), "slots": s.get("slots"),
                                   "camera_ok": s.get("camera_ok"), "buzzer": s.get("buzzer")}) + "\n")

    marks: list[tuple[float, str]] = []
    lines: queue.Queue = queue.Queue()  # (absolute time, text) of every line typed, read by one thread only
    if plan:
        def read_lines():
            for line in sys.stdin:
                lines.put((time.time(), line.strip()))
        threading.Thread(target=read_lines, daemon=True).start()
    sio.connect(f"http://{a.board}:7000", transports=["websocket", "polling"])
    if plan:
        while (el := time.time() - t0) < a.seconds:
            while not lines.empty():
                at, text = lines.get()
                t = round(at - t0, 3)
                with lock:
                    marks.append((t, text))
                    meta.write(json.dumps({"kind": "mark", "t": t, "text": text}) + "\n")
            done = len([m for m in marks if not m[1]])
            nxt = plan["steps"][done] if done < len(plan["steps"]) else None
            cue = f"next at {nxt['at']} s: {nxt['do']}  [Enter as you do it]" if nxt else "no more steps; hold still"
            print(f"\r  {el:5.1f} / {a.seconds:.0f} s | {cue}" + " " * 10, end="", flush=True)
            time.sleep(0.05)
        print()
    else:
        time.sleep(a.seconds)
    sio.disconnect()
    with lock:
        meta.close()
    if plan:
        write_labels(out, a, plan, marks, lambda prompt: (print(prompt, end='', flush=True), lines.get()[1])[1])

    (out / "README.md").write_text(
        f"# Sequence {a.name}\n\nRecorded {time.strftime('%Y-%m-%d %H:%M')} from the board at {a.board}, "
        f"{a.seconds:.0f} s, {n['frames']} frames, {n['readings']} board readings.\n\n"
        f"What happens: {a.note or 'not described'}\n\nBoard files copied: {', '.join(pulled)}\n")
    print(f"saved {n['frames']} frames and {n['readings']} readings to {out}")


def check_board_ready(board: str) -> None:
    """Refuse to record while the boxes overlap, are being edited, or wait for new calibration photos."""
    st = json.load(urllib.request.urlopen(f"http://{board}:7000/state", timeout=10))
    problems = list(st.get("layout_errors") or [])
    if st.get("setup"):
        problems.append("box editing is on: press Save boxes or Cancel on the board page")
    if st.get("recalibrate"):
        problems.append(f"spots {', '.join(sorted(st['recalibrate']))} moved: save the empty and the full photo")
    if problems:
        raise SystemExit("Not recording: " + "; ".join(problems))


def show_plan(name: str, plan: dict, seconds: float) -> None:
    print(f"\n{name}: {plan['role'].upper()} capture, {seconds:.0f} s, lighting {plan['lighting']}")
    print(f"  before you start: {plan['setup']}")
    for st in plan["steps"]:
        print(f"  at about {st['at']:>2} s: {st['do']}")
    if not plan["steps"]:
        print("  no actions: keep hands out of view")
    print("  Press Enter exactly when you do each step. Type a word + Enter for anything unplanned.\n")


def write_labels(out: Path, a, plan: dict, marks: list[tuple[float, str]], ask) -> None:
    """labels.json for experiments/recognition (recog/real_eval.discover): human-validated after confirmation."""
    step_marks = [t for t, text in marks if not text]
    notes = [{"t": t, "text": text} for t, text in marks if text]
    steps = [{**st, "marked_t": (step_marks[i] if i < len(step_marks) else None)} for i, st in enumerate(plan["steps"])]
    print("\nMarks (recording clock):")
    for st in steps:
        print(f"  {st['marked_t'] if st['marked_t'] is not None else 'MISSING':>7} s  {st['do']}")
    for n_ in notes:
        print(f"  {n_['t']:>7} s  note: {n_['text']}")
    problems = []
    if len(step_marks) < len(plan["steps"]):
        problems.append(f"{len(plan['steps']) - len(step_marks)} step(s) not marked")
    if len(step_marks) > len(plan["steps"]):
        problems.append(f"{len(step_marks) - len(plan['steps'])} extra Enter mark(s)")
    ok = not problems and ask("Are these marks right (each at the moment you acted)? [y/N] ").strip().lower() == "y"
    date = time.strftime("%Y%m%d")
    session = a.session or (f"ref-{date}" if plan["role"] == "reference" else f"eval-{plan['occasion']}-{date}")
    labels = rp.label_windows(plan["start"], plan["steps"], step_marks, a.seconds) if not problems else []
    doc = {
        "schema": "shelf-recording-labels/1", "recording": a.name, "plan": plan["base"], "role": plan["role"],
        "scenario": plan.get("scenario", "reference"), "session_id": session, "occasion": plan["occasion"],
        "lighting": plan["lighting"], "assigned": rp.ASSIGNED, "start": plan["start"], "steps": steps,
        "notes": notes, "labels": labels, "seconds": a.seconds,
        "clock": "seconds since the recorder connected; frame and mark times in meta.jsonl use the same clock",
        "transition_unlabelled_s": [rp.TRANSITION_BEFORE, rp.TRANSITION_AFTER],
        "validated": ok, "validated_by": a.labeller if ok else "", "validation_source": "human" if ok else "",
        "validated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z") if ok else None,
        "problems": problems,
    }
    if plan["role"] == "evaluation":
        doc["expected_requests"] = plan["expected_requests"]
    (out / "labels.json").write_text(json.dumps(doc, indent=2))
    print(f"labels.json: {'VALIDATED' if ok else 'NOT validated (' + ('; '.join(problems) or 'not confirmed') + '): record again with a new name or fix by hand'}")


if __name__ == "__main__":
    main()
