"""Rule check tests. They need no API. Every forbidden case must be blocked on every run."""

import copy
from datetime import datetime, timedelta

import pytest

from backend.rules import (
    ALLOWED,
    FORBIDDEN,
    NEEDS_APPROVAL,
    TZ,
    Context,
    Line,
    check,
    load_rules,
    needed_by,
)

RULES = load_rules()
NOW = datetime(2026, 11, 13, 21, 0, tzinfo=TZ)  # Friday evening
NEEDED = needed_by(RULES, NOW)  # Monday 07:00
ON_TIME = NEEDED - timedelta(hours=1)
LATE = NEEDED + timedelta(days=1)

LF_MILK = {
    "id": "MOCK-MILK-LF-1L", "name": "Lactose-free milk 1 L", "product_group": "milk",
    "substitute_group": "lactose_free_milk_any_brand", "properties": ["lactose_free"],
    "allergens": ["milk"], "traces": [],
}


def product(**changes):
    p = copy.deepcopy(LF_MILK)
    p.update(changes)
    return p


def ctx(**changes):
    base = dict(slot_group="lactose_free_milk", needed_by=NEEDED, current_units=4, target_units=24)
    base.update(changes)
    return Context(**base)


def line(p=None, qty=20, price=1.89, delivery=ON_TIME, usual=True):
    return Line(product=p or product(), quantity=qty, unit_price_eur=price, delivery=delivery, is_usual=usual)


def result(lines, c=None, rules=RULES):
    return check(lines if isinstance(lines, list) else [lines], rules, c or ctx()).result


# Forbidden: nobody can approve these.

FORBIDDEN_PRODUCTS = {
    "nut traces": product(name="Granola yogurt", traces=["nuts"]),
    "contains nuts": product(name="Almond drink", product_group="plant_drink", allergens=["nuts"]),
    "contains peanuts": product(allergens=["milk", "peanuts"]),
    "contains sesame": product(allergens=["sesame"]),
    "sesame traces": product(traces=["sesame"]),
    "upper-case allergen": product(allergens=["NUTS"]),
    "allergens missing": product(allergens=None),
    "traces missing": product(traces=None),
    "regular milk": product(name="Semi-skimmed milk", properties=[]),
    "milk with other properties only": product(properties=["organic"]),
}


@pytest.mark.parametrize("name", FORBIDDEN_PRODUCTS)
def test_forbidden_products(name):
    p = FORBIDDEN_PRODUCTS[name]
    assert result(line(p, usual=False)) == FORBIDDEN
    assert result(line(p, usual=True)) == FORBIDDEN


@pytest.mark.parametrize("name", FORBIDDEN_PRODUCTS)
def test_forbidden_wins_over_cheap_small_on_time(name):
    """A forbidden line stays forbidden whatever the money and time look like."""
    assert result(line(FORBIDDEN_PRODUCTS[name], qty=1, price=0.01)) == FORBIDDEN


def test_one_forbidden_line_blocks_the_whole_order():
    assert result([line(qty=5), line(product(traces=["nuts"]), qty=5, usual=False)]) == FORBIDDEN


def test_forbidden_reason_is_given():
    d = check([line(product(traces=["nuts"]))], RULES, ctx())
    assert d.result == FORBIDDEN
    assert any("nuts" in r for r in d.reasons)
    assert d.rule_set_version == RULES["version"]


def test_empty_proposal_is_forbidden():
    assert result([]) == FORBIDDEN


# Allowed.

def test_same_product_under_limit_is_allowed():
    assert result(line()) == ALLOWED


def test_approved_substitute_is_allowed():
    assert result(line(product(id="B", name="Brand B"), usual=False)) == ALLOWED


def test_allowed_decision_cites_version():
    assert check([line()], RULES, ctx()).rule_set_version == 3


# Needs approval.

def test_over_auto_limit_needs_approval():
    assert result(line(qty=20, price=8.0)) == NEEDS_APPROVAL  # €160


def test_exactly_at_limit_is_allowed():
    assert result(line(qty=20, price=7.5)) == ALLOWED  # €150


def test_knob_can_lower_the_limit():
    assert result(line(), ctx(auto_limit_eur=20)) == NEEDS_APPROVAL  # €37.80 > €20


def test_knob_cannot_raise_the_signed_limit():
    assert result(line(qty=20, price=8.0), ctx(auto_limit_eur=1000)) == NEEDS_APPROVAL


def test_substitute_outside_approved_groups_needs_approval():
    oat = product(name="Oat drink", product_group="plant_drink", substitute_group="plant_drink_any_brand", allergens=["oats"])
    assert result(line(oat, usual=False)) == NEEDS_APPROVAL


def test_late_delivery_needs_approval():
    assert result(line(delivery=LATE)) == NEEDS_APPROVAL


def test_missing_delivery_needs_approval():
    assert result(line(delivery=None)) == NEEDS_APPROVAL


def test_missing_price_needs_approval():
    assert result(line(price=None)) == NEEDS_APPROVAL


def test_standing_order_grew_20_percent_needs_approval():
    assert result(line(qty=12), ctx(previous_standing_qty=10)) == NEEDS_APPROVAL


def test_standing_order_grew_10_percent_is_allowed():
    assert result(line(qty=11), ctx(previous_standing_qty=10, current_units=0)) == ALLOWED


def test_above_target_stock_needs_approval():
    assert result(line(qty=30)) == NEEDS_APPROVAL


def test_weekly_limit_needs_approval():
    assert result(line(), ctx(weekly_spent_eur=790)) == NEEDS_APPROVAL


def test_split_order_total_counts_against_limit():
    lines = [line(qty=10, price=8.0), line(product(id="B"), qty=10, price=8.0, usual=False)]
    assert result(lines) == NEEDS_APPROVAL


# The rule set itself.

def test_needed_by_skips_weekend():
    assert NEEDED.weekday() == 0 and NEEDED.hour == 7


def test_new_rule_set_version_can_allow_sesame():
    """Only a newly signed rule set can change a forbidden result."""
    v4 = copy.deepcopy(RULES)
    v4["version"] = 4
    v4["banned_allergens"] = ["nuts", "peanuts"]
    d = check([line(product(traces=["sesame"]))], v4, ctx())
    assert d.result == ALLOWED and d.rule_set_version == 4
