# Shelf recognition: what runs where, and how to deploy it

The shelf camera reports what it sees (visible items), product by product. It is an **observation**: it never changes
stock, never starts an order and grants no purchasing authority, also in continuous mode. Purchasing stays with the
signed rule set and the head chef's approval.

## The parts

| Part | Runs on | Port | Code |
|---|---|---|---|
| Kitchen agent backend and demo page (`/approve`, "Visible items") | laptop | 8000 | `backend/`, `web/approve.html` |
| Recognition service: Kimi K3.1 Pro on Verda with the labelled reference photos; reconciles the local boxes and Kimi's objects of one frame | laptop | 8100 | `experiments/recognition/shelf_service/` |
| Box app: camera, the one recognition coordinator (`device/python/visible.py`), camera setup page | board (UNO Q, App Lab app `user:safe-kitchen-agent`) | 7000 | `device/` |
| Local detection: YOLOX-nano ONNX, systemd user unit `valio-local-detect` | board | 8200 | `experiments/recognition/local_yolox/` |
| Product set: the four active products, thresholds, excluded products, local model coverage | shared file | | `products/active.json` (the box reads it as `assets/products.json`) |

Active inventory (set `kitchen-20261115-b`): PROfeel Strawberry, PROfeel Mint Chocolate, Bonne orange juice, Aito
Barista oat drink, with the owner's catalogue EANs and ordering-unit conversions in `products/active.json`. PROfeel
Salted Caramel and the old props of 14 Nov are recognised as **not in inventory** (grey), never counted, never
purchasable. Where a product stands does not matter: there are no positions.

## One scan

1. **Capture**: the newest camera frame (never older than 2 s); its capture time is the time shown.
2. **Local**: the board's model on exactly that frame (`POST /detect` with the JPEG). Its boxes appear at once, amber,
   "identity pending", when the scene differs from the shown result.
3. **Settled**: a second frame 0.8 s later must show the same scene (`device/python/scene.py`); a moving scene is
   followed for up to 8 s, and a scene that never settles gets no cloud check.
4. **Cloud**: `POST /recognize/hybrid`. On the first scan and every changed, settled scene, Kimi checks the whole
   frame (with the reference photos and the owner's package descriptions); while the scene is unchanged that check is
   reused without a cloud request (at most 10 minutes). One cloud request at a time.
5. **Reconcile** (`shelf_service/hybrid.py`): a local box and a Kimi object at the same place are one object, never
   two counts. A local identity stands only for a verified class of a locally covered product above its threshold
   (`kimi_at_or_below`: 0.95 for the shakes, 0.85 for Bonne and Aito); unmapped, low-score, removed/old and conflicting
   boxes take Kimi's decision; a confident local identity that Kimi contradicts becomes one ambiguous object that could
   be either. Products the local model does not cover or missed (Aito) come from Kimi. An escalated box Kimi's
   whole-frame check did not cover gets one crop check.
6. **Publish**: numbered boxes on the analysed picture and a numbered list; each object green (recognised inventory
   product), amber (unresolved) or grey (not in inventory); each line says who identified it: local vision, Kimi, both,
   or a conflict. A failure keeps the last observation with its own capture time; a failed cloud check leaves the
   counts uncertain, never zero.

## Continuous recognition

Both pages have **Recognize now** (setup page: "Recognize shelf"), **Start continuous recognition**, **Stop** and
**Pause between completed scans** (seconds, default 5, 1 to 600). The loop runs on the box, not in a browser: two
open pages, a reload or Start pressed twice never make a second loop, and it keeps running with only the demo page
open (or none). Each cycle is one complete scan (steps 1 to 6); the pause starts after the result or the failure is
published. Status: analysing locally, checking with Kimi, waiting for the next scan, stopped, error.

- "Recognize now" while a scan runs joins it (its progress is shown; nothing is queued); during the pause it starts
  the next scan at once.
- Stop prevents further scans. The scan running at that moment finishes, but its result is discarded, so a late
  answer never replaces the last observation (which keeps its own time). A request during that last scan keeps it.
- A scan that does not finish within 150 s is released and its late answer ignored; no second cloud request is sent
  while the old one is still open. After a failure the pause grows (5, 10, 20 … at most 60 s) until a scan succeeds.
- The demo page sends Start/Stop through the backend (`POST /observations/continuous`, delivered by the box's command
  poll, about 1 s); the setup page talks to the box directly. Continuous mode stops when the box app restarts.

## Laptop

Run from `valio-challenge/`; credentials stay in `.env` (never committed).

```
# backend (the demo's settings: recording mode, observe-only shelf events)
RECORDING_SESSION=shelf-recordings-20261114 SHELF_EVENTS=observe uv run uvicorn backend.app:app --host 0.0.0.0 --port 8000

# recognition service
cd experiments/recognition
export SHELF_PRODUCTS=$PWD/../../products/active.json          # the shared product file (also the default)
export VERDA_INFERENCE_API_KEY="$(grep '^VERDA_INFERENCE_API_KEY=' ../../.env | cut -d= -f2- | tr -d '"')"
uv run --project shelf_service uvicorn shelf_service.app:app --host 0.0.0.0 --port 8100
```

`SHELF_CORS_ORIGINS` (default `http://172.20.10.2:7000`) names the board page. Reference photos are saved from the
camera setup page ("Save reference photo") into `shelf_service/store/enrolment/` (not in git); the two most recent
manual photos per product go to Kimi. `python -m shelf_service.replay <frame.jpg> [--local-json <answer>]` replays a
saved raw frame. Tests: `uv run pytest` here and `uv run --project shelf_service python -m pytest shelf_service/tests`
in `experiments/recognition`.

## Board

**Before any change**, back up: `tar czf` of `~/ArduinoApps/safe-kitchen-agent` without `data/`, and `sha256sum` of
`data/*` (calibration and config stay untouched). For the local detector, back up `~/valio-onnx` without the model
files and note where `models/active` points.

Box app (`~/ArduinoApps/safe-kitchen-agent`): copy `device/python/*.py`, `device/assets/*`, `products/active.json`
as `assets/products.json`, `device/sketch/` (with `src/`) and `device/app.yaml`; then
`arduino-app-cli app stop user:safe-kitchen-agent` and start it **detached**:
`setsid nohup arduino-app-cli app start user:safe-kitchen-agent > /tmp/app-start.log 2>&1 &`. Never put the start
under `timeout`: an interrupted start cancels the sketch upload and leaves the app stopped. The page's "Update from
the laptop" button fetches the same files from the backend (`/device/files`). Box settings (`data/config.json`):
`backend_url` (the laptop), `local_model` "on", `local_detect_url` "" (127.0.0.1, else the container's gateway),
`slot_detector` "off".

Local detector (`~/valio-onnx`; install once as in `experiments/recognition/local_yolox/README.md`, "Service and
start on boot": venv, `serve.py`, `yolox_local.py`, the user unit, lingering for start at boot). The model:

| | |
|---|---|
| name | `kitchen-restock-yolox-nano-v3-7products` (the owner's file; **not in git**) |
| board path | `~/valio-onnx/models/kitchen-restock-yolox-nano-v3-7products/model.onnx`, `models/active` links to its folder |
| sha256 | `ddeb2f00c303b649e0658ebe2d142cb65943d2961748d4818c7ce6c5e04e29ec` (3,694,671 bytes) |
| manifest | `model.json` beside it = `local_yolox/models/kitchen-restock-yolox-nano-v3-7products.model.json` |
| input / output | float32 [1, 3, 416, 416] / [1, 3549, 15]: decoded box, objectness, **10** class values |
| class order | **awaiting the trainer's metadata**: all 10 classes stay numbered and unmapped; Kimi identifies their boxes |
| preprocessing | RGB 0-255, top-left letterbox on grey 114, **provisional** (not in the export) |
| coverage | partial by design (old assets kept, no Aito class): `local_model_coverage` in `products/active.json` |

```
mkdir -p ~/valio-onnx/models/kitchen-restock-yolox-nano-v3-7products        # then copy model.onnx and model.json in
sha256sum ~/valio-onnx/models/kitchen-restock-yolox-nano-v3-7products/model.onnx
ln -sfn kitchen-restock-yolox-nano-v3-7products ~/valio-onnx/models/active && systemctl --user restart valio-local-detect
curl -s localhost:8200/health                                               # model_info: 10 classes, class_map
```

The previous model (`kitchen-restock-yolox-nano-v1`) stays in its own folder: link `active` back to roll back. When
the trainer's class order arrives, write it into `model.json` (`classes`, identities of `products/active.json` in the
trainer's index order, removed and old products under their own identities) and restart the unit; nothing else
changes.

## Pages

- Demo page: `http://<laptop>:8000/approve` (kitchen role), section "Visible items".
- Camera setup page: `http://<board>:7000/`.

On the team hotspot the laptop is usually `172.20.10.4` and the board `172.20.10.2`.
