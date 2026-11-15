"""How a recognition prediction could combine with the two-photo detector (a proposal, not wired in).

The rule: recognition may only VETO a reading, turning it into "uncertain" (never a stock change, as
interface 2 says). It never creates a stock change on its own. So it can remove false replenishments but
can also hide a real shortage (a false veto), which the scorer counts as a missed request.

Per frame, per slot, given the baseline reading (state, fill, why), the slot's assigned product code and
the model's label for every slot in the same frame:
1. no prediction, or the baseline is already uncertain or unset: keep the baseline;
2. the assigned product: keep the baseline;
3. "empty": keep an empty or low reading; a "full" reading becomes uncertain;
4. another catalogue product: uncertain, "wrong product in slot" (identity follows the product);
5. "unknown": uncertain;
6. after 1-5, an empty or low reading becomes uncertain when the slot's assigned product is seen in another
   slot of the same frame: the product moved, it did not run out, so no replenishment.
"""

from __future__ import annotations

from .core import EMPTY, UNKNOWN

Reading = tuple  # (state, fill, why)


def gate_frame(baseline: dict[int, Reading], assigned: dict[int, str], preds: dict[int, str]) -> dict[int, Reading]:
    out: dict[int, Reading] = {}
    for slot, (st, fill, why) in baseline.items():
        p = preds.get(slot)
        if p is None or st is None or st == "uncertain" or p == assigned.get(slot):
            out[slot] = (st, fill, why)
        elif p == EMPTY:
            out[slot] = (st, fill, why) if st in ("empty", "low") else ("uncertain", fill, f"model sees empty, photos say {st}")
        elif p == UNKNOWN:
            out[slot] = ("uncertain", fill, "model does not recognise the slot content")
        else:
            out[slot] = ("uncertain", fill, f"wrong product in slot: {p}")
    for slot, (st, fill, why) in list(out.items()):
        if st in ("empty", "low"):
            elsewhere = [s for s, p in preds.items() if s != slot and p == assigned.get(slot)]
            if elsewhere:
                out[slot] = ("uncertain", fill, f"product {assigned[slot]} seen in slot {elsewhere[0]}: moved, not used up")
    return out
