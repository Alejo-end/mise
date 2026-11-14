# Parallel work on the Valio device and shelf

Base: branch `valio/hw-checkpoint` (pushed to GitHub), commit `dd7ecb1` (14 Nov 2026, 05:40). It
contains Alejo's `review-14-nov` (not yet in `main`). Read this before you edit. It fixes scope, who
owns which file, the interfaces between the parts, and the ports and test data of each checkout.
The task of each worker is in `docs/worker-briefs/`.

## Scope for this round

The entry is the Aimo Agent Gateway. The shelf box is one client that proves it (HANDBOOK.md,
DEMO_PLAN.md). The shelf's job in the demo is step 5: "take the milk off the shelf, the camera
triggers the agent, the box beeps, a safe substitute needs approval". The team brief forbids
features the work order does not list.

In scope (makes demo step 5 reliable, no new product concepts):
- three fixed taped slots, one catalogue product per slot, the two-photo detector as baseline;
- editable slot rectangles that may not overlap;
- the backend as the one source of truth for which product a slot holds;
- "uncertain" for obscured or unknown views and for stale frames: never a stock change;
- no stock events while calibrating; one order per real shortage.

Not in this round (needs a concrete requirement first, see "Machine learning" and the open
decisions): free-form storage surfaces holding several products, product identity tracked across
areas, detecting a product moved between slots, physical dimensions, AI-assisted setup, a
learned detector.

## Sessions

| Session | Checkout | Branch | Ports | Live or test data |
|---|---|---|---|---|
| Integration | `junction2026/` | `valio/hw-checkpoint`, merges | backend 8000 (the board points here), camera page 8765 | `valio-challenge/data/audit.sqlite`, live |
| Worktree 1: detection reliability | `junction2026-wt-detection/` | `wt/detection-reliability` | backend 8101 on 127.0.0.1 only | `valio-challenge/data/wt-detection.sqlite`, pytest `tmp_path` |
| Worktree 2: shelf configuration | `junction2026-wt-shelf/` | `wt/shelf-config` | backend 8201 on 127.0.0.1 only | `valio-challenge/data/wt-shelf.sqlite`, pytest `tmp_path` |
| Worktree 3: real Valio data and quotes | `junction2026-wt-valio/` | `wt/valio-data` | backend 8301 on 127.0.0.1 only | `valio-challenge/data/wt-valio.sqlite`, `data/valio/` (Valio files, never committed) |
| Worktree 4: recognition experiment | `junction2026-wt-recognition/` | `exp/recognition` | none on the laptop; Modal app `valio-recognition-exp`, Modal Volume `valio-recognition` | `experiments/recognition/` and the Modal Volume |

Each worktree has its own `valio-challenge/.env` (git ignores it) with its `AUDIT_DB`, a test
approver PIN and `RESTOCK_MIN_S`. Start a worktree backend with
`uv run uvicorn backend.app:app --host 127.0.0.1 --port 8101` (or 8201, 8301).

Only the integration session may: deploy to the board, restart the board or its app, change
`data/config.json` or the calibration on the board, point the board at a backend, record camera
sequences, change the files marked "integration", and merge. A worktree that needs any of these
asks and hands over a commit.

## File ownership

| Owner | Files |
|---|---|
| Worktree 1 | `device/python/` (all of it, incl. `shelf_detect.py`), `device/assets/index.html`; in `backend/agent.py`: `handle_event`, the pending-slot bookkeeping (`pending_slots`, `pending_since`, the new pending record of interface 8, the `restore` lines for them); in `backend/app.py`: the `Status` event model and the `health` part of `GET /device` (interface 7); `backend/settings.py` (new); `.env.example` (new); `tests/device/` except `sequences/`; `tests/test_duplicate_orders.py`, `tests/test_restock_policy.py` (new), `tests/test_health.py` (new); `tools/laptop-camera.html` |
| Worktree 2 | `rules/*.yaml` (`targets`), in `backend/rules.py`: loading, saving, versioning and signing (`load_rules`, `sign`, `target_for_slot` and new helpers); `backend/shelf.py` (new); in `backend/app.py`: `SlotState`, `ManualLow`, the `/events` normalisation, new `/shelf` endpoints; `web/` pages for the shelf; `tests/test_shelf_config.py` (new) |
| Worktree 3 | `backend/valio.py`, `backend/allergens.py`, `backend/dirty_data.py`; in `backend/rules.py`: the rule check (`Context`, `Line`, `check`, `check_line` and what they call); in `backend/agent.py`: the buying path (`buy`, `buy_exact`, `quote_exact`, `_quote`, `_line`, `_ask`, `_order`); in `backend/gateway.py`: quote and order validation; `docs/valio-api/`; `tests/test_valio_quotes.py` (new). Alejo's existing tests change only for an intended, documented behaviour change |
| Worktree 4 | `experiments/recognition/` (new, everything in it). Nothing outside it |
| Integration | `pyproject.toml`, `uv.lock` (ask: integration adds a dependency), `device/app.yaml`, `device/sketch/`, `backend/audit.py`, `backend/signing.py`, `fixtures/`, `tests/device/sequences/`, `tools/record_sequence.py`, this file, `docs/worker-briefs/`, `TEAM_BRIEF.md`, merges |

The board snapshot and device backups stay out of git (`device/board-snapshot/` is ignored); the
integration session keeps them in a local folder next to the checkout. Anything not listed
(`sandbox.py`, `model_agent.py`, the handbook, other entries): Alejo's, do not edit. Never edit a
file or function another worktree owns; write the need into your handover note instead. A worktree
may read any file and import any function.

## Interfaces (frozen for this round)

### 1. Slot event, device to backend (`POST /events`)

The backend's `SlotState` model accepts `state` only as `"full"`, `"low"` or `"empty"` and answers
422 to anything else. It ignores unknown fields. So:

- The device never sends `"uncertain"`. Uncertainty stays on the device and on its page.
- Version 2 adds three optional fields. Sending them before the backend reads them is harmless.

```json
{"type": "slot_state", "slot": 2, "state": "empty", "estimated_units": 0,
 "fill": 0.03, "frame_id": 1234, "frame_time": "2026-11-14T04:39:46+02:00"}
```

`fill`: 0 to 1. `frame_id`: grows with every new frame since the app started. `frame_time`: arrival
of the newest confirming frame. Worktree 1 sends them; worktree 2 adds them to `SlotState` and,
when `fill` is present, computes units as `round(fill x target_units)` from the shelf
configuration. Version 1 events (no `fill`) keep working.

### 2. Detection contract on the device (worktree 1)

- A frame is checked at most once. A state counts only after `confirm_frames` (default 3)
  different frames over at least `confirm_seconds` (default 3) agree.
- A slot is `uncertain`, which resets the confirmation and never becomes a stock change, when:
  1. its patch has too little detail: contrast (std of the 48 x 48 patch) below `min_detail`,
     default 0.03 (covered or dark lens); or
  2. its view does not match the calibration: `min(d_empty, d_full) > mismatch_ratio x
     d(empty photo, full photo)`, default 0.85 (camera moved, or an unknown object in the slot).
- Evidence for the defaults (05:15, `steady-01` and synthetic frames): real frames 0.89 to 1.24
  on the ratio, because the camera had moved since calibration and shows a cup and a keyboard, so
  rule 2 marks them uncertain, as it should; a painted "empty" slot 0.03; a flat or dark view 0.66
  to 0.74 on the ratio (rule 2 misses it) but contrast 0.002 (rule 1 catches it); real frames have
  contrast 0.23 to 0.31, calibration photos 0.17 to 0.21. A finger blurring the lens (contrast 0.15
  to 0.24) is caught by neither rule; the 3-frame, 3-second confirmation must absorb it. Both
  defaults are provisional until real-shelf recordings exist.
- The first draft of this rule (ratio only, as `uncertain_ratio`) was wrong: it misses a covered
  lens. Do not use it.
- No camera frame for `CAMERA_GRACE_S` makes every slot uncertain.
- Saving a calibration photo or the rectangles starts a quiet period (`calibration_quiet_s`,
  default 15): readings show on the page, no events go out.
- New keys (`confirm_frames`, `confirm_seconds`, `min_detail`, `mismatch_ratio`, `calibration_quiet_s`) live in the device's `config.json`, owned by worktree 1. Defaults keep old configs valid.

### 3. Trigger contract in the backend (worktree 1, `handle_event`)

- The pending check and add in `handle_event` run under a separate `trigger_lock` (not
  `self.lock`, which `_ask` takes inside the buying path: reusing it deadlocks), so two events
  cannot both pass the pending check.
- A "full" reading less than `RESTOCK_MIN_S` after the slot's trigger is logged as "too soon for a
  delivery" and keeps the slot pending. The time comes back from the audit log after a restart.
- `RESTOCK_MIN_S` defaults to 0 in the code. That keeps Alejo's committed spec,
  `test_one_empty_shelf_makes_one_order` (empty, full, empty orders twice), which is the exact
  sequence of the live duplicate. The live backend runs with `RESTOCK_MIN_S=120` until decision C.
- Demo reset: approve or skip the request, or wait `RESTOCK_MIN_S`. The gateway calls and the
  rule check do not change.

### 4. Shelf configuration, owned by the backend (worktree 2)

The backend is the one source of truth for what a slot holds. Rule set v4
(`rules/school_kitchen_valio.yaml`) names real product codes per slot. Today the board's config
still says slot 1 `target_units: 1`, the rule set says 30.

```
GET /shelf            -> [{"slot": 1, "product": "rye bread 500 g", "product_id": "477032",
                           "group": "rye_bread", "target_units": 30, "reorder_below": 8}, ...]
PUT /shelf/{slot}     <- {"product_id": ..., "target_units": ..., "reorder_below": ...}
```

`GET /shelf` reads the active rule set. `PUT` waits for decision A below. Until then worktree 2
builds the read side, the event normalisation and the validation, not the write path.

### 5. Device configuration, owned by the device

`data/config.json` on the board: `rect`, `full_at`, `empty_below`, `present_diff`,
`frame_interval_s`, `camera_source`, `backend_url`, `manual_slot`, `temperature_limit_c`, and the
new keys of interface 2. After worktree 2 lands, `product_hint` and `target_units` on the device
are labels only.

### 6. Recorded sequences and the replay model

`tests/device/sequences/<name>/` is read-only (frames, `meta.jsonl`, calibration, config). Recorded
by integration with `tools/record_sequence.py`. `tests/device/replay.py` models the board loop; a
commit that changes the loop on the board changes the model too.

### 7. Health and uncertainty (worktree 1), next to the typed stock events

Stock events stay as in interface 1. Uncertainty travels in the existing `status` event (the board
sends one every 10 s), in a new optional field. Old backends ignore it; old boards just omit it.

```json
{"type": "status", "health": {
  "camera_ok": true, "frame_age_s": 0.4, "config_valid": true, "config_errors": [],
  "slots": {"2": {"state": "low", "uncertain": "view does not match the photos", "fill": 0.43,
                  "calibration": "ok", "assignment_id": "v4:464316"}}}}
```

`calibration` is `ok`, `missing` or `stale` (interface 9). `GET /device` returns the last `health`
and its age; a `health` older than 30 s counts as unknown. Health never orders and never cancels:
only `slot_state` events trigger purchases.

### 8. Pending order record (worktree 1 writes it, worktree 2 reads it)

`Agent.pending_for_slot(slot) -> dict | None` returns
`{"slot", "product_id", "rule_set_version", "run_id", "since", "status": "approval" | "ordered"}`
for an open approval or a placed, not yet restocked order. It is rebuilt from the audit log on
restart. A pending order keeps the product and rule set version it was made with, even if the
slot's assignment changes later.

### 9. Shelf assignment and calibration validity (worktree 2 backend, worktree 1 device)

The expected assignment of a slot (what should be there) is separate from the observation (what the
camera sees). `GET /shelf` (worktree 2) returns per slot an `assignment_id` =
`"v{rule set version}:{product_id}"`. The device (worktree 1) stores the `assignment_id` it was
calibrated for next to the calibration photos (`data/slotN_meta.json`). If the backend's
`assignment_id` differs, the slot's calibration is `stale`: the slot is uncertain until someone
recalibrates. A product change goes through a new, signed rule set version (decision A), never an
unsigned edit.

Where things are enforced:

| Check | Where |
|---|---|
| Frame freshness, each frame once, confirmation over time | device, `shelf_detect.observe` and `main.loop` |
| Device online (no event for 30 s) | backend, `_watch_device` |
| Slot rectangles do not overlap | device, `on_save_config` (`shelf_detect.overlapping_slots`) |
| Calibration matches the current assignment | device, against `GET /shelf` (interface 9) |
| Rule set signed and matching the catalog | backend at start (`load_rules`) |
| One order per shortage, restock window | backend, `Agent.handle_event` |
| Quote expiry, pack size, limits, deadline | backend, rule check and gateway (worktree 3) |

## Decisions that need Alejo

| | Decision | Why it matters | Default until he answers |
|---|---|---|---|
| A | May a person change which product a slot holds without a new, re-signed rule set version? | Slot targets sit in the signed rule set. An edit outside it weakens "every decision names the rule set version" | Read-only `/shelf`; product changes go through a new rule set version |
| B | Is the board here (`Junction`, all Modulinos answer, no `~/safe-kitchen-agent.backup-20261114`) the demo board, or is there a second UNO Q? | The handbook describes a board with that backup and no Modulinos answering | Treat this board as the demo board |
| C | May a "full" reading right after an order count as a refill (`RESTOCK_MIN_S`)? His test `test_one_empty_shelf_makes_one_order` says yes, at once; the live duplicate on 14 Nov (`ORD-68CE0E08`, `ORD-592FF708`) came from exactly that | Yes: a demo can repeat step 5 at once, but a hand or a recalibration can order twice. No: one false "full" cannot reorder, but repeating step 5 needs a skip or a wait | Code 0 (his spec), live 120. If he picks a window, his test needs a time step or `restock_min_s=0` |

## Evidence

At `80a8402`: 147 passed, 6 skipped (real Valio product file not on this laptop), 5 strict xfails.
The xfails are the failures shown on 14 Nov: one frame seen twice confirms a state (real data),
a stale glitch frame flips a slot and buys, the slot rectangles overlap, and one "full" reading lets
a slot order twice (live orders `ORD-68CE0E08`, `ORD-592FF708`). Also seen: calibrating causes
events; a board restart resends states; the camera tab in the background gives about 1 frame/s.

## Machine learning

The two-photo detector stays the baseline, as DEMO_PLAN.md says ("a later version" may use Edge
Impulse) and as TEAM_BRIEF.md says ("two photos for each shelf slot are enough"). Evaluate a model
only against a recorded sequence where the baseline misreads a real shelf change. None exists:
`steady-01` shows a cup and a keyboard, not the shelf.

## Recordings integration still owes

With the person at the board, about 15 s each, after the camera points at the cardboard shelf and
the rectangles are recalibrated without overlap: a hand over each slot; remove and refill each
slot; the camera tab in the background; a light change.

## Handover and merge

Ready when `uv run pytest` is green and every xfail the worktree fixed is a plain test. Merge
order: either first. The backend ignores unknown event fields, so version 2 events are safe before worktree 2 lands. Integration then
deploys to the board, records a sequence, and runs the replay suite against it.

## Amendments after worker 1 (14 Nov, 06:35). They override the text above where they differ

Merged on `valio/integration-unattended` (merge `e55bd03`): 256 passed, 6 skipped, measured by
integration. Details: `docs/handoff/w1-reliability.md`.

- **Settings.** Backend policy values live in `backend/settings.py` with committed defaults;
  `.env.example` lists every key. `Agent()` without settings uses the defaults and ignores the
  environment; the app reads the environment and logs a notice when a value differs. The per-worktree
  `.env` no longer needs `RESTOCK_MIN_S`. Worktree 1 owns three more lines in `backend/app.py`:
  `SETTINGS = Settings.from_env()`, the `Agent(..., settings=SETTINGS)` call with its notice, and
  `OFFLINE_AFTER_S`.
- **Interface 3, restock (committed default C2).** After an order the slot stays pending until a delivery
  check for the order arrives, or the slot reads stocked without a break for `RESTOCK_CONFIRM_S` (60)
  and at least `RESTOCK_MIN_S` (120) after the order. Readings come from `slot_state` events and the
  status health; one reading never ends it. An unsure reading, a stale calibration, no picture, a frame
  older than `FRAME_STALE_S` (10), an invalid configuration or a health gap over `HEALTH_STALE_S` (30)
  restarts the wait. This is a mitigation, not proof of a delivery. Demo reset: skip the request, run a
  delivery check, or put the product back for 60 s at least 120 s after the order.
- **Interface 7, health.** Adds a top-level `assignment_check` (`ok` / `unknown`); `calibration` is `ok`
  when either assignment is unknown; malformed health gets a 422 (the board then sends a plain status);
  health extends or breaks a restock hold but never orders, cancels or closes a request. `GET /device`
  adds `health`, `health_age_s`, `health_known` (false after 30 s).
- **Interface 8, pending record.** `since` is the trigger time; `None` while the pipeline runs; a delivery
  check and an expired approval are applied before answering.
- **Interface 9, calibration.** Each photo save writes `data/slotN_meta.json` with the assignment it was
  taken for; the full photo's assignment counts; photos without a record are never stale; the board
  polls `GET /shelf` every 60 s and a failed poll counts as unknown, never stale. Recalibrate each slot
  once after worktree 2's `/shelf` is live.
- **Interface 2.** "No camera frame for `CAMERA_GRACE_S`" is also enforced by the frame's age in
  `main.loop`.
- **Where things are enforced, added:** restock (delivery check, stocked hold, window from the order):
  backend `Agent._settle`, `Agent._hold`, `Agent.observe_health`. Health freshness 30 s and frame
  freshness 10 s: backend `GET /device` and the hold's gap rule. Stale calibration is uncertain: device
  `shelf_detect.gate_calibration` in `main.process_frame`. Box configuration valid: device
  `shelf_detect.config_errors`, reported in health only. Policy defaults equal the tested ones:
  `backend/settings.py`, `tests/test_restock_policy.py`.
- **Decision C, now precise.** "After the agent ordered for a slot, may the camera alone make that slot
  orderable again, and how sure must it be?" C1 (Alejo's original): both values 0, one "full" frees it.
  C2 (committed default): 120 s / 60 s. C3: delivery check only. His test
  `test_one_empty_shelf_makes_one_order` is pinned to C1 with a comment.

## Amendments after worker 2 (14 Nov, 06:45). They override the text above where they differ

Merged on `valio/integration-unattended` (merge `e3d7db3`): 292 passed, 6 skipped, measured by
integration. Details: `docs/handoff/w2-shelf-config.md`.

- **Interface 4, shelf configuration.** `backend/shelf.py` keeps `Assignment` (from the active signed rule
  set only) apart from `Observation` (the last `slot_state`, kept in memory). `GET /shelf` returns one row
  per slot: the assignment fields at the top level, then `observation` (with the slot's board health and
  its freshness) and `pending_order` (`Agent.pending_for_slot` plus `made_for_current_assignment`).
  `PUT /shelf/{slot}` with an approver PIN signs a new rule set version (version + 1, `based_on` the
  parent's hash, signature written before the YAML), logs a `rule_set` audit entry and activates it.
  Files in `rules/` are never rewritten; new versions live next to the audit database
  (`data/<audit stem>-rule-versions/`). The backend refuses to start on a changed, unsigned, missing or
  out-of-sequence version, or on a base file re-signed after shelf changes.
- **`assignment_id`** is `v{version that put this product into the slot}:{product_id}`, so a change marks
  only that slot's calibration stale; a target-only change keeps the id. Live v4: `v4:477032`,
  `v4:464316`, `v4:463335`, checked with the board's own parser.
- **Events.** `SlotState` accepts `fill`, `frame_id`, `frame_time`; with `fill`, `/events` computes units
  from the backend's target. Version 1 events are unchanged.
- **Ownership note.** Worker 2 also changed the start-up line of `backend/app.py` (builds `Shelf`) and
  added `_approver(pin)` sharing the approval lockout.

## Open decisions (all need Alejo; the defaults are what the code does now)

| | Decision | Default in the code |
|---|---|---|
| A | May a slot's product change at runtime at all, and without re-signing? | `PUT /shelf` exists and always re-signs; no unsigned path. Remove the route if runtime changes are not wanted |
| B | Is the board "Junction" the demo board? | Treated as the demo board |
| C | After an order, may the camera alone make the slot orderable again, and how sure must it be? | C2: 120 s after the order and 60 s of steady "full", or a delivery check |
| D | Does `assignment_id` follow the version that put the product in the slot (only that slot recalibrates) or the active version (every change recalibrates every slot)? | Per slot (one-line change in `Assignment.assignment_id`) |
| E | After a product change, is an older request approved later ordered under its trigger's rule set version or rechecked against the newest? | The newest: `_order` and `_recheck` use the active rule set (worker 2 measured it) |
| F | Who may sign a new rule set version? | Any approver PIN; head chef only is not built |

## Amendments after worker 3 (14 Nov, 07:00). They override the text above where they differ

Merged on `valio/integration-unattended` (merge `f198850`): 322 passed, 0 skipped, measured by
integration with the shared Valio files linked (all 6 real-catalogue tests run). Details:
`docs/handoff/w3-valio-data.md`; field status table in `docs/valio-api/API.md`.

- **Sources, labelled everywhere as sponsor-provided challenge data and mock APIs.** Static files
  downloaded once (35 API calls in total, never the whole catalogue priced), read-only in a shared folder
  outside git, linked as `valio-challenge/data/valio`. Recorded API answers (secrets removed) are reused by
  tests and experiments instead of new calls. The product JSON schema is only on the password-protected
  portal and was not downloaded.
- **Field status.** Sponsor file: pack size, lot size, deposits (listed, not priced), allergens, delivery
  reliability, carbon, sales unit. Sponsor mock API: price per sales unit (without and with 13.5 % VAT,
  validity), availability, available date, order cut-off, sales block. Unavailable: delivery charge,
  arrival time or window, deposit price. Simulated by our gateway: order placement and confirmation, quote
  expiry (earliest of 15 minutes, the cut-off and the price validity). The invented 06:00 arrival is gone.
- **Ownership note.** Worker 3 added `VALIO_RECORD_DIR` and `VALIO_MAX_CALLS` to `.env.example` and
  made `Agent.check_delivery` read a bare delivery date.

### Demo blockers found on the live data (HANDBOOK six steps)

1. **Step 1 fails on rule set v4:** our agent passes the Driving Test 5 of 6 (`api_error`) and gets no
   licence, already at base `eca4100`. The sandbox gets the backend's rule set but runs the stand-in
   catalogue. Proposed fix: the sandbox uses `rules/school_kitchen.yaml`. Files: `app.py` start-up and
   `sandbox.py` (Alejo's).
2. **Every live order goes to a person:** the API gives delivery on the requested Monday with no time,
   and the rule set needs Monday 07:00. Allowing it needs a signed rule set with
   `delivery.date_only_same_day: allowed` (decision G).
3. **Step 5 "stock is short" cannot happen live** for milk 463335: the mock API reports 9,999,999,999
   available (decision I).
4. **Step 4's second order cannot go through alone:** rule set v4 has no no-allergen product as a usual
   product or approved substitute, and step 3's EUR 1,400 order uses up the EUR 800 weekly limit. Needs a
   new signed rule set, and step 4 before step 3 (or a fresh audit database).

### More open decisions (Alejo)

| | Decision | Default in the code |
|---|---|---|
| G | May a delivery on the needed day with no arrival time count as in time? | No: it goes to a person unless the signed rule set says `delivery.date_only_same_day: allowed` |
| H | Should an unknown delivery charge send an order to a person? | No: the quote and approval say "delivery charge unknown", the total excludes it |
| I | Does the mock API's 9,999,999,999 available count as known stock? | Treated as stock as reported |

## Cloud workload round (14 Nov, from 07:10)

| Session | Checkout | Branches | Cloud |
|---|---|---|---|
| Worker 5: sandbox fixes and agent benchmark | `junction2026-wt-bench/` | `fix/sandbox-harness` (reversible fixes in `backend/sandbox.py`, for Alejo's review), `exp/agent-bench` | Modal app `valio-agent-bench`, Volume `valio-agent-bench` |
| Worker 6: recognition at scale | `junction2026-wt-recognition/` | `exp/recognition` | Modal app `valio-recognition-exp`, Volume `valio-recognition` |

Credentials for cloud jobs: only the Modal Secret `verda-inference`. No Valio key is in the cloud; no
experiment calls a Valio API or places an external order. The board and the live backend stay unchanged.
Neither branch merges into `valio/integration-unattended` or `main` without review.
