# Handoff: worker 2, shelf configuration in the signed rule model

Branch `wt/shelf-config`, worktree `junction2026-wt-shelf/`. Unattended round of 14 Nov 2026, 05:42 to
about 06:40 (with a pause for the allowance reset). Brief: `docs/worker-briefs/w2-shelf-config.md`.

## Commits

| Commit | What |
|---|---|
| `fa322a3` | WIP: `backend/shelf.py`, rule set versions in `backend/rules.py`, `GET`/`PUT /shelf`, `SlotState` v2, tests |
| `400a435` | Merge of `valio/integration-unattended` (worker 1, `e55bd03` + contract amendments `c77932c`). The only conflict was the imports in `backend/app.py`, and both sides were kept. `/shelf` now reads `Agent.pending_for_slot` and the board's health. The fallback is removed, and so is the new env key |
| this commit | Removes an unused helper and adds this handoff |

Not pushed and not merged into `main`.

## What was built

### Two data structures (`backend/shelf.py`)

- `Assignment` (frozen dataclass) is the expectation, and it comes only from the active signed rule set: `slot`,
  `product_id`, `product`, `group`, `target_units`, `reorder_below`, `rule_set_version` (the active
  version), `assigned_in_version` (the version that put this product into this slot), and the
  property `assignment_id = f"v{assigned_in_version}:{product_id}"`.
- `Observation` (frozen dataclass) is the last `slot_state` from the device: `state`, `units` (normalised),
  `device_units` (what the board estimated), `fill`, `frame_id`, `frame_time`, `received_at`.
  The board's per-slot health (interface 7) is added only in the `GET /shelf` answer, next to the
  observation and never next to the assignment. The `Shelf` object keeps the observations in memory, and
  they are not kept across a restart. The board resends its state.
- `Shelf` holds the signed chain of rule sets (the last one is active), the observations, and one
  lock for changes.

### Endpoints (`backend/app.py`)

`GET /shelf` returns a list, one row per slot, as in interfaces 4 and 9:

```json
[{"slot": 3, "product_id": "MOCK-MILK-LF-1L-B", "product": "Lactose-free milk drink 1 L, brand B",
  "group": "lactose_free_milk", "target_units": 24, "reorder_below": 6,
  "rule_set_version": 4, "assigned_in_version": 4, "assignment_id": "v4:MOCK-MILK-LF-1L-B",
  "observation": {"state": "low", "units": 6, "device_units": 1, "fill": 0.25, "frame_id": 7,
                  "frame_time": "...", "received_at": "...",
                  "health": {"state": "low", "calibration": "stale", "assignment_id": "v3:...",
                             "age_s": 4.1, "known": true}},
  "pending_order": {"slot": 3, "product_id": "MOCK-MILK-LF-1L", "rule_set_version": 3, "run_id": "...",
                    "since": "...", "status": "approval", "made_for_current_assignment": false}}]
```

`observation` is `null` until the board reports the slot. `pending_order` is
`Agent.pending_for_slot(slot)` (interface 8) plus `made_for_current_assignment`. The board's own
parser, `shelf_detect.assignments_from_shelf`, reads this answer. A test checks that.

`PUT /shelf/{slot}` takes this body:
`{"pin", "product_id", "target_units"?, "reorder_below"?, "product"?, "group"?}`. Answers:

- 200 `{"status": "changed", "rule_set_version", "sha256", "assignment", "previous", "calibration_stale", "pending_order", "warnings", "summary"}`.
- 200 `{"status": "unchanged", ...}` when nothing differs. No version is signed in that case.
- 403 for a wrong or missing PIN, 429 after 5 wrong PINs (the same counter and lock as approvals), and 503 when no PINs are set.
- 404 for a slot that is not in the rule set. New slots need a person to sign a new rule file.
- 409 when the product is already in another slot (one product per slot, one slot per product).
- 422 for a product that is not in the catalog, a product the rule check forbids (from `rules.safety`:
  banned allergen, a `must_have` rule, missing allergen data under rules that forbid it),
  `reorder_below >= target_units`, a list instead of one `product_id`, or a product that is not an
  approved substitute of the slot's group when no `group` is sent.

When the rule check would send a product's orders to a person (`needs_approval`, for example no
allergen data under rule set v4), the change is accepted and the answer and the audit entry carry a
warning.

`product` is optional. It defaults to the old name when the product is the same, and otherwise to the
catalog name. The agent uses this name as its search query and to read the pack size
(`agent.base_name`, `pack_of`).

`SlotState` accepts the optional `fill` (0 to 1), `frame_id` (0 or more) and `frame_time` (interface
1). When `fill` is present, `/events` sets `estimated_units = round(fill x target_units)` from the
active assignment, and an empty slot gives 0, as on the board. Version 1 events are unchanged.

### How a product change creates and signs a new rule set version

1. The approver PIN gives the approver's name. All of the following happens under `Shelf.lock`.
2. `Shelf.plan` checks the change against the catalog and the rules (see the 4xx list above). It
   builds the new `targets`. Every target gets `assigned_in` (carried over, or the parent version for
   old files). The changed slot gets `assigned_in = version + 1` only when its product changes.
3. `rules.write_version` copies everything from the active rule set except the targets. It sets
   `version + 1`, `signed_by: <approver>`, `based_on: {version, sha256 of the parent}`, `change: <text>`
   and the new `targets`. It then writes `<rules stem>.v<N>.signed.json` with the same record
   `rules.sign` writes (`version`, `signed_by`, `sha256`, `signed_at`), followed by
   `<rules stem>.v<N>.yaml`, each through a temporary file and a rename. It writes the signature first,
   so a crash leaves no unsigned version. It refuses if the file already exists, and it reads the file
   back through `load_rules`.
4. An audit entry is written: step `rule_set`, actor the approver, the new version, before and after
   assignments, file name, sha256, `based_on`, any pending order, and the warnings. The story shows it as
   "rule_set: Head chef signed rule set v4 (from v3): slot 3 now holds ... calibrate it again."
5. `_activate` sets the module `ruleset`, `agent.rules` and `driving_test.rules` to the new version,
   still under the lock.

The base files in `rules/` are never rewritten. The versions live next to the audit log:
`data/<audit stem>-rule-versions/`. For the live backend that is `data/audit-rule-versions/`, for this
worktree `data/wt-shelf-rule-versions/`, and in tests `tmp_path`. `data/` is git-ignored.

At start, `rules.load_rule_chain(base, versions_dir)` loads the base file (it is verified as before),
then v+1, v+2 and so on. Each one must match its signed hash and name the version and hash before it.
The backend refuses to start (`RuleSetChanged`) in these cases: a changed or unsigned version file;
a version not made from its parent; a gap; or versions at or below the base file's version, which means
the base file was re-signed after the shelf changes. The message says to move the folder away. The app's
existing `_check_targets` then checks the active version against the catalog.

### Pending orders

A change while a slot has an open request or a placed order is allowed. It is recorded in the
`rule_set` audit entry and in the answer, and it is shown on `/shelf` with
`made_for_current_assignment: false`. The change does not touch the request or the order:
`Agent.pending_for_slot` keeps the product and rule set version of the trigger, including after a
restart (from the audit log).

## Test results (measured)

| When | Command | Result |
|---|---|---|
| 05:47, before any change | `uv run pytest -q` | 162 passed, 6 skipped |
| 06:34, WIP commit `fa322a3` | `uv run pytest -q` | 196 passed, 6 skipped |
| First run after the merge | `uv run pytest -q` | 1 failed (`test_env_example_lists_every_key_the_backend_reads`: my `RULE_VERSIONS_DIR`), 291 passed, 6 skipped. Fixed by removing the key |
| After the merge, final | `uv run pytest -q` | 292 passed, 6 skipped (256 from the merge + 36 in `tests/test_shelf_config.py`) |
| Final | `uv run pytest -q tests/test_shelf_config.py` | 36 passed |

The 6 skipped tests need the real Valio product file, which is not on this laptop.

`tests/test_shelf_config.py` covers these cases:

- Ids and the board's parser:
  - The ids of the real rule set v4 are `v4:477032`, `v4:464316` and `v4:463335`, as the board's own parser reads them.
  - The board's parser reads `/shelf` before and after a change.
- Separation: the observation is kept apart from the assignment, and health shows with the observation.
- Events:
  - Version 1 events keep their units.
  - Version 2 units come from `fill` and the backend's target.
  - A v2 event above the reorder level orders nothing.
  - Out-of-range `fill` and `frame_id` get a 422.
- Signing:
  - A product change writes the v4 files with the right signature and `based_on`, and the base file is unchanged.
  - The new version is active in `/shelf`, `/rules`, `/device`, `agent.rules` and `driving_test.rules`.
  - The audit entry is written.
  - The agent buys the new product after the change.
  - A target-only change keeps the `assignment_id` and counts with the new target.
  - Identical values sign nothing.
- Persistence: a change survives a restart, and the next change builds v5 on v4.
- Refusals at start:
  - a changed version file
  - an unsigned version file
  - a hand-signed version not made from its parent
  - versions from an older base
  - a gap
  - a second writer of the same version
- Invalid changes (12 cases): each is refused and signs nothing.
- A new product with its own group is accepted.
- PIN: wrong or missing PIN, the shared lock, and the 503 when no PINs are set.
- Pending orders: an open approval survives a product change. Its options are unchanged, approving it
  orders exactly those lines, and after a restart the placed order still names the old product and v3.

Smoke test on the real server, measured at 06:37. I ran `uvicorn` on 127.0.0.1:8201 with a scratch audit
DB, `CATALOG=local` and a test PIN; the worktree `.env` was not read.

- `GET /shelf` gave `v3:` ids.
- A v2 event for slot 2 (`fill` 0.25) ordered.
- `PUT /shelf/3` gave `changed 4 v3:MOCK-MILK-LF-1L -> v4:MOCK-MILK-LF-1L-B stale True`.
- A wrong PIN gave 403.
- After a real restart, slot 3 was still `v4:MOCK-MILK-LF-1L-B`, slot 2's pending order showed
  `ordered`, `/device` showed rule set version 4 signed by `Head chef`, and the story had the
  `rule_set` line.
- No errors in the server logs.

## Measured gap: an old request is ordered under the new rule set version

Measured with a script at 06:37. A request opened under v3, then slot 3 was changed (v4), then the
request was approved. The trigger, the rule checks and the open request cite v3.
`Agent.pending_for_slot(3)` still returns `product_id MOCK-MILK-LF-1L, rule_set_version 3`. But the
`recheck`, `order` and `approval` entries of that run cite **v4**, because `Agent._order` and
`_recheck` use `self.rules` at approval time. The product is the original one, and the version on the
order entry is the new one.

`agent.py` is not mine (the buying path belongs to worktree 3, pending to worktree 1).

- If an order must cite the version of its trigger, the owner of `_order` must keep the rule set per run.
- If it should be rechecked against the newest signed rules, which is safer when a later version
  bans more, it stays as it is.

A person must decide. A shelf change alters only targets, so the safety result of the recheck is the
same either way.

## Interpretation to confirm: which version is in `assignment_id`

The contract writes `assignment_id = "v{rule set version}:{product_id}"`. I used **the version that
put the product into the slot** (`assigned_in_version`), not the active version. So changing slot 3
makes only slot 3's calibration stale. Slots 1 and 2 keep `v3:...` and their photos stay valid. A
target-only change keeps the id.

- For every unchanged rule file the two readings give the same string. The live rule set v4 gives
  `v4:464316` for slot 2, as in interfaces 7 and 9 and in worker 1's tests.
- The board compares strings only (`shelf_detect.assignments_from_shelf`, `calibration_status`).
- If integration wants the literal reading (every change marks all slots stale), change
  `Assignment.assignment_id` to use `rule_set_version`. The test
  `test_the_board_reads_the_shelf_answer_before_and_after_a_change` would then need its expectation
  changed.

## Decision A and other open decisions

- **Decision A** (may a person change a slot's product without re-signing?) is still open. I built the
  contract's default: every change, including a target-only change, signs a new version, and there is
  no unsigned path. Interface 4 said "PUT waits for decision A", while the brief (task 4) said to build
  the write path with re-signing. I followed the brief. If Alejo says no product changes at runtime at
  all, remove the `PUT` route, and the read side keeps working.
- Who may sign: any approver PIN in `APPROVER_PINS` can sign a new version, and the version records that
  approver's name as `signed_by`. The rule file says the head chef signs. Restricting signing to
  `approvers.default` would be one comparison in `shelf_change`, but it is not built.
- Side effect of re-signing on every change, read from the code and not tested: `Agent.restore` keeps
  label checks only for the active version. After a shelf change and a restart, every label must be
  checked once more. Before a restart, the label checks in memory remain.
- Side effect, read from the code: a pending order's restock hold uses the active assignment's
  `reorder_below` and `target_units`, not those of its trigger.
- Base file re-signed after runtime changes: the backend refuses to start (clear message, folder to
  move). I chose refusing over silently dropping a person's change.

## Files touched outside my list

- `backend/app.py`: the start line `ruleset = load_rules(...)` became `shelf = Shelf(...)` plus
  `ruleset = shelf.rules`. A restart cannot load the active version otherwise.
- `backend/app.py`: `_approver(pin)` repeats the PIN check of `approvals_decide` and shares its
  `pin_guard`. I did not edit `approvals_decide`. Integration may make it call `_approver`.
- `agent.rules` and `driving_test.rules` are reassigned on activation (attribute assignment only).
  `agent.py` and `sandbox.py` were not edited.

## What is not tested

- Two `PUT /shelf` calls at the same moment on the real server. The lock and the "exists already"
  refusal are unit-tested only.
- A product change while a buy is running (between the trigger and its request). The run can read
  v3 and v4 in the same buy. Only targets differ.
- The real Valio catalog: its product names as search queries after a change, and `PUT` with real
  codes. The product file is not on this laptop. Only the v4 ids are checked, without the catalog.
- The board: no `adb` and no ssh, as the rules require. Whether the board marks slot 3 stale after a
  live change is not run; only its parser is tested on the laptop.
- No web page for shelf changes was built. The brief does not list one.

## What needs a person or the board

1. Decision A, and who may sign (above). Also decide the version on an order made after a change (the measured gap above).
2. Integration: merge, then on the live backend check that `GET /shelf` gives the `v4:` ids, and
   recalibrate each slot once so the board stores the ids (worker 1's handoff says the same).
3. After any live `PUT /shelf`, recalibrate the changed slot. The board shows it as `stale`
   at its next `GET /shelf` poll (every 60 s, per worker 1's handoff; not run here).
