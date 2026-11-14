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


def test_one_empty_shelf_makes_one_order(tmp_path):
    # Pinned to the immediate refill policy: restock_min_s=0 and no stocked hold (restock_confirm_s=0), so one
    # "full" reading makes the slot orderable again at once. The committed default waits instead
    # (backend/settings.py). Which one the kitchen wants is decision C in docs/PARALLEL_WORK.md.
    agent = A.Agent(LocalCatalog(), load_rules(), AuditLog(tmp_path / "a.sqlite"), restock_min_s=0, restock_confirm_s=0)
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


def test_nothing_safe_in_stock_goes_to_the_head_chef_and_orders_nothing(agent):
    """The kitchen still needs the product, so a person must hear of it. The benchmark found that nobody was told."""
    agent.catalog.products["MOCK-BREAD-RYE-500"]["stock"] = 0
    r = agent.handle_event({"type": "slot_state", "slot": 1, "state": "empty", "estimated_units": 0})
    assert r["status"] == "needs_approval"
    (request,) = agent.open_approvals()
    assert request["options"] == [] and "nothing safe can be ordered" in request["what"]
    assert agent.decide(request["id"], "approve", 0, "Head chef") == {"status": "bad_option"}  # there is nothing to approve
    assert agent.decide(request["id"], "skip", None, "Head chef") == {"status": "skipped"}
    assert not agent.orders and 1 not in agent.pending_slots


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


def test_a_date_in_the_request_is_not_the_quantity(agent):
    agent.handle_request("lactose-free milk by 17.11.")
    asked = next(e for e in agent.log.entries() if e["step"] == "trigger")
    assert asked["detail"]["quantity"] == 1  # not 17
    assert A.units_for(A.DEADLINE.sub(" ", "500 ml of lactose-free milk by tomorrow 12:00"), "lactose-free milk 1 L") == 1


@pytest.mark.parametrize("text,slot", [
    ("24 lactose free milk", 3), ("24 lactose-free milk", 3), ("some MILK, lactose-free, please", 3),
    ("twenty natural yogurt", 2), ("20 almond drink", None), ("milk", None),
])
def test_requests_match_targets_by_words(text, slot):
    t = A.match_target(text, load_rules()["targets"])
    assert (t["slot"] if t else None) == slot


def test_a_text_request_is_not_tied_to_a_shelf_slot(agent):
    assert agent.handle_request("6 kg natural yogurt")["status"] == "ordered"
    order = next(iter(agent.orders.values()))
    assert order["slot"] is None and 2 not in agent.pending_slots and 2 not in agent.last_order_qty
    # The camera can still trigger its own order for the slot.
    assert agent.handle_event({"type": "slot_state", "slot": 2, "state": "empty", "estimated_units": 0})["status"] == "ordered"


def test_a_past_date_means_next_year():
    assert f"{A.parse_deadline('milk by 2.11.', FRI):%Y-%m-%d}" == "2027-11-02"


def test_less_stock_at_the_recheck_asks_again_with_the_fresh_lines(agent):
    """The chef approved 10 usual + 14 brand B. Then only 4 of brand B are left."""
    agent.auto_limit_eur = 40
    agent.handle_event({"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0})
    aid = agent.open_approvals()[0]["id"]
    agent.catalog.products["MOCK-MILK-LF-1L-B"]["stock"] = 4
    r = agent.decide(aid, "approve", 0, "Head chef")
    assert r["status"] == "needs_approval" and not agent.orders
    (again,) = agent.open_approvals()
    assert [(l["product_id"], l["quantity"]) for l in again["options"][0]["lines"]] == [
        ("MOCK-MILK-LF-1L", 10), ("MOCK-MILK-LF-1L-B", 4)]
    assert agent.decide(again["id"], "approve", 0, "Head chef")["status"] == "ordered"
    assert sum(l["quantity"] for l in next(iter(agent.orders.values()))["lines"]) == 14


def test_a_cheaper_price_at_the_recheck_is_the_price_that_is_logged(agent):
    agent.catalog.products["MOCK-YOG-NAT-1KG"]["price_eur"] = 2.95
    ev = {"type": "slot_state", "slot": 2, "state": "empty", "estimated_units": 0}
    real = agent.catalog.availability

    def cheaper_second_time(product_id, quantity, needed_by, calls=[]):
        calls.append(product_id)
        if calls.count(product_id) == 2:
            agent.catalog.products[product_id]["price_eur"] = 2.50
        return real(product_id, quantity, needed_by)

    agent.catalog.availability = cheaper_second_time
    assert agent.handle_event(ev)["status"] == "ordered"
    assert next(iter(agent.orders.values()))["total_eur"] == 30.0  # 12 x 2.50, not 12 x 2.95


def test_other_pack_sizes_and_missing_prices_are_left_out(agent):
    products = agent.catalog.products
    products["MOCK-MILK-LF-1L"]["stock"] = 0
    products["MOCK-MILK-LF-1L-B"]["pack"] = {"amount": 5.0, "unit": "l"}  # a crate of five cartons
    products["MOCK-MILK-LF-1L-C"]["pack"] = {"amount": 1.0, "unit": "l"}
    agent.auto_limit_eur = 40
    agent.handle_event({"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0})
    ids = {l["product_id"] for o in agent.open_approvals()[0]["options"] for l in o["lines"]}
    assert "MOCK-MILK-LF-1L-B" not in ids and "MOCK-MILK-LF-1L-C" in ids
    left_out = next(e for e in agent.log.entries() if e["step"] == "candidates")["detail"]["left_out"]
    assert left_out["other_pack_size"] == ["MOCK-MILK-LF-1L-B"]


def test_unknown_stock_goes_to_a_person(agent):
    """A product that ships from the supplier has no known stock. The agent must not order it alone."""
    real = agent.catalog.availability

    def supplier_ships_it(product_id, quantity, needed_by):
        claim = real(product_id, quantity, needed_by)
        claim.value.update(available_units=quantity, quantity_known=False)
        return claim

    agent.catalog.availability = supplier_ships_it
    r = agent.handle_event({"type": "slot_state", "slot": 2, "state": "empty", "estimated_units": 0})
    assert r["status"] == "needs_approval"
    assert any("does not know the stock" in x for x in agent.open_approvals()[0]["options"][0]["reasons"])


def test_the_ranking_and_the_rule_check_agree_on_approved_product_codes(agent):
    """A product code written as a number in the rule file is approved by both."""
    agent.rules["approved_substitute_groups"]["rye_bread"] = [123]
    twin = dict(agent.catalog.products["MOCK-BREAD-RYE-500"], id="123", name="Rye bread 500 g, twin",
                substitute_group="other", stock=100)
    agent.catalog.products["123"] = twin
    agent.catalog.products["MOCK-BREAD-RYE-500"]["stock"] = 5
    agent.auto_limit_eur = 10
    agent.handle_event({"type": "slot_state", "slot": 1, "state": "empty", "estimated_units": 0})
    best = agent.open_approvals()[0]["options"][0]
    assert best["label"] == "best" and best["lines"][1]["product_id"] == "123"
    assert not any("outside the approved groups" in r for r in best["reasons"])


def test_a_label_check_is_asked_once_per_product(agent):
    """The chef approves a product with no allergen data once. The same product then orders alone."""
    agent.rules["unknown_allergens"] = "needs_approval"
    first = agent.order_exact("MOCK-MILK-LF-1L-X", 10, "run-1")
    assert first["status"] == "needs_approval"
    ordered = agent.decide(agent.open_approvals()[0]["id"], "approve", 0, "Head chef")
    assert ordered["status"] == "ordered"
    assert [e["detail"]["product_id"] for e in agent.log.entries() if e["step"] == "label_check"] == ["MOCK-MILK-LF-1L-X"]

    again = agent.quote_exact("MOCK-MILK-LF-1L-X", 10)
    assert again["decision"].result == "allowed"
    assert any("Head chef checked the label" in r for r in again["decision"].reasons)

    from backend.agent import Agent
    restarted = Agent(agent.catalog, agent.rules, agent.log)  # the memory comes back from the audit log
    assert restarted.quote_exact("MOCK-MILK-LF-1L-X", 10)["decision"].result == "allowed"


def test_a_label_check_ends_when_the_data_or_the_rule_set_changes(agent):
    from backend.agent import Agent
    agent.rules["unknown_allergens"] = "needs_approval"
    agent.order_exact("MOCK-MILK-LF-1L-X", 10, "run-1")
    agent.decide(agent.open_approvals()[0]["id"], "approve", 0, "Head chef")

    newer = Agent(agent.catalog, agent.rules | {"version": agent.rules["version"] + 1}, agent.log)
    assert newer.quote_exact("MOCK-MILK-LF-1L-X", 10)["decision"].result == "needs_approval"

    agent.catalog.products["MOCK-MILK-LF-1L-X"]["name"] = "Lactose-free milk drink 1 L, new recipe"
    assert agent.quote_exact("MOCK-MILK-LF-1L-X", 10)["decision"].result == "needs_approval"


def test_a_label_check_never_opens_a_forbidden_product(agent):
    from backend import rules as R
    nuts = agent.catalog.products["MOCK-ALMOND-1L"]
    ctx = R.Context("plant_drink", R.needed_by(agent.rules, __import__("datetime").datetime.now(R.TZ)),
                    label_checks={R.label_key(nuts): "Head chef checked the label"})
    assert R.check_line(R.Line(nuts, 1, 1.0, ctx.needed_by), agent.rules, ctx).result == "forbidden"
