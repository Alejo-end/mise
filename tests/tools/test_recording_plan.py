"""The guided recording plan: labels come from the person's marks and typed counts, on the recording clock."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import recording_plan as rp  # noqa: E402

COUNTS = {"1": 2, "2": 1, "3": 2}


def test_the_plan_uses_the_product_set_and_no_bread_or_yoghurt():
    text = repr(rp.PLAN) + repr(rp.IDENTITY)
    assert "bread" not in text and "yoghurt" not in text and "477032" not in text and "464316" not in text
    assert rp.IDENTITY == {"1": "exp:chips", "2": "exp:energy-drink", "3": "exp:milk"}
    refs = {n for n, p in rp.PLAN.items() if p["role"] == "reference"}
    evals = {n for n, p in rp.PLAN.items() if p["role"] == "evaluation"}
    assert all(n.startswith("ref-") for n in refs) and all(n.startswith("eval-") for n in evals)
    assert all(p["expected_requests"] == {"1": 0, "2": 0, "3": 0} for n, p in rp.PLAN.items() if n in evals)


def test_the_unfamiliar_item_never_enters_the_reference_gallery():
    for name, p in rp.PLAN.items():
        if p["role"] == "reference":
            assert all(op[0] != "put" for st in p["steps"] for op in st["ops"]), name


def test_evaluation_needs_an_occasion():
    with pytest.raises(SystemExit):
        rp.plan_for("eval-identity-day")
    assert rp.plan_for("eval-identity-day-a")["occasion"] == "a"
    assert rp.plan_for("steady-02") is None  # free recordings keep the old behaviour


def test_counts_come_from_the_person_not_from_targets():
    p = rp.plan_for("eval-removal-one-s3-day-a")
    w, end = rp.label_windows(rp.start_content(p, COUNTS), p["steps"], [5.4], 20.0)
    s3 = [x for x in w if "3" in x["slots"]]
    assert [(x["from_t"], x["to_t"], x["slots"]["3"], x["counts"]["3"]) for x in s3] == \
           [(0.0, 4.9, "exp:milk", 2), (6.9, 20.0, "exp:milk", 1)]
    assert end["3"]["count"] == 1 and end["1"]["count"] == 2


def test_taking_all_makes_the_slot_empty_and_the_fitness_bar_is_unknown():
    p = rp.plan_for("eval-removal-all-s3-day-b")
    _, end = rp.label_windows(rp.start_content(p, COUNTS), p["steps"], [5.0], 20.0)
    assert rp.label_value(end["3"]) == "empty"
    p = rp.plan_for("eval-unfamiliar-s3-day-a")
    w, end = rp.label_windows(rp.start_content(p, COUNTS), p["steps"], [5.0], 20.0)
    last = [x for x in w if "3" in x["slots"]][-1]
    assert last["slots"]["3"] == "unknown" and last["identities"]["3"] == "exp:fitness-bar" and last["counts"]["3"] == 1


def test_orientation_and_barcode_visibility_are_labelled_per_view():
    p = rp.plan_for("ref-s1-poses-day")
    w, _ = rp.label_windows(rp.start_content(p, COUNTS), p["steps"], [5.0, 10.0, 15.0], 20.0)
    views = [(x["orientation"]["1"], x["barcode_visible"]["1"]) for x in w if "1" in x["slots"]]
    assert views == [("front", False), ("side", False), ("barcode-side", True), ("angled", False)]
    p = rp.plan_for("eval-barcode-hidden-day-a")
    w, _ = rp.label_windows(rp.start_content(p, COUNTS), p["steps"], [5.0, 10.0], 20.0)
    assert all(x["barcode_visible"][s] is False for x in w for s in x["slots"])


def test_obstruction_windows_do_not_overlap_per_slot():
    p = rp.plan_for("eval-hand-day-a")
    w, _ = rp.label_windows(rp.start_content(p, COUNTS), p["steps"], [3.1, 6.0, 9.2, 12.1, 15.0, 18.2], 20.0)
    for slot in "123":
        ws = [x for x in w if slot in x["slots"]]
        assert [x["slots"][slot] for x in ws] == [rp.IDENTITY[slot], "obstructed", rp.IDENTITY[slot]]
        assert all(a["to_t"] <= b["from_t"] for a, b in zip(ws, ws[1:]))
