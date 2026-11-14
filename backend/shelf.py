"""Shelf configuration: what each slot should hold, kept apart from what the camera last saw.

Two data structures, never mixed:

- An Assignment is the expectation. It comes only from the active, signed rule set: slot, product,
  group, target units, reorder level. Its assignment_id is "v{version}:{product_id}", where version
  is the rule set version that put this product into this slot. The device stores the id it was
  calibrated for; a different id makes that slot's calibration stale (docs/PARALLEL_WORK.md,
  interface 9).
- An Observation is what the device last reported for the slot: state, units, fill, frame. It never
  changes an assignment, and an assignment never changes an observation.

A product change is a new rule set version (rules.write_version): version + 1, signed like
`python -m backend.rules sign`, written to a folder next to the audit log, logged, then activated.
Nothing edits the active rule set in place. Slot rectangles belong to the device; nothing here
edits them.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from . import rules as R


def versions_dir_for(audit_db: Path | str) -> Path:
    """Rule set versions written by the backend live next to the audit log that records them."""
    audit_db = Path(audit_db)
    return audit_db.parent / f"{audit_db.stem}-rule-versions"


@dataclass(frozen=True)
class Assignment:
    """What a slot should hold, as the active rule set says."""

    slot: int
    product_id: str | None
    product: str
    group: str
    target_units: int
    reorder_below: int
    rule_set_version: int  # the active rule set this assignment is part of
    assigned_in_version: int  # the rule set version that put this product into this slot

    @property
    def assignment_id(self) -> str:
        return f"v{self.assigned_in_version}:{self.product_id}"

    @classmethod
    def from_target(cls, target: dict, rules: dict) -> "Assignment":
        return cls(slot=int(target["slot"]), product_id=None if target.get("product_id") is None else str(target["product_id"]),
                   product=target["product"], group=target["group"], target_units=int(target["target_units"]),
                   reorder_below=int(target["reorder_below"]), rule_set_version=int(rules["version"]),
                   assigned_in_version=int(target.get("assigned_in", rules["version"])))

    def to_dict(self) -> dict:
        return asdict(self) | {"assignment_id": self.assignment_id}


@dataclass(frozen=True)
class Observation:
    """The last slot_state the device sent for a slot. Units come from fill and the assignment when fill is sent."""

    slot: int
    state: str
    units: int | None
    device_units: int | None  # what the device itself estimated; it may count with an old target
    fill: float | None
    frame_id: int | None
    frame_time: str | None
    received_at: str

    def to_dict(self) -> dict:
        return asdict(self)


def assignments(rules: dict) -> list[Assignment]:
    return sorted((Assignment.from_target(t, rules) for t in rules["targets"]), key=lambda a: a.slot)


def assignment_for(rules: dict, slot: int) -> Assignment | None:
    t = R.target_for_slot(rules, slot)
    return Assignment.from_target(t, rules) if t else None


def units_from_fill(state: str, fill: float, target_units: int) -> int:
    """Interface 1: units = round(fill x target_units). An empty slot holds 0, as on the device."""
    return 0 if state == "empty" else round(fill * target_units)


class ShelfError(Exception):
    """A shelf change that is refused. status is the HTTP status to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class Shelf:
    """The signed rule set chain, the expected assignments it gives, and the observations from the device."""

    def __init__(self, base: Path | str, versions_dir: Path | str):
        self.base = Path(base)
        self.versions_dir = Path(versions_dir)
        self.chain = R.load_rule_chain(self.base, self.versions_dir)
        self.observations: dict[int, Observation] = {}
        self.lock = threading.Lock()  # one change at a time: read the active version, write the next, activate it

    @property
    def rules(self) -> dict:
        return self.chain[-1]

    def by_version(self, version: int | None) -> dict | None:
        return next((r for r in self.chain if r["version"] == version), None)

    def assignments(self) -> list[Assignment]:
        return assignments(self.rules)

    # Observations -----------------------------------------------------------

    def observe(self, event: dict) -> dict:
        """Keep a slot_state as the slot's observation. Return the event the agent gets.

        A version 2 event (with fill) gets its units from fill and the active assignment, because the
        device may still count with an old target. A version 1 event keeps its estimated_units.
        """
        a = assignment_for(self.rules, int(event["slot"]))
        out = dict(event)
        if event.get("fill") is not None and a is not None:
            out["estimated_units"] = units_from_fill(event["state"], float(event["fill"]), a.target_units)
        self.observations[int(event["slot"])] = Observation(
            slot=int(event["slot"]), state=event["state"], units=out.get("estimated_units"),
            device_units=event.get("estimated_units"), fill=event.get("fill"), frame_id=event.get("frame_id"),
            frame_time=event.get("frame_time"), received_at=datetime.now(R.TZ).isoformat(timespec="seconds"))
        return out

    # The view ---------------------------------------------------------------

    def view(self, pending: Callable[[int], dict | None], health: Callable[[int], dict | None]) -> list[dict]:
        """GET /shelf: per slot the assignment, and apart from it the observation and any pending order."""
        out = []
        for a in self.assignments():
            obs, h, p = self.observations.get(a.slot), health(a.slot), pending(a.slot)
            if p is not None:
                p = dict(p) | {"made_for_current_assignment": p.get("product_id") == a.product_id
                               and (p.get("rule_set_version") or 0) >= a.assigned_in_version}
            observation = None if obs is None and h is None else (obs.to_dict() if obs else {}) | {"health": h}
            out.append(a.to_dict() | {"observation": observation, "pending_order": p})
        return out

    # Changes ----------------------------------------------------------------

    def plan(self, slot: int, change: dict, catalog) -> tuple[list[dict], Assignment, Assignment, list[str]]:
        """Check a change against the catalog and the rules. Return (new targets, before, after, warnings)."""
        rules = self.rules
        before = assignment_for(rules, slot)
        if before is None:
            raise ShelfError(404, f"slot {slot} is not in rule set v{rules['version']}. The shelf has slots "
                                  f"{', '.join(str(a.slot) for a in self.assignments())}; new slots need a person to "
                                  "sign a new rule file")
        pid = str(change["product_id"]).strip()
        product = catalog.product(pid).value
        if product is None:
            raise ShelfError(422, f"product {pid} is not in the catalog '{catalog.name}'")
        other = next((a for a in self.assignments() if a.slot != slot and a.product_id == pid), None)
        if other:
            raise ShelfError(409, f"{product.get('name', pid)} is already the product of slot {other.slot}. "
                                  "Each product has one slot, and each slot one product")
        forbidden, person = R.safety(product, rules)
        if forbidden:
            raise ShelfError(422, f"the rule check forbids {product.get('name', pid)} in this kitchen: "
                                  + "; ".join(forbidden))
        same_product = pid == before.product_id
        group = change.get("group")
        if group is None:
            if same_product or R.is_approved_substitute(product, rules, before.group):
                group = before.group
            else:
                raise ShelfError(422, f"{product.get('name', pid)} is not an approved substitute for "
                                      f"{before.group.replace('_', ' ')}, so slot {slot} needs a new group. Send "
                                      "'group' (its substitutes need approval unless the rule set lists them)")
        target_units = before.target_units if change.get("target_units") is None else int(change["target_units"])
        reorder_below = before.reorder_below if change.get("reorder_below") is None else int(change["reorder_below"])
        if not 0 <= reorder_below < target_units:
            raise ShelfError(422, f"reorder_below ({reorder_below}) must be below target_units ({target_units})")
        name = (change.get("product") or ("" if not same_product else before.product)
                or product.get("name") or pid).strip()

        new_version = rules["version"] + 1
        targets = []
        for t in rules["targets"]:
            t = dict(t)
            t.setdefault("assigned_in", rules["version"])
            if int(t["slot"]) == slot:
                t.update(product=name, product_id=pid, group=group, target_units=target_units,
                         reorder_below=reorder_below)
                if not same_product:
                    t["assigned_in"] = new_version
            targets.append(t)
        after = Assignment.from_target(next(t for t in targets if int(t["slot"]) == slot), {"version": new_version})
        warnings = [f"{p}. Orders of it wait for a person until someone approves its label once" for p in person]
        return targets, before, after, warnings

    def change(self, slot: int, change: dict, approver: str, catalog, log,
               pending: Callable[[int], dict | None], activate: Callable[[dict], None]) -> dict:
        """Sign and activate a new rule set version with one slot changed. Return what happened.

        A pending order of the slot is not touched: it keeps the product and the rule set version it
        was made with. The change and the pending order go into the audit log together.
        """
        with self.lock:
            targets, before, after, warnings = self.plan(slot, change, catalog)
            unchanged = {k: v for k, v in asdict(after).items() if k not in ("rule_set_version", "assigned_in_version")}
            if unchanged == {k: v for k, v in asdict(before).items() if k not in ("rule_set_version", "assigned_in_version")}:
                return {"status": "unchanged", "rule_set_version": self.rules["version"], "assignment": before.to_dict()}
            p = pending(slot)
            parts = []
            if after.product_id != before.product_id:
                parts.append(f"slot {slot} now holds {after.product} ({after.product_id}) instead of "
                             f"{before.product} ({before.product_id})")
            else:
                parts.append(f"slot {slot} keeps {after.product} ({after.product_id})")
            for key, label in (("target_units", "target"), ("reorder_below", "reorder level"), ("group", "group")):
                if getattr(after, key) != getattr(before, key):
                    parts.append(f"{label} {getattr(before, key)} -> {getattr(after, key)}")
            text = ", ".join(parts)
            try:
                new = R.write_version(self.rules, self.base, self.versions_dir, targets, approver, text)
            except R.RuleSetChanged as e:
                raise ShelfError(409, str(e)) from e
            parent = self.rules
            self.chain.append(new)
            after = assignment_for(new, slot)
            summary = f"{approver} signed rule set v{new['version']} (from v{parent['version']}): {text}."
            if after.assignment_id != before.assignment_id:
                summary += (f" The camera calibration of slot {slot} is now stale "
                            f"({before.assignment_id} -> {after.assignment_id}); calibrate it again.")
            if p is not None:
                summary += (f" The {p.get('status', 'pending')} order {p.get('run_id')} for product {p.get('product_id')} "
                            f"under v{p.get('rule_set_version')} stays as it was.")
            log.write("rule_set", {
                "summary": summary, "slot": slot, "before": before.to_dict(), "after": after.to_dict(),
                "file": R.version_path(self.base, self.versions_dir, new["version"]).name, "sha256": new["sha256"],
                "based_on": new["based_on"], "pending_order": p, "warnings": warnings}, approver, new["version"])
            activate(new)
            return {"status": "changed", "rule_set_version": new["version"], "sha256": new["sha256"],
                    "assignment": after.to_dict(), "previous": before.to_dict(),
                    "calibration_stale": after.assignment_id != before.assignment_id,
                    "pending_order": p, "warnings": warnings, "summary": summary}

