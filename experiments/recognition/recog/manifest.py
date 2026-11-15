"""The data manifest of the recognition experiment: reference captures and evaluation recordings, kept apart.

{
  "schema": "recognition-manifest/1",
  "assigned": {"1": "477032", "2": "464316", "3": "463335"},           # rule set v4, slot -> product code
  "products": [{"code": "477032", "name": "rye bread 500 g", "role": "demo" | "lookalike"}, ...],
  "reference": [                                                        # what a model may learn from
    {"source": "catalogue", "code": "463335", "image": "reference/463335/1.jpg", "url": "..."},
    {"source": "capture", "recording": "ref-s3-milk-day", "code": "463335", "slot": 3}
  ],
  "evaluation": [                                                       # what accuracy is measured on
    {"recording": "eval-identity-day", "scenario": "identity",
     "labels": [{"from_t": 0, "to_t": 15, "slots": {"1": "477032", "2": "464316", "3": "463335"}}],
     "expected_requests": {"1": 0, "2": 0, "3": 0}}
  ]
}

Label values per slot: a product code, "empty", "unknown" (a product or object not in the catalogue
options), or "obstructed" (a hand or object covers the slot). Scenarios: identity, unfamiliar, moved,
obstruction, removal, light.
"""

from __future__ import annotations

from .core import EMPTY, OBSTRUCTED, UNKNOWN

SCENARIOS = {"identity", "unfamiliar", "moved", "obstruction", "removal", "light"}


def validate(m: dict) -> list[str]:
    """Problems with a manifest; an empty list means it is usable."""
    errs: list[str] = []
    codes = {p["code"] for p in m.get("products", [])}
    valid = codes | {EMPTY, UNKNOWN, OBSTRUCTED}
    for slot, code in m.get("assigned", {}).items():
        if code not in codes:
            errs.append(f"assigned slot {slot}: {code} is not in products")
    ref_recs = set()
    for r in m.get("reference", []):
        if r.get("source") not in ("catalogue", "capture"):
            errs.append(f"reference {r}: source must be catalogue or capture")
        if r.get("code") not in codes:
            errs.append(f"reference {r}: code not in products")
        if r.get("source") == "capture":
            ref_recs.add(r.get("recording"))
    for e in m.get("evaluation", []):
        rec = e.get("recording")
        if rec in ref_recs:
            errs.append(f"evaluation {rec} is also a reference capture: references never count as evaluation")
        if e.get("source") == "catalogue" or "image" in e:
            errs.append(f"evaluation {rec}: catalogue images are references only, never evaluation data")
        if e.get("scenario") not in SCENARIOS:
            errs.append(f"evaluation {rec}: scenario must be one of {sorted(SCENARIOS)}")
        if not e.get("labels"):
            errs.append(f"evaluation {rec}: no labels")
        for lab in e.get("labels", []):
            if not lab.get("from_t", 0) < lab.get("to_t", 0):
                errs.append(f"evaluation {rec}: label window {lab.get('from_t')}..{lab.get('to_t')} is empty")
            for slot, v in lab.get("slots", {}).items():
                if v not in valid:
                    errs.append(f"evaluation {rec}: slot {slot} label {v!r} is not a product code or "
                                f"{EMPTY}/{UNKNOWN}/{OBSTRUCTED}")
        if "expected_requests" not in e:
            errs.append(f"evaluation {rec}: expected_requests missing (purchase requests a person expects per slot)")
    return errs


def truth_at(evaluation: dict, t: float, slot: int) -> str | None:
    """The labelled content of a slot at time t, or None when no label window covers t."""
    for lab in evaluation.get("labels", []):
        if lab["from_t"] <= t < lab["to_t"]:
            return lab["slots"].get(str(slot))
    return None
