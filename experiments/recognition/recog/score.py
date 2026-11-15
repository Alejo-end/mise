"""Scores for the recognition experiment. Inputs are prediction records and the manifest's labels.

A prediction record: {"recording", "frame", "t", "slot", "label", "model"}; label is a product code,
"empty" or "unknown". Ground truth comes from manifest.truth_at().
"""

from __future__ import annotations

from collections import Counter, defaultdict

from .core import EMPTY, OBSTRUCTED, UNKNOWN
from .manifest import truth_at


def _rate(n: int, d: int):
    return None if d == 0 else round(n / d, 4)


def score_predictions(preds: list[dict], evaluation: dict, assigned: dict[str, str], codes: set[str]) -> dict:
    c: Counter = Counter()
    confusion: dict[str, Counter] = defaultdict(Counter)
    for p in preds:
        truth = truth_at(evaluation, p["t"], p["slot"])
        if truth is None:
            c["unlabelled"] += 1
            continue
        pred = p["label"]
        confusion[truth][pred] += 1
        if truth in codes:
            c["product_total"] += 1
            c["product_correct"] += pred == truth
            c["product_false_unknown"] += pred == UNKNOWN
            c["product_wrong_product"] += pred in codes and pred != truth
            if assigned.get(str(p["slot"])) != truth:  # a product in a slot that is not its own: moved
                c["moved_total"] += 1
                c["moved_identity_follows"] += pred == truth
        elif truth == UNKNOWN:
            c["unfamiliar_total"] += 1
            c["unfamiliar_said_unknown"] += pred == UNKNOWN
            c["unfamiliar_said_product"] += pred in codes  # the dangerous error
        elif truth == OBSTRUCTED:
            c["obstructed_total"] += 1
            c["obstructed_said_unknown"] += pred == UNKNOWN
            c["obstructed_said_empty"] += pred == EMPTY  # would look like a shortage
        elif truth == EMPTY:
            c["empty_total"] += 1
            c["empty_correct"] += pred == EMPTY
    return {
        "counts": dict(c),
        "identity_accuracy": _rate(c["product_correct"], c["product_total"]),
        "false_unknown_rate": _rate(c["product_false_unknown"], c["product_total"]),
        "wrong_product_rate": _rate(c["product_wrong_product"], c["product_total"]),
        "moved_identity_follows_rate": _rate(c["moved_identity_follows"], c["moved_total"]),
        "unfamiliar_unknown_rate": _rate(c["unfamiliar_said_unknown"], c["unfamiliar_total"]),
        "unfamiliar_hallucinated_product_rate": _rate(c["unfamiliar_said_product"], c["unfamiliar_total"]),
        "obstructed_unknown_rate": _rate(c["obstructed_said_unknown"], c["obstructed_total"]),
        "obstructed_said_empty_rate": _rate(c["obstructed_said_empty"], c["obstructed_total"]),
        "empty_accuracy": _rate(c["empty_correct"], c["empty_total"]),
        "confusion": {k: dict(v) for k, v in confusion.items()},
    }


def score_requests(actual: dict[int, int], expected: dict[str, int]) -> dict:
    """Purchase requests (orders + approvals) per slot after replaying a recording, against what a person expects."""
    out = {}
    for slot in sorted({*actual, *(int(s) for s in expected)}):
        a, e = actual.get(slot, 0), int(expected.get(str(slot), 0))
        out[slot] = {"requests": a, "expected": e, "false": max(0, a - e), "missed": max(0, e - a)}
    out["total_false"] = sum(v["false"] for v in out.values() if isinstance(v, dict))
    out["total_missed"] = sum(v["missed"] for v in out.values() if isinstance(v, dict))
    return out
