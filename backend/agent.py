"""Buying pipeline. Fixed code, no language model yet.

The pipeline searches, compares and proposes. backend.rules decides.
"""

from __future__ import annotations

import math
import re
import threading
import uuid
from datetime import date, datetime, time, timedelta

from . import rules as R
from .audit import AuditLog
from .valio import Claim

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "ten": 10, "twenty": 20, "hundred": 100}


class Agent:
    def __init__(self, catalog, ruleset: dict, log: AuditLog):
        self.catalog = catalog
        self.rules = ruleset
        self.log = log
        self.lock = threading.Lock()
        self.approvals: dict[str, dict] = {}
        self.weekly_spent_eur = 0.0
        self.last_order_qty: dict[int, int] = {}
        self.pending_slots: set[int] = set()
        self.auto_limit_eur: float | None = None
        self.temperature_c: float | None = None

    # Entry points ---------------------------------------------------------

    def handle_event(self, ev: dict) -> dict:
        kind = ev.get("type")
        if ev.get("auto_limit_eur") is not None:
            self.auto_limit_eur = float(ev["auto_limit_eur"])
        if ev.get("temperature_c") is not None:
            self.temperature_c = float(ev["temperature_c"])

        if kind == "temperature_alarm":
            self.log.write("alarm", {"temperature_c": ev["temperature_c"], "limit_c": ev.get("limit_c")}, "device")
            return {"status": "logged"}
        if kind == "status":
            return {"status": "ok"}
        if kind not in ("slot_state", "manual_low"):
            return {"status": "ignored", "reason": f"unknown event type {kind}"}

        slot = int(ev["slot"])
        target = R.target_for_slot(self.rules, slot)
        if target is None:
            return {"status": "ignored", "reason": f"slot {slot} has no target in the rule set"}
        state = "low" if kind == "manual_low" else ev.get("state")
        units = int(ev.get("estimated_units") or 0) if state != "empty" else 0
        if state == "full" or (state == "low" and kind == "slot_state" and units >= target["reorder_below"]):
            self.pending_slots.discard(slot)
            return {"status": "ok", "reason": "stock above the reorder level"}
        if slot in self.pending_slots:
            return {"status": "ignored", "reason": f"slot {slot} already has an order or a request in progress"}
        self.pending_slots.add(slot)

        run = uuid.uuid4().hex[:8]
        how = "knob press" if kind == "manual_low" else "camera"
        self.log.write(
            "trigger",
            {"summary": f"Slot {slot} ({target['product']}) is {state} by {how}, about {units} units left.", "event": ev},
            "device", self.rules["version"], run_id=run,
        )
        return self.buy(target, units, target["target_units"] - units, run)

    def handle_request(self, text: str, product_id: str | None = None) -> dict:
        """A text request from the chef, for example '1,000 litres of lactose-free milk by Monday'."""
        run = uuid.uuid4().hex[:8]
        target = next((t for t in self.rules["targets"] if base_name(t["product"]) in text.lower()), None)
        qty = units_for(text, target["product"] if target else "") or 1
        deadline = parse_deadline(text, datetime.now(R.TZ))
        understood = f"{qty} × {target['product'] if target else 'unknown product'}"
        if deadline:
            understood += f", needed by {deadline:%a %d.%m. %H:%M}"
        self.log.write("trigger", {"summary": f"The chef asked: \"{text}\". Understood as {understood}.", "text": text,
                                   "quantity": qty, "needed_by": deadline, "product_id": product_id}, "chef",
                       self.rules["version"], run_id=run)
        if product_id:
            # The chef (or a judge) names one product. It still goes through the rule check.
            group = target["group"] if target else "unknown"
            return self.buy_exact(product_id, qty, group, run)
        if target is None:
            self.log.write("blocked", {"summary": "No product in the rule set matches the request."}, "agent", run_id=run)
            return {"status": "no_match"}
        return self.buy(target, 0, qty, run, standing=False, deadline=deadline)

    def decide(self, approval_id: str, action: str, option: int | None, approver: str) -> dict:
        with self.lock:
            a = self.approvals.get(approval_id)
            if a is None or a["status"] != "open":
                return {"status": "not_open"}
            a["status"] = {"approve": "approved", "skip": "skipped"}.get(action, action)
        self.log.write("approval", {"status": a["status"], "option": option, "approval_id": approval_id}, approver,
                       self.rules["version"], run_id=a["run_id"])
        if action == "skip":
            self.pending_slots.discard(a["slot"])
            return {"status": "skipped"}
        idx = option if option is not None else 0
        if not 0 <= idx < len(a["options"]):
            return {"status": "bad_option"}
        opt = a["options"][idx]
        return self._order(opt["lines"], a["ctx"], a["run_id"], a["slot"], approved_by=approver,
                           approved_total=opt["total_eur"])

    def open_approvals(self) -> list[dict]:
        return [{k: v for k, v in a.items() if k != "ctx"} for a in self.approvals.values() if a["status"] == "open"]

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
        )

    def _quote(self, product_id: str, qty: int, ctx: R.Context, run: str) -> dict | None:
        p: Claim = self.catalog.product(product_id)
        if p.value is None:
            return None
        a: Claim = self.catalog.availability(product_id, qty, ctx.needed_by)
        return {"product": p.value, "unit_price_eur": a.value["unit_price_eur"], "available": a.value["available_units"],
                "delivery": a.value["delivery"], "claims": [p.to_dict(), a.to_dict()]}

    def _line(self, q: dict, qty: int, usual: bool) -> R.Line:
        return R.Line(q["product"], qty, q["unit_price_eur"], datetime.fromisoformat(q["delivery"]), usual)

    def buy(self, target: dict, units: int, need: int, run: str, standing: bool = True,
            deadline: datetime | None = None) -> dict:
        ctx = self._ctx(target, units, standing, deadline)
        v = self.rules["version"]
        query = base_name(target["product"])
        found: Claim = self.catalog.search(query)
        self.log.write("search", {"query": query, "count": len(found.value), "ids": [p["id"] for p in found.value],
                                  "checked_at": found.checked_at, "sha256": found.sha256}, "agent", v, run_id=run)

        quotes = {}
        for p in found.value:
            q = self._quote(p["id"], need, ctx, run)
            if q:
                quotes[p["id"]] = q
        self.log.write("candidates", {"need": need, "candidates": [
            {"id": pid, "price_eur": q["unit_price_eur"], "available": q["available"], "delivery": q["delivery"],
             "checked_at": q["claims"][1]["checked_at"], "sha256": q["claims"][1]["sha256"]} for pid, q in quotes.items()]},
            "agent", v, run_id=run)

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
        if usual_q and usual_q["available"] >= need:
            options.append(("usual", [(usual_q, need, True)]))
        else:
            have = usual_q["available"] if usual_q else 0
            rest = need - have
            base = [(usual_q, have, True)] if have else []
            ranked = rank_substitutes(safe, target, ctx)
            seen = set()
            for label, lead in ranked:
                # Fill the gap with the lead substitute first, then the others in rank order.
                parts, left = list(base), rest
                for q in [lead] + [q for _, q in ranked if q is not lead] + rest_by_rank(safe, target, ranked):
                    if left <= 0:
                        break
                    n = min(left, q["available"])
                    if n > 0 and all(q is not p[0] for p in parts):
                        parts.append((q, n, False))
                        left -= n
                key = tuple((q["product"]["id"], n) for q, n, _ in parts)
                if left <= 0 and key not in seen:
                    seen.add(key)
                    options.append((label, parts))

        if not options:
            self.log.write("blocked", {"summary": f"No safe product can fill {need} units of {target['product']}."},
                           "agent", v, run_id=run)
            self.pending_slots.discard(target["slot"])
            return {"status": "blocked"}

        checked = []
        for label, parts in options[:3]:
            lines = [self._line(q, n, u) for q, n, u in parts]
            d = R.check(lines, self.rules, ctx)
            names = " + ".join(f"{n} × {q['product']['name']}" for q, n, u in parts)
            self.log.write("rule_check", {"subject": names, "option": label, **d.to_dict()}, "rule_check", v, run_id=run)
            checked.append({
                "label": label,
                "result": d.result,
                "reasons": d.reasons,
                "summary": names,
                "trade_off": trade_off(label, parts, ctx),
                "total_eur": round(sum(l.total_eur or 0 for l in lines), 2),
                "lines": [{"product_id": q["product"]["id"], "name": q["product"]["name"], "quantity": n, "usual": u,
                           "unit_price_eur": q["unit_price_eur"], "delivery": q["delivery"],
                           "price_checked_at": q["claims"][1]["checked_at"], "sha256": q["claims"][1]["sha256"]}
                          for q, n, u in parts],
            })

        if checked[0]["result"] == R.ALLOWED:
            return self._order(checked[0]["lines"], ctx, run, target["slot"], approved_by=None,
                               approved_total=checked[0]["total_eur"])
        return self._ask(checked, ctx, run, target["slot"], target["product"])

    def buy_exact(self, product_id: str, qty: int, group: str, run: str) -> dict:
        v = self.rules["version"]
        ctx = R.Context(slot_group=group, needed_by=R.needed_by(self.rules, datetime.now(R.TZ)),
                        weekly_spent_eur=self.weekly_spent_eur, auto_limit_eur=self.auto_limit_eur)
        q = self._quote(product_id, qty, ctx, run)
        if q is None:
            self.log.write("blocked", {"summary": f"Product {product_id} is not in the catalog."}, "agent", v, run_id=run)
            return {"status": "no_match"}
        lines = [self._line(q, qty, False)]
        d = R.check(lines, self.rules, ctx)
        self.log.write("rule_check", {"subject": f"{qty} × {q['product']['name']}", **d.to_dict()}, "rule_check", v, run_id=run)
        if d.result == R.FORBIDDEN:
            self.log.write("blocked", {"summary": f"{q['product']['name']} is forbidden: {'; '.join(d.reasons)}. "
                                       "Nobody can approve it. Only a new signed rule set can change this."},
                           "rule_check", v, run_id=run)
            return {"status": "forbidden", "reasons": d.reasons, "rule_set_version": v}
        opt = {"label": "requested", "result": d.result, "reasons": d.reasons, "summary": f"{qty} × {q['product']['name']}",
               "trade_off": "The product you asked for.", "total_eur": lines[0].total_eur,
               "lines": [{"product_id": product_id, "name": q["product"]["name"], "quantity": qty, "usual": False,
                          "unit_price_eur": q["unit_price_eur"], "delivery": q["delivery"],
                          "price_checked_at": q["claims"][1]["checked_at"], "sha256": q["claims"][1]["sha256"]}]}
        if d.result == R.ALLOWED:
            return self._order(opt["lines"], ctx, run, None, None, opt["total_eur"])
        return self._ask([opt], ctx, run, None, q["product"]["name"])

    def _ask(self, options: list[dict], ctx: R.Context, run: str, slot: int | None, what: str) -> dict:
        aid = uuid.uuid4().hex[:6]
        a = {"id": aid, "status": "open", "slot": slot, "what": what, "options": options, "run_id": run,
             "approver": self.rules.get("approvers", {}).get("default", "Head chef"),
             "created": datetime.now(R.TZ).isoformat(timespec="seconds"), "ctx": ctx}
        with self.lock:
            self.approvals[aid] = a
        self.log.write("approval", {"status": "open", "approval_id": aid, "approver": a["approver"],
                                    "options": [{"label": o["label"], "summary": o["summary"], "total_eur": o["total_eur"],
                                                 "result": o["result"]} for o in options]},
                       "agent", self.rules["version"], run_id=run)
        return {"status": "needs_approval", "approval_id": aid}

    def _order(self, lines: list[dict], ctx: R.Context, run: str, slot: int | None,
               approved_by: str | None, approved_total: float) -> dict:
        v = self.rules["version"]
        # Step 8: ask price and stock again just before the order.
        fresh, changes = [], []
        for l in lines:
            a = self.catalog.availability(l["product_id"], l["quantity"], ctx.needed_by)
            p = self.catalog.product(l["product_id"])
            if a.value["unit_price_eur"] != l["unit_price_eur"]:
                changes.append(f"{l['name']} price {l['unit_price_eur']} → {a.value['unit_price_eur']}")
            if a.value["available_units"] < l["quantity"]:
                changes.append(f"{l['name']} only {a.value['available_units']} of {l['quantity']} left")
            fresh.append((p.value, a.value, l))
        self.log.write("recheck", {"summary": "; ".join(changes) or "nothing changed.", "changes": changes}, "agent", v, run_id=run)

        if changes:
            rl = [R.Line(p, min(l["quantity"], a["available_units"]), a["unit_price_eur"],
                         datetime.fromisoformat(a["delivery"]), l["usual"]) for p, a, l in fresh]
            d = R.check(rl, self.rules, ctx)
            self.log.write("rule_check", {"subject": "order after recheck", **d.to_dict()}, "rule_check", v, run_id=run)
            new_total = round(sum(x.total_eur or 0 for x in rl), 2)
            if d.result == R.FORBIDDEN:
                self.log.write("blocked", {"summary": "The recheck made the order forbidden."}, "rule_check", v, run_id=run)
                return {"status": "forbidden", "reasons": d.reasons}
            if d.result == R.NEEDS_APPROVAL and (approved_by is None or new_total > approved_total):
                return self._ask([{"label": "after recheck", "result": d.result, "reasons": d.reasons,
                                   "summary": "Order changed after the recheck", "trade_off": "; ".join(changes),
                                   "total_eur": new_total, "lines": lines}], ctx, run, slot, "changed order")

        claim = self.catalog.place_order([{"product_id": l["product_id"], "quantity": l["quantity"]} for l in lines])
        order_id = claim.value["order_id"]
        total = round(sum(l["unit_price_eur"] * l["quantity"] for l in lines), 2)
        self.weekly_spent_eur += total
        if slot is not None:
            # The slot stays pending until the camera sees it full again, so one empty shelf makes one order.
            self.last_order_qty[slot] = sum(l["quantity"] for l in lines)
        summary = ", ".join(f"{l['quantity']} × {l['name']}" for l in lines) + f", €{total:.2f}"
        summary += f", approved by {approved_by}." if approved_by else ", within the auto-order rules."
        self.log.write("order", {"summary": summary, "lines": lines, "total_eur": total, "sha256": claim.sha256},
                       approved_by or "agent", v, order_id=order_id, run_id=run)
        return {"status": "ordered", "order_id": order_id, "total_eur": total}


def rest_by_rank(safe: dict, target: dict, ranked: list) -> list[dict]:
    chosen = [q for _, q in ranked]
    return sorted((q for pid, q in safe.items() if pid != target.get("product_id") and q not in chosen),
                  key=lambda q: q["unit_price_eur"])


def rank_substitutes(safe: dict, target: dict, ctx: R.Context) -> list[tuple[str, dict]]:
    """best: closest match, better: close match with a better price or date, good: cheapest safe option in time."""
    subs = [q for pid, q in safe.items() if pid != target.get("product_id") and q["available"] > 0]
    if not subs:
        return []
    def closeness(q):
        p = q["product"]
        return (p.get("substitute_group", "").startswith(target["group"]), datetime.fromisoformat(q["delivery"]) <= ctx.needed_by)
    by_match = sorted(subs, key=lambda q: (closeness(q), -q["unit_price_eur"]), reverse=True)
    best = by_match[0]
    out = [("best", best)]
    better = next((q for q in by_match[1:] if closeness(q)[0] and (
        q["unit_price_eur"] < best["unit_price_eur"] or q["delivery"] < best["delivery"])), None)
    if better:
        out.append(("better", better))
    in_time = [q for q in subs if datetime.fromisoformat(q["delivery"]) <= ctx.needed_by and q not in (best, better)]
    if in_time:
        out.append(("good", min(in_time, key=lambda q: q["unit_price_eur"])))
    return out


def trade_off(label: str, parts: list, ctx: R.Context) -> str:
    q, n, _ = next((x for x in parts if not x[2]), parts[0])
    p = q["product"]
    late = datetime.fromisoformat(q["delivery"]) > ctx.needed_by
    when = f"arrives {datetime.fromisoformat(q['delivery']):%a %H:%M}" + (" (late)" if late else "")
    if label == "usual":
        return f"The usual product, €{q['unit_price_eur']:.2f} each, {when}."
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


def parse_deadline(text: str, now: datetime) -> datetime | None:
    """'by Monday' -> next Monday 07:00, 'by tomorrow 12:00' -> tomorrow 12:00, 'by 17.11.' -> 17 Nov 07:00."""
    t = text.lower()
    m = re.search(r"\bby\s+(today|tomorrow|" + "|".join(WEEKDAYS) + r"|\d{1,2}\.\d{1,2}\.?)(?:\s+(?:at\s+)?(\d{1,2})[:.](\d{2}))?", t)
    if not m:
        return None
    word, hh, mm = m.group(1), int(m.group(2) or 7), int(m.group(3) or 0)
    if word == "today":
        day = now.date()
    elif word == "tomorrow":
        day = now.date() + timedelta(days=1)
    elif word in WEEKDAYS:
        ahead = (WEEKDAYS.index(word) - now.weekday()) % 7 or 7
        day = now.date() + timedelta(days=ahead)
    else:
        d, mo = (int(x) for x in word.strip(".").split("."))
        day = date(now.year + (1 if mo < now.month else 0), mo, d)
    return datetime.combine(day, time(hh, mm), tzinfo=now.tzinfo)


def units_for(text: str, product: str) -> int | None:
    """Turn '1,000 litres' into a count of the product's packs, for example 1000 packs of 1 L."""
    qty = parse_quantity(text)
    if qty is None:
        return None
    m = re.search(r"\d[\d,\s]*\s*(ml|l|litres?|liters?|kg|kilos?|g)\b", text.lower())
    pack = re.search(r"(\d+(?:[.,]\d+)?)\s*(ml|l|kg|g)\b", product.lower())
    if not m or not pack:
        return qty  # a plain count of packs
    asked_dim, asked_scale = UNIT_SCALE[m.group(1)]
    pack_dim, pack_scale = UNIT_SCALE[pack.group(2)]
    if asked_dim != pack_dim:
        return qty
    pack_size = float(pack.group(1).replace(",", ".")) * pack_scale
    return max(1, math.ceil(qty * asked_scale / pack_size - 1e-9))


def parse_quantity(text: str) -> int | None:
    m = re.search(r"(\d[\d,\s]*)", text)
    if m:
        return int(re.sub(r"[,\s]", "", m.group(1)))
    for w, n in NUMBER_WORDS.items():
        if re.search(rf"\b{w}\b", text.lower()):
            return n
    return None
