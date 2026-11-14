# Worker 2: shelf configuration in the signed rule model

Worktree `/Users/bigboss/Ai/Claudecode/junction2026-wt-shelf`, branch `wt/shelf-config`, backend port
8201 on 127.0.0.1 only. Your files are in the contract (worktree 2). First run
`git merge --ff-only valio/hw-checkpoint` if your branch is behind.

## Goal

Which product a slot should hold becomes configuration that follows the existing signed rule set
model. The demo stays three fixed slots, one product each (DEMO_PLAN.md).

## Tasks

1. Read how Alejo's `backend/rules.py` loads and "signs" a rule set (a recorded hash per version;
   a changed file with the same version is refused) and how `rules/*.signed.json` work.
2. `backend/shelf.py`: keep the **expected assignment** (slot -> product_id, product name, group,
   target units, reorder level, rule set version, `assignment_id = "v{version}:{product_id}"`)
   separate from the **observation** (the last `slot_state` and `health` from the device). Two data
   structures, never one dict that mixes them.
3. `GET /shelf`: the assignments of the active rule set, each with its `assignment_id`, and the
   latest observation shown apart from it (interface 9).
4. A product change goes through the signed flow, never an unsigned shortcut: `PUT /shelf/{slot}`
   with an approver PIN writes a new rule set version (version + 1), records its signature the way
   `rules.py` already does, writes an audit entry, and activates it. Validate against the catalog
   and the rules: the product exists, is allowed for the kitchen (rule check), exactly one product
   per slot. Decision A (may a person change a slot's product without re-signing?) stays open: your
   flow is the default the contract names, re-signing on every change.
5. A pending order keeps its original product and rule set version: use
   `Agent.pending_for_slot(slot)` (interface 8, worktree 1 builds it; until then, read
   `agent.orders` and `agent.open_approvals()` without changing `agent.py`). A product change while
   an order is pending is allowed, recorded, and shown on `/shelf`; it does not touch that order.
6. `SlotState` accepts the version 2 fields (`fill`, `frame_id`, `frame_time`, interface 1) as
   optional, and the `/events` handler computes units as `round(fill x target_units)` from the
   active assignment when `fill` is present. Version 1 events keep working.
7. Tests in `tests/test_shelf_config.py`: persistence across a backend restart, a refused unsigned or
   changed file, a product change creating a new version and a new `assignment_id` (which makes the
   device's calibration stale, interface 9), a pending order surviving a product change, invalid
   products, and version 1 and version 2 events. Slot rectangles stay device-owned: do not add
   region editing; the overlap check exists on the device (`shelf_detect.overlapping_slots`). If you
   validate a layout anywhere, import that function rather than writing a second one.

Do not edit `agent.py`, `valio.py`, `gateway.py` or the device. Handoff: `docs/handoff/w2-shelf-config.md`.
