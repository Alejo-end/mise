# Parallel work on the Valio device and shelf

Base: branch `valio/hw-checkpoint`, commit `80a8402` (14 Nov 2026, 05:05). It contains Alejo's
`review-14-nov` (not yet in `main`). Read this before you edit. It fixes scope, who owns which
file, the interfaces between the parts, and the ports and test data of each checkout.

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

Each worktree has its own `valio-challenge/.env` (git ignores it) with its `AUDIT_DB` and the test
PIN `1234`. Start a worktree backend with `uv run uvicorn backend.app:app --host 127.0.0.1 --port 8101`
(or 8201).

Only the integration session may: deploy to the board, restart the board or its app, change
`data/config.json` or the calibration on the board, point the board at a backend, record camera
sequences, change the files marked "integration", and merge. A worktree that needs any of these
asks and hands over a commit.

## File ownership

| Owner | Files |
|---|---|
| Worktree 1 | `device/python/shelf_detect.py`; in `device/python/main.py`: `camera_worker`, `preview_worker`, `loop`, `process_frame`, `on_calibrate`, the event payloads, the detection keys of `DEFAULT_CONFIG`; `device/assets/index.html` (slot rectangles, calibration, uncertain display); in `backend/agent.py`: `handle_event` and the pending-slot bookkeeping it uses (`pending_slots`, `pending_since`, `restore` lines for them); `tests/device/` except `sequences/`; `tests/test_duplicate_orders.py`; `tools/laptop-camera.html` |
| Worktree 2 | `rules/*.yaml` (`targets`), `backend/rules.py` (loading, saving and signing targets), `backend/shelf.py` (new), in `backend/app.py`: the event models (`SlotState` and friends), the `/events` normalisation, new `/shelf` endpoints; `web/` pages for the shelf; `tests/test_shelf_config.py` (new) |
| Integration | `pyproject.toml`, `uv.lock`, `device/app.yaml`, `device/sketch/`, `device/board-snapshot/`, `backend/audit.py`, `backend/valio.py`, `backend/gateway.py`, `backend/signing.py`, `fixtures/`, `tests/device/sequences/`, `tools/record_sequence.py`, this file, `TEAM_BRIEF.md` |

Anything else (the rest of `agent.py`, `sandbox.py`, `allergens.py`, the handbook): Alejo's, ask
first. Never edit a file the other worktree owns; write the need into the handover note.

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

- `handle_event` runs under the agent lock, so two events cannot both pass the pending check.
- A "full" reading less than `RESTOCK_MIN_S` (environment, default 120) after the slot's trigger is
  logged as "ignored: too soon after the order to be a delivery" and keeps the slot pending.
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

## Decisions that need Alejo

| | Decision | Why it matters | Default until he answers |
|---|---|---|---|
| A | May a person change which product a slot holds without a new, re-signed rule set version? | Slot targets sit in the signed rule set. An edit outside it weakens "every decision names the rule set version" | Read-only `/shelf`; product changes go through a new rule set version |
| B | Is the board here (`Junction`, all Modulinos answer, no `~/safe-kitchen-agent.backup-20261114`) the demo board, or is there a second UNO Q? | The handbook describes a board with that backup and no Modulinos answering | Treat this board as the demo board |
| C | How soon may a slot reorder after a refill in the demo (`RESTOCK_MIN_S`)? | Repeating demo step 5 inside that window gives "already in progress" | 120 s, and approve or skip to reset |

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
