"""The guided recording plan: labels come from the person's marks, on the recording clock."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import recording_plan as rp  # noqa: E402


def test_every_capture_of_missing_data_is_planned_and_roles_stay_apart():
    refs = {n for n, p in rp.PLAN.items() if p["role"] == "reference"}
    evals = {n for n, p in rp.PLAN.items() if p["role"] == "evaluation"}
    assert all(n.startswith("ref-") for n in refs) and all(n.startswith("eval-") for n in evals)
    assert len(refs) == 8 and len(evals) == 9
    assert all("expected_requests" in rp.PLAN[n] for n in evals)


def test_evaluation_needs_an_occasion_and_lookalikes_need_codes():
    with pytest.raises(SystemExit):
        rp.plan_for("eval-identity-day", [])
    assert rp.plan_for("eval-identity-day-a", [])["occasion"] == "a"
    with pytest.raises(SystemExit):
        rp.plan_for("eval-lookalike-s3-day-a", [])
    p = rp.plan_for("eval-lookalike-s3-day-b", ["464306"])
    assert p["steps"][0]["after"] == {"3": "464306"}
    p = rp.plan_for("ref-s3-lookalikes-day", ["464306", "464401", "464398"])
    assert p["seconds"] == 15 and p["start"]["3"] == "464306" and [s["at"] for s in p["steps"]] == [5, 10]
    assert rp.plan_for("steady-02", []) is None  # free recordings keep the old behaviour


def test_windows_follow_the_marks_and_leave_the_transition_unlabelled():
    p = rp.plan_for("eval-removal-s3-day-a", [])
    w = rp.label_windows(p["start"], p["steps"], [5.4], 20.0)
    s3 = [x for x in w if "3" in x["slots"]]
    assert s3 == [{"from_t": 0.0, "to_t": 4.9, "slots": {"3": "463335"}, "validated": True},
                  {"from_t": 6.9, "to_t": 20.0, "slots": {"3": "empty"}, "validated": True}]
    assert [x["slots"] for x in w if "1" in x["slots"]] == [{"1": "477032"}]  # untouched slot: one window


def test_obstruction_windows_do_not_overlap_per_slot():
    p = rp.plan_for("eval-hand-day-a", [])
    w = rp.label_windows(p["start"], p["steps"], [3.1, 6.0, 9.2, 12.1, 15.0, 18.2], 20.0)
    for slot in "123":
        ws = [x for x in w if slot in x["slots"]]
        assert [x["slots"][slot] for x in ws] == [rp.FULL[slot], "obstructed", rp.FULL[slot]]
        assert all(a["to_t"] <= b["from_t"] for a, b in zip(ws, ws[1:]))
