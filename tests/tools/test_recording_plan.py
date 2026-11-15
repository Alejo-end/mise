"""The guided recording plan for the active four-product set: labels and counts per product, never per place;
labels come from the person's marks and typed counts, on the recording clock. Counts are units, never packs."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import recording_plan as rp  # noqa: E402

STRAW, MINT, BONNE, AITO = "aimo:profeel-strawberry", "aimo:profeel-mint-chocolate", "aimo:bonne-orange-juice", "aimo:aito-barista-oat"
CARAMEL = "aimo:profeel-salted-caramel"
COUNTS = {STRAW: 2, MINT: 2, BONNE: 6, AITO: 1}


def test_the_plan_is_per_product_for_the_four_active_products():
    assert rp.SET_ID == "kitchen-20261115-b" and rp.KEYS == [STRAW, MINT, BONNE, AITO]
    text = repr(rp.PLAN)
    for gone in ("bread", "yoghurt", "477032", "464316", "exp:chips", "exp:milk", "energy-drink", "position"):
        assert gone not in text, gone  # no fixed places: products are named, not located
    assert "any arrangement" in text and rp.REMOVED == CARAMEL and CARAMEL not in rp.KEYS
    evals = {n for n, p in rp.PLAN.items() if p["role"] == "evaluation"}
    assert {"eval-rearranged-day", "eval-flavour-hidden-day", "eval-removed-flavour-day", "eval-carton-removal-day"} <= evals
    assert all(rp.PLAN[n]["expected_requests"] == {k: 0 for k in rp.KEYS} for n in evals)


def test_rearranging_changes_no_identity_or_count():
    p = rp.plan_for("eval-rearranged-day-a")
    w, end = rp.label_windows(rp.start_content(p, COUNTS), p["steps"], [5.0, 10.0], 20.0)
    for k in rp.KEYS:
        assert [x["slots"][k] for x in w if k in x["slots"]] == [k, "obstructed", k]
        assert end[k]["count"] == COUNTS[k] and end[k]["identity"] == k
    assert all("no change" in v for v in p["expected_stock_events"].values())


def test_unfamiliar_and_removed_items_never_enter_the_reference_gallery():
    for name, p in rp.PLAN.items():
        if p["role"] == "reference":
            assert all(op[0] != "put" for st in p["steps"] for op in st["ops"]), name


def test_evaluation_needs_an_occasion():
    with pytest.raises(SystemExit):
        rp.plan_for("eval-identity-day")
    assert rp.plan_for("eval-identity-day-a")["occasion"] == "a"
    assert rp.plan_for("steady-02") is None  # free recordings keep the old behaviour


def test_bonne_counts_cartons_from_the_person_not_packs_or_targets():
    p = rp.plan_for("eval-carton-removal-day-a")
    w, end = rp.label_windows(rp.start_content(p, COUNTS), p["steps"], [5.4], 20.0)
    b = [x for x in w if BONNE in x["slots"]]
    assert [(x["from_t"], x["to_t"], x["slots"][BONNE], x["counts"][BONNE]) for x in b] == [(0.0, 4.9, BONNE, 6), (6.9, 20.0, BONNE, 5)]
    assert end[AITO]["count"] == 1 and "1 carton" in p["expected_stock_events"][BONNE]


def test_a_shake_with_its_flavour_turned_away_keeps_its_true_label():
    p = rp.plan_for("eval-flavour-hidden-day-a")
    w, _ = rp.label_windows(rp.start_content(p, COUNTS), p["steps"], [5.0, 12.0], 20.0)
    views = [(x["slots"][STRAW], x["orientation"][STRAW]) for x in w if STRAW in x["slots"]]
    assert views == [(STRAW, "front"), (STRAW, "flavour-hidden"), (STRAW, "front")]  # ground truth, not a guess
    assert "flavour uncertain" in p["expected_stock_events"][STRAW]


def test_the_removed_flavour_and_the_unfamiliar_item_have_their_own_labels():
    p = rp.plan_for("eval-removed-flavour-day-a")
    w, end = rp.label_windows(rp.start_content(p, COUNTS), p["steps"], [5.0], 20.0)
    c = [x for x in w if CARAMEL in x["slots"]]
    assert [x["slots"][CARAMEL] for x in c] == ["empty", "not-inventory"] and end[STRAW]["count"] == 2  # nothing else changes
    p = rp.plan_for("eval-unfamiliar-day-a")
    w, _ = rp.label_windows(rp.start_content(p, COUNTS), p["steps"], [5.0], 20.0)
    assert [x for x in w if rp.UNFAMILIAR in x["slots"]][-1]["slots"][rp.UNFAMILIAR] == "unknown"
    assert rp.UNFAMILIAR not in rp.start_content(rp.plan_for("eval-identity-day-a"), COUNTS)  # only where it is used


def test_orientation_and_barcode_visibility_are_labelled_per_view():
    p = rp.plan_for("ref-profeel-strawberry-poses-day")
    w, _ = rp.label_windows(rp.start_content(p, COUNTS), p["steps"], [5.0, 10.0, 15.0, 20.0], 25.0)
    views = [(x["orientation"][STRAW], x["barcode_visible"][STRAW]) for x in w if STRAW in x["slots"]]
    assert views == [("front", False), ("side", False), ("barcode-side", True), ("angled", False), ("flavour-hidden", False)]
    assert len(rp.plan_for("ref-bonne-orange-juice-poses-day")["steps"]) == 3  # no flavour text to hide on the juice
    p = rp.plan_for("eval-barcode-hidden-day-a")
    w, _ = rp.label_windows(rp.start_content(p, COUNTS), p["steps"], [5.0, 10.0], 20.0)
    assert all(x["barcode_visible"][k] is False for x in w for k in x["slots"])


def test_obstruction_windows_do_not_overlap_per_product():
    p = rp.plan_for("eval-hand-day-a")
    marks = [st["at"] + 0.1 for st in p["steps"]]
    w, _ = rp.label_windows(rp.start_content(p, COUNTS), p["steps"], marks, float(p["seconds"]))
    for k in rp.KEYS:
        ws = [x for x in w if k in x["slots"]]
        assert [x["slots"][k] for x in ws] == [k, "obstructed", k]
        assert all(a["to_t"] <= b["from_t"] for a, b in zip(ws, ws[1:]))
