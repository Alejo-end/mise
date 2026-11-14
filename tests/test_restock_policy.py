"""The restock policy: when may a slot with an order or a request order again?

Owned by worktree 1 (backend/agent.py handle_event and the pending-slot bookkeeping, backend/settings.py).
Contract: docs/PARALLEL_WORK.md, interfaces 3, 7 and 8, decision C. The business meaning is in
docs/handoff/w1-reliability.md.

Policy (committed default): after an order, the slot stays pending until (a) a delivery check for the
order arrives, or (b) the slot reads stocked without a break for RESTOCK_CONFIRM_S (60 s) and at least
RESTOCK_MIN_S (120 s) after the order. The board's health (in the status event, every 10 s) shows that
the stocked state continues. One "full" reading never ends it.
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from backend import agent as A
from backend import audit as audit_module
from backend.audit import AuditLog
from backend.rules import TZ, load_rules
from backend.settings import DEFAULTS, Settings
from backend.valio import LocalCatalog

ROOT = Path(__file__).resolve().parent.parent
YOGURT_EMPTY = {"type": "slot_state", "slot": 2, "state": "empty", "estimated_units": 0, "auto_limit_eur": 40}
YOGURT_FULL = {"type": "slot_state", "slot": 2, "state": "full", "estimated_units": 12, "auto_limit_eur": 40}
MILK_EMPTY = {"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0, "auto_limit_eur": 40}
MILK_FULL = {"type": "slot_state", "slot": 3, "state": "full", "estimated_units": 24, "auto_limit_eur": 40}


class Clock:
    """The time the agent and the audit log see. Starts at the real time, moves only when told."""

    def __init__(self):
        self.t = datetime.now(TZ)

    def advance(self, seconds: float):
        self.t += timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()

    class Now(datetime):
        @classmethod
        def now(cls, tz=None):
            return c.t if tz is None else c.t.astimezone(tz)

    monkeypatch.setattr(A, "datetime", Now)
    monkeypatch.setattr(audit_module, "datetime", Now)
    return c


@pytest.fixture
def db(tmp_path):
    return tmp_path / "a.sqlite"


def make(db, **kw):
    return A.Agent(LocalCatalog(), load_rules(), AuditLog(db), **kw)


def orders(agent, slot):
    return [o for o in agent.orders.values() if o["slot"] == slot]


def requests_for(agent, slot):
    return orders(agent, slot) + [a for a in agent.open_approvals() if a["slot"] == slot]


def health(states: dict[int, str], uncertain=None, camera_ok=True, calibration="ok", config_valid=True) -> dict:
    return {"type": "status", "auto_limit_eur": 40, "health": {
        "camera_ok": camera_ok, "frame_age_s": 0.4, "config_valid": config_valid, "config_errors": [],
        "slots": {str(n): {"state": st, "uncertain": uncertain, "fill": {"full": 1.0, "low": 0.4}.get(st, 0.0),
                           "calibration": calibration, "assignment_id": None} for n, st in states.items()}}}


def report(agent, clock, seconds: float, ev: dict, every: float = 10):
    """The board's status every 10 s for this many seconds, starting one step from now."""
    for _ in range(int(seconds // every)):
        clock.advance(every)
        agent.handle_event(ev)


def entries(agent, step):
    return [e for e in agent.log.entries(10000) if e["step"] == step]


# One "full" reading is never a refill ---------------------------------------------------------

def test_the_live_duplicate_makes_one_order(clock, db):
    """14 Nov: 04:20:53 ORD-68CE0E08, 04:20:56 one "full" reading, 04:21:19 ORD-592FF708 (the same 12 x yogurt)."""
    agent = make(db)
    assert agent.handle_event(dict(YOGURT_EMPTY))["status"] == "ordered"
    clock.advance(3)
    assert agent.handle_event(dict(YOGURT_FULL))["status"] == "ignored"
    clock.advance(23)
    assert agent.handle_event(dict(YOGURT_EMPTY))["status"] == "ignored"
    assert len(orders(agent, 2)) == 1


def test_a_false_full_inside_the_window_keeps_the_order(clock, db):
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    clock.advance(30)
    r = agent.handle_event(dict(YOGURT_FULL))
    assert r["status"] == "ignored" and "too soon" in r["reason"]
    report(agent, clock, 20, health({2: "empty"}))  # the hand is gone, the shelf is empty again
    clock.advance(200)
    agent.handle_event(dict(YOGURT_EMPTY))
    assert len(orders(agent, 2)) == 1 and agent.pending_for_slot(2)["status"] == "ordered"


def test_one_full_reading_outside_the_window_is_not_a_refill(clock, db):
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    clock.advance(600)  # long after RESTOCK_MIN_S
    r = agent.handle_event(dict(YOGURT_FULL))
    assert r["status"] == "ignored" and "must read stocked" in r["reason"]
    clock.advance(10)
    agent.handle_event(health({2: "empty"}))  # the next report: empty again
    clock.advance(5)
    assert agent.handle_event(dict(YOGURT_EMPTY))["status"] == "ignored"
    assert len(orders(agent, 2)) == 1


def test_a_full_reading_and_silence_is_not_a_refill(clock, db):
    """An old board sends no health: a single "full" event never ends the order, however long ago."""
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    clock.advance(3)
    agent.handle_event(dict(YOGURT_FULL))
    clock.advance(3600)
    assert agent.handle_event(dict(YOGURT_EMPTY))["status"] == "ignored"
    assert len(orders(agent, 2)) == 1


# A genuine refill -------------------------------------------------------------------------------

def test_a_genuine_early_refill_ends_the_order_after_the_window(clock, db):
    """Refilled 5 s after the order and full from then on: pending until 120 s after the order, then free."""
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    clock.advance(5)
    agent.handle_event(dict(YOGURT_FULL))
    report(agent, clock, 110, health({2: "full"}))  # t = 115 s: held 110 s, but under 120 s after the order
    assert agent.pending_for_slot(2) is not None
    report(agent, clock, 10, health({2: "full"}))  # t = 125 s
    assert agent.pending_for_slot(2) is None
    restock = entries(agent, "restock")[-1]["detail"]
    assert restock["how"] == "stocked" and restock["held_s"] == 120 and "not proof of a delivery" in restock["summary"]
    clock.advance(30)
    assert agent.handle_event(dict(YOGURT_EMPTY))["status"] == "ordered"  # a new shortage, a new order
    assert len(orders(agent, 2)) == 2


def test_a_late_refill_must_still_hold_for_the_confirm_time(clock, db):
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    clock.advance(300)
    agent.handle_event(dict(YOGURT_FULL))
    report(agent, clock, 50, health({2: "full"}))
    assert agent.pending_for_slot(2) is not None  # held 50 s
    report(agent, clock, 10, health({2: "full"}))
    assert agent.pending_for_slot(2) is None  # held 60 s


def test_low_above_the_reorder_level_counts_as_stocked(clock, db):
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    clock.advance(200)
    agent.handle_event({"type": "slot_state", "slot": 2, "state": "low", "estimated_units": 6, "auto_limit_eur": 40})
    report(agent, clock, 60, health({2: "low"}))  # fill 0.4 x 12 = 5 units, at least reorder_below 4
    assert agent.pending_for_slot(2) is None


def test_the_board_health_alone_can_start_the_hold(clock, db):
    """A "full" event lost (backend down, or sent before a restart): the health still shows the refill."""
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    clock.advance(100)
    report(agent, clock, 80, health({2: "full"}))
    assert agent.pending_for_slot(2) is None


def test_a_delivery_check_ends_the_order_at_once(clock, db):
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    clock.advance(5)
    r = agent.handle_event({"type": "delivery_check", "counts": {"2": 12}, "temperature_c": 4.0})
    assert r["order_id"] == orders(agent, 2)[0]["order_id"]
    assert agent.pending_for_slot(2) is None
    assert entries(agent, "restock")[-1]["detail"]["how"] == "delivery_check"
    assert agent.handle_event(dict(YOGURT_EMPTY))["status"] == "ordered"


def test_a_delivery_check_through_the_http_path_is_seen_too(clock, db):
    """POST /deliveries/{order_id}/check calls check_delivery directly, not handle_event."""
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    agent.check_delivery(orders(agent, 2)[0]["order_id"], {"2": 12}, 4.0)
    assert agent.pending_for_slot(2) is None
    assert agent.handle_event(dict(YOGURT_EMPTY))["status"] == "ordered"


# What breaks a hold ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    health({2: "full"}, uncertain="too dark or covered"),
    health({2: "full"}, uncertain="calibrating"),
    health({2: "full"}, calibration="stale"),
    health({2: "full"}, camera_ok=False),
    health({2: "full"}, config_valid=False),
    health({2: "empty"}),
    health({2: "low"}) | {"health": {"slots": {"2": {"state": "low", "fill": 0.1}}}},  # 1 unit, below reorder level
])
def test_an_unsure_or_short_report_starts_the_hold_again(clock, db, bad):
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    clock.advance(200)
    agent.handle_event(dict(YOGURT_FULL))
    report(agent, clock, 50, health({2: "full"}))
    clock.advance(10)
    agent.handle_event(bad)
    report(agent, clock, 60, health({2: "full"}))  # the hold starts again 10 s after the break: held 50 s
    assert agent.pending_for_slot(2) is not None
    report(agent, clock, 10, health({2: "full"}))
    assert agent.pending_for_slot(2) is None


@pytest.mark.parametrize("age", [45.0, None])
def test_reports_from_a_stalled_camera_never_complete_the_hold(clock, db, age):
    """Codex review: a hung capture() keeps camera_ok true while the board repeats its last reading."""
    stalled = health({2: "full"})
    if age is None:
        del stalled["health"]["frame_age_s"]
    else:
        stalled["health"]["frame_age_s"] = age
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    clock.advance(200)
    agent.handle_event(dict(YOGURT_FULL))
    report(agent, clock, 300, stalled)
    assert agent.pending_for_slot(2)["status"] == "ordered"


def test_a_gap_in_the_reports_starts_the_hold_again(clock, db):
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    clock.advance(200)
    agent.handle_event(dict(YOGURT_FULL))
    clock.advance(DEFAULTS.health_stale_s + 1)  # the box was silent: no evidence that it stayed full
    agent.handle_event(health({2: "full"}))
    report(agent, clock, 50, health({2: "full"}))
    assert agent.pending_for_slot(2) is not None
    report(agent, clock, 10, health({2: "full"}))
    assert agent.pending_for_slot(2) is None


def test_a_report_without_the_slot_changes_nothing(clock, db):
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    clock.advance(200)
    agent.handle_event(dict(YOGURT_FULL))
    report(agent, clock, 60, health({1: "full"}))  # slot 2 missing from the report
    assert agent.pending_for_slot(2) is not None


# Open approvals and outstanding orders ---------------------------------------------------------

def test_a_full_reading_never_closes_an_open_request(clock, db):
    agent = make(db)
    assert agent.handle_event(dict(MILK_EMPTY))["status"] == "needs_approval"  # partial stock: the chef decides
    clock.advance(200)
    agent.handle_event(dict(MILK_FULL))
    report(agent, clock, 120, health({3: "full"}))
    assert agent.pending_for_slot(3)["status"] == "approval" and len(agent.open_approvals()) == 1
    assert agent.handle_event(dict(MILK_EMPTY))["status"] == "ignored"
    assert len(requests_for(agent, 3)) == 1


def test_the_hold_starts_only_when_the_order_is_placed(clock, db):
    """Stocked readings while the chef decides do not count towards the hold after the approval."""
    agent = make(db)
    agent.handle_event(dict(MILK_EMPTY))
    report(agent, clock, 300, health({3: "full"}))
    aid = agent.open_approvals()[0]["id"]
    assert agent.decide(aid, "approve", 0, "Head chef")["status"] == "ordered"
    report(agent, clock, 110, health({3: "full"}))  # held 110 s, but under 120 s after the order
    assert agent.pending_for_slot(3)["status"] == "ordered"
    report(agent, clock, 10, health({3: "full"}))
    assert agent.pending_for_slot(3) is None


def test_an_expired_request_frees_the_slot_for_the_next_reading(clock, db):
    agent = make(db)
    agent.handle_event(dict(MILK_EMPTY))
    clock.advance(2 * 3600 + 1)
    assert agent.handle_event(dict(MILK_EMPTY))["status"] == "needs_approval"
    assert len(agent.open_approvals()) == 1


def test_skipping_the_request_frees_the_slot(clock, db):
    agent = make(db)
    agent.handle_event(dict(MILK_EMPTY))
    agent.decide(agent.open_approvals()[0]["id"], "skip", None, "Head chef")
    assert agent.pending_for_slot(3) is None
    assert agent.handle_event(dict(MILK_EMPTY))["status"] == "needs_approval"


def test_other_slots_are_independent(clock, db):
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    agent.handle_event(dict(MILK_EMPTY))
    clock.advance(200)
    agent.handle_event(dict(YOGURT_FULL))
    report(agent, clock, 60, health({2: "full", 3: "full"}))
    assert agent.pending_for_slot(2) is None and agent.pending_for_slot(3)["status"] == "approval"


def test_health_never_orders(clock, db):
    agent = make(db)
    report(agent, clock, 120, health({1: "empty", 2: "empty", 3: "empty"}))
    assert agent.orders == {} and agent.open_approvals() == []


def test_requests_added_by_another_thread_do_not_break_a_reading(db):
    """Codex review: _ask adds approvals under self.lock while a reading checks them under trigger_lock."""
    import sys
    import threading

    agent = make(db)
    # Slot 2 pending while its pipeline runs: the check reads every approval to find none of its run.
    agent.pending_slots.add(2)
    agent.pending[2] = {"run_id": "running", "product_id": None, "rule_set_version": 3, "since": ""}
    for i in range(3000):  # a long dict, so another thread can change it during one check
        agent.approvals[f"old{i}"] = {"id": f"old{i}", "run_id": f"old{i}", "status": "closed", "slot": None}
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    stop, errors = threading.Event(), []

    def add():
        i = 0
        while not stop.is_set():
            i += 1
            agent.approvals[f"x{i}"] = {"id": f"x{i}", "run_id": f"r{i}", "status": "closed", "slot": None}
            agent.approvals.pop(f"x{i - 1}", None)

    t = threading.Thread(target=add)
    t.start()
    try:
        for _ in range(300):
            try:
                assert agent._pending_status(2) is None
            except RuntimeError as e:
                errors.append(e)
    finally:
        stop.set()
        t.join()
        sys.setswitchinterval(interval)
    assert errors == []


# Repeated observations ---------------------------------------------------------------------------

def test_repeated_readings_while_waiting_log_once_and_order_once(clock, db):
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    for _ in range(5):
        clock.advance(2)
        agent.handle_event(dict(YOGURT_FULL))
        agent.handle_event(health({2: "full"}))
    for _ in range(5):
        clock.advance(2)
        agent.handle_event(dict(YOGURT_EMPTY))
    waits = [e for e in entries(agent, "device") if "One reading is not a refill" in e["detail"]["summary"]]
    assert len(waits) == 1 and len(orders(agent, 2)) == 1


# The pending record of interface 8 -----------------------------------------------------------------

def test_the_pending_record_names_the_order(clock, db):
    agent = make(db)
    assert agent.pending_for_slot(2) is None
    agent.handle_event(dict(YOGURT_EMPTY))
    rec = agent.pending_for_slot(2)
    order = orders(agent, 2)[0]
    assert rec == {"slot": 2, "product_id": "MOCK-YOG-NAT-1KG", "rule_set_version": 3, "run_id": order["run_id"],
                   "since": rec["since"], "status": "ordered"}
    assert datetime.fromisoformat(rec["since"]) <= datetime.fromisoformat(order["time"])


def test_the_pending_record_keeps_its_rule_set_after_a_new_version(clock, db):
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    rules = load_rules()
    newer = A.Agent(LocalCatalog(), rules | {"version": rules["version"] + 1}, AuditLog(db))
    assert newer.pending_for_slot(2)["rule_set_version"] == rules["version"]


# A backend restart in each state ----------------------------------------------------------------

def test_restart_with_an_order_waiting(clock, db):
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    before = agent.pending_for_slot(2)
    clock.advance(30)
    again = make(db)
    assert again.pending_for_slot(2) == before
    assert again.handle_event(dict(YOGURT_FULL))["status"] == "ignored"
    assert again.handle_event(dict(YOGURT_EMPTY))["status"] == "ignored"
    assert len(orders(again, 2)) == 1


def test_restart_during_a_hold_starts_the_hold_again(clock, db):
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    clock.advance(200)
    agent.handle_event(dict(YOGURT_FULL))
    report(agent, clock, 50, health({2: "full"}))
    again = make(db)
    report(again, clock, 10, health({2: "full"}))  # 60 s since the first reading, but the hold was in memory
    assert again.pending_for_slot(2) is not None
    report(again, clock, 60, health({2: "full"}))
    assert again.pending_for_slot(2) is None


def test_restart_with_an_open_request(clock, db):
    agent = make(db)
    agent.handle_event(dict(MILK_EMPTY))
    again = make(db)
    assert again.pending_for_slot(3)["status"] == "approval"
    assert again.handle_event(dict(MILK_EMPTY))["status"] == "ignored"
    assert len(requests_for(again, 3)) == 1


def test_restart_after_a_restock(clock, db):
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    clock.advance(200)
    agent.handle_event(dict(YOGURT_FULL))
    report(agent, clock, 60, health({2: "full"}))
    assert agent.pending_for_slot(2) is None
    again = make(db)
    assert again.pending_for_slot(2) is None and 2 not in again.pending_slots
    assert again.handle_event(dict(YOGURT_EMPTY))["status"] == "ordered"


def test_restart_after_a_delivery_check(clock, db):
    agent = make(db)
    agent.handle_event(dict(YOGURT_EMPTY))
    agent.check_delivery(orders(agent, 2)[0]["order_id"], {"2": 12}, 4.0)  # not yet seen by the pending check
    again = make(db)
    assert again.pending_for_slot(2) is None
    assert again.handle_event(dict(YOGURT_EMPTY))["status"] == "ordered"


def test_restart_after_a_skipped_request_on_a_slot_with_an_old_order(clock, db):
    """The restore before 14 Nov kept the slot pending because some order for the slot existed, ever."""
    agent = make(db, restock_min_s=0, restock_confirm_s=0)
    agent.handle_event(dict(MILK_EMPTY))
    agent.decide(agent.open_approvals()[0]["id"], "approve", 0, "Head chef")
    agent.handle_event(dict(MILK_FULL))  # restocked at once under the immediate policy
    agent.handle_event(dict(MILK_EMPTY))
    agent.decide(agent.open_approvals()[0]["id"], "skip", None, "Head chef")
    again = make(db)
    assert 3 not in again.pending_slots and again.pending_for_slot(3) is None


def test_restart_after_a_failed_pipeline(clock, db):
    class Broken(LocalCatalog):
        def search(self, q):
            raise ConnectionError("catalog down")

    agent = A.Agent(Broken(), load_rules(), AuditLog(db))
    assert agent.handle_event(dict(YOGURT_EMPTY))["status"] == "error"
    again = make(db)
    assert 2 not in again.pending_slots


def copy_log(src, dst, skip_step: str):
    """dst holds src's entries without those of one step, as if they were older than the entries restore reads."""
    out = AuditLog(dst)
    for e in AuditLog(src).entries(100000):
        if e["step"] != skip_step:
            out.write(e["step"], e["detail"], e["actor"], e["rule_set_version"], e["order_id"], e["run_id"])


def test_restart_when_the_trigger_of_an_order_is_beyond_the_entries_read(clock, db, tmp_path):
    """Codex review: restore reads the newest 100,000 entries; the order alone must keep its slot pending."""
    make(db).handle_event(dict(YOGURT_EMPTY))
    copy_log(db, tmp_path / "b.sqlite", "trigger")
    again = make(tmp_path / "b.sqlite")
    assert again.pending_for_slot(2)["status"] == "ordered"
    assert again.handle_event(dict(YOGURT_EMPTY))["status"] == "ignored"


def test_restart_when_the_trigger_of_a_request_is_beyond_the_entries_read(clock, db, tmp_path):
    make(db).handle_event(dict(MILK_EMPTY))
    copy_log(db, tmp_path / "b.sqlite", "trigger")
    again = make(tmp_path / "b.sqlite")
    assert again.pending_for_slot(3)["status"] == "approval"
    assert again.handle_event(dict(MILK_EMPTY))["status"] == "ignored"


def test_restart_restores_a_log_written_before_this_change(clock, db):
    """Trigger and restock entries from before 14 Nov carry no product_id, and a restock no run_id."""
    log = AuditLog(db)
    log.write("trigger", {"summary": "Slot 2 is empty by camera.", "event": dict(YOGURT_EMPTY)}, "device", 3, run_id="old1")
    log.write("order", {"summary": "12 x yogurt", "lines": [{"product_id": "MOCK-YOG-NAT-1KG", "quantity": 12}],
                        "total_eur": 30.0, "sha256": "x", "slot": 2}, "agent", 3, order_id="ORD-OLD", run_id="old1")
    rec = make(db).pending_for_slot(2)
    assert rec["product_id"] == "MOCK-YOG-NAT-1KG" and rec["run_id"] == "old1" and rec["status"] == "ordered"
    log.write("restock", {"slot": 2, "summary": "Slot 2 is stocked again."}, "device", 3)
    assert make(db).pending_for_slot(2) is None


# Configuration: the code default is the live configuration -------------------------------------

def test_the_committed_defaults():
    assert DEFAULTS == Settings(restock_min_s=120.0, restock_confirm_s=60.0, offline_after_s=30.0, health_stale_s=30.0,
                                frame_stale_s=10.0)


def test_an_agent_without_settings_ignores_the_environment(monkeypatch, db):
    monkeypatch.setenv("RESTOCK_MIN_S", "0")
    monkeypatch.setenv("RESTOCK_CONFIRM_S", "0")
    assert make(db).settings == DEFAULTS


def test_settings_from_the_environment():
    assert Settings.from_env({}) == DEFAULTS
    assert Settings.from_env({"RESTOCK_MIN_S": "", "OFFLINE_AFTER_S": " "}) == DEFAULTS
    s = Settings.from_env({"RESTOCK_MIN_S": "0", "RESTOCK_CONFIRM_S": "0"})
    assert (s.restock_min_s, s.restock_confirm_s) == (0, 0) and s.overrides() == {"RESTOCK_MIN_S": 0, "RESTOCK_CONFIRM_S": 0}
    assert Settings.from_env({"RESTOCK_MIN_S": "120"}).overrides() == {}
    for bad in ("-1", "two minutes", "nan", "inf"):
        with pytest.raises(ValueError, match="RESTOCK_MIN_S"):
            Settings.from_env({"RESTOCK_MIN_S": bad})


def test_explicit_values_override_single_settings(db):
    agent = make(db, restock_min_s=0, settings=Settings(restock_confirm_s=5))
    assert (agent.settings.restock_min_s, agent.settings.restock_confirm_s) == (0, 5)


def env_example() -> dict[str, str]:
    lines = (ROOT / ".env.example").read_text().splitlines()
    return dict(l.split("=", 1) for l in lines if "=" in l and not l.lstrip().startswith("#"))


def test_env_example_lists_every_setting_with_its_default():
    example = env_example()
    for key, value in Settings.from_env({}).__dict__.items():
        assert float(example[key.upper()]) == value


def test_env_example_lists_every_key_the_backend_reads():
    read = set()
    for f in (ROOT / "backend").glob("*.py"):
        read |= set(re.findall(r"os\.(?:getenv|environ\.get)\(\s*\"([A-Z_]+)\"", f.read_text()))
    read |= {k.upper() for k in DEFAULTS.__dict__}
    assert read and read <= set(env_example())


def test_env_example_holds_no_secret():
    example = env_example()
    for key in ("APPROVER_PINS", "VALIO_API_KEY", "ANTHROPIC_API_KEY"):
        assert example[key] == ""
