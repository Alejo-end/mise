"""Rule check in plain code.

The language model may search, compare and explain. This module alone decides
what is allowed. It takes one proposal and returns allowed, needs_approval or
forbidden, always with reasons and the rule set version.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

TZ = ZoneInfo("Europe/Helsinki")

ALLOWED = "allowed"
NEEDS_APPROVAL = "needs_approval"
FORBIDDEN = "forbidden"
_SEVERITY = {ALLOWED: 0, NEEDS_APPROVAL: 1, FORBIDDEN: 2}

DEFAULT_RULES = Path(__file__).resolve().parent.parent / "rules" / "school_kitchen.yaml"


class RuleSetChanged(Exception):
    """The rule file changed but its version did not. A new version must be signed."""


def signature_path(path: Path) -> Path:
    return path.with_suffix(".signed.json")


def load_rules(path: Path | str = DEFAULT_RULES, verify: bool = True) -> dict:
    """Load a rule set. Refuse it if its content no longer matches the hash signed for its version."""
    path = Path(path)
    raw = path.read_bytes()
    rules = yaml.safe_load(raw)
    for key in ("version", "banned_allergens", "targets", "limits"):
        if key not in rules:
            raise ValueError(f"rule set {path} has no '{key}'")
    rules["sha256"] = hashlib.sha256(raw).hexdigest()
    if verify:
        sig = signature_path(path)
        if not sig.exists():
            raise RuleSetChanged(f"{path.name} is not signed. Run: python -m backend.rules sign {path}")
        signed = json.loads(sig.read_text())
        if signed["version"] == rules["version"] and signed["sha256"] != rules["sha256"]:
            raise RuleSetChanged(f"{path.name} changed but is still version {rules['version']}. "
                                 "Raise the version and sign it again.")
        if signed["version"] != rules["version"]:
            raise RuleSetChanged(f"{path.name} is version {rules['version']}, but version {signed['version']} "
                                 f"was signed. Run: python -m backend.rules sign {path}")
    return rules


def sign(path: Path | str) -> dict:
    """Record the version, signer and hash of a rule file. The head chef runs this after reading it."""
    path = Path(path)
    rules = load_rules(path, verify=False)
    record = {"version": rules["version"], "signed_by": rules.get("signed_by"), "sha256": rules["sha256"],
              "signed_at": datetime.now(TZ).isoformat(timespec="seconds")}
    signature_path(path).write_text(json.dumps(record, indent=2) + "\n")
    return record


def target_for_slot(rules: dict, slot: int) -> dict | None:
    return next((t for t in rules["targets"] if t["slot"] == slot), None)


def needed_by(rules: dict, now: datetime) -> datetime:
    """Resolve 'next weekday HH:MM' to a time. Other values must be ISO times."""
    spec = rules.get("delivery", {}).get("latest_needed", "next weekday 07:00")
    if not spec.startswith("next weekday"):
        return datetime.fromisoformat(spec)
    hh, mm = (int(x) for x in spec.split()[-1].split(":"))
    day = now.date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, time(hh, mm), tzinfo=now.tzinfo or TZ)


@dataclass
class Line:
    """One product line of a proposal."""

    product: dict
    quantity: int
    unit_price_eur: float | None
    delivery: datetime | None
    is_usual: bool = True

    @property
    def total_eur(self) -> float | None:
        if self.unit_price_eur is None:
            return None
        return round(self.unit_price_eur * self.quantity, 2)


@dataclass
class Context:
    """Facts the check needs besides the proposal itself."""

    slot_group: str
    needed_by: datetime
    current_units: int = 0
    target_units: int = 0
    weekly_spent_eur: float = 0.0
    auto_limit_eur: float | None = None  # live value from the knob, capped by the signed limit
    previous_standing_qty: int | None = None
    cold_room_alarm_c: float | None = None  # set while the cold room is too warm


@dataclass
class Decision:
    result: str
    reasons: list[str] = field(default_factory=list)
    rule_set_version: int | None = None

    def to_dict(self) -> dict:
        return {"result": self.result, "reasons": self.reasons, "rule_set_version": self.rule_set_version}


def _worst(a: str, b: str) -> str:
    return a if _SEVERITY[a] >= _SEVERITY[b] else b


def check_line(line: Line, rules: dict, ctx: Context) -> Decision:
    """Safety rules for one product. These do not depend on money or time."""
    p = line.product
    name = p.get("name", p.get("id", "product"))
    result, reasons = ALLOWED, []

    allergens, traces = p.get("allergens"), p.get("traces")
    if allergens is None or traces is None:
        return Decision(FORBIDDEN, [f"{name}: allergen data is missing, so it counts as unsafe"], rules["version"])
    banned = {a.lower() for a in rules["banned_allergens"]}
    for a in allergens:
        if a.lower() in banned:
            result = FORBIDDEN
            reasons.append(f"{name} contains {a}, which this kitchen bans")
    for a in traces:
        if a.lower() in banned:
            result = FORBIDDEN
            reasons.append(f"{name} may contain traces of {a}, which this kitchen bans")

    props = {x.lower() for x in p.get("properties", [])}
    for rule in rules.get("must_have", []):
        if p.get("product_group") == rule["product_group"] and rule["property"] not in props:
            result = FORBIDDEN
            reasons.append(f"{name} is {rule['product_group']} but not {rule['property'].replace('_', '-')}")

    if result == FORBIDDEN:
        return Decision(result, reasons, rules["version"])

    if not line.is_usual:
        approved = rules.get("approved_substitute_groups", {}).get(ctx.slot_group, [])
        if p.get("substitute_group") not in approved:
            result = NEEDS_APPROVAL
            reasons.append(f"{name} is a substitute outside the approved groups for {ctx.slot_group.replace('_', ' ')}")
    if line.unit_price_eur is None:
        result = NEEDS_APPROVAL
        reasons.append(f"{name}: no price, so a person must look")
    if line.delivery is None:
        result = NEEDS_APPROVAL
        reasons.append(f"{name}: no delivery time, so a person must look")
    elif line.delivery > ctx.needed_by:
        result = NEEDS_APPROVAL
        reasons.append(
            f"{name} arrives {line.delivery:%a %H:%M}, after the needed time {ctx.needed_by:%a %H:%M}"
        )
    return Decision(result, reasons, rules["version"])


def check(lines: list[Line], rules: dict, ctx: Context) -> Decision:
    """Check a whole proposal: every line, then the order totals."""
    if not lines:
        return Decision(FORBIDDEN, ["the proposal has no lines"], rules["version"])
    result, reasons = ALLOWED, []
    for line in lines:
        d = check_line(line, rules, ctx)
        result = _worst(result, d.result)
        reasons += d.reasons
    if result == FORBIDDEN:
        return Decision(result, reasons, rules["version"])

    limits = rules["limits"]
    signed_limit = float(limits["auto_order_eur"])
    limit = signed_limit if ctx.auto_limit_eur is None else min(float(ctx.auto_limit_eur), signed_limit)
    totals = [l.total_eur for l in lines]
    if None not in totals:
        total = round(sum(totals), 2)
        if total > limit:
            result = NEEDS_APPROVAL
            reasons.append(f"total €{total:.2f} is over the auto-order limit €{limit:.0f}")
        weekly = float(limits.get("weekly_eur", float("inf")))
        if ctx.weekly_spent_eur + total > weekly:
            result = NEEDS_APPROVAL
            reasons.append(
                f"this week would reach €{ctx.weekly_spent_eur + total:.2f}, over the weekly limit €{weekly:.0f}"
            )

    qty = sum(l.quantity for l in lines)
    if ctx.target_units and ctx.current_units + qty > ctx.target_units:
        result = NEEDS_APPROVAL
        reasons.append(f"{qty} units would take stock above the target of {ctx.target_units}")

    growth = float(limits.get("standing_order_growth", 0.15))
    if ctx.previous_standing_qty:
        if qty > ctx.previous_standing_qty * (1 + growth):
            pct = (qty / ctx.previous_standing_qty - 1) * 100
            result = NEEDS_APPROVAL
            reasons.append(f"the standing order grew {pct:.0f}%, more than {growth * 100:.0f}%")

    if ctx.cold_room_alarm_c is not None:
        result = NEEDS_APPROVAL
        reasons.append(f"the cold room is at {ctx.cold_room_alarm_c:.1f} °C, so auto-orders wait for a person")

    if result == ALLOWED:
        reasons.append("usual product or approved substitute, within target stock, under the limits, in time")
    return Decision(result, reasons, rules["version"])


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "sign":
        print(json.dumps(sign(sys.argv[2]), indent=2))
    else:
        print("usage: python -m backend.rules sign <rules.yaml>")
