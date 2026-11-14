# Shelf capture guide (canonical)

**Draft procedure — scripts tested; end-to-end physical capture not yet validated by Anatoly; subject to revision.**

The shelf layout of section 3a (one camera, one visible row per product) is a **proposed demo setup, pending team
agreement** (`decisions/2026-11-14-proposed-demo-capture-setup.md`).

This replaces the capture parts of `experiments/recognition/MISSING_DATA.md` (branch `exp/recognition`, written
for bread and yoghurt) and the instructions scattered over the 14 Nov chat. Products, commands and clip names
below were checked against the code on branch `valio/integration-unattended` at the commit that adds this file.

## What has been checked where (14 Nov, 09:50 EET)

| Item | In code (tests) | In the browser / on the board | On real recordings |
|---|---|---|---|
| Box editor: draft, refused save with red conflicts, valid save, recalibration required | Yes (`tests/device/test_layout_editor.py`) | Yes, Brave on the board page, 07:20 | No |
| Recorder refuses while boxes overlap, are being edited or wait for photos | Yes | Yes, against the live board, 07:55 | No |
| Enter marks on the recording clock, typed counts, labels.json, confirmation | Yes (`tests/tools/`) | One simulated 8 s take against the live board (deleted) | No |
| Product set chips / energy drink / milk, fitness bar unfamiliar | Yes | — | No |
| Exact-GTIN enrolment, owner-confirmed search, previous orders | Yes (synthetic records) | Lookups of real barcodes in the downloaded catalogue | No |
| Pre-upload check and upload to Modal | Yes | Upload to a scratch Volume path, then removed | No |
| Recording mode (no purchasing side effects) | Yes (`tests/test_app.py`) | Live backend runs in it | No |
| Board and laptop on the same network | — | Checked 10:08: laptop 172.20.10.4, board answers, backend sees the board online | — |
| Calibration and capture with the real products, one-row layout, dim light, light change | No | No | **No: untested** |

## 1. Last-known versions and network status

- **Board** (Arduino UNO Q, app `safe-kitchen-test`, laptop-camera variant): `main.py` c60b68271b87,
  `shelf_detect.py` 7095fd9ead9a, `index.html` 8fdd4027b2fb = commit 0d93b6a (tag `deploy/live-0d93b6a`), checked
  over USB at 09:43. The app container runs; the board is on the hotspot "Anatoly’s iPhone" as 172.20.10.2.
- **Backend** (laptop, port 8000): integration code at 851a992 (later commits are documentation), audit database
  `junction2026/valio-challenge/data/audit.sqlite`, **recording mode on** (`RECORDING_SESSION=shelf-recordings-20261114`),
  signed rule set v6 with all three slots unmapped for purchasing.
- **Network (historical):** at 09:43 the laptop was briefly on another network (10.0.34.44), so the board could not
  reach the backend. Resolved: Anatoly confirmed the same network, and at 10:08 the laptop had 172.20.10.4, the board
  answered and the backend saw it online. Keep running the reachability checks of section 2 before each session.

## 2. Setup and preflight

Physical: board on USB to the laptop (the recorder copies the board's photos over adb); camera on the laptop;
laptop and board on the hotspot "Anatoly’s iPhone" (Maximize Compatibility on). Open the camera bridge page
http://localhost:8765/laptop-camera.html in a browser tab and allow the camera (it is served by the main checkout,
`junction2026/valio-challenge/tools`; if it does not load: `cd /Users/bigboss/Ai/Claudecode/junction2026/valio-challenge/tools && python3 -m http.server --bind 127.0.0.1 8765`).

Run these from any terminal; every line must give the expected answer before calibrating:

```
ipconfig getifaddr en0                                   # 172.20.10.4
ping -c 2 172.20.10.2                                    # replies
~/Library/Arduino15/packages/arduino/tools/adb/32.0.0/adb devices    # 251305272  device
curl -s http://172.20.10.2:7000/state | python3 -c "import sys,json; s=json.load(sys.stdin); print('camera', s.get('camera_ok'), '| layout errors', s.get('layout_errors'), '| editing', s.get('setup'), '| needs photos', s.get('recalibrate'))"
curl -s localhost:8000/device | python3 -c "import sys,json; d=json.load(sys.stdin); print('board online', d['online'], '| recording', d['recording_session'], '| rule set', d['rule_set_version'])"
curl -s localhost:8000/shelf | python3 -c "import sys,json; [print(s['slot'], s['product_id'], s['product'][:40]) for s in json.load(sys.stdin)]"
```

Expected: camera True; board online True; recording `shelf-recordings-20261114`; rule set 6; product_id `None` for
slots 1 to 3. Before calibration "needs photos" lists the spots; after calibration it must be `{}` and the layout
errors `[]`.

## 3. Products

| Slot | Product | Observed identity (labels) | Purchasing |
|---|---|---|---|
| 1 | chips | `exp:chips` | unmapped (signed rule set v4) |
| 2 | energy drink | `exp:energy-drink` | unmapped (v5) |
| 3 | milk | `exp:milk` | unmapped (v6) |
| — | small fitness bar | `exp:fitness-bar`, labelled `unknown`; never in a reference clip | — |

Source: `tools/recording_products.json`. Observed identity and purchasing eligibility are separate: no recording
can lead to an order (recording mode, and the slots are unmapped).

## 3a. Proposed demo setup (pending team agreement)

- One stationary camera; the three zones above.
- Several units of one product stand **side by side in one visible row** inside its zone, with small gaps: no
  stacking, no unit behind another.
- The camera sees every unit and its identifying packaging. Check this on the board page before saving the photos.
- At least one product is tested with 2 or 3 identical units (whatever is available, for example 3 milk cartons).

This is a **visibility constraint for the prototype**, not a capability. The board's template detector compares a
zone with its empty and full photos; it estimates a fill level and is **not a validated object counter**. Stock
that is fully hidden (stacked, behind another unit, outside the view) may be unobservable: the system cannot know it
is there and does not reliably notice that something is hidden. Depth estimation and a second camera are future
options; no hardware is added now.

## 4. Optional: catalogue enrolment (separate from recognition labels)

Linking a physical product to its exact catalogue item once. It does not change the labels or purchasing, and
recognition must later work without a visible barcode. From `/Users/bigboss/Ai/Claudecode/junction2026-int/valio-challenge`,
in your own terminal (it asks for confirmation):

```
uv run python tools/enrol_product.py 1 --gtin <digits under the barcode>      # exact GTIN only
uv run python tools/enrol_product.py 2 --search "brand words"                 # you pick only the exact product, or 0
uv run python tools/enrol_product.py 3 --from-orders                           # from previous orders
uv run python tools/enrol_product.py unfamiliar --none                        # no exact catalogue item
```

## 5. Calibration (board page http://172.20.10.2:7000)

1. Step "Place the boxes": press **Edit boxes** (purchasing pauses while editing).
2. Drag each box by its **number tag** over its product spot: 1 chips, 2 energy drink, 3 milk; the round corner
   resizes. Wait for "No overlaps", then press **Save boxes**. A refused save keeps your moves and marks the
   overlapping boxes red.
3. With all three spots empty: **Shelf is empty: save photo**.
4. With every item you have in its spot, fronts toward the camera: **Shelf is full: save photo**.
5. Preflight again: needs photos `{}`, layout errors `[]`. Moving a box later asks for both photos again.

## 6. Recording

Working directory: `/Users/bigboss/Ai/Claudecode/junction2026-int/valio-challenge`. One command per clip:

```
uv run --with "python-socketio[client]" python tools/record_sequence.py <NAME>
```

What happens: it refuses if the boxes are not ready; shows the clip's steps; asks **how many items are in slots
1, 2, 3 right now** (type the actual numbers, for example `2 1 2`; empty clips skip this); waits for Enter to
start; shows a countdown with the next cue; you press **Enter at the moment you do each step** (a word + Enter adds
a free note); at the end it lists your marks and the item counts and asks you to confirm. Only "y" makes the
labels validated. Marks and frame times share one clock (seconds since the recorder connected); the 0.5 s before
and 1.5 s after each mark stay unlabelled. Saved to `data/recordings/reference/<NAME>/` or
`data/recordings/evaluation/<NAME>/` (outside git): frames, `meta.jsonl`, board `config.json` and photos,
`labels.json`, `README.md`.

**A and B takes:** every evaluation clip is recorded twice as separate recordings, `NAME-a` and then `NAME-b`.
Between them, take the items out and put them back by hand (different positions), so the two takes are
independent sessions. All `-a` clips form one session and all `-b` clips another; reference clips form their own.

**Failed take** (you pressed Enter at the wrong moment, a step is missing, or you answered "n"): the clip stays
with labels not validated. Keep it, rename it, and record again with the same name:

```
mv data/recordings/evaluation/NAME data/recordings/evaluation/NAME.failed-$(date +%H%M)
```

### Minimal plan, normal room light (about 15 clips, 20 minutes)

| Order | Clip | Seconds | What you do |
|---|---|---|---|
| 1 | `ref-empty-day` | 10 | all spots empty |
| 2 | `ref-full-day` | 10 | all items in place, fronts to the camera |
| 3 | `ref-s1-poses-day`, `ref-s2-poses-day`, `ref-s3-poses-day` | 20 each | at 5 s side (barcode hidden), at 10 s barcode to the camera, at 15 s angled or upside down (hidden) |
| 4 | `eval-identity-day-a`, then `-b` | 15 | untouched |
| 5 | `eval-removal-one-s3-day-a`, then `-b` | 20 | at 5 s take ONE milk out, keep it away |
| 6 | `eval-unfamiliar-s3-day-a`, then `-b` | 20 | at 5 s take all milk out and put the fitness bar there |
| 7 | `eval-barcode-hidden-day-a`, then `-b` | 20 | barcodes hidden; at 5 s all to their sides, at 10 s angled or upside down |
| 8 | `eval-hand-day-a`, then `-b` | 20 | hand over slot 1 at 3-6 s, slot 2 at 9-12 s, slot 3 at 15-18 s |

### Extension: several identical units of one product

Use two or three identical units in one zone, side by side in one visible row with small gaps (for example three
identical milk cartons in zone 3; type `1 1 3`):
`ref-counts-day` (one unit out of each slot at 5, 10, 15 s), `eval-removal-one-s3-day-a/-b` (3 to 2),
`eval-removal-all-s3-day-a/-b` (3 to empty). Other supported clips: `eval-swap-s2-s3-day-a/-b`,
`eval-move-s3-to-s2-day-a/-b`.

### Counting in the one-row setup: what the existing commands cover

Every guided clip asks for the **actual number of units** in each zone before it starts (type what is there, never
the target stock); the marked steps then change the counts. With the existing commands:

| Need | Supported clip | Count change |
|---|---|---|
| Actual counts, untouched | `ref-full-day`, `eval-identity-day-a/-b` | none |
| Removal of one unit | `eval-removal-one-s3-day-a/-b`; `ref-counts-day` (one unit from each zone) | n to n-1 |
| Removal of all units | `eval-removal-all-s3-day-a/-b` | n to 0 |
| Orientation, count unchanged | `ref-s1/s2/s3-poses-day`, `eval-barcode-hidden-day-a/-b` | none (units turned in place) |

**Not supported yet (needs a recorder change, `tools/recording_plan.py`):**
- *Return:* take one unit out and put it back within the same clip (count n, n-1, n). No such clip exists. A
  removal clip followed by a separate clip after putting the unit back is not equivalent: the return itself
  happens between clips and is not recorded.
- *Rearrangement without a count change:* shuffle the units or change their gaps within the zone. No such clip
  exists (`eval-swap-s2-s3-day` exchanges zones, which changes the identity per zone).
- *Layout tag:* labels do not yet carry "clean layout" or "stress"; until they do, the separation below is by clip
  name.

### Clean-layout counting versus stress tests: evaluate separately

| Group | Clips | Use |
|---|---|---|
| Clean layout (one visible row, no hands) | `ref-*` (normal light), `eval-identity-day`, `eval-removal-one-s3-day`, `eval-removal-all-s3-day`, `eval-barcode-hidden-day` | counting and identity results |
| Stress (kept out of the counting results) | `eval-hand-day` (obstruction), `eval-unfamiliar-s3-day` (unfamiliar product), `eval-move-s3-to-s2-day` and `eval-swap-s2-s3-day` (relocation), dim and light-change clips | uncertainty and robustness results |

The obstruction, unfamiliar-product and relocation tests stay as they are.

### Untested conditions

`ref-full-dim`, `ref-empty-dim`, `eval-identity-dim-a/-b` and `eval-light-change-a/-b` exist in the plan but are
**untested**, as are glare, other camera angles, other shelves, more than one unfamiliar item and look-alike
products (none available). Record them only after the normal-light plan works.

## 7. Validate and upload

```
uv run python tools/check_recordings.py              # OK/BAD per clip with the reason; failed takes are skipped
uv run python tools/check_recordings.py --upload     # uploads valid clips to valio-recognition:/inputs/recordings/<NAME>
```

Upload needs this laptop (authenticated Modal CLI). The cloud evaluation uses only human-validated clips and keeps
reference and evaluation sessions apart. The recognition watchers were stopped while no recordings existed; after
an upload, tell the integration session so it starts the evaluation runs.

## 8. End recording mode deliberately

Recording mode stays on until someone ends it on purpose. Tell the integration session, or in a terminal:

```
pkill -f "uvicorn backend.app:app"
cd /Users/bigboss/Ai/Claudecode/junction2026-int/valio-challenge && AUDIT_DB=/Users/bigboss/Ai/Claudecode/junction2026/valio-challenge/data/audit.sqlite uv run uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

Check: `curl -s localhost:8000/device` shows `"recording_session": null`. The slots stay unmapped, so even then no
purchase happens until the team signs purchasable products again (`PUT /shelf/{slot}` with a catalogue product).
