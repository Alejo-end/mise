"""Buying pipeline. Fixed code, no language model yet.

The pipeline searches, compares and proposes. backend.rules decides.
"""

from __future__ import annotations

import math
import re
import threading
import uuid
from dataclasses import asdict, replace
from datetime import date, datetime, time, timedelta

from . import rules as R
from .audit import AuditLog
from .settings import DEFAULTS, Settings
from .valio import Claim, words

APPROVAL_EXPIRY = timedelta(hours=2)

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10, "twenty": 20, "hundred": 100}


class Agent:
    def __init__(self, catalog, ruleset: dict, log: AuditLog, restock_min_s: float | None = None,
                 restock_confirm_s: float | None = None, settings: Settings | None = None):
        self.catalog = catalog
        self.rules = ruleset
        self.log = log
        # A function that adds a second source's data to a list of products before the rule check
        # (backend/openfoodfacts.py). None: the wholesaler's data alone decides.
        self.second_source = None
        self.lock = threading.Lock()
        # Guards the pending-slot check in handle_event. Separate from self.lock, which _ask takes
        # inside the buying path, so holding this one while buying would deadlock.
        self.trigger_lock = threading.Lock()
        # The restock policy (backend/settings.py). Without settings the committed defaults apply, never
        # the environment, so a local .env cannot change what a test checks; backend.app passes the
        # environment's values. restock_min_s and restock_confirm_s override single values.
        given = {"restock_min_s": restock_min_s, "restock_confirm_s": restock_confirm_s}
        self.settings = replace(settings or DEFAULTS, **{k: float(v) for k, v in given.items() if v is not None})
        # Pending slots (interface 3 and 8 of docs/PARALLEL_WORK.md). A slot is pending from its camera or
        # knob trigger until its request ends (skipped, expired, blocked, failed) or its order is restocked.
        self.pending_since: dict[int, datetime] = {}  # slot -> time of the trigger
        self.pending: dict[int, dict] = {}  # slot -> {"run_id", "product_id", "rule_set_version", "since"}
        self.stocked_hold: dict[int, dict] = {}  # slot -> {"start", "last", "told"}: stocked readings in a row
        self.last_health: dict | None = None  # {"report": health of the last status event, "received": datetime}
        self.approvals: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}  # order_id -> order entry, for delivery checks and weekly spend
        self.placed_runs: dict[str, dict] = {}  # run id -> result, so one run never orders twice
        self.attempted: dict[str, list[dict]] = {}  # run id -> lines of an order call that was tried
        self.last_order_qty: dict[int, int] = {}
        self.pending_slots: set[int] = set()
        self.auto_limit_eur: float | None = None
        self.cold_alarm_c: float | None = None
        self.deliveries: list[dict] = []  # delivery checks, for supplier reliability
        self.label_checks: dict[str, str] = {}  # R.label_key of a product -> who checked its label, and when
        self.restore()

    # State ----------------------------------------------------------------

    @property
    def weekly_spent_eur(self) -> float:
        """Spend on orders placed since Monday 00:00 this week."""
        now = datetime.now(R.TZ)
        monday = datetime.combine(now.date() - timedelta(days=now.weekday()), time(0), tzinfo=R.TZ)
        return round(sum(o["total_eur"] for o in self.orders.values() if datetime.fromisoformat(o["time"]) >= monday), 2)

    def restore(self) -> None:
        """Rebuild orders, open approvals, pending slots and deliveries from the audit log after a restart."""
        decided = set()
        entries = self.log.entries(limit=100000)
        for e in entries:
            d, step = e["detail"], e["step"]
            if step == "order":
                self.orders[e["order_id"]] = {"order_id": e["order_id"], "time": e["time"], "total_eur": d["total_eur"],
                                              "lines": d["lines"], "slot": d.get("slot"), "run_id": e["run_id"]}
                self.placed_runs[e["run_id"]] = {"status": "ordered", "order_id": e["order_id"], "total_eur": d["total_eur"]}
                if d.get("slot") is not None:
                    self.last_order_qty[d["slot"]] = sum(l["quantity"] for l in d["lines"])
            elif step == "delivery_check":
                self.deliveries.append(d)
            elif step == "approval" and d.get("status") == "open" and "full" in d:
                a = d["full"]
                a["ctx"] = context_from_dict(a["ctx"] | {"label_checks": self.label_checks})
                self.approvals[a["id"]] = a
            elif step == "approval" and d.get("approval_id"):
                decided.add(d["approval_id"])
            elif step == "alarm":
                self.cold_alarm_c = d["temperature_c"] if d.get("active", True) else None
            elif step == "label_check" and e["rule_set_version"] == self.rules["version"]:
                self.label_checks[d["label_key"]] = d["note"]  # a new rule set asks again
        for aid in decided:
            a = self.approvals.get(aid)
            if a:
                a["status"] = "closed"
        self._restore_pending(entries)

    def _restore_pending(self, entries: list[dict]) -> None:
        """Pending slots after a restart: the last camera or knob trigger of each slot, when its run still has
        an open approval, or a placed order with no restock and no delivery check after it.

        Everything else that ended a request (skipped, expired, blocked, an error) leaves no open approval
        and no order for the run, so it needs no entry of its own here. A stocked hold is not restored: after
        a restart the slot must read stocked for RESTOCK_CONFIRM_S again.
        """
        last: dict[int, dict] = {}
        triggered: set[str] = set()

        def record(slot: int, e: dict, product_id=None, known=False) -> dict:
            if not known and e["rule_set_version"] == self.rules["version"]:
                product_id = (R.target_for_slot(self.rules, slot) or {}).get("product_id")  # logs before 14 Nov
            return {"run_id": e["run_id"], "product_id": product_id, "rule_set_version": e["rule_set_version"],
                    "since": e["time"], "ended": False}

        for e in entries:
            d, step = e["detail"], e["step"]
            ev = d.get("event") if step == "trigger" else None
            if isinstance(ev, dict) and ev.get("type") in ("slot_state", "manual_low") and ev.get("slot") is not None:
                last[int(ev["slot"])] = record(int(ev["slot"]), e, d.get("product_id"), "product_id" in d)
                triggered.add(e["run_id"])
            elif step in ("order", "approval") and e.get("run_id") and e["run_id"] not in triggered:
                # The trigger is older than the entries read (restore reads the newest 100,000): the order or the
                # request stands in for it, so its slot is not left free.
                slot = d.get("slot") if step == "order" else (d.get("full") or {}).get("slot")
                if slot is not None:
                    last[int(slot)] = record(int(slot), e)
                    triggered.add(e["run_id"])
            elif step == "restock" and d.get("slot") in last:
                r = last[d["slot"]]
                r["ended"] |= e.get("run_id") in (None, r["run_id"])  # restocks before 14 Nov name no run
            elif step == "delivery_check" and e.get("run_id"):
                for r in last.values():
                    r["ended"] |= r["run_id"] == e["run_id"]
        for slot, r in last.items():
            ended = r.pop("ended")
            ordered = r["run_id"] in self.placed_runs
            asking = any(a["run_id"] == r["run_id"] and a["status"] == "open" for a in self.approvals.values())
            if not ended and (ordered or asking):
                self.pending_slots.add(slot)
                self.pending_since[slot] = datetime.fromisoformat(r["since"])
                self.pending[slot] = r

    # Entry points ---------------------------------------------------------

    def handle_event(self, ev: dict) -> dict:
        kind = ev.get("type")
        if ev.get("auto_limit_eur") is not None:
            self.auto_limit_eur = float(ev["auto_limit_eur"])

        if kind == "temperature_alarm":
            active = bool(ev.get("active", True))
            self.cold_alarm_c = float(ev["temperature_c"]) if active else None
            self.log.write("alarm", {"temperature_c": ev["temperature_c"], "limit_c": ev.get("limit_c"), "active": active},
                           "device")
            return {"status": "logged"}
        if kind == "delivery_check":
            result = self.check_delivery(ev.get("order_id"), ev.get("counts", {}), ev.get("temperature_c"),
                                         ev.get("seconds_measured"))
            with self.trigger_lock:
                for slot in list(self.pending_slots):
                    self._settle(slot)
            return result
        if kind == "status":
            if isinstance(ev.get("health"), dict):
                self.observe_health(ev["health"])
            return {"status": "ok"}
        if kind not in ("slot_state", "manual_low"):
            return {"status": "ignored", "reason": f"unknown event type {kind}"}

        slot = int(ev["slot"])
        target = R.target_for_slot(self.rules, slot)
        if target is None:
            return {"status": "ignored", "reason": f"slot {slot} has no target in the rule set"}
        if target.get("product_id") is None and kind in ("slot_state", "manual_low"):
            return {"status": "ignored", "reason": f"slot {slot} holds {target.get('product')}, which is not mapped "
                                                   "to a catalog product: no purchase"}
        state = "low" if kind == "manual_low" else ev.get("state")
        units = int(ev.get("estimated_units") or 0) if state != "empty" else 0
        self.open_approvals()  # an approval past its expiry ends here and frees its slot
        now = datetime.now(R.TZ)
        # The check and the add below must be one step, or two events at once could both order.
        with self.trigger_lock:
            self._settle(slot)
            if kind == "slot_state" and stocked(state, units, target):
                return self._stocked_reading(slot, now, f"reads {state}")
            self._break_hold(slot, f"reads {state}")
            if slot in self.pending_slots:
                return {"status": "ignored", "reason": f"slot {slot} already has an order or a request in progress"}
            run = uuid.uuid4().hex[:8]
            self.pending_slots.add(slot)
            self.pending_since[slot] = now
            self.pending[slot] = {"run_id": run, "product_id": target.get("product_id"),
                                  "rule_set_version": self.rules["version"], "since": now.isoformat(timespec="seconds")}
            self.stocked_hold.pop(slot, None)

        how = ("the touch screen" if ev.get("source") == "screen" else "knob press") if kind == "manual_low" else "camera"
        self.log.write(
            "trigger",
            {"summary": f"Slot {slot} ({target['product']}) is {state} by {how}, about {units} units left.", "event": ev,
             "slot": slot, "product_id": target.get("product_id")},
            "device", self.rules["version"], run_id=run,
        )
        return self._safely(run, slot, lambda: self.buy(target, units, target["target_units"] - units, run))

    # Pending slots and the restock policy (worktree 1) --------------------
    #
    # After a trigger the slot is pending: no second order for it. With an order placed, the slot stays
    # pending until (a) a delivery check for that order arrives, or (b) the slot reads stocked without a
    # break for RESTOCK_CONFIRM_S and at least RESTOCK_MIN_S have passed since the order. Rule (b) is a
    # mitigation, not proof of a delivery: a product put back or stock from a back room passes it too, and
    # the order is not marked delivered. One stocked reading never ends it while RESTOCK_CONFIRM_S > 0.
    # Readings come from slot_state events (sent on a change) and from the health in the board's status
    # event (every 10 s), which shows that the stocked state continues. Health never orders, never
    # cancels an order and never closes an approval; it only extends or breaks a stocked hold.

    def pending_for_slot(self, slot: int) -> dict | None:
        """Interface 8: the open approval or the placed, not yet restocked order of a slot, else None.

        {"slot", "product_id", "rule_set_version", "run_id", "since", "status": "approval" | "ordered"}.
        product_id and rule_set_version are the slot's assignment when it was triggered, so they stay
        even if the assignment changes later. since is the trigger time. While the pipeline is still
        running (between the trigger and its approval or order, about a second), the slot is pending
        but this returns None.
        """
        self.open_approvals()  # an approval past its expiry ends here and frees its slot
        with self.trigger_lock:
            self._settle(slot)
            status = self._pending_status(slot)
            if status is None:
                return None
            r = self.pending[slot]
            return {"slot": slot, "product_id": r["product_id"], "rule_set_version": r["rule_set_version"],
                    "run_id": r["run_id"], "since": r["since"], "status": status}

    def observe_health(self, health: dict) -> None:
        """Use the health of a status event (interface 7) as readings for the slots with a placed order."""
        now = datetime.now(R.TZ)
        self.last_health = {"report": health, "received": now}
        slots = health.get("slots") if isinstance(health.get("slots"), dict) else {}
        age = health.get("frame_age_s")
        box = ("the camera gives no picture" if health.get("camera_ok") is False else
               "the camera sent no recent frame" if not isinstance(age, (int, float))
               or age > self.settings.frame_stale_s else
               "the box configuration is not valid" if health.get("config_valid") is False else None)
        with self.trigger_lock:
            for slot in sorted(self.pending_slots):
                self._settle(slot)
                h = slots.get(str(slot))
                target = R.target_for_slot(self.rules, slot)
                if not isinstance(h, dict) or target is None or self._pending_status(slot) != "ordered":
                    continue  # no word about this slot, or nothing to restock yet
                why = box or slot_problem(h)
                units = round(h["fill"] * target["target_units"]) if isinstance(h.get("fill"), (int, float)) else 0
                if why is None and stocked(h.get("state"), units, target):
                    self._hold(slot, now, f"reads {h['state']}")
                else:
                    self._break_hold(slot, why or f"reads {h.get('state') or 'nothing yet'}")

    def _pending_status(self, slot: int) -> str | None:
        r = self.pending.get(slot) if slot in self.pending_slots else None
        if r is None:
            return None
        if r["run_id"] in self.placed_runs:
            return "ordered"
        # A snapshot: _ask adds approvals under self.lock from the buying path, not under trigger_lock.
        if any(a["run_id"] == r["run_id"] and a["status"] in ("open", "deciding") for a in list(self.approvals.values())):
            return "approval"
        return None  # the pipeline is still running

    def _pending_order(self, slot: int) -> dict | None:
        placed = self.placed_runs.get(self.pending[slot]["run_id"]) if slot in self.pending else None
        return self.orders.get(placed["order_id"]) if placed else None

    def _settle(self, slot: int) -> None:
        """End a slot's pending order when a delivery check for it has arrived (rule a). Caller holds trigger_lock."""
        order = self._pending_order(slot) if self._pending_status(slot) == "ordered" else None
        if order and any(d.get("order_id") == order["order_id"] for d in self.deliveries):
            self._end_pending(slot, order, "delivery_check", (
                f"The delivery check of order {order['order_id']} arrived, so the order for slot {slot} is fulfilled. "
                "A new shortage makes a new order."))

    def _stocked_reading(self, slot: int, now: datetime, what: str) -> dict:
        """A slot_state event at or above the reorder level. Caller holds trigger_lock."""
        status = self._pending_status(slot)
        if status == "ordered":
            return self._hold(slot, now, what)
        if status == "approval":
            # The chef decides an open request. A reading does not close it, and no hold builds up before the order.
            return {"status": "ok", "reason": "stock above the reorder level; the request waits for the chef"}
        if slot in self.pending_slots:
            return {"status": "ignored", "reason": f"the request for slot {slot} is still being prepared"}
        return {"status": "ok", "reason": "stock above the reorder level"}

    def _hold(self, slot: int, now: datetime, what: str) -> dict:
        """One stocked reading of a slot with a placed order (rule b). Caller holds trigger_lock."""
        s, order = self.settings, self._pending_order(slot)
        h = self.stocked_hold.get(slot)
        if h is None or (now - h["last"]).total_seconds() > s.health_stale_s:
            h = self.stocked_hold[slot] = {"start": now, "last": now, "told": False}  # a gap starts the hold again
        h["last"] = now
        held = (now - h["start"]).total_seconds()
        age = (now - datetime.fromisoformat(order["time"])).total_seconds()
        if held >= s.restock_confirm_s and age >= s.restock_min_s:
            self._end_pending(slot, order, "stocked", (
                f"Slot {slot} is stocked again: it {what} and has read stocked for {held:.0f} s, "
                f"{age:.0f} s after order {order['order_id']}. A new shortage makes a new order. "
                "This is not proof of a delivery, so the order is not marked delivered."), held)
            return {"status": "ok", "reason": "stock above the reorder level"}
        if age < s.restock_min_s:
            reason = f"too soon after the order to be a delivery ({age:.0f} s)"
        else:
            reason = f"the slot must read stocked for {s.restock_confirm_s:.0f} s; {held:.0f} s so far"
        if not h["told"]:
            h["told"] = True
            wait = (f"That is too soon for a delivery (under {s.restock_min_s:.0f} s), so the order stays in progress."
                    if age < s.restock_min_s else "The order stays in progress.")
            self.log.write("device", {"slot": slot, "order_id": order["order_id"], "summary": (
                f"Slot {slot} {what} {age:.0f} s after order {order['order_id']}. {wait} One reading is not a refill: "
                f"the order ends when the slot reads stocked for {s.restock_confirm_s:.0f} s without a break, at "
                f"least {s.restock_min_s:.0f} s after the order, or with a delivery check.")},
                "device", self.rules["version"], run_id=self.pending[slot]["run_id"])
        return {"status": "ignored", "reason": reason}

    def _break_hold(self, slot: int, why: str) -> None:
        """A reading below the reorder level, or an unsure one, ends a stocked hold. Caller holds trigger_lock."""
        h = self.stocked_hold.pop(slot, None)
        if h and h["told"]:
            self.log.write("device", {"slot": slot, "summary": (
                f"Slot {slot} {why}, so it no longer reads stocked. The order stays in progress, and the wait for "
                "a refill starts again.")}, "device", self.rules["version"],
                run_id=(self.pending.get(slot) or {}).get("run_id"))

    def _end_pending(self, slot: int, order: dict, how: str, summary: str, held_s: float | None = None) -> None:
        r = self.pending.pop(slot)
        self.pending_slots.discard(slot)
        self.stocked_hold.pop(slot, None)
        self.log.write("restock", {"slot": slot, "summary": summary, "how": how, "order_id": order["order_id"],
                                   "held_s": None if held_s is None else round(held_s)},
                       "agent" if how == "delivery_check" else "device", self.rules["version"], run_id=r["run_id"])

    def _safely(self, run: str, slot: int | None, step) -> dict:
        """Run a pipeline step. On any error, free the slot, log it and say so, instead of hanging."""
        try:
            return step()
        except Exception as e:  # the catalog or the network failed; the log must show it
            if slot is not None:
                self.pending_slots.discard(slot)
            self.log.write("error", {"summary": f"{type(e).__name__}: {e}", "slot": slot}, "agent",
                           self.rules["version"], run_id=run)
            return {"status": "error", "reason": str(e)}

    def handle_request(self, text: str, product_id: str | None = None, asked_by: str | None = None) -> dict:
        """A text request from a person in the kitchen, for example '1,000 litres of lactose-free milk by Monday'.
        `asked_by` is the name that the log and the approval request show."""
        run = uuid.uuid4().hex[:8]
        target = match_target(text, self.rules["targets"])
        # The deadline comes out first, so "by 17.11." is not read as 17 units.
        qty = units_for(DEADLINE.sub(" ", text.lower()), target["product"] if target else "") or 1
        deadline = parse_deadline(text, datetime.now(R.TZ))
        understood = f"{qty} × {target['product'] if target else 'unknown product'}"
        if deadline:
            understood += f", needed by {deadline:%a %d.%m. %H:%M}"
        self.log.write("trigger", {"summary": f"{asked_by or 'The chef'} asked: \"{text}\". Understood as {understood}.",
                                   "text": text, "quantity": qty, "needed_by": deadline, "product_id": product_id},
                       asked_by or "chef",
                       self.rules["version"], run_id=run)
        if product_id:
            # The chef (or a judge) names one product. It still goes through the rule check.
            return self.order_exact(product_id, qty, run)
        if target is None:
            self.log.write("blocked", {"summary": "No product in the rule set matches the request."}, "agent", run_id=run)
            return {"status": "no_match"}
        return self._safely(run, None, lambda: self.buy(target, 0, qty, run, standing=False, deadline=deadline))

    def decide(self, approval_id: str, action: str, option: int | None, approver: str) -> dict:
        idx = option if option is not None else 0
        with self.lock:
            a = self.approvals.get(approval_id)
            if a is None or a["status"] != "open":
                return {"status": "not_open"}
            if self._expired(a):
                a["status"] = "expired"
                self.pending_slots.discard(a["slot"])
                self.log.write("approval", {"status": "expired", "approval_id": approval_id}, "agent",
                               self.rules["version"], run_id=a["run_id"])
                return {"status": "expired", "reason": "the quote is older than 2 hours; ask again for a new quote"}
            if action != "skip" and not 0 <= idx < len(a["options"]):
                return {"status": "bad_option"}
            a["status"] = "deciding"  # nobody else can decide it while the order runs
        if action == "skip":
            a["status"] = "skipped"
            self.pending_slots.discard(a["slot"])
            self.log.write("approval", {"status": "skipped", "option": None, "approval_id": approval_id}, approver,
                           self.rules["version"], run_id=a["run_id"])
            return {"status": "skipped"}
        opt = a["options"][idx]
        try:
            result = self._order(opt["lines"], a["ctx"], a["run_id"], a["slot"], approved_by=approver,
                                 approved_total=opt["total_eur"])
        except Exception as e:
            a["status"] = "open"  # reopen, so the chef can try again
            self.log.write("error", {"summary": f"Order after approval failed: {type(e).__name__}: {e}. "
                                     "The request is open again.", "slot": a["slot"]}, "agent",
                           self.rules["version"], run_id=a["run_id"])
            return {"status": "error", "reason": str(e)}
        a["status"] = "approved"
        self.log.write("approval", {"status": "approved", "option": idx, "approval_id": approval_id,
                                    "result": result["status"]}, approver, self.rules["version"], run_id=a["run_id"])
        if result["status"] == "ordered":
            self._remember_labels(opt["lines"], approver, a["run_id"])
        return result

    def _remember_labels(self, lines: list[dict], approver: str, run: str) -> None:
        """The approver read the label of each product that needed it. Keep that, so the same product asks once."""
        for l in lines:
            p = self.catalog.product(l["product_id"]).value
            if not p or not R.needs_label_check(p, self.rules) or R.label_key(p) in self.label_checks:
                continue
            note = f"{approver} checked the label on {datetime.now(R.TZ):%d.%m.%Y}"
            self.label_checks[R.label_key(p)] = note
            self.log.write("label_check", {
                "summary": f"{approver} approved {p['name']} after a label check. The agent does not ask again about "
                           "this label while the product data and the rule set stay the same.",
                "product_id": p["id"], "label_key": R.label_key(p), "note": note}, approver, self.rules["version"], run_id=run)

    def _expired(self, a: dict) -> bool:
        return datetime.now(R.TZ) - datetime.fromisoformat(a["created"]) > APPROVAL_EXPIRY

    def open_approvals(self) -> list[dict]:
        out = []
        for a in list(self.approvals.values()):
            if a["status"] == "open" and self._expired(a):
                a["status"] = "expired"
                self.pending_slots.discard(a["slot"])
                self.log.write("approval", {"status": "expired", "approval_id": a["id"]}, "agent",
                               self.rules["version"], run_id=a["run_id"])
            elif a["status"] == "open":
                out.append({k: v for k, v in a.items() if k != "ctx"})
        return out

    # Pipeline -------------------------------------------------------------

    def _ctx(self, target: dict, units: int, standing: bool, deadline: datetime | None = None) -> R.Context:
        return R.Context(
            slot_group=target["group"],
            needed_by=deadline or R.needed_by(self.rules, datetime.now(R.TZ)),
            current_units=units,
            target_units=target["target_units"] if standing else 0,
            weekly_spent_eur=self.weekly_spent_eur,
            auto_limit_eur=self.auto_limit_eur,
            previous_standing_qty=self.last_order_qty.get(target["slot"]) if standing else None,
            cold_room_alarm_c=self.cold_alarm_c,
            label_checks=self.label_checks,
        )

    def _quote(self, product_id: str, qty: int, ctx: R.Context, run: str) -> dict | None:
        p: Claim = self.catalog.product(product_id)
        if p.value is None:
            return None
        a: Claim = self.catalog.availability(product_id, qty, ctx.needed_by)
        v = a.value
        return {"product": p.value, "unit_price_eur": v["unit_price_eur"], "available": v["available_units"],
                "delivery": v["delivery"], "stock_known": v.get("quantity_known", True),
                "problem": v.get("problem"), "closing_time": v.get("closing_time"),
                "price_valid_to": v.get("price_valid_to"), "unit_price_with_tax_eur": v.get("unit_price_with_tax_eur"),
                "tax_rate_pct": v.get("tax_rate_pct"), "sales_unit": v.get("sales_unit") or p.value.get("sales_unit"),
                "claims": [p.to_dict(), a.to_dict()]}

    def _line(self, q: dict, qty: int, usual: bool) -> R.Line:
        delivery, known = delivery_at(q["delivery"])
        return R.Line(q["product"], qty, q["unit_price_eur"], delivery, usual, q["stock_known"], known)

    @property
    def delivery_charge(self) -> str:
        return getattr(self.catalog, "delivery_charge", "unknown: the catalog supplies no delivery charge")

    @property
    def source(self) -> str:
        return getattr(self.catalog, "source", getattr(self.catalog, "name", "catalog"))

    def _free(self, slot: int | None) -> None:
        if slot is not None:
            self.pending_slots.discard(slot)

    def buy(self, target: dict, units: int, need: int, run: str, standing: bool = True,
            deadline: datetime | None = None) -> dict:
        ctx = self._ctx(target, units, standing, deadline)
        v = self.rules["version"]
        slot = target["slot"] if standing else None  # a text request is not tied to a shelf slot
        query = base_name(target["product"])
        found: Claim = self.catalog.search(query)
        self.log.write("search", {"query": query, "count": len(found.value), "ids": [p["id"] for p in found.value],
                                  "checked_at": found.checked_at, "sha256": found.sha256}, "agent", v, run_id=run)

        candidates = list(found.value)
        usual_id = target.get("product_id")
        if usual_id and all(p["id"] != usual_id for p in candidates):
            usual = self.catalog.product(usual_id).value  # a long hit list can miss the usual product
            if usual:
                candidates.insert(0, usual)
        # One unit must mean the same amount for every candidate. A crate of five is not a carton.
        want_pack = pack_of(target["product"])
        other_pack = [p["id"] for p in candidates
                      if p["id"] != usual_id and "pack" in p and want_pack and not same_pack(p["pack"], want_pack)]
        candidates = [p for p in candidates if p["id"] not in other_pack]
        other_packs = f" {len(other_pack)} products of another pack size were not considered." if other_pack else ""
        if hasattr(self.catalog, "prefetch"):
            self.catalog.prefetch([p["id"] for p in candidates], ctx.needed_by)

        quotes, not_orderable, why = {}, [], {}
        for p in candidates:
            q = self._quote(p["id"], need, ctx, run)
            if q is None:
                continue
            if q["unit_price_eur"] is None or q["delivery"] is None:
                not_orderable.append(p["id"])  # no price or no delivery date: nothing to order now
                if q.get("problem"):
                    why[p["id"]] = q["problem"]
                continue
            quotes[p["id"]] = q
        self.log.write("candidates", {"need": need, "source": self.source, "candidates": [
            {"id": pid, "price_eur": q["unit_price_eur"], "available": q["available"], "delivery": q["delivery"],
             "closing_time": q.get("closing_time"), "price_valid_to": q.get("price_valid_to"),
             "checked_at": q["claims"][1]["checked_at"], "sha256": q["claims"][1]["sha256"]} for pid, q in quotes.items()],
            "left_out": {"other_pack_size": other_pack, "no_price_or_delivery": not_orderable, "why": why}},
            "agent", v, run_id=run)

        if self.second_source:
            self.second_source([q["product"] for q in quotes.values()])
        # Safety check of each product alone. Forbidden products are dropped here.
        safe = {}
        for pid, q in quotes.items():
            usual = pid == target.get("product_id")
            d = R.check_line(self._line(q, need, usual), self.rules, ctx)
            if d.result == R.FORBIDDEN:
                self.log.write("rule_check", {"subject": q["product"]["name"], **d.to_dict()}, "rule_check", v, run_id=run)
            else:
                safe[pid] = q

        usual_q = safe.get(target.get("product_id"))
        options = []
        # Every line's quantity is a whole multiple of its product's lot size (allot), so a split never
        # makes a line Valio would refuse.
        if usual_q and allot(usual_q, need) >= need:
            options.append(("usual", [(usual_q, need, True)]))
        else:
            have = allot(usual_q, need) if usual_q else 0
            rest = need - have
            base = [(usual_q, have, True)] if have else []
            ranked = rank_substitutes(safe, target, ctx, self.rules, self.reliability())
            seen = set()
            for label, lead in ranked:
                # Fill the gap with the lead substitute first, then the others in rank order.
                parts, left = list(base), rest
                for q in [lead] + [q for _, q in ranked if q is not lead] + rest_by_rank(safe, target, ranked):
                    if left <= 0:
                        break
                    n = allot(q, left)
                    if n > 0 and all(q is not p[0] for p in parts):
                        parts.append((q, n, False))
                        left -= n
                key = tuple((q["product"]["id"], n) for q, n, _ in parts)
                if left <= 0 and key not in seen:
                    seen.add(key)
                    options.append((label, parts))
            if not options:
                # Nothing safe fills the whole gap: offer what is safe and in stock, and name the shortfall.
                parts = list(base) + [(q, allot(q, need), False) for _, q in ranked]
                parts += [(q, allot(q, need), False) for q in rest_by_rank(safe, target, ranked)]
                parts, left = _take(parts, need)
                if parts:
                    shortfall = left
                    options.append(("partial", parts))

        if not options:
            self.log.write("blocked", {"summary": f"No safe product can fill {need} units of {target['product']}."
                                       + other_packs}, "agent", v, run_id=run)
            # Nothing can be ordered, and that is a decision for a person too: the kitchen still needs the product.
            # The request has no option. The approver reads it and closes it, and the slot stays held until then.
            return self._ask([], ctx, run, slot, f"{target['product']}: nothing safe can be ordered")

        checked = []
        for label, parts in options[:3]:
            lines = [self._line(q, n, u) for q, n, u in parts]
            d = R.check(lines, self.rules, ctx)
            if label == "partial":
                # A short order is a change to what was asked, so a person decides.
                d.result = R.NEEDS_APPROVAL if d.result == R.ALLOWED else d.result
                d.reasons.insert(0, f"only {need - shortfall} of {need} can be ordered safely; {shortfall} short."
                                 + other_packs)
            names = " + ".join(f"{n} × {q['product']['name']}" for q, n, u in parts)
            self.log.write("rule_check", {"subject": names, "option": label, **d.to_dict()}, "rule_check", v, run_id=run)
            checked.append({
                "label": label,
                "result": d.result,
                "reasons": d.reasons,
                "summary": names,
                "trade_off": trade_off(label, parts, ctx) + " " + charges_note([q["product"] for q, _, _ in parts],
                                                                               self.delivery_charge),
                "total_eur": round(sum(l.total_eur or 0 for l in lines), 2),
                "charges": charges([q["product"] for q, _, _ in parts], self.delivery_charge),
                "source": self.source,
                "lines": [line_of(q, n, u) for q, n, u in parts],
            })

        if checked[0]["result"] == R.ALLOWED:
            return self._order(checked[0]["lines"], ctx, run, slot, approved_by=None,
                               approved_total=checked[0]["total_eur"])
        return self._ask(checked, ctx, run, slot, target["product"])

    def _role(self, product: dict) -> tuple[str, bool]:
        """What a product is to this kitchen: (the group of its shelf slot, whether it is the usual product)."""
        for t in self.rules["targets"]:
            if t.get("product_id") == product["id"]:
                return t["group"], True
        for t in self.rules["targets"]:
            if R.is_approved_substitute(product, self.rules, t["group"]):
                return t["group"], False
        return "unknown", False

    def quote_exact(self, product_id: str, qty: int, deadline: datetime | None = None,
                    mandate_eur: float | None = None, scope_groups: list[str] | None = None, run: str = "quote") -> dict:
        """Price, stock and the rule decision for one named product. It orders nothing and logs nothing.

        mandate_eur is the spend limit of the one who asks. The lower of it and the knob counts.
        scope_groups are the product groups of the asker's licence. A product outside them goes to a person.
        The status is "quote", "no_match" (not in the catalog) or "not_orderable" (no price or no delivery date).
        """
        limits = [x for x in (self.auto_limit_eur, mandate_eur) if x is not None]
        ctx = R.Context(slot_group="unknown", needed_by=deadline or R.needed_by(self.rules, datetime.now(R.TZ)),
                        weekly_spent_eur=self.weekly_spent_eur, auto_limit_eur=min(limits) if limits else None,
                        cold_room_alarm_c=self.cold_alarm_c, label_checks=self.label_checks)
        p = self.catalog.product(product_id).value
        if p is None:
            return {"status": "no_match"}
        if not lot_ok(qty, p.get("lot_size")):
            return {"status": "invalid_quantity", "name": p["name"],
                    "reason": f"{qty} {p.get('sales_unit') or 'units'} is not a multiple of the allowed lot size "
                              f"{p['lot_size']} {p.get('sales_unit') or ''}".rstrip()}
        q = self._quote(product_id, qty, ctx, run)
        if q is None:
            return {"status": "no_match"}
        name = q["product"]["name"]
        if self.second_source:
            self.second_source([q["product"]])
        # A forbidden product is refused as forbidden, also when it has no price or date now: safety first.
        if (q["unit_price_eur"] is None or q["delivery"] is None) and not R.safety(q["product"], self.rules)[0]:
            return {"status": "not_orderable", "name": name,
                    "reason": q.get("problem") or ("no price" if q["unit_price_eur"] is None else "no delivery date")}
        ctx.slot_group, usual = self._role(q["product"])
        line = self._line(q, qty, usual)
        d = R.check([line], self.rules, ctx)
        if scope_groups is not None and ctx.slot_group not in scope_groups and d.result != R.FORBIDDEN:
            d.result = R.NEEDS_APPROVAL
            d.reasons = [r for r in d.reasons if not r.startswith("usual product or approved substitute")]
            d.reasons.append(f"{name} is outside the product groups of the agent's licence")
        return {"status": "quote", "product": q["product"], "name": name, "usual": usual, "ctx": ctx,
                "unit_price_eur": q["unit_price_eur"], "available_units": q["available"], "delivery": q["delivery"],
                "total_eur": line.total_eur, "decision": d, "claims": q["claims"], "stock_known": q["stock_known"],
                "line": line_of(q, qty, usual), "charges": charges([q["product"]], self.delivery_charge),
                "source": self.source}

    def buy_exact(self, product_id: str, qty: int, run: str, deadline: datetime | None = None,
                  mandate_eur: float | None = None, scope_groups: list[str] | None = None, actor: str = "agent",
                  max_unit_price_eur: float | None = None) -> dict:
        """Order one named product, through the same rule check as every other order.

        max_unit_price_eur is the price of the quote that the buyer holds. If the price is higher now,
        nothing is ordered and the status is "price_changed".
        """
        v = self.rules["version"]
        q = self.quote_exact(product_id, qty, deadline, mandate_eur, scope_groups, run)
        if q["status"] == "no_match":
            self.log.write("blocked", {"summary": f"Product {product_id} is not in the catalog."}, "agent", v, run_id=run)
            return q
        if q["status"] == "not_orderable":
            self.log.write("blocked", {"summary": f"{q['name']} cannot be ordered now: {q['reason']}."}, "agent", v,
                           run_id=run)
            return {"status": "not_orderable", "reason": q["reason"]}
        if q["status"] == "invalid_quantity":
            self.log.write("blocked", {"summary": f"{q['name']}: {q['reason']}."}, "agent", v, run_id=run)
            return {"status": "invalid_quantity", "reason": q["reason"]}
        d, name = q["decision"], q["name"]
        if max_unit_price_eur is not None and q["unit_price_eur"] > max_unit_price_eur:
            self.log.write("blocked", {"summary": f"The price of {name} rose from {max_unit_price_eur} to "
                                       f"{q['unit_price_eur']} after the quote. Nothing is ordered."}, "agent", v, run_id=run)
            return {"status": "price_changed", "quoted_unit_price_eur": max_unit_price_eur,
                    "unit_price_eur": q["unit_price_eur"]}
        self.log.write("rule_check", {"subject": f"{qty} × {name}", **d.to_dict()}, "rule_check", v, run_id=run)
        if d.result == R.FORBIDDEN:
            self.log.write("blocked", {"summary": f"{name} is forbidden: {'; '.join(d.reasons)}. "
                                       "Nobody can approve it. Only a new signed rule set can change this."},
                           "rule_check", v, run_id=run)
            return {"status": "forbidden", "reasons": d.reasons, "rule_set_version": v}
        opt = {"label": "requested", "result": d.result, "reasons": d.reasons, "summary": f"{qty} × {name}",
               "trade_off": "The product you asked for. " + charges_note([q["product"]], self.delivery_charge),
               "total_eur": q["total_eur"], "charges": q["charges"], "source": q["source"], "lines": [q["line"]]}
        if d.result == R.ALLOWED:
            return self._order(opt["lines"], q["ctx"], run, None, None, opt["total_eur"], actor=actor)
        return self._ask([opt], q["ctx"], run, None, name)

    def order_exact(self, product_id: str, qty: int, run: str, **who) -> dict:
        """buy_exact for a caller outside this class: an error becomes a logged result, not an exception."""
        return self._safely(run, None, lambda: self.buy_exact(product_id, qty, run, **who))

    def run_state(self, run_id: str) -> dict | None:
        """What became of one order request: ordered, waiting for a person, or how the request ended. None if unknown."""
        if run_id in self.placed_runs:
            return self.placed_runs[run_id]
        asked = [a for a in self.approvals.values() if a["run_id"] == run_id]
        if not asked:
            return None
        self.open_approvals()  # lets an old request expire
        last = asked[-1]
        waiting = last["status"] in ("open", "deciding")
        return {"status": "waiting_for_approval" if waiting else last["status"], "approval_id": last["id"]}

    def _ask(self, options: list[dict], ctx: R.Context, run: str, slot: int | None, what: str) -> dict:
        aid = uuid.uuid4().hex[:6]
        a = {"id": aid, "status": "open", "slot": slot, "what": what, "options": options, "run_id": run,
             "approver": self.rules.get("approvers", {}).get("default", "Head chef"),
             "created": datetime.now(R.TZ).isoformat(timespec="seconds"), "ctx": ctx}
        with self.lock:
            self.approvals[aid] = a
        self.log.write("approval", {"status": "open", "approval_id": aid, "approver": a["approver"],
                                    "options": [{"label": o["label"], "summary": o["summary"], "total_eur": o["total_eur"],
                                                 "result": o["result"]} for o in options],
                                    "full": {k: v for k, v in a.items() if k != "ctx"} | {"ctx": context_to_dict(ctx)}},
                       "agent", self.rules["version"], run_id=run)
        return {"status": "needs_approval", "approval_id": aid}

    def _recheck(self, lines: list[dict], ctx: R.Context, run: str, slot: int | None,
                 approved_by: str | None, approved_total: float) -> tuple[list[dict] | None, dict | None]:
        """Ask price and stock again. Returns the lines to order, or the result that ends the run."""
        v = self.rules["version"]
        # Step 8: ask price and stock again just before the order.
        fresh, changes, short, unknown, moved = [], [], False, False, False
        for l in lines:
            a = self.catalog.availability(l["product_id"], l["quantity"], ctx.needed_by)
            p = self.catalog.product(l["product_id"])
            price, left = a.value["unit_price_eur"], a.value["available_units"]
            if price != l["unit_price_eur"]:
                changes.append(f"{l['name']} price {l['unit_price_eur']} → {price}")
            if a.value["delivery"] != l["delivery"]:
                moved = True
                changes.append(f"{l['name']} delivery {l['delivery']} → {a.value['delivery'] or a.value.get('problem')}")
            if left < l["quantity"]:
                short = True
                changes.append(f"{l['name']} only {left} of {l['quantity']} left")
            unknown |= price is None or a.value["delivery"] is None
            if left > 0:  # the line as the catalog answers now: fresh price, date and proof, no more than is left
                fresh.append((p.value, l | {"quantity": min(l["quantity"], left), "unit_price_eur": price,
                                            "delivery": a.value["delivery"],
                                            "closing_time": a.value.get("closing_time"),
                                            "price_valid_to": a.value.get("price_valid_to"),
                                            "stock_known": a.value.get("quantity_known", True),
                                            "price_checked_at": a.checked_at,
                                            "sha256": a.sha256}))
        self.log.write("recheck", {"summary": "; ".join(changes) or "nothing changed.", "changes": changes}, "agent", v, run_id=run)

        if not changes and fresh:
            # Price, stock and delivery are unchanged, but the product data may not be (a property or an allergen
            # changed since the quote). The rules are checked on the product as the catalog describes it now.
            rl = [R.Line(p, l["quantity"], l["unit_price_eur"], *_delivery_pair(l["delivery"], l["usual"], l["stock_known"]))
                  for p, l in fresh]
            d = R.check(rl, self.rules, ctx)
            if d.result == R.FORBIDDEN:
                self.log.write("rule_check", {"subject": "order after recheck (current product data)", **d.to_dict()},
                               "rule_check", v, run_id=run)
                self.log.write("blocked", {"summary": "The product data changed since the quote; the rule check now "
                                           "forbids the order."}, "rule_check", v, run_id=run)
                self._free(slot)
                return None, {"status": "forbidden", "reasons": d.reasons}

        if changes:
            if not fresh or unknown:
                self.log.write("blocked", {"summary": "After the recheck nothing can be ordered: " + "; ".join(changes)},
                               "agent", v, run_id=run)
                self._free(slot)
                return None, {"status": "blocked"}
            rl = [R.Line(p, l["quantity"], l["unit_price_eur"], *_delivery_pair(l["delivery"], l["usual"], l["stock_known"]))
                  for p, l in fresh]
            d = R.check(rl, self.rules, ctx)
            self.log.write("rule_check", {"subject": "order after recheck", **d.to_dict()}, "rule_check", v, run_id=run)
            new_total = round(sum(x.total_eur or 0 for x in rl), 2)
            lines = [l for _, l in fresh]
            if d.result == R.FORBIDDEN:
                self.log.write("blocked", {"summary": "The recheck made the order forbidden."}, "rule_check", v, run_id=run)
                self._free(slot)
                return None, {"status": "forbidden", "reasons": d.reasons}
            # Less stock or another delivery date is a change to what was decided, so a person looks again,
            # also after an approval.
            if short or moved or (d.result == R.NEEDS_APPROVAL and (approved_by is None or new_total > approved_total)):
                reasons = d.reasons if d.result == R.NEEDS_APPROVAL else [
                    ("stock fell" if short else "the delivery date changed") + " after the quote, so a person must look again"]
                names = " + ".join(f"{l['quantity']} × {l['name']}" for l in lines)
                products = [p for p, _ in fresh]
                return None, self._ask([{"label": "after recheck", "result": R.NEEDS_APPROVAL, "reasons": reasons,
                                   "summary": names,
                                   "trade_off": "; ".join(changes) + ". " + charges_note(products, self.delivery_charge),
                                   "total_eur": new_total, "charges": charges(products, self.delivery_charge),
                                   "source": self.source, "lines": lines}], ctx, run, slot, "changed order")
        return lines, None

    def _order(self, lines: list[dict], ctx: R.Context, run: str, slot: int | None,
               approved_by: str | None, approved_total: float, actor: str = "agent") -> dict:
        v = self.rules["version"]
        if run in self.placed_runs:
            # This run already ordered. A retry must not order again.
            self.log.write("recheck", {"summary": f"run {run} already placed {self.placed_runs[run]['order_id']}; "
                                       "not ordering twice.", "changes": []}, "agent", v, run_id=run)
            return self.placed_runs[run]
        if run in self.attempted:
            # The order call for this run failed before, and the catalog may hold the order already.
            # The same lines go out again under the same key. A new recheck would count our own order as lost stock.
            lines = self.attempted[run]
        else:
            lines, done = self._recheck(lines, ctx, run, slot, approved_by, approved_total)
            if done is not None:
                return done
        self.attempted[run] = lines
        payload = [{"product_id": l["product_id"], "quantity": l["quantity"]} for l in lines]
        for attempt in range(3):
            try:
                # The run id is the key. A repeated call returns the first order, so a retry is safe.
                claim = self.catalog.place_order(payload, idempotency_key=run)
                break
            except (TimeoutError, ConnectionError):
                if attempt == 2:
                    raise
        order_id = claim.value["order_id"]
        total = round(sum(l["unit_price_eur"] * l["quantity"] for l in lines), 2)
        now = datetime.now(R.TZ).isoformat(timespec="seconds")
        self.orders[order_id] = {"order_id": order_id, "time": now, "total_eur": total, "lines": lines, "slot": slot,
                                 "run_id": run}
        self.placed_runs[run] = {"status": "ordered", "order_id": order_id, "total_eur": total, **DRY}
        if slot is not None:
            # The slot stays pending until the camera sees it full again, so one empty shelf makes one order.
            self.last_order_qty[slot] = sum(l["quantity"] for l in lines)
        summary = ", ".join(f"{l['quantity']} × {l['name']}" for l in lines) + f", €{total:.2f}"
        summary += f", approved by {approved_by}." if approved_by else ", within the auto-order rules."
        summary += " Recorded by the gateway, not sent to Valio. Delivery charge " + self.delivery_charge + "."
        self.log.write("order", {"summary": summary, "lines": lines, "total_eur": total, "sha256": claim.sha256,
                                 "slot": slot, "delivery_charge": self.delivery_charge, **DRY},
                       approved_by or actor, v, order_id=order_id, run_id=run)
        return self.placed_runs[run]

    # Cold Chain Proof -----------------------------------------------------

    def check_delivery(self, order_id: str | None, counts: dict, temperature_c: float | None,
                       seconds_measured: float | None = None) -> dict:
        """Compare what arrived with what was ordered: units per slot, temperature and time."""
        checked = {d["order_id"] for d in self.deliveries}
        if order_id is None:
            open_orders = [o for o in self.orders.values() if o["order_id"] not in checked]
            if not open_orders:
                return {"status": "no_open_order"}
            order_id = max(open_orders, key=lambda o: o["time"])["order_id"]
        order = self.orders.get(order_id)
        if order is None:
            return {"status": "unknown_order", "order_id": order_id}

        now = datetime.now(R.TZ)
        slot = order.get("slot")
        ordered = sum(l["quantity"] for l in order["lines"])
        counted = None
        if slot is not None and str(slot) in {str(k) for k in counts}:
            counted = int({str(k): v for k, v in counts.items()}[str(slot)])
        short = None if counted is None else max(0, ordered - counted)

        problems, per_supplier = [], {}
        for l in order["lines"]:
            p = self.catalog.product(l["product_id"]).value or {}
            limit = p.get("max_temp_c")
            warm = temperature_c is not None and limit is not None and temperature_c > limit
            at, known = delivery_at(l["delivery"])
            late = at is not None and (now > at + timedelta(hours=1) if known else now.date() > at.date())
            supplier = p.get("supplier", "unknown")
            s = per_supplier.setdefault(supplier, {"supplier": supplier, "warm": False, "late": False, "limit_c": limit})
            s["warm"] |= warm
            s["late"] |= late
            if warm:
                problems.append(f"{l['name']} arrived at {temperature_c:.1f} °C, above its {limit} °C limit")
            if late:
                problems.append(f"{l['name']} arrived after {l['delivery'][11:16] or 'the day'} on {l['delivery'][:10]}")
        if short:
            problems.insert(0, f"{short} of {ordered} units missing")
        for s in per_supplier.values():
            s["complete"] = not short  # a count is per slot, so a short slot counts against every supplier on it
        result = {
            "order_id": order_id, "slot": slot, "ordered": ordered, "counted": counted, "short": short,
            "temperature_c": temperature_c, "seconds_measured": seconds_measured,
            "complete": None if short is None else short == 0,
            "cold": not any(s["warm"] for s in per_supplier.values()),
            "on_time": not any(s["late"] for s in per_supplier.values()),
            "suppliers": list(per_supplier.values()), "problems": problems,
            "summary": (f"Delivery {order_id}: " + ("; ".join(problems) if problems else
                        "complete, cold and on time" if counted is not None else "cold and on time, units not counted") + "."),
        }
        self.deliveries.append(result)
        self.log.write("delivery_check", result, "device", self.rules["version"], order_id=order_id,
                       run_id=order.get("run_id"))
        return {"status": "ok" if not problems else "problem", **result}

    def reliability(self) -> dict[str, dict]:
        """Per supplier: the share of checked deliveries that were complete, cold and on time."""
        out: dict[str, dict] = {}
        for d in self.deliveries:
            for s in d["suppliers"]:
                r = out.setdefault(s["supplier"], {"supplier": s["supplier"], "deliveries": 0, "good": 0})
                r["deliveries"] += 1
                r["good"] += int(s["complete"] and not s["warm"] and not s["late"])
        for r in out.values():
            r["score"] = round(r["good"] / r["deliveries"], 2)
        return out


DRY = {"sent_to_valio": False,
       "note": "Recorded by the gateway, not sent to Valio: the challenge API has no order endpoint."}


def delivery_at(value: str | None) -> tuple[datetime | None, bool]:
    """A quote's delivery as (time, whether the time is known). A bare date ('2026-11-16') is that day at 00:00
    with the time unknown: Valio's availability API gives a date, and we invent no arrival time."""
    if not value:
        return None, True
    if len(value) == 10:
        return datetime.combine(date.fromisoformat(value), time(0), tzinfo=R.TZ), False
    return datetime.fromisoformat(value), True


def _delivery_pair(value, usual, stock_known):
    at, known = delivery_at(value)
    return at, usual, stock_known, known


def in_time(value: str | None, needed_by: datetime) -> bool:
    """True when the delivery meets the deadline: by time when the source gives one, else by date."""
    at, known = delivery_at(value)
    if at is None:
        return False
    return at <= needed_by if known else at.date() <= needed_by.date()


def when_text(value: str | None, needed_by: datetime) -> str:
    at, known = delivery_at(value)
    text = f"arrives {at:%a %H:%M}" if known else f"available {at:%a %d.%m.}, arrival time not supplied"
    return text + ("" if in_time(value, needed_by) else " (late)")


def allot(q: dict, want: int) -> int:
    """The most whole units, up to `want` and the stock, that are a multiple of the product's lot size."""
    n, lot = min(int(want), int(q["available"])), q["product"].get("lot_size")
    while n > 0 and not lot_ok(n, lot):
        n -= 1
    return n


def lot_ok(qty: float, lot) -> bool:
    """True when qty is a whole multiple of the allowed lot size (in sales units). No lot size: any qty."""
    if not lot:
        return True
    steps = qty / float(lot)
    return abs(steps - round(steps)) < 1e-6


def charges(products: list[dict], delivery_charge: str) -> dict:
    """Charges besides the goods. Unknown stays unknown: never 0 in a total."""
    deposit = {}
    for p in products:
        if p.get("deposits"):
            items = ", ".join(f"{d.get('quantity'):g} {d.get('unit')} of material {d.get('material')}" for d in p["deposits"])
            deposit[p["id"]] = f"unknown: the product file lists returnable packaging ({items} per sales unit) without a price"
    return {"delivery": delivery_charge, "deposits": deposit,
            "totals_cover": "goods only, without tax, at the catalog price per sales unit"}


def charges_note(products: list[dict], delivery_charge: str) -> str:
    note = "Delivery charge unknown, not in the total."
    if any(p.get("deposits") for p in products):
        note += " Deposit charge unknown, not in the total."
    return note


def line_of(q: dict, n: int, usual: bool) -> dict:
    """One order line as an option, an approval and an order keep it."""
    p = q["product"]
    return {"product_id": p["id"], "name": p["name"], "quantity": n, "usual": usual,
            "unit_price_eur": q["unit_price_eur"], "delivery": q["delivery"], "stock_known": q["stock_known"],
            "sales_unit": q.get("sales_unit") or p.get("sales_unit"), "closing_time": q.get("closing_time"),
            "price_valid_to": q.get("price_valid_to"), "delivery_reliability": p.get("delivery_reliability"),
            "price_checked_at": q["claims"][1]["checked_at"], "sha256": q["claims"][1]["sha256"]}


def stocked(state: str | None, units: int, target: dict) -> bool:
    """A reading at or above the slot's reorder level: "full", or "low" with enough units left."""
    return state == "full" or (state == "low" and units >= target["reorder_below"])


def slot_problem(h: dict) -> str | None:
    """Why one slot's health is no evidence of its stock, or None when it is."""
    if h.get("uncertain"):
        return f"is uncertain ({h['uncertain']})"
    if h.get("calibration") in ("missing", "stale"):
        return f"has a {h['calibration']} calibration"
    return None


def rest_by_rank(safe: dict, target: dict, ranked: list) -> list[dict]:
    chosen = [q for _, q in ranked]
    return sorted((q for pid, q in safe.items() if pid != target.get("product_id") and q not in chosen),
                  key=lambda q: q["unit_price_eur"])


def _take(parts: list, need: int) -> tuple[list, int]:
    """Take units from parts in order until need is met. Returns the parts used and what is still missing."""
    out, left = [], need
    for q, n, usual in parts:
        n = allot(q, min(n, left))
        if n > 0 and all(q is not p[0] for p in out):
            out.append((q, n, usual))
            left -= n
    return out, left


def rank_substitutes(safe: dict, target: dict, ctx: R.Context, rules: dict,
                     reliability: dict | None = None) -> list[tuple[str, dict]]:
    """best: closest match, better: close match with a better price or date, good: cheapest safe option in time.

    A close match is one the rule set approves as a substitute, by the same test as the rule check.
    Among equal matches, a supplier with a better record in our own delivery checks ranks first (unknown
    suppliers count as 1.0). Valio's delivery reliability file does not rank: it is shown with each option
    as information, and a missing value stays unknown.
    """
    reliability = reliability or {}
    def score(q):
        return reliability.get(q["product"].get("supplier"), {}).get("score", 1.0)
    subs = [q for pid, q in safe.items() if pid != target.get("product_id") and q["available"] > 0]
    if not subs:
        return []
    def closeness(q):
        ok = R.is_approved_substitute(q["product"], rules, target["group"])
        return (ok, in_time(q["delivery"], ctx.needed_by))
    by_match = sorted(subs, key=lambda q: (closeness(q), score(q), -q["unit_price_eur"]), reverse=True)
    best = by_match[0]
    out = [("best", best)]
    better = next((q for q in by_match[1:] if closeness(q)[0] and (
        q["unit_price_eur"] < best["unit_price_eur"] or q["delivery"] < best["delivery"])), None)
    if better:
        out.append(("better", better))
    timely = [q for q in subs if in_time(q["delivery"], ctx.needed_by) and q not in (best, better)]
    if timely:
        out.append(("good", min(timely, key=lambda q: q["unit_price_eur"])))
    return out


def trade_off(label: str, parts: list, ctx: R.Context) -> str:
    q, n, _ = next((x for x in parts if not x[2]), parts[0])
    p = q["product"]
    when = when_text(q["delivery"], ctx.needed_by)
    if label == "usual":
        return f"The usual product, €{q['unit_price_eur']:.2f} each, {when}."
    if label == "partial":
        got = sum(x[1] for x in parts)
        return f"Only {got} units are safe and in stock. The rest is not ordered; look for another source."
    split = f"{parts[0][1]} usual + " if parts[0][2] else ""
    extra = len(parts) - (2 if parts[0][2] else 1)
    if extra > 0:
        n = f"{n} (+{extra} more products)"
    return {
        "best": f"{split}{n} of the closest match, €{q['unit_price_eur']:.2f} each, {when}.",
        "better": f"{split}{n} of a close match at a better price or date, €{q['unit_price_eur']:.2f} each, {when}.",
        "good": f"{split}{n} of the cheapest safe option, €{q['unit_price_eur']:.2f} each, {when}.",
    }.get(label, f"{split}{n} × {p['name']}, {when}.")


def base_name(product: str) -> str:
    """'lactose-free milk 1 L' -> 'lactose-free milk'."""
    return re.sub(r"\s+\d.*$", "", product).lower()


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
UNIT_SCALE = {"l": ("l", 1), "litre": ("l", 1), "litres": ("l", 1), "liter": ("l", 1), "liters": ("l", 1),
              "ml": ("l", 0.001), "kg": ("kg", 1), "kilo": ("kg", 1), "kilos": ("kg", 1), "g": ("kg", 0.001)}


NEEDED_HOUR = 7  # "by Monday" with no time means Monday 07:00, before the kitchen starts
DEADLINE = re.compile(r"\bby\s+(today|tomorrow|" + "|".join(WEEKDAYS)
                      + r"|\d{1,2}\.\d{1,2}\.?)(?:\s+(?:at\s+)?(\d{1,2})[:.](\d{2}))?")


def match_target(text: str, targets: list[dict]) -> dict | None:
    """The rule-set target a request names. Hyphens and word order do not matter.

    A target matches when at least two words of its name are in the text (or its one word).
    The target with the most matching words wins.
    """
    said = set(words(text))
    best, best_score = None, 0
    for t in targets:
        name = words(base_name(t["product"]))
        score = sum(1 for w in name if w in said)
        if score >= min(2, len(name)) and score > best_score:
            best, best_score = t, score
    return best


def pack_of(product: str) -> dict | None:
    """'lactose-free milk 1 L' -> 1 litre, 'rye bread 500 g' -> 0.5 kilogram. None if the text names no size."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(ml|l|kg|g)\b", product.lower())
    if not m:
        return None
    unit, scale = UNIT_SCALE[m.group(2)]
    return {"amount": float(m.group(1).replace(",", ".")) * scale, "unit": unit}


def same_pack(a: dict | None, b: dict | None) -> bool:
    return bool(a and b) and a["unit"] == b["unit"] and abs(a["amount"] - b["amount"]) < 1e-6


def parse_deadline(text: str, now: datetime) -> datetime | None:
    """'by Monday' -> next Monday 07:00, 'by tomorrow 12:00' -> tomorrow 12:00, 'by 17.11.' -> 17 Nov 07:00."""
    m = DEADLINE.search(text.lower())
    if not m:
        return None
    word, hh, mm = m.group(1), int(m.group(2) or NEEDED_HOUR), int(m.group(3) or 0)
    if word == "today":
        day = now.date()
    elif word == "tomorrow":
        day = now.date() + timedelta(days=1)
    elif word in WEEKDAYS:
        ahead = (WEEKDAYS.index(word) - now.weekday()) % 7 or 7
        day = now.date() + timedelta(days=ahead)
    else:
        d, mo = (int(x) for x in word.strip(".").split("."))
        day = date(now.year, mo, d)
        if day < now.date():  # the date passed this year, so the next one is meant
            day = date(now.year + 1, mo, d)
    return datetime.combine(day, time(hh, mm), tzinfo=now.tzinfo)


def units_for(text: str, product: str) -> int | None:
    """Turn '1,000 litres' into a count of the product's packs, for example 1000 packs of 1 L."""
    qty = parse_quantity(text)
    if qty is None:
        return None
    m = re.search(r"\d[\d,\s]*\s*(ml|l|litres?|liters?|kg|kilos?|g)\b", text.lower())
    pack = pack_of(product)
    if not m or not pack:
        return qty  # a plain count of packs
    asked_unit, asked_scale = UNIT_SCALE[m.group(1)]
    if asked_unit != pack["unit"]:
        return qty
    return max(1, math.ceil(qty * asked_scale / pack["amount"] - 1e-9))


def parse_quantity(text: str) -> int | None:
    m = re.search(r"(\d[\d,\s]*)", text)
    if m:
        return int(re.sub(r"[,\s]", "", m.group(1)))
    for w, n in NUMBER_WORDS.items():
        if re.search(rf"\b{w}\b", text.lower()):
            return n
    return None


def context_to_dict(ctx: R.Context) -> dict:
    d = asdict(ctx)
    d["needed_by"] = ctx.needed_by.isoformat()
    return d


def context_from_dict(d: dict) -> R.Context:
    return R.Context(**(d | {"needed_by": datetime.fromisoformat(d["needed_by"])}))
