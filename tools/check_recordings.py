"""Check guided shelf recordings before upload, and upload the valid ones.

  uv run python tools/check_recordings.py                 # check every clip under data/recordings/
  uv run python tools/check_recordings.py --upload        # also upload the valid ones to Modal

The checks mirror what the cloud evaluation accepts (experiments/recognition/recog/real_eval.discover on branch
exp/recognition): human-validated labels with a role and a session, non-overlapping windows per slot, expected
requests on evaluation clips, and every frame named in meta.jsonl present. Failed takes (folders renamed to
*.failed-*) are skipped. Upload: Modal Volume valio-recognition, /inputs/recordings/<name>/ (laptop-dependent:
needs the authenticated Modal CLI). A clip already on the Volume is not uploaded again.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODAL = str(Path.home() / ".local/bin/modal")


def problems_of(d: Path) -> list[str]:
    p = []
    try:
        lab = json.loads((d / "labels.json").read_text())
    except (OSError, ValueError) as e:
        return [f"labels.json unreadable: {e}"]
    if lab.get("validated") is not True or not str(lab.get("validated_by", "")).strip():
        p.append("not validated by the person (validated=true and validated_by required)")
    if lab.get("validation_source") != "human":
        p.append("validation_source is not 'human'")
    if lab.get("role") not in ("reference", "evaluation") or not lab.get("session_id"):
        p.append("role and session_id required")
    if lab.get("role") != d.parent.name:
        p.append(f"saved under {d.parent.name}/ but role is {lab.get('role')}")
    if not lab.get("labels") or not lab.get("assigned"):
        p.append("labels and assigned required")
    if lab.get("role") == "evaluation" and "expected_requests" not in lab:
        p.append("expected_requests missing")
    seen: dict[str, list[tuple[float, float]]] = {}
    for w in lab.get("labels") or []:
        if not w.get("from_t", 0) < w.get("to_t", 0):
            p.append(f"empty window {w}")
        for s in w.get("slots", {}):
            if any(w["from_t"] < b and a < w["to_t"] for a, b in seen.get(s, [])):
                p.append(f"overlapping windows for slot {s}")
            seen.setdefault(s, []).append((w["from_t"], w["to_t"]))
    try:
        frames = [json.loads(x) for x in (d / "meta.jsonl").read_text().splitlines() if x.strip()]
        frames = [f for f in frames if f.get("kind") == "frame"]
        if not frames:
            p.append("no frames")
        missing = [f["file"] for f in frames if not (d / "frames" / f["file"]).is_file()]
        if missing:
            p.append(f"{len(missing)} frame files missing")
    except OSError as e:
        p.append(f"meta.jsonl unreadable: {e}")
    if not (d / "config.json").is_file():
        p.append("config.json missing (board files not copied)")
    if lab.get("unfamiliar", {}).get("identity") and lab.get("role") == "reference":
        ident = lab["unfamiliar"]["identity"]
        if any(ident in (w.get("identities") or {}).values() for w in lab.get("labels") or []):
            p.append("the unfamiliar item appears in a reference clip")
    return p


def on_volume(name: str) -> bool:
    r = subprocess.run([MODAL, "volume", "ls", "valio-recognition", f"/inputs/recordings/{name}"],
                       capture_output=True, text=True)
    return r.returncode == 0 and bool(r.stdout.strip())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT / "data" / "recordings"))
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--volume-prefix", default="/inputs/recordings", help="tests use a scratch prefix")
    a = ap.parse_args()
    clips = sorted(d for d in Path(a.root).glob("*/*") if d.is_dir() and ".failed-" not in d.name)
    if not clips:
        print(f"No clips under {a.root}")
        return
    ok = []
    for d in clips:
        p = problems_of(d)
        print(f"{'OK ' if not p else 'BAD'} {d.parent.name}/{d.name}" + ("" if not p else ": " + "; ".join(p)))
        if not p:
            ok.append(d)
    print(f"{len(ok)} of {len(clips)} clips valid")
    if a.upload:
        for d in ok:
            dest = f"{a.volume_prefix}/{d.name}"
            if a.volume_prefix == "/inputs/recordings" and on_volume(d.name):
                print(f"skip {d.name}: already on the Volume")
                continue
            r = subprocess.run([MODAL, "volume", "put", "valio-recognition", str(d), dest], capture_output=True, text=True)
            print(f"{'uploaded' if r.returncode == 0 else 'FAILED'} {d.name} -> valio-recognition:{dest}"
                  + ("" if r.returncode == 0 else f": {r.stderr.strip()[:200]}"))
    sys.exit(0 if len(ok) == len(clips) else 1)


if __name__ == "__main__":
    main()
