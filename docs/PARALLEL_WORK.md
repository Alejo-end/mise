# Parallel work on the Valio device and shelf

Base: branch `valio/hw-checkpoint`, commit `4f62200` (14 Nov 2026, 04:50). Read this before
you edit. It fixes who owns which file, the interfaces between the parts, and the ports
and test data of each checkout.

## Sessions

| Session | Checkout | Branch | Ports | Live or test data |
|---|---|---|---|---|
| Integration | `junction2026/` | `valio/hw-checkpoint`, merges | backend 8000 (the board points here), camera page 8765 | `valio-challenge/data/audit.sqlite`, live |
| Worktree 1: detection reliability | `junction2026-wt-detection/` | `wt/detection-reliability` | backend 8101 on 127.0.0.1 only | `valio-challenge/data/wt-detection.sqlite`, pytest `tmp_path` |
| Worktree 2: shelf configuration | `junction2026-wt-shelf/` | `wt/shelf-config` | backend 8201 on 127.0.0.1 only | `valio-challenge/data/wt-shelf.sqlite`, pytest `tmp_path` |

Each worktree has its own `valio-challenge/.env` (git ignores it) with its `AUDIT_DB` and
the test PIN `1234`. Start a worktree backend with
`uv run uvicorn backend.app:app --host 127.0.0.1 --port 8101` (or 8201).

Only the integration session may:
- deploy to the board, restart the board or its app, or change `data/config.json` and the
  calibration on the board;
- point the board at a backend;
- record new camera sequences (it needs the live board);
- change the files marked "integration" below, and merge.

A worktree that needs any of these asks the integration session and hands over a commit.

## File ownership

| Owner | Files |
|---|---|
| Worktree 1 | `device/python/shelf_detect.py`; in `device/python/main.py`: `camera_worker`, `preview_worker`, `loop`, `process_frame`, the event payloads; `device/assets/index.html` (slot rectangles and calibration); `backend/agent.py` **only** `handle_event` lines 85 to 129 (trigger, pending slot, restock); `tests/device/` except `sequences/`; `tests/test_duplicate_orders.py`; `tools/laptop-camera.html` |
| Worktree 2 | `rules/school_kitchen.yaml` (`targets`); `backend/rules.py` (loading, saving and signing targets); `backend/shelf.py` (new); `backend/app.py` (`Event` model, the `/events` normalisation, new `/shelf` endpoints); `web/` (pages to edit products per slot); `tests/test_shelf_config.py` (new) |
| Integration | `pyproject.toml`, `uv.lock`, `device/app.yaml`, `device/sketch/`, `device/board-snapshot/`, `backend/audit.py`, `backend/valio.py`, `fixtures/`, `tests/device/sequences/`, `tools/record_sequence.py`, this file, `TEAM_BRIEF.md` |

Any other file: ask integration first. Never edit a file the other worktree owns, even for
a one-line fix; write the need into the handover note instead.

## Interfaces (frozen for this round)

### 1. Slot event, device to backend (`POST /events`), version 2

```json
{"type": "slot_state", "slot": 2, "state": "empty",
 "fill": 0.37, "frame_id": 1234, "frame_time": "2026-11-14T04:39:46+02:00",
 "estimated_units": 4}
```

- `fill`: 0 to 1, from `shelf_detect.classify`.
- `frame_id`: a number that grows with every new camera frame since the app started.
- `frame_time`: when the newer of the two confirming frames arrived.
- `estimated_units`: kept so version 1 senders still work.

Worktree 1 makes the device send `fill`, `frame_id` and `frame_time`. Worktree 2 makes the
backend accept them, and compute units as `round(fill x target_units)` from its shelf
configuration when `fill` is present. The backend must keep accepting version 1 events
(no `fill`). Neither side changes the other side's code.

### 2. Shelf configuration, owned by the backend

The backend is the one source of truth for what a slot holds. Today two copies disagree:
the rule set says slot 1 has `target_units: 30, reorder_below: 8`, the board's config says
`target_units: 1`, so slot 1 always reads below the reorder level.

```
GET /shelf            -> [{"slot": 1, "product": "rye bread 500 g", "product_id": "MOCK-BREAD-RYE-500",
                           "group": "rye_bread", "target_units": 30, "reorder_below": 8}, ...]
PUT /shelf/{slot}     <- {"product_id": ..., "target_units": ..., "reorder_below": ...}
```

A change is checked against the catalog and the rules, needs an approver PIN, goes into the
audit log, and survives a restart. The targets are part of the signed rule set today:
worktree 2 keeps that story honest (re-sign on change, or move targets out of the signed
part with a log entry that says so).

### 3. Device configuration, owned by the device

`data/config.json` on the board: `rect`, `full_at`, `empty_below`, `present_diff`,
`frame_interval_s`, `camera_source`, `backend_url`, `manual_slot`, `temperature_limit_c`.
After worktree 2 lands, `product_hint` and `target_units` on the device are labels only.

### 4. Recorded sequences (read-only)

`tests/device/sequences/<name>/`: `frames/NNNNNN.jpg` (the board's 480 x 360 preview),
`meta.jsonl` (one line per frame and per board reading), `calibration/slotN_{empty,full}.npy`,
`config.json`, `README.md`. Recorded by integration with `tools/record_sequence.py`.

### 5. Replay model

`tests/device/replay.py`, `replay_baseline()`, models the board loop. It matches the board's
own readings on `steady-01`. If worktree 1 changes the loop on the board, it changes the
model in the same commit, so the tests keep describing the real board.

## Evidence at the checkpoint

`uv run pytest`: 91 passed, 5 strict xfails. Each xfail is a requirement the baseline breaks,
shown by running it:

| Test | Failure | Owner |
|---|---|---|
| `test_a_state_needs_two_different_frames_in_the_recording` | In `steady-01` every first state was confirmed by frame 0 seen twice: the board got about 1 frame/s and checks once a second | 1 |
| `test_a_stale_glitch_frame_cannot_change_a_slot` | One glitch frame, then the camera stops: `camera_ok` stays true for 2 s and the same frame confirms "empty" | 1 |
| `test_a_stale_glitch_frame_cannot_trigger_a_purchase` | The same, end to end: the agent orders 12 x yogurt for an unchanged shelf | 1 |
| `test_slot_rectangles_do_not_overlap` | Live calibration: the three rectangles nearly coincide, slots move together | 1 (validation), integration (recalibration) |
| `test_one_false_full_reading_between_lows_does_not_order_twice` | One "full" reading counts as restocked. Seen live: orders `ORD-68CE0E08` and `ORD-592FF708`, 26 s apart, same yogurt | 1 |

Also seen, not yet a test:
- Calibrating a slot resets its state, so the next frame is a "change" and can order.
- A board restart resends every slot state; with no order pending, that opens a request
  (seen 04:37:27 after a deployment). Expected behaviour given the reading, but noisy.
- The camera page runs at about 1 frame/s when its tab is in the background (browser timer
  throttling), so stale checks are the normal case, not the exception.
- Risk, not shown: `handle_event` checks and adds the pending slot without the lock; two
  events at the same moment could both order.

## Machine learning

The current detector stays as the baseline. Evaluate a model only against a recorded
sequence where the baseline misreads a **real** shelf change: record it, add it as a strict
xfail replay test, then compare model and baseline on every sequence. Adopt the model only
if it fixes those failures, adds none, and runs on the UNO Q within the 1 s check. So far no
recognition failure is shown: every failure above is staleness, configuration or restock
logic. `steady-01` shows a cup and a keyboard, not a shelf, so real-shelf recordings come first.

## Recordings integration still owes

Needs the person at the board, about 15 s each: a hand over each slot; remove and refill
each slot; the camera tab in the background; a light change. Point the camera at the real
shelf and recalibrate without overlapping rectangles first.

## Handover and merge

A worktree is ready when `uv run pytest` is green and every xfail it fixed is now a plain
test. Merge order: worktree 2 first (the backend accepts version 2 events, harmless while the
device sends version 1), then worktree 1. Integration then deploys to the board, records a
new sequence, and runs the replay suite against it.
