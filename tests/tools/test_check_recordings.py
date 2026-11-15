"""The pre-upload check accepts what the cloud evaluation accepts and rejects the rest."""

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import check_recordings as cr  # noqa: E402
import recording_plan as rp  # noqa: E402

SEQ = Path(__file__).resolve().parents[1] / "device" / "sequences" / "steady-01"


def make_clip(root: Path, name: str, validated=True, role=None) -> Path:
    plan = rp.plan_for(name)
    d = root / (role or plan["role"]) / name
    (d / "frames").mkdir(parents=True)
    shutil.copy(SEQ / "config.json", d / "config.json")
    meta = []
    for i, f in enumerate(sorted((SEQ / "frames").iterdir())[:4]):
        shutil.copy(f, d / "frames" / f.name)
        meta.append({"kind": "frame", "t": float(i), "file": f.name})
    (d / "meta.jsonl").write_text("\n".join(json.dumps(m) for m in meta))
    start = rp.start_content(plan, {s: 2 for s in rp.SLOTS})
    windows, _ = rp.label_windows(start, plan["steps"], [5.0 * (i + 1) for i in range(len(plan["steps"]))], plan["seconds"])
    doc = {"role": plan["role"], "session_id": "test", "labels": windows, "assigned": rp.IDENTITY,
           "validated": validated, "validated_by": "person" if validated else "", "validation_source": "human" if validated else "",
           "unfamiliar": {"identity": rp.UNFAMILIAR}}
    if plan["role"] == "evaluation":
        doc["expected_requests"] = plan["expected_requests"]
    (d / "labels.json").write_text(json.dumps(doc))
    return d


def test_valid_clips_pass_and_invalid_ones_say_why(tmp_path):
    assert cr.problems_of(make_clip(tmp_path, "ref-full-day")) == []
    assert cr.problems_of(make_clip(tmp_path, "eval-unfamiliar-day-a")) == []
    bad = make_clip(tmp_path, "eval-hand-day-a", validated=False)
    assert any("not validated" in p for p in cr.problems_of(bad))
    wrong = make_clip(tmp_path, "ref-empty-day", role="evaluation")
    assert any("role" in p for p in cr.problems_of(wrong))
    (make_clip(tmp_path, "ref-profeel-strawberry-poses-day") / "frames" / "000001.jpg").unlink()
    assert any("missing" in p for p in cr.problems_of(tmp_path / "reference" / "ref-profeel-strawberry-poses-day"))
