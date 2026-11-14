"""Repeated low-stock observations must not create duplicate orders.

Owned by worktree 1 (backend/agent.py handle_event). Contract: docs/PARALLEL_WORK.md, interface 3.
"""

import pytest

from backend import agent as A
from backend.audit import AuditLog
from backend.rules import load_rules
from backend.valio import LocalCatalog

YOGURT_EMPTY = {"type": "slot_state", "slot": 2, "state": "empty", "estimated_units": 0, "auto_limit_eur": 40}
YOGURT_FULL = {"type": "slot_state", "slot": 2, "state": "full", "estimated_units": 12, "auto_limit_eur": 40}


LIVE_WINDOW_S = 120  # RESTOCK_MIN_S of the live backend (decision C, docs/PARALLEL_WORK.md)


@pytest.fixture
def agent(tmp_path):
    return A.Agent(LocalCatalog(), load_rules(), AuditLog(tmp_path / "a.sqlite"), restock_min_s=LIVE_WINDOW_S)


def slot_requests(agent, slot):
    """Orders placed plus approval requests still open, for one slot."""
    return ([o for o in agent.orders.values() if o["slot"] == slot]
            + [a for a in agent.open_approvals() if a["slot"] == slot])


def test_repeated_low_events_create_one_order(agent):
    for _ in range(5):
        agent.handle_event(dict(YOGURT_EMPTY))
    assert len(slot_requests(agent, 2)) == 1


def test_state_resent_after_a_board_restart_is_ignored_while_an_order_is_pending(agent):
    agent.handle_event(dict(YOGURT_EMPTY))
    # The board forgets slot states when it restarts and sends its first readings again.
    agent.handle_event(dict(YOGURT_EMPTY))
    agent.handle_event({"type": "slot_state", "slot": 2, "state": "low", "estimated_units": 1, "auto_limit_eur": 40})
    assert len(slot_requests(agent, 2)) == 1


def test_the_pending_order_survives_a_backend_restart(agent, tmp_path):
    agent.handle_event(dict(YOGURT_EMPTY))
    restarted = A.Agent(LocalCatalog(), load_rules(), AuditLog(tmp_path / "a.sqlite"))
    restarted.handle_event(dict(YOGURT_EMPTY))
    assert len(slot_requests(restarted, 2)) == 1


def test_one_false_full_reading_between_lows_does_not_order_twice(agent):
    """Seen live on 14 Nov: 04:20:53 order ORD-68CE0E08 (12 x yogurt), 04:20:56 'Slot 2 is stocked
    again', 04:21:19 order ORD-592FF708, the same 12 x yogurt again."""
    agent.handle_event(dict(YOGURT_EMPTY))
    agent.handle_event(dict(YOGURT_FULL))  # 3 s later, a hand or the calibration, not a delivery
    agent.handle_event(dict(YOGURT_EMPTY))
    assert len(slot_requests(agent, 2)) == 1


def test_the_immediate_policy_reorders_at_once_as_in_test_requests(tmp_path):
    """Alejo's original spec (no window, no stocked hold), pinned in test_requests until decision C."""
    agent = A.Agent(LocalCatalog(), load_rules(), AuditLog(tmp_path / "a.sqlite"), restock_min_s=0, restock_confirm_s=0)
    agent.handle_event(dict(YOGURT_EMPTY))
    agent.handle_event(dict(YOGURT_FULL))  # counted as a refill at once
    agent.handle_event(dict(YOGURT_EMPTY))
    assert len([o for o in agent.orders.values() if o["slot"] == 2]) == 2


def test_the_code_default_keeps_the_live_duplicate_to_one_order(tmp_path):
    """No argument and no environment: the committed default (backend/settings.py) waits."""
    agent = A.Agent(LocalCatalog(), load_rules(), AuditLog(tmp_path / "a.sqlite"))
    agent.handle_event(dict(YOGURT_EMPTY))
    agent.handle_event(dict(YOGURT_FULL))
    agent.handle_event(dict(YOGURT_EMPTY))
    assert len(slot_requests(agent, 2)) == 1


def test_the_restock_window_survives_a_backend_restart(agent, tmp_path):
    agent.handle_event(dict(YOGURT_EMPTY))
    restarted = A.Agent(LocalCatalog(), load_rules(), AuditLog(tmp_path / "a.sqlite"), restock_min_s=LIVE_WINDOW_S)
    assert restarted.handle_event(dict(YOGURT_FULL))["status"] == "ignored"
    restarted.handle_event(dict(YOGURT_EMPTY))
    assert len(slot_requests(restarted, 2)) == 1


def test_simultaneous_events_for_one_slot_create_one_order(agent):
    import threading
    start = threading.Barrier(8)

    def send():
        start.wait()
        agent.handle_event(dict(YOGURT_EMPTY))

    threads = [threading.Thread(target=send) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(slot_requests(agent, 2)) == 1
