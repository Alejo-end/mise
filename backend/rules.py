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
    if rules.get("unknown_allergens", FORBIDDEN) not in (FORBIDDEN, NEEDS_APPROVAL):
        raise ValueError(f"rule set {path}: unknown_allergens must be {FORBIDDEN} or {NEEDS_APPROVAL}")
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
        at = datetime.fromisoformat(spec)
        return at if at.tzinfo else at.replace(tzinfo=TZ)
    hh, mm = (int(x) for x in spec.split()[-1].split(":"))
    day = now.date() + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return datetime.combine(day, time(hh, mm), tzinfo=now.tzinfo or TZ)


def is_approved_substitute(product: dict, rules: dict, slot_group: str) -> bool:
    """True when the rule set approves the product as a substitute for the slot's group.

    An entry of approved_substitute_groups is a substitute group name or one exact product id.
    """
    approved = {str(x) for x in rules.get("approved_substitute_groups", {}).get(slot_group) or []}
    return str(product.get("substitute_group")) in approved or str(product.get("id")) in approved


@dataclass
class Line:
    """One product line of a proposal."""

    product: dict
    quantity: int
    unit_price_eur: float | None
    delivery: datetime | None
    is_usual: bool = True
    stock_known: bool = True  # False when the supplier ships the product and the wholesaler cannot count it

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
    label_checks: dict = field(default_factory=dict)  # label_key of a product -> who checked its label, and when


@dataclass
class Decision:
    result: str
    reasons: list[str] = field(default_factory=list)
    rule_set_version: int | None = None

    def to_dict(self) -> dict:
        return {"result": self.result, "reasons": self.reasons, "rule_set_version": self.rule_set_version}


def _worst(a: str, b: str) -> str:
    return a if _SEVERITY[a] >= _SEVERITY[b] else b


LABEL_FIELDS = ("id", "name", "allergens", "traces", "undeclared", "text_allergens", "allergen_conflicts", "properties")


def label_key(product: dict) -> str:
    """A fingerprint of what a label check covers. It changes when the name or the allergen data of the product changes."""
    seen = {k: product.get(k) for k in LABEL_FIELDS}
    return hashlib.sha256(json.dumps(seen, sort_keys=True, default=str).encode()).hexdigest()


def safety(p: dict, rules: dict) -> tuple[list[str], list[str]]:
    """The safety rules for one product: (reasons that forbid it, reasons a person must check its label).

    The rule set's `unknown_allergens` says what happens when the manufacturer gave no allergen
    data, or left a banned allergen undeclared: `forbidden` (the default) or `needs_approval`.
    A banned allergen that only the ingredient text names always goes to a person.
    """
    name = p.get("name", p.get("id", "product"))
    forbidden, person = [], []
    to_person = rules.get("unknown_allergens", FORBIDDEN) == NEEDS_APPROVAL

    allergens, traces = p.get("allergens"), p.get("traces")
    if allergens is None or traces is None:
        if not to_person:
            return [f"{name}: allergen data is missing, so it counts as unsafe"], []
        person.append(f"{name}: the manufacturer gave no allergen data, so a person must check the label")
        allergens, traces = allergens or [], traces or []
    banned = {a.lower() for a in rules["banned_allergens"]}
    for a in allergens:
        if a.lower() in banned:
            forbidden.append(f"{name} contains {a}, which this kitchen bans")
    for a in traces:
        if a.lower() in banned:
            forbidden.append(f"{name} may contain traces of {a}, which this kitchen bans")
    for a in p.get("undeclared") or []:
        if a.lower() in banned:
            if to_person:
                person.append(f"{name}: the manufacturer left {a} undeclared, so a person must check the label")
            else:
                forbidden.append(f"{name}: the manufacturer left {a} undeclared, so it counts as unsafe")
    declared = {a.lower() for a in allergens} | {a.lower() for a in traces}
    for a in p.get("text_allergens") or []:
        if a.lower() in banned and a.lower() not in declared:
            person.append(f"{name}: the ingredient text names {a}, but the allergen field does not, "
                          "so a person must check the label")

    props = {x.lower() for x in p.get("properties", [])}
    conflicts = {a.lower() for a in p.get("allergen_conflicts") or []}
    for rule in rules.get("must_have", []):
        if str(p.get("product_group")) != str(rule["product_group"]):
            continue
        wanted = rule["property"]
        if wanted not in props:
            forbidden.append(f"{name} is {rule['product_group']} but not {wanted.replace('_', '-')}")
        elif wanted.endswith("_free") and wanted[:-len("_free")] in conflicts:
            person.append(f"{name} claims to be {wanted.replace('_', '-')}, but its allergen field lists "
                          f"{wanted[:-len('_free')]}, so a person must check the label")
    return forbidden, person


def needs_label_check(product: dict, rules: dict) -> bool:
    """True when the product can be ordered only after a person reads its label."""
    forbidden, person = safety(product, rules)
    return bool(person) and not forbidden


def check_line(line: Line, rules: dict, ctx: Context) -> Decision:
    """Safety rules for one product, then substitute, stock, price and delivery.

    A person who approves a product that needs a label check does that check once. After that,
    ctx.label_checks holds the product, and the same product with the same data does not ask again.
    """
    p = line.product
    name = p.get("name", p.get("id", "product"))
    forbidden, person = safety(p, rules)
    if forbidden:
        return Decision(FORBIDDEN, forbidden + person, rules["version"])
    result, reasons = ALLOWED, []
    checked = ctx.label_checks.get(label_key(p)) if person else None
    if checked:
        reasons.append(f"{name}: {checked}")
    elif person:
        result = NEEDS_APPROVAL
        reasons += person

    if not line.is_usual and not is_approved_substitute(p, rules, ctx.slot_group):
        result = NEEDS_APPROVAL
        reasons.append(f"{name} is not the usual product or an approved substitute of any slot in the rule set"
                       if ctx.slot_group == "unknown" else
                       f"{name} is a substitute outside the approved groups for {ctx.slot_group.replace('_', ' ')}")
    if not line.stock_known:
        result = NEEDS_APPROVAL
        reasons.append(f"{name}: the wholesaler does not know the stock, so a person must look")
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
