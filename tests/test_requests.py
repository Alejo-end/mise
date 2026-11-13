"""Request parsing and the buying pipeline on the stand-in catalog."""

from datetime import datetime

import pytest

from backend import agent as A
from backend.audit import AuditLog
from backend.rules import TZ, load_rules
from backend.valio import LocalCatalog

FRI = datetime(2026, 11, 13, 21, 0, tzinfo=TZ)


@pytest.mark.parametrize("text,want", [
    ("1,000 litres of lactose-free milk by Monday", "2026-11-16 07:00"),
    ("milk by tomorrow 12:00", "2026-11-14 12:00"),
    ("milk by Friday", "2026-11-20 07:00"),
    ("milk by 17.11.", "2026-11-17 07:00"),
    ("milk by 3.1. at 9.30", "2027-01-03 09:30"),
])
def test_deadline(text, want):
    assert f"{A.parse_deadline(text, FRI):%Y-%m-%d %H:%M}" == want


def test_no_deadline():
    assert A.parse_deadline("some milk please", FRI) is None


@pytest.mark.parametrize("text,product,want", [
    ("1,000 litres of lactose-free milk", "lactose-free milk 1 L", 1000),
    ("500 ml of lactose-free milk", "lactose-free milk 1 L", 1),
    ("6 kg natural yogurt", "natural yogurt 1 kg", 6),
    ("3 kg rye bread", "rye bread 500 g", 6),
    ("twenty natural yogurt", "natural yogurt 1 kg", 20),
    ("12 natural yogurt", "natural yogurt 1 kg", 12),
])
def test_units(text, product, want):
    assert A.units_for(text, product) == want


@pytest.fixture
def agent(tmp_path):
    return A.Agent(LocalCatalog(), load_rules(), AuditLog(tmp_path / "a.sqlite"))


def test_partial_stock_is_split_with_a_safe_substitute(agent):
    agent.auto_limit_eur = 40
    r = agent.handle_event({"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0})
    assert r["status"] == "needs_approval"
    best = agent.open_approvals()[0]["options"][0]
    assert [l["quantity"] for l in best["lines"]] == [10, 14]  # 10 usual + 14 substitute
    assert best["lines"][0]["usual"] and not best["lines"][1]["usual"]


def test_forbidden_products_never_appear_as_options(agent):
    agent.auto_limit_eur = 40
    agent.handle_event({"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0})
    ids = {l["product_id"] for o in agent.open_approvals()[0]["options"] for l in o["lines"]}
    assert not ids & {"MOCK-ALMOND-1L", "MOCK-MILK-REG-1L", "MOCK-MILK-LF-1L-X"}


def test_approval_places_order_and_logs_it(agent):
    agent.auto_limit_eur = 40
    agent.handle_event({"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0})
    aid = agent.open_approvals()[0]["id"]
    r = agent.decide(aid, "approve", 0, "Head chef")
    assert r["status"] == "ordered"
    steps = [e["step"] for e in agent.log.entries()]
    for s in ("trigger", "search", "candidates", "rule_check", "approval", "recheck", "order"):
        assert s in steps
    assert agent.log.entries()[-1]["actor"] == "Head chef"


def test_one_empty_shelf_makes_one_order(agent):
    ev = {"type": "slot_state", "slot": 2, "state": "empty", "estimated_units": 0}
    assert agent.handle_event(ev)["status"] == "ordered"
    assert agent.handle_event(ev)["status"] == "ignored"
    agent.handle_event({"type": "slot_state", "slot": 2, "state": "full", "estimated_units": 12})
    assert agent.handle_event(ev)["status"] == "ordered"


def test_forced_nut_product_is_blocked(agent):
    r = agent.handle_request("20 almond drink", "MOCK-ALMOND-1L")
    assert r["status"] == "forbidden" and "nuts" in r["reasons"][0]
    assert not agent.open_approvals()


def test_large_request_uses_deadline_and_needs_approval(agent):
    r = agent.handle_request("1,000 litres of lactose-free milk by Monday")
    assert r["status"] == "needs_approval"
    opts = agent.open_approvals()[0]["options"]
    assert all(sum(l["quantity"] for l in o["lines"]) == 1000 for o in opts)


def test_skip_closes_the_request(agent):
    agent.auto_limit_eur = 40
    agent.handle_event({"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0})
    aid = agent.open_approvals()[0]["id"]
    assert agent.decide(aid, "skip", None, "Head chef")["status"] == "skipped"
    assert not agent.open_approvals()


def test_price_change_before_order_asks_again(agent):
    agent.auto_limit_eur = 40
    agent.handle_event({"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0})
    aid = agent.open_approvals()[0]["id"]
    agent.catalog.products["MOCK-MILK-LF-1L-B"]["price_eur"] = 2.99
    r = agent.decide(aid, "approve", 0, "Head chef")
    assert r["status"] == "needs_approval"
    assert any(e["step"] == "recheck" and e["detail"]["changes"] for e in agent.log.entries())


def test_audit_log_refuses_edits(agent):
    import sqlite3
    agent.log.write("trigger", {"summary": "x"}, "test")
    with pytest.raises(sqlite3.DatabaseError):
        agent.log.db.execute("UPDATE audit SET actor = 'someone else'")
    with pytest.raises(sqlite3.DatabaseError):
        agent.log.db.execute("DELETE FROM audit")


def test_short_stock_offers_the_safe_part_for_approval(agent):
    """5 safe loaves exist and the only substitute has sesame: offer the 5, name the shortfall."""
    agent.catalog.products["MOCK-BREAD-RYE-500"]["stock"] = 5
    r = agent.handle_event({"type": "slot_state", "slot": 1, "state": "empty", "estimated_units": 0})
    assert r["status"] == "needs_approval"
    (opt,) = agent.open_approvals()[0]["options"]
    assert opt["label"] == "partial" and opt["result"] == "needs_approval"
    assert [(l["product_id"], l["quantity"]) for l in opt["lines"]] == [("MOCK-BREAD-RYE-500", 5)]
    assert "25 short" in opt["reasons"][0]


def test_nothing_safe_in_stock_is_blocked(agent):
    agent.catalog.products["MOCK-BREAD-RYE-500"]["stock"] = 0
    r = agent.handle_event({"type": "slot_state", "slot": 1, "state": "empty", "estimated_units": 0})
    assert r["status"] == "blocked"


def test_ranking_uses_the_rule_sets_approved_groups(agent):
    """A substitute the rule set does not approve is never ranked as the closest match."""
    other = dict(agent.catalog.products["MOCK-BREAD-RYE-500"], id="MOCK-BREAD-RYE-B", name="Rye bread 500 g, brand B",
                 price_eur=2.29, stock=100, keywords=["rye bread", "bread"])
    agent.catalog.products["MOCK-BREAD-RYE-B"] = other
    agent.catalog.products["MOCK-BREAD-RYE-500"]["stock"] = 5
    agent.handle_event({"type": "slot_state", "slot": 1, "state": "empty", "estimated_units": 0})
    opts = agent.open_approvals()[0]["options"]
    assert all(o["result"] == "needs_approval" for o in opts)
    assert any("outside the approved groups" in r for o in opts for r in o["reasons"])


def test_approved_substitute_is_ranked_best(agent):
    agent.auto_limit_eur = 40
    agent.handle_event({"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0})
    best = agent.open_approvals()[0]["options"][0]
    assert best["label"] == "best" and best["lines"][1]["product_id"] == "MOCK-MILK-LF-1L-B"
