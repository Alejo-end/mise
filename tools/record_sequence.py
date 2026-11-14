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


def pull_board_files(out: Path, serial: str, app: str = "safe-kitchen-test", board: str = "172.20.10.2",
                     via: str = "auto") -> list[str]:
    """Copy config.json and the calibration photos of the running app from the board: over adb when the board is
    on USB to this laptop, otherwise over SSH (asks for the board password unless a key or SSH_ASKPASS is set)."""
    cal = out / "calibration"
    cal.mkdir(parents=True, exist_ok=True)
    data = f"/home/arduino/ArduinoApps/{app}/data"
    use_adb = via == "adb" or (via == "auto" and serial in subprocess.run(
        [str(ADB), "devices"], capture_output=True, text=True).stdout)
    if use_adb:
        names = subprocess.run([str(ADB), "-s", serial, "shell", f"ls {data}"], capture_output=True, text=True,
                               check=True).stdout.split()
        fetch = lambda n: subprocess.run([str(ADB), "-s", serial, "exec-out", f"cat {data}/{n}"], capture_output=True,
                                         check=True).stdout
    else:
        ssh = ["ssh", "-o", "ConnectTimeout=15", f"arduino@{board}"]
        names = subprocess.run(ssh + [f"ls {data}"], capture_output=True, text=True, check=True).stdout.split()
        fetch = lambda n: subprocess.run(ssh + [f"cat {data}/{n}"], capture_output=True, check=True).stdout
    got = []
    for n in names:
        if n == "config.json" or n.endswith(".npy"):
            (out / "config.json" if n == "config.json" else cal / n).write_bytes(fetch(n))
            got.append(n)
    return got


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("name")
    p.add_argument("--seconds", type=float, default=15)
    p.add_argument("--board", default="172.20.10.2")
    p.add_argument("--serial", default="251305272", help="adb serial of the board")
    p.add_argument("--app", default="safe-kitchen-test", choices=["safe-kitchen-test", "safe-kitchen-agent"],
                   help="the running board app: safe-kitchen-test (laptop camera) or safe-kitchen-agent (USB camera on the board)")
    p.add_argument("--via", default="auto", choices=["auto", "adb", "ssh"], help="how to copy the board files")
    p.add_argument("--note", default="", help="what happens in the sequence, for the README")
    p.add_argument("--session", default="", help="recording session id (default: role, occasion and date)")
    p.add_argument("--labeller", default="person at the shelf (Enter marks, confirmed after capture)")
    a = p.parse_args()

    plan = rp.plan_for(a.name)
    counts = {}
    if plan:
        a.seconds = plan["seconds"] if "--seconds" not in sys.argv else a.seconds
        check_board_ready(a.board)
        show_plan(a.name, plan, a.seconds)
        counts = ask_counts(plan)
        input("Press Enter to START recording (then Enter at each step) ")
        out = ROOT / "data" / "recordings" / plan["role"] / a.name
    else:
        out = ROOT / "tests" / "device" / "sequences" / a.name
    if out.exists():
        raise SystemExit(f"{out} exists; pick a new name")
    (out / "frames").mkdir(parents=True)
    pulled = pull_board_files(out, a.serial, a.app, a.board, a.via)

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
        write_labels(out, a, plan, counts, marks, lambda prompt: (print(prompt, end='', flush=True), lines.get()[1])[1])

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
    uncal = sorted(k for k, v in ((st.get("health") or {}).get("slots") or {}).items()
                   if (v or {}).get("calibration") not in (None, "ok"))
    if uncal:
        problems.append(f"spots {', '.join(uncal)} have no valid calibration photos: save the empty and the full photo")
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


def ask_counts(plan: dict) -> dict[str, int]:
    """The number of items actually in each slot now, typed by the person (never the configured target)."""
    if plan["start"] == "empty":
        return {s: 0 for s in rp.SLOTS}
    names = ", ".join(f"slot {s} {rp.LABEL[s]}" for s in rp.SLOTS)
    while True:
        raw = input(f"How many items are in each slot right now ({names})? Type {len(rp.SLOTS)} numbers, e.g. 2 1 2: ")
        parts = raw.replace(",", " ").split()
        if len(parts) == len(rp.SLOTS) and all(x.isdigit() and int(x) >= 1 for x in parts):
            return dict(zip(rp.SLOTS, map(int, parts)))
        print("  Please type one whole number of at least 1 per slot.")


def write_labels(out: Path, a, plan: dict, counts: dict, marks: list[tuple[float, str]], ask) -> None:
    """labels.json for experiments/recognition (recog/real_eval.discover): human-validated after confirmation."""
    step_marks = [t for t, text in marks if not text]
    notes = [{"t": t, "text": text} for t, text in marks if text]
    steps = [{**st, "marked_t": (step_marks[i] if i < len(step_marks) else None)} for i, st in enumerate(plan["steps"])]
    print("\nMarks (recording clock):")
    for st in steps:
        print(f"  {st['marked_t'] if st['marked_t'] is not None else 'MISSING':>7} s  {st['do']}")
    for n_ in notes:
        print(f"  {n_['t']:>7} s  note: {n_['text']}")
    start = rp.start_content(plan, counts)
    final = start
    for st in plan["steps"]:
        final = rp.apply_ops(final, st["ops"])
    print("Items per slot at the start -> at the end: " + "; ".join(
        f"slot {s} {start[s]['count']} -> {final[s]['count']}" + (" (fitness bar)" if final[s]["identity"] == rp.UNFAMILIAR else "")
        for s in rp.SLOTS))
    problems = []
    if len(step_marks) < len(plan["steps"]):
        problems.append(f"{len(plan['steps']) - len(step_marks)} step(s) not marked")
    if len(step_marks) > len(plan["steps"]):
        problems.append(f"{len(step_marks) - len(plan['steps'])} extra Enter mark(s)")
    ok = not problems and ask("Are the marks right (each at the moment you acted) and the item counts true? [y/N] ").strip().lower() == "y"
    date = time.strftime("%Y%m%d")
    session = a.session or (f"ref-{date}" if plan["role"] == "reference" else f"eval-{plan['occasion']}-{date}")
    labels = rp.label_windows(start, plan["steps"], step_marks, a.seconds)[0] if not problems else []
    doc = {
        "schema": "shelf-recording-labels/1", "recording": a.name, "plan": plan["base"], "role": plan["role"],
        "scenario": plan.get("scenario", "reference"), "session_id": session, "occasion": plan["occasion"],
        "lighting": plan["lighting"], "product_set": rp.SET_ID, "assigned": rp.IDENTITY,
        "unfamiliar": {"identity": rp.UNFAMILIAR, "label_value": "unknown", "in_reference_gallery": False},
        "identity_note": "observed identities (tools/recording_products.json); purchasing eligibility is the signed rule set",
        "counts_start": {s: start[s]["count"] for s in rp.SLOTS}, "counts_end": {s: final[s]["count"] for s in rp.SLOTS},
        "counts_source": "typed by the person before the recording, then changed only by the marked steps",
        "steps": steps,
        "notes": notes, "labels": labels, "seconds": a.seconds,
        "clock": "seconds since the recorder connected; frame and mark times in meta.jsonl use the same clock",
        "transition_unlabelled_s": [rp.TRANSITION_BEFORE, rp.TRANSITION_AFTER],
        "validated": ok, "validated_by": a.labeller if ok else "", "validation_source": "human" if ok else "",
        "validated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z") if ok else None,
        "problems": problems,
    }
    if plan["role"] == "evaluation":
        doc["expected_requests"] = plan["expected_requests"]  # all 0: every slot is unmapped for purchasing
        doc["expected_stock_events"] = plan["expected_stock_events"]
    (out / "labels.json").write_text(json.dumps(doc, indent=2))
    print(f"labels.json: {'VALIDATED' if ok else 'NOT validated (' + ('; '.join(problems) or 'not confirmed') + '): record again with a new name or fix by hand'}")


if __name__ == "__main__":
    main()
