"""Repeated low-stock observations must not create duplicate orders.

xfail(strict=True) marks a requirement the baseline breaks, with the evidence. Worktree 1
owns the fix in backend/agent.py (handle_event, the trigger and restock part).
"""

import pytest

from backend import agent as A
from backend.audit import AuditLog
from backend.rules import load_rules
from backend.valio import LocalCatalog

YOGURT_EMPTY = {"type": "slot_state", "slot": 2, "state": "empty", "estimated_units": 0, "auto_limit_eur": 40}
YOGURT_FULL = {"type": "slot_state", "slot": 2, "state": "full", "estimated_units": 12, "auto_limit_eur": 40}


@pytest.fixture
def agent(tmp_path):
    return A.Agent(LocalCatalog(), load_rules(), AuditLog(tmp_path / "a.sqlite"))


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


@pytest.mark.xfail(strict=True, reason=(
    "baseline: one 'full' reading marks the slot restocked. Seen live on 14 Nov: 04:20:53 order "
    "ORD-68CE0E08 (12 x yogurt), 04:20:56 'Slot 2 is stocked again', 04:21:19 order ORD-592FF708, "
    "the same 12 x yogurt again."))
def test_one_false_full_reading_between_lows_does_not_order_twice(agent):
    agent.handle_event(dict(YOGURT_EMPTY))
    agent.handle_event(dict(YOGURT_FULL))  # 3 s later, a hand or the calibration, not a delivery
    agent.handle_event(dict(YOGURT_EMPTY))
    assert len(slot_requests(agent, 2)) == 1
