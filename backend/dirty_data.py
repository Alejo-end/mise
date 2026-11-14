"""Count the real Valio catalog under a kitchen's banned allergens.

    uv run python -m backend.dirty_data [rules file]

Every product goes through the rule check as one line, with the must-have rules off, so the
counts show the allergen rules alone. "Missed" cannot be counted: that needs a sample that a
person checked by hand. The last lines print 20 "clear" products to check by hand.
"""

from __future__ import annotations

import random
import sys
from collections import Counter
from datetime import datetime

from . import rules as R
from .valio import PRODUCT_FILE, ROOT, ValioCatalog

DEFAULT_RULES = ROOT / "rules" / "school_kitchen_valio.yaml"


def count(catalog, ruleset: dict) -> dict:
    """Results and reasons over every product of the catalog."""
    ruleset = ruleset | {"unknown_allergens": R.NEEDS_APPROVAL, "must_have": []}
    ctx = R.Context(slot_group="any", needed_by=R.needed_by(ruleset, datetime.now(R.TZ)))
    results, why, clear = Counter(), Counter(), []
    for p in catalog.products.values():
        d = R.check_line(R.Line(p, 1, 1.0, ctx.needed_by), ruleset, ctx)
        results[d.result] += 1
        if d.result == R.ALLOWED:
            clear.append(p)
        elif d.result == R.NEEDS_APPROVAL:
            text = " ".join(d.reasons)
            why["no allergen data" if "no allergen data" in text else "undeclared" if "undeclared" in text
                else "only the ingredient text names it" if "ingredient text" in text else "other"] += 1
    return {"products": len(catalog.products), "results": dict(results), "to_a_person": dict(why), "clear": clear}


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
    print("Missed products cannot be counted without a hand-checked sample. Check these 20 clear products by hand:")
    for p in random.Random(14).sample(c["clear"], min(20, len(c["clear"]))):
        print(f"  {p['id']}  {p['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
