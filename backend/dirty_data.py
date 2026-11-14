"""Count the real Valio catalog under a kitchen's banned allergens.

    uv run python -m backend.dirty_data [rules file]

Every product goes through the rule check as one line, with the must-have rules off, so the
counts show the allergen rules alone. "Missed" cannot be counted: that needs a sample that a
person checked by hand. The last lines print 20 "clear" products to check by hand.

The count also says where the products with no allergen data sit: in which category groups and
with how many suppliers. A wholesaler closes that gap with its suppliers, not with the chef.
"""

from __future__ import annotations

import random
import sys
from collections import Counter
from datetime import datetime

from . import rules as R
from .valio import PRODUCT_FILE, ROOT, ValioCatalog

DEFAULT_RULES = ROOT / "rules" / "school_kitchen_valio.yaml"

# Category groups (first two digits of the category code) that we read as not food, from the product
# names: 20 kitchen equipment, 63 cleaning and hygiene, 64 disposables and packaging, 67 seasonal goods
# and newspapers, 90 office supplies. The data has no category names, so this is our reading.
NON_FOOD_READING = ("20", "63", "64", "67", "90")


def count(catalog, ruleset: dict) -> dict:
    """Results and reasons over every product of the catalog."""
    ruleset = ruleset | {"unknown_allergens": R.NEEDS_APPROVAL, "must_have": []}
    ctx = R.Context(slot_group="any", needed_by=R.needed_by(ruleset, datetime.now(R.TZ)))
    results, why, clear, no_data = Counter(), Counter(), [], []
    for p in catalog.products.values():
        d = R.check_line(R.Line(p, 1, 1.0, ctx.needed_by), ruleset, ctx)
        results[d.result] += 1
        if d.result == R.ALLOWED:
            clear.append(p)
        elif d.result == R.NEEDS_APPROVAL:
            text = " ".join(d.reasons)
            reason = ("no allergen data" if "no allergen data" in text else "undeclared" if "undeclared" in text
                      else "only the ingredient text names it" if "ingredient text" in text else "other")
            why[reason] += 1
            if reason == "no allergen data":
                no_data.append(p)
    return {"products": len(catalog.products), "results": dict(results), "to_a_person": dict(why), "clear": clear,
            "no_data": where_the_gap_is(catalog, no_data)}


def where_the_gap_is(catalog, no_data: list[dict]) -> dict:
    """The products with no allergen data, by our non-food reading and by supplier."""
    non_food = sum(str(p["product_group"]).startswith(NON_FOOD_READING) for p in no_data)
    all_by, gap_by = Counter(p["supplier"] for p in catalog.products.values()), Counter(p["supplier"] for p in no_data)
    half, running = 0, 0
    for _, k in gap_by.most_common():
        half, running = half + 1, running + k
        if running * 2 >= len(no_data):
            break
    silent = [s for s, k in gap_by.items() if k == all_by[s]]  # suppliers with no allergen data on any product
    return {"non_food": non_food, "food": len(no_data) - non_food, "suppliers": len(all_by),
            "suppliers_with_a_gap": len(gap_by), "suppliers_for_half": half if no_data else 0,
            "suppliers_with_no_data_at_all": len(silent), "their_products": sum(gap_by[s] for s in silent)}


def main(rules_file: str | None = None) -> int:
    data = ROOT / "data" / "valio"
    if not (data / PRODUCT_FILE).exists():
        print(f"The product file is not in {data}. Download it from the Valio portal.")
        return 2
    ruleset = R.load_rules(rules_file or DEFAULT_RULES)
    c = count(ValioCatalog(data, "https://not-used.invalid", "no-key-needed"), ruleset)
    n = c["products"]
    print(f"{n} products under the bans: {', '.join(ruleset['banned_allergens'])}")
    for name, key in (("forbidden", R.FORBIDDEN), ("to a person", R.NEEDS_APPROVAL), ("clear", R.ALLOWED)):
        k = c["results"].get(key, 0)
        print(f"  {name:12} {k:6}  {100 * k / n:4.1f}%")
    for reason, k in sorted(c["to_a_person"].items(), key=lambda x: -x[1]):
        print(f"    to a person, {reason}: {k}")
    g = c["no_data"]
    print(f"The {g['non_food'] + g['food']} products with no allergen data:")
    print(f"  {g['non_food']} are in category groups that we read as not food ({', '.join(NON_FOOD_READING)}), {g['food']} are food")
    print(f"  they come from {g['suppliers_with_a_gap']} of {g['suppliers']} suppliers, and {g['suppliers_for_half']} suppliers hold half")
    print(f"  {g['suppliers_with_no_data_at_all']} suppliers give no allergen data on any product ({g['their_products']} products)")
    print("Missed products cannot be counted without a hand-checked sample. Check these 20 clear products by hand:")
    for p in random.Random(14).sample(c["clear"], min(20, len(c["clear"]))):
        print(f"  {p['id']}  {p['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
