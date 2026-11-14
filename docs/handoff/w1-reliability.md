# Handoff, worktree 1: purchasing reliability and reproducible configuration

Branch `wt/detection-reliability`, worktree `junction2026-wt-detection/`, 14 Nov 2026, 05:42 to about
06:30, with a pause for the usage reset. Brief: `docs/worker-briefs/w1-reliability.md`. Nothing
was deployed, pushed or merged. The board, the live backend (8000) and the camera page (8765) were
not touched; no backend was started on 8101 either (all checks ran through pytest).

## Commits

| Commit | What |
|---|---|
| `efb0b6a` | Restock policy, `backend/settings.py`, pending record (interface 8), health on `GET /device` (interface 7), Alejo's test pinned |
| `6f039ab` | Device health and calibration validity (interfaces 7, 9), `.env.example`, restock and health tests |
| `ab72e46` | Fixes for the Codex review (stalled camera, restore window, concurrent approvals, expired approvals) and `FRAME_STALE_S` |
| this file | Handoff |

## 1. What a refill means for the business

After an order for a shelf slot, the camera can read the slot "full" again for four different reasons:

| The "full" reading is | Fulfils the order? | Ends the shortage? |
|---|---|---|
| the delivery of this order | yes | yes |
| stock carried in from a back room | no: the order is still on its way | yes, for now |
| the same product put back (a person took it and returned it, or a demo reset) | no | yes, for now |
| a false reading: a hand, a reflection, a recalibration, a moved camera | no | no |

Only a delivery fulfils an order, and a delivery cannot arrive before the order was placed (in
practice not before the quoted delivery time in the order lines). The camera cannot tell these four
apart. A "full" reading therefore never proves a delivery; at best a long, steady "full" shows that
the shortage is over for now. The live duplicate of 14 Nov (04:20:53 `ORD-68CE0E08`, 12 x yogurt;
04:20:56 one "full" reading, logged "Slot 2 is stocked again"; 04:21:19 `ORD-592FF708`, the same
order again) was the last row read as the first.

## 2. The restock policy implemented (committed default)

A camera or knob trigger makes the slot pending: no second order or request for it. The slot stays
pending:

- while its approval request is open. A reading never closes a request; the chef approves or skips,
  or the request expires after 2 hours (unchanged);
- after its order is placed, until either
  - (a) a delivery check for that order arrives (knob long press, a `delivery_check` event, or
    `POST /deliveries/{order_id}/check`), or
  - (b) the slot reads stocked (full, or low at or above `reorder_below`) without a break for
    `RESTOCK_CONFIRM_S`, and at least `RESTOCK_MIN_S` have passed since the order was placed.

Readings for (b) are the `slot_state` events (the board sends one on each confirmed change) and the
`health` in the board's `status` event (every 10 s, interface 7), which shows that the stocked state
continues. A reading that is below the reorder level, uncertain, from a stale or missing calibration,
from a box with no camera picture, no frame newer than `FRAME_STALE_S`, or an invalid
configuration, or that follows a gap of more than `HEALTH_STALE_S` since the previous reading,
starts the hold again. Health never orders, never
cancels an order and never closes a request. One "full" reading never ends a pending order while
`RESTOCK_CONFIRM_S > 0`. Rule (b) is a mitigation, not proof of a delivery: a product put back or
back-room stock passes it too, and the order is not marked delivered. The code
(`backend/agent.py`, the "Pending slots and the restock policy" block) and `backend/settings.py` say
so too.

Every step is in the audit log: the first stocked reading of a hold ("One reading is not a refill:
the order ends when ..."), a break of the hold, and the end (`restock` entry with `how`
`delivery_check` or `stocked`, the order id and the seconds held).

### Configuration keys and committed defaults (`backend/settings.py`)

| Key | Default | Meaning |
|---|---|---|
| `RESTOCK_MIN_S` | 120 | Seconds after the order before a stocked slot can end the pending order |
| `RESTOCK_CONFIRM_S` | 60 | Seconds the slot must read stocked without a break; 0 = no hold (one reading is enough) |
| `OFFLINE_AFTER_S` | 30 | Seconds without any event before the box counts as offline (was a constant in `app.py`) |
| `HEALTH_STALE_S` | 30 | A health report older than this counts as unknown; a gap this long breaks a hold |
| `FRAME_STALE_S` | 10 | A health report whose newest camera frame is older than this (or has no frame age) is no evidence of stock (added after the Codex review) |

`Agent()` without settings uses these defaults and never reads the environment, so a local `.env`
cannot change what a test checks (measured: the full suite gives the same counts with
`RESTOCK_MIN_S=0 RESTOCK_CONFIRM_S=0 HEALTH_STALE_S=5` and with `RESTOCK_MIN_S=600` in the
environment). `backend/app.py` passes `Settings.from_env()`; a value that differs from the default
writes a "Settings differ from the committed defaults" notice into the audit log at start. A bad
value (negative, not a number, NaN, infinity) stops the start with the key's name.
`valio-challenge/.env.example` lists every key the backend reads, with its default and meaning; a
test checks that it covers every `os.getenv` key in `backend/` and every setting with its default,
and that it holds no secret.

What a demo rehearsal must know: demo step 5 (milk, slot 3) goes to the chef on the stand-in catalog
(partial stock; not checked on the real catalog). Skipping the request
frees the slot at once. After an approval (an order), repeating step 5 needs one of: a delivery check
(knob long press), or the milk back on the shelf for at least 60 s and at least 120 s after the order
(only with the new board code, which sends health).

## 3. Decision C: what Alejo must choose

> After the agent ordered for a shelf slot, may the camera alone make that slot orderable again, and
> how sure must it be?

| Option | Settings | Effect |
|---|---|---|
| C1, his original spec | `RESTOCK_MIN_S=0`, `RESTOCK_CONFIRM_S=0` | One "full" reading frees the slot. Step 5 repeats at once. A hand or a recalibration orders twice: exactly the live duplicate |
| C2, committed default | `RESTOCK_MIN_S=120`, `RESTOCK_CONFIRM_S=60` | Free after a delivery check, or after 60 s of steady "full" at least 120 s after the order. One false reading cannot reorder. Step 5 repeats after a skip, a delivery check, or about 2 minutes with the product back. Needs the new board code for the "steady" part |
| C3, delivery only | `RESTOCK_CONFIRM_S=31536000` (a year) | Only a delivery check (or a skip or expiry before an order) frees the slot. Matches "only a delivery fulfils an order". Every repeat of step 5 after an order needs a delivery check |

His test `tests/test_requests.py::test_one_empty_shelf_makes_one_order` is pinned to C1 with
`restock_min_s=0, restock_confirm_s=0` and a comment that names decision C (the one edit in his file).
If he picks C1, change the two defaults in `backend/settings.py` and `.env.example`, and
`test_the_committed_defaults`. If he picks C3, change `RESTOCK_CONFIRM_S` the same way. For C2 nothing
changes. A further option, not built: refuse to end a pending order before the earliest quoted delivery
time of its lines (a stocked slot before that is never this delivery).

## 4. Reproducible configuration

- `backend/settings.py` (new): the five keys above, a frozen dataclass, `from_env()`, `overrides()`.
- `backend/agent.py` and `backend/app.py` read it; `OFFLINE_AFTER_S` in `app.py` now comes from it.
- `valio-challenge/.env.example` (new, every key, empty secrets). The root `.gitignore` ignores
  `.env.*`, so the file is force-added (`git add -f`); integration may add `!valio-challenge/.env.example`
  to `.gitignore` (root file, not mine).
- The live `.env` in `junction2026/valio-challenge/` (not read; the brief says it holds `RESTOCK_MIN_S=120`)
  should drop that line: the default is now 120. Keeping it is harmless (equal to the default, no notice).

## 5. Interfaces delivered

### 7. Health in the status event

- Device: `shelf_detect.build_health()` builds the dict of the contract plus a top-level
  `assignment_check` (`ok` once `GET /shelf` answered, else `unknown`). `main.py` sends it in the
  `status` event every 10 s and shows it in `GET /state` on port 7000; a failure to build it is logged
  and the plain status still goes out. `config_valid`/`config_errors` come from
  `shelf_detect.config_errors()`: overlaps, rectangles outside the picture, duplicate slots,
  thresholds, the interface 2 keys. The configuration is reported, not enforced (see proposals).
- Device loop: a frame older than `CAMERA_GRACE_S` (2 s) now counts as no frame in `main.loop`
  (every slot uncertain), also when `capture()` hangs and `camera_ok` stays true. Before, only a
  `capture()` that returned nothing for 2 s did that. `tests/device/replay.py` already modelled the
  2 s grace this way, so the model and the board now agree.
- Backend: `Status` accepts an optional typed `health` (`Health`, `SlotHealth`; unknown fields kept,
  wrong types 422). `GET /device` adds `health` (last report), `health_age_s` and `health_known`
  (`false` when older than `HEALTH_STALE_S` or missing). The agent uses health only as readings for
  slots with a placed order (section 2).

### 8. `Agent.pending_for_slot(slot)`

Returns `{"slot", "product_id", "rule_set_version", "run_id", "since", "status"}` with `status`
`approval` or `ordered`, else `None`. `product_id` and `rule_set_version` are the slot's assignment at
the trigger and stay after a rule set change; `since` is the trigger time. It lets an expired approval
end and applies a delivery check first. During the pipeline run (between trigger and its approval or
order, about a second) it returns `None` although the slot is pending (the contract's two statuses only).

Restart: the pending state is rebuilt from the last camera or knob trigger of each slot. The slot is
pending when that run has an open approval, or a placed order with no `restock` and no
`delivery_check` after it. This fixes a restore bug: before, a skipped request on a slot that had any
earlier order stayed pending after a restart. Logs from before this change (triggers without
`product_id`, restocks without `run_id`) restore too (tested). A stocked hold is not restored: after a
restart the slot must read stocked for `RESTOCK_CONFIRM_S` again.

### 9. Calibration validity on the device

- On "save photo", `main.py` writes `data/slotN_meta.json`:
  `{"empty": {"assignment_id", "saved"}, "full": {"assignment_id", "saved"}}`, with the assignment from
  the last `GET /shelf` answer (`None` while the backend has no `/shelf`).
- `main.poll_shelf()` asks `GET /shelf` every 60 s. A failure keeps the last answer; before the first
  answer the check is `unknown`.
- `shelf_detect.calibration_status()`: `missing` without an empty photo; `stale` when the photos'
  assignment (the full photo's, else the empty photo's) differs from the backend's; otherwise `ok`.
  Unknown on either side is `ok`, never `stale`. `gate_calibration()` makes a stale slot `uncertain`
  ("photos are for another product; save the empty and full photos again"), which the page shows as
  "unsure: ...". The replay model (`tests/device/replay.py`) follows the same gate.
- Consequence: photos saved before `/shelf` has `assignment_id` are never checked. After worktree 2's
  `/shelf` is live on the board's backend, recalibrate each slot once to arm the check.

## 6. Tests (all runs `uv run pytest -q` in `valio-challenge/`)

| When | Result |
|---|---|
| Start, `eca4100` | 162 passed, 6 skipped |
| Policy in, before pinning his test | 160 passed, 2 failed (`test_one_empty_shelf_makes_one_order`, my old 0-s test), as expected |
| `efb0b6a` | 163 passed, 6 skipped |
| `6f039ab` | 250 passed, 6 skipped |
| With `RESTOCK_MIN_S=0 RESTOCK_CONFIRM_S=0 HEALTH_STALE_S=5`, and with `RESTOCK_MIN_S=600`, in the environment | 251 passed, 6 skipped both (same as without) |
| Mutation: default `RESTOCK_CONFIRM_S` set to 0 | 15 failed in `test_restock_policy.py`/`test_duplicate_orders.py`, reverted |
| After the Codex fixes, `ab72e46` | 256 passed, 6 skipped (five runs in a row; same with `RESTOCK_MIN_S=0 RESTOCK_CONFIRM_S=0 FRAME_STALE_S=1` in the environment) |
| Mutations after the fixes: each of the four fixes undone alone | its test fails (2, 2, 1 and 1 failures; the concurrency test failed 3 of 3 runs without the snapshot, passed 3 of 3 with it) |
| Final, with this file | 256 passed, 6 skipped |

The 6 skips need the real Valio product file, which is not on this laptop. `python3 -m py_compile`
passes for `device/python/main.py` and `shelf_detect.py`.

New tests: `tests/test_restock_policy.py` (a false full inside and outside the window, one full and
silence, a genuine early refill, a late refill, low above the reorder level, health alone starting the
hold, delivery check through the event and the HTTP path, seven kinds of break, a gap, a stalled
camera, a report without the slot, approvals added by another thread, open approvals, the hold starting only at the order, skip, expiry, slots
independent, health never orders, repeated readings logging once, the pending record, a new rule set
version, a restart in nine states (two with the trigger beyond the entries read), old log formats, the committed defaults, the environment ignored
by `Agent()`, `.env.example`), `tests/test_health.py` (Status model, `GET /device`, staleness, 422 on
bad health, the board's own health accepted, health never orders or closes a request, settings in the
app, the immediate policy end to end), `tests/device/test_device_health.py` (calibration status,
records, `/shelf` parsing, the stale gate in the replay model, config errors, the health dict).

## 7. Codex review

One run, as the brief says: `codex exec -s read-only -C <worktree> "Review the diff of branch
wt/detection-reliability against valio/hw-checkpoint for correctness bugs in purchasing state
transitions: pending slots, restock window, sustained full, restart restore, locking."`, 15-minute
cap, exit 0, read-only (it changed no file). It reviewed commit `6f039ab`. Its final message is in
`data/codex-review-w1.md` and its log in `data/codex-review-w1.log` (git ignores `data/`). It found
four bugs, all rated P2, each reproduced by Codex. All four were real and are fixed in `ab72e46`, each
with a test that fails without the fix:

| Finding | Fix |
|---|---|
| Stale frames can complete the restock hold: `observe_health()` ignored `frame_age_s`; a hung camera keeps `camera_ok` true and repeats the last "full" | Backend: health with no frame age, or a frame older than `FRAME_STALE_S` (10 s), breaks the hold. Board: `main.loop` treats a frame older than 2 s as no frame |
| Restore reads the newest 100,000 entries; with the trigger outside and the order inside, the slot was free and a duplicate followed (the base branch suppressed it) | The order or the open request stands in for a trigger that was not read |
| `_pending_status()` iterated the live approvals while `_ask` adds them under `self.lock`: `RuntimeError: dictionary changed size during iteration` | Iterate a snapshot (`list(self.approvals.values())`) |
| An expired approval kept blocking slot events, because the new stocked path no longer called `open_approvals()` | Already fixed before the review finished (uncommitted then): `handle_event` calls `open_approvals()` for every slot event |

No finding was rejected. Not re-reviewed by Codex after the fixes (the brief asks for one run).

## 8. Not tested

- Nothing ran on the board. `main.py` only compiles on the laptop. Not tested: the frame-age check
  in `main.loop` (if the camera tab in the background gives less than one frame every 2 s, slots
  show "unsure" between frames and confirmations reset; about 1 frame/s was seen on 14 Nov), the
  `GET /shelf` poll, writing `slotN_meta.json` on the board, the `status` post with health, `GET /state` with
  health, the cost of building the health every loop (it checks two files per slot and the config
  each second), and how the page shows the new "unsure" reason.
- The health a real board sends was not seen by a real backend; only `build_health()` output was
  posted to the app in a test.
- No recorded sequence of a real refill exists, so the 60 s / 120 s defaults are not measured
  against real shelf behaviour. Measured only: the live false "full" lasted at most 23 s
  (04:20:56 to 04:21:19, from the audit log in the contract).
- `GET /shelf` does not exist yet (worktree 2). Interface 9 was tested with the contract's format only.
- Concurrency: the trigger lock covers the check-and-add, health and settling. `decide()` (not mine)
  still frees slots without that lock, as before; not changed and not stress-tested.

## 9. Assumptions (not measured)

- `RESTOCK_CONFIRM_S=60`: six status reports. Chosen to be more than twice the one false "full" seen
  live; no measurement of how long a hand or a recalibration can look "full".
- `RESTOCK_MIN_S` counts from the order, not the trigger (stricter for approved requests: a delivery
  cannot precede the order). The contract said "trigger"; see proposals.
- Health "low" counts as stocked when `round(fill x target_units) >= reorder_below`, with the
  backend's `target_units`.
- A delivery check ends the pending order even when it reports units missing; the next shortage
  reading orders again.
- `FRAME_STALE_S=10`: five times the board's own 2 s grace. Not measured.
- A knob long press sends a delivery check without an order id; the existing `check_delivery` (not
  mine) then takes the newest unchecked order of any slot. With two open orders it can end the other
  slot's pending order. Not changed; worth a sentence in the demo script.

## 10. Proposed edits to `docs/PARALLEL_WORK.md` (integration owns it)

1. Sessions: drop "and `RESTOCK_MIN_S`" from the per-worktree `.env`; the policy is committed in
   `backend/settings.py`.
2. Interface 3, replace the three restock bullets with: "After an order the slot stays pending until a
   delivery check for the order arrives, or the slot reads stocked without a break for
   `RESTOCK_CONFIRM_S` (60) and at least `RESTOCK_MIN_S` (120) after the order. Readings come from
   `slot_state` events and the status health; one reading never ends it. Defaults are committed in
   `backend/settings.py`; `Agent()` ignores the environment. Alejo's test is pinned to
   `restock_min_s=0, restock_confirm_s=0` (decision C)." Demo reset: "skip the request, run a delivery
   check, or put the product back for 60 s at least 120 s after the order."
3. Interface 7: add the top-level `assignment_check` (`ok`/`unknown`); `calibration` is `ok` when
   either assignment is unknown; malformed health gets a 422 (the board then sends a plain status);
   health extends or breaks a restock hold but never orders, cancels or closes a request.
4. Interface 8: `since` is the trigger time; `None` while the pipeline runs; a delivery check and an
   expired approval are applied before answering.
5. Interface 9: the record format of `slotN_meta.json`; the full photo's assignment counts; photos
   without a record are never stale; recalibrate once after worktree 2's `/shelf` is live.
6. Interface 2: "No camera frame for `CAMERA_GRACE_S`" is now enforced by the frame's age in
   `main.loop`, not only by `camera_ok`.
7. "Where things are enforced", add rows:
   | Check | Where |
   |---|---|
   | Restock: delivery check, stocked hold, window from the order | backend, `Agent._settle`, `Agent._hold`, `Agent.observe_health` |
   | Health freshness (30 s), frame freshness in health (10 s) | backend, `GET /device` (`health_known`), the hold's gap rule, `observe_health` |
   | Stale calibration is uncertain | device, `shelf_detect.gate_calibration` in `main.process_frame` |
   | Box configuration valid | device, `shelf_detect.config_errors`, reported in health only (not enforced) |
   | Policy defaults equal the tested ones | `backend/settings.py`, `tests/test_restock_policy.py` |
8. Decision C row: replace the default column with "Committed C2: `RESTOCK_MIN_S=120`,
   `RESTOCK_CONFIRM_S=60`. His test pinned to C1." and point to section 3 of this file.
9. File ownership: worktree 1 also touched three lines of `backend/app.py` outside the `Status` model
   and `GET /device` health (the brief asks the app to read `backend/settings.py`): `SETTINGS =
   Settings.from_env()`, `Agent(..., settings=SETTINGS)` with the override notice, and
   `OFFLINE_AFTER_S`. Integration may want to list them, and `.gitignore` needs
   `!valio-challenge/.env.example`.

## 11. Proposals not built (no feature outside the work order)

- Make an invalid box configuration (overlapping rectangles) mark every slot uncertain on the
  device. Today it is only reported, because the board's current config overlaps and enforcing it
  before the recalibration would stop demo step 5.
- The earliest-delivery bound of decision C (section 3).

## 12. Needs a person or the board

- Decision C (Alejo).
- Deploy `device/python/main.py` and `shelf_detect.py` together with the backend (integration). Until
  the board sends health, a pending order ends only with a delivery check, a skip or an expiry.
- After worktree 2's `/shelf` with `assignment_id` is live: save the empty and full photos of each slot
  once, so the calibration check is armed.
- Remove `RESTOCK_MIN_S` from the live `.env` (optional, equal to the default).
- Record a real refill sequence to check the 60 s / 120 s defaults.
