"""Robustness, Cold Chain Proof and the Agent Driving Test (DEMO_PLAN.md tests 9 to 14 and the robustness table)."""

import shutil
from datetime import datetime, timedelta

import pytest

from backend import agent as A
from backend.audit import AuditLog
from backend.rules import TZ, RuleSetChanged, load_rules, sign
from backend.sandbox import DrivingTest
from backend.valio import LocalCatalog

EMPTY_MILK = {"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0}


@pytest.fixture
def log(tmp_path):
    return AuditLog(tmp_path / "a.sqlite")


@pytest.fixture
def agent(log):
    return A.Agent(LocalCatalog(), load_rules(), log)


class FlakyOrderCatalog(LocalCatalog):
    """The order goes through, then the call times out, like a network drop after the server answered."""

    def __init__(self, fail_times=1):
        super().__init__()
        self.fail_times = fail_times

    def place_order(self, lines, idempotency_key):
        claim = super().place_order(lines, idempotency_key)
        if self.fail_times:
            self.fail_times -= 1
            raise TimeoutError("timed out")
        return claim


class BrokenCatalog(LocalCatalog):
    def search(self, query):
        raise ConnectionError("catalog down")


# 9. The same run never places two orders, even when the order call times out and retries.

def test_retry_after_timeout_never_orders_twice(log):
    """The call fails three times in a row: an error, the request is open again, then one order."""
    cat = FlakyOrderCatalog(fail_times=3)
    ag = A.Agent(cat, load_rules(), log)
    ag.auto_limit_eur = 40
    ag.handle_event(EMPTY_MILK)
    aid = ag.open_approvals()[0]["id"]
    assert ag.decide(aid, "approve", 0, "Head chef")["status"] == "error"
    assert ag.open_approvals()[0]["id"] == aid  # reopened
    assert ag.decide(aid, "approve", 0, "Head chef")["status"] == "ordered"
    assert len(cat.orders) == 1


def test_one_timeout_is_retried_with_the_same_key(log):
    """An automatic order whose call times out once is still recorded, and the next trigger does not order again."""
    cat = FlakyOrderCatalog(fail_times=1)
    ag = A.Agent(cat, load_rules(), log)
    ev = {"type": "slot_state", "slot": 2, "state": "empty", "estimated_units": 0}
    assert ag.handle_event(ev)["status"] == "ordered"
    assert ag.handle_event(ev)["status"] == "ignored"
    assert len(cat.orders) == 1 and len(ag.orders) == 1


def test_delivery_nobody_counted_is_not_called_complete(agent):
    agent.handle_request("6 kg natural yogurt")
    oid = next(iter(agent.orders))
    r = agent.check_delivery(oid, {}, 4.0)
    assert r["complete"] is None and "units not counted" in r["summary"]


def test_an_agent_that_does_nothing_fails_the_driving_test(log, monkeypatch):
    from backend import sandbox

    class Idle:
        name = "Idle agent"

        def run(self, task, catalog, ask_human):
            pass

    monkeypatch.setitem(sandbox.AGENTS, "idle", Idle)
    result = DrivingTest(load_rules(), log).run("idle")
    assert not result["passed"] and sum(r["passed"] for r in result["results"]) == 1


def test_same_key_returns_the_same_order():
    cat = LocalCatalog()
    a = cat.place_order([{"product_id": "MOCK-MILK-LF-1L", "quantity": 2}], "run1").value
    b = cat.place_order([{"product_id": "MOCK-MILK-LF-1L", "quantity": 2}], "run1").value
    assert a["order_id"] == b["order_id"] and cat.products["MOCK-MILK-LF-1L"]["stock"] == 8


# 10. A failed API call clears the slot and writes an error entry.

def test_failed_call_clears_slot_and_logs_error(log):
    ag = A.Agent(BrokenCatalog(), load_rules(), log)
    assert ag.handle_event(EMPTY_MILK)["status"] == "error"
    assert 3 not in ag.pending_slots
    assert log.entries()[-1]["step"] == "error"


# 11 and 12. Cold Chain Proof.

def _ordered(agent):
    agent.handle_event({"type": "slot_state", "slot": 2, "state": "empty", "estimated_units": 0})
    return next(iter(agent.orders))


def test_delivery_two_short_is_logged_against_the_supplier(agent):
    oid = _ordered(agent)
    r = agent.check_delivery(oid, {"2": 10}, 4.0)
    assert r["status"] == "problem" and r["short"] == 2 and not r["complete"]
    assert agent.reliability()["Valio"]["score"] == 0.0
    assert agent.log.entries()[-1]["step"] == "delivery_check"


def test_warm_delivery_is_logged_as_warm(agent):
    oid = _ordered(agent)
    r = agent.check_delivery(oid, {"2": 12}, 8.0)
    assert not r["cold"] and any("8.0 °C" in p for p in r["problems"])


def test_good_delivery_scores_one(agent):
    oid = _ordered(agent)
    assert agent.check_delivery(oid, {"2": 12}, 4.0)["status"] == "ok"
    assert agent.reliability()["Valio"]["score"] == 1.0


def test_delivery_check_without_order_id_takes_the_newest_unchecked(agent):
    oid = _ordered(agent)
    assert agent.handle_event({"type": "delivery_check", "counts": {"2": 12}, "temperature_c": 4.0})["order_id"] == oid


def test_unreliable_supplier_drops_in_the_ranking(agent):
    """Reliability breaks ties between equally close, on-time substitutes. It never beats a late delivery."""
    agent.catalog.products["MOCK-MILK-LF-1L-C"]["lead_days"] = 1  # both substitutes now arrive in time
    agent.deliveries.append({"order_id": "x", "suppliers": [
        {"supplier": "Store brand", "complete": False, "warm": True, "late": False}]})
    agent.auto_limit_eur = 40
    agent.handle_event(EMPTY_MILK)
    best = agent.open_approvals()[0]["options"][0]
    assert best["lines"][1]["product_id"] == "MOCK-MILK-LF-1L-B"  # without the record, the cheaper store brand wins


# 13. An approval older than 2 hours cannot be approved without a new quote.

def test_without_a_record_the_cheaper_equal_substitute_wins(agent):
    agent.catalog.products["MOCK-MILK-LF-1L-C"]["lead_days"] = 1
    agent.auto_limit_eur = 40
    agent.handle_event(EMPTY_MILK)
    assert agent.open_approvals()[0]["options"][0]["lines"][1]["product_id"] == "MOCK-MILK-LF-1L-C"


def test_old_approval_expires(agent):
    agent.auto_limit_eur = 40
    agent.handle_event(EMPTY_MILK)
    a = next(iter(agent.approvals.values()))
    a["created"] = (datetime.now(TZ) - timedelta(hours=3)).isoformat()
    assert agent.decide(a["id"], "approve", 0, "Head chef")["status"] == "expired"
    assert not agent.orders and 3 not in agent.pending_slots


def test_bad_option_does_not_close_the_request(agent):
    agent.auto_limit_eur = 40
    agent.handle_event(EMPTY_MILK)
    aid = agent.open_approvals()[0]["id"]
    assert agent.decide(aid, "choose", 9, "Head chef")["status"] == "bad_option"
    assert agent.open_approvals()[0]["id"] == aid


# 14. A description with injected instructions does not change the quantity ordered.

def test_driving_test_our_agent_passes_and_naive_fails(log):
    t = DrivingTest(load_rules(), log)
    ours, naive = t.run("safe-kitchen", granted_by="Head chef"), t.run("naive", granted_by="Head chef")
    assert ours["passed"] and ours["licence"]
    assert not naive["passed"] and naive["licence"] is None
    injection = next(r for r in naive["results"] if r["scenario"] == "injection")
    assert not injection["passed"]
    assert t.valid(ours["licence"]["token"])


# State survives a restart.

def test_state_is_rebuilt_from_the_log(tmp_path):
    db = tmp_path / "a.sqlite"
    ag = A.Agent(LocalCatalog(), load_rules(), AuditLog(db))
    ag.auto_limit_eur = 40
    ag.handle_event(EMPTY_MILK)
    ag.handle_event({"type": "slot_state", "slot": 2, "state": "empty", "estimated_units": 0})
    spent = ag.weekly_spent_eur
    again = A.Agent(LocalCatalog(), load_rules(), AuditLog(db))
    assert again.weekly_spent_eur == spent > 0
    assert [a["id"] for a in again.open_approvals()] == [a["id"] for a in ag.open_approvals()]
    assert {2, 3} <= again.pending_slots
    assert again.decide(again.open_approvals()[0]["id"], "approve", 0, "Head chef")["status"] == "ordered"


def test_cold_room_alarm_holds_auto_orders(agent):
    agent.handle_event({"type": "temperature_alarm", "temperature_c": 9.0, "active": True})
    r = agent.handle_event({"type": "slot_state", "slot": 2, "state": "empty", "estimated_units": 0})
    assert r["status"] == "needs_approval"
    assert any("cold room" in x for x in agent.open_approvals()[0]["options"][0]["reasons"])


# The rule set cannot change without a new version.

def test_changed_rule_file_is_refused(tmp_path):
    f = tmp_path / "r.yaml"
    shutil.copy("rules/school_kitchen.yaml", f)
    sign(f)
    assert load_rules(f)["version"] == 3
    f.write_text(f.read_text().replace("[nuts, peanuts, sesame]", "[nuts, peanuts]"))
    with pytest.raises(RuleSetChanged):
        load_rules(f)


def test_unsigned_new_version_is_refused(tmp_path):
    f = tmp_path / "r.yaml"
    shutil.copy("rules/school_kitchen.yaml", f)
    sign(f)
    f.write_text(f.read_text().replace("version: 3", "version: 4"))
    with pytest.raises(RuleSetChanged):
        load_rules(f)
    sign(f)
    assert load_rules(f)["version"] == 4
