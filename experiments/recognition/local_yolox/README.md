# Local YOLOX-nano on the UNO Q — EXPERIMENTAL, observational

Status 15 Nov 2026, 06:40: **the hybrid local detector is kitchen-restock-yolox-nano-v3-7products** (owner's file
`kitchen-restock-yolox-nano-v3-7products (ONNX).onnx`, sha256 `ddeb2f00c303b649e0658ebe2d142cb65943d2961748d4818c7ce6c5e04e29ec`,
3,694,671 bytes; **not in git**; board: `~/valio-onnx/models/kitchen-restock-yolox-nano-v3-7products/model.onnx`, the
`active` link). Input float32 [1, 3, 416, 416]; output [1, 3549, 15] = box (decoded inside the graph) + objectness +
**10 class values** (the three `head.cls_preds` layers have 10 output channels; the name says 7 products). No class
names or preprocessing in the export (no metadata properties). The owner states it keeps old assets and has no Aito
class, intentionally; it need not cover every active product.

- Class order: **awaiting trainer metadata**. All 10 classes stay numbered and unmapped (`classes: null` in
  `models/kitchen-restock-yolox-nano-v3-7products.model.json`); their boxes are shown at once and identified by the
  automatic whole-frame Kimi check. Never inferred from the file name, the inventory order or predictions.
- Preprocessing: `rgb255`, top-left letterbox on 114 grey, **provisional** (runtime check on six saved raw frames:
  RGB 0-255 gave objectness 0.87-0.97 with boxes on packages, BGR 0-255 0.05-0.83, 0-1 scaling and ImageNet
  normalisation a flat 0.82-0.95 everywhere and no box at 0.3). Replace with the trainer's configuration.
- The parser reads the class count from the output shape (15 - 5 = 10) and never decodes boxes again.
- The previous model, kitchen-restock-yolox-nano-v1 (3 classes, 14 Nov props), stays in its own folder for
  diagnostics; the old `active` link target is backed up beside it.

Partial coverage (`local_model_coverage` in the shared product file, `ProductSet.coverage`): a model is never refused
for missing products. An active product is counted from a local box only through a verified class where the map
allows it and above its threshold; Aito is cloud-covered. The recognition action of both pages runs this model on the
board first (the box posts its frame to `POST /detect`), then the whole-frame cloud check (`shelf_service/hybrid.py`).

## Models on the board and their manifests

Each model has its own folder, `~/valio-onnx/models/<name>/`, with `model.onnx` and `model.json`, its manifest:
the product set it was trained for, its class order as identities of that set (or `null`), its preprocessing and
whether that is confirmed, and its sha256. `~/valio-onnx/models/active` links to the folder the service runs.
`serve.py` reports the manifest (`model_info` in `/health` and every `/detect`); a class list whose length differs
from the model's, or a manifest for another file, is reported as an error and treated as unknown. Compatibility is
decided by the explicit coverage map (since 15 Nov 06:40; before, a model had to have a class for every active
product): each verified class maps to an active, removed or old product, or stays unmapped; unmapped, low-score and
conflicting boxes always go to the cloud check. Classes keep the trainer's indices and names: a caramel class is not
renamed or renumbered, and its boxes go to the cloud check and are never counted.

- `models/kitchen-restock-yolox-nano-v3-7products.model.json` (here) = the manifest of the current model: classes
  `null` (10 classes, awaiting trainer metadata), preprocessing `rgb255` provisional, role hybrid-local.
- `models/kitchen-restock-yolox-nano-v1.model.json` = the previous model: set `kitchen-20261114-b`, classes `null`,
  preprocessing `bgr255` provisional, role diagnostics.

Installing the trainer's model when it arrives (from the trainer: the class order, the preprocessing and the export
settings; nothing is guessed). List the classes exactly in the trainer's index order, as identities of the product
file; a removed product keeps its own identity (`aimo:profeel-salted-caramel`), it is never dropped or renumbered:

```
mkdir -p ~/valio-onnx/models/<name> && cp model.onnx ~/valio-onnx/models/<name>/
# model.json: {"name": "<name>", "sha256": "<sha256 of model.onnx>", "product_set": "<set it was trained for>",
#              "classes": ["aimo:...", ...] in the trainer's class order (all of them), "role": "active",
#              "preprocessing": {"variant": "bgr255" | "rgb255" | "rgb_imagenet", "status": "confirmed"},
#              "note": "source of the class order and preprocessing"}
ln -sfn <name> ~/valio-onnx/models/active && systemctl --user restart valio-local-detect
```

The old model stays in its own folder for diagnostics (link `active` back to it to compare).

## Model v1 (previous, diagnostics)

| | |
|---|---|
| name | kitchen-restock-yolox-nano-v1 (supplied by Anatoly, trained by a team contact; **not in git**) |
| location | board: `/home/arduino/valio-onnx/models/kitchen-restock-yolox-nano-v1/model.onnx` (since 15 Nov; before: `~/valio-onnx/model.onnx`); original file `kitchen-restock-yolox-nano-v1 (ONNX).onnx` held by Anatoly |
| trained for | the three props of 14 Nov, archived product set `kitchen-20261114-b` (owner's statement); diagnostics only since 15 Nov |
| sha256 | `b16f112b65c079b0df3f768d3fa1587412fc5a871cf3c28583819f83389705ff` (3,689,211 bytes); check with `sha256sum model.onnx` |
| format | ONNX opset 11, no metadata properties |
| input | `images` float32 [1, 3, 416, 416] |
| output | float32 [1, 3549, 8], 3549 = 52² + 26² + 13² (strides 8/16/32); per row cx, cy, w, h (input pixels), objectness, 3 class values |
| decoding | **inside the graph**: (xy + grid) × stride, exp(wh) × stride; objectness and class values already sigmoid. No NMS in the graph. |

`yolox_local.py` therefore does not decode again: score = objectness × best class value, score filter (0.3 by
default), class-aware NMS (IoU 0.45), boxes divided by the letterbox ratio and clipped to the frame.

### Missing metadata (being obtained from the person who trained the model)

1. Class names in index order (0, 1, 2).
2. Training preprocessing: colour order (BGR or RGB), value range/normalisation, letterbox placement and pad value,
   resize method. We use standard YOLOX (top-left letterbox, pad 114, BGR, 0–255, bilinear) as a working default;
   it is a parameter (`variant`) reported with every result, not a finding.
3. The score and NMS thresholds used in validation, and the validation results.
4. The training data and the YOLOX exp/export configuration.

### Preprocessing variants tried (laptop, same frames) — none is settled by this

- `rgb_imagenet` (mean/std) gave no box at score ≥ 0.3 on these frames (max 0.002). That is not sufficient evidence
  to rule it out (few frames, and other settings such as colour order or padding may interact); the export
  configuration decides.
- `bgr255` and `rgb255` both give boxes, with different class numbers for the same object.
- Board and laptop give identical results for the same frame and variant.

## Board results (real frames, `bgr255`, score ≥ 0.3)

Runtime: Debian 13.1 aarch64, kernel 7.0.0, Python 3.13.5, **onnxruntime 1.32.0 (CPUExecutionProvider)**, numpy
2.5.4, Pillow 13.0.0, isolated venv `~/valio-onnx/venv` (no OpenCV).

Saved raw board frames (640×480; regression cases 14:04–14:12, enrolment photos, saved empty surface), 10 runs each:

| threads | init | median | p90 | max | CPU (cores) | peak RSS |
|---|---|---|---|---|---|---|
| 4 | 0.228 s | 256.7 ms | 286.1 ms | 329.8 ms | 3.44 | 100.0 MiB |
| 2 | 0.232 s | 260.6 ms | 265.6 ms | 280.3 ms | 1.98 | 100.6 MiB |

Two threads are as fast as four and leave half the CPU to the camera app, so the service uses 2.
(`results/bench-t2.json`, `results/bench-t4.json`)

Detections per frame (class number, score, box x-range in pixels). Scene contents are the owner's labels; no class
number is matched to a product.

| frame | scene (owner) | detections | localisation notes |
|---|---|---|---|
| empty surface | nothing | none (max raw score 0.039) | |
| chips alone | chips | class 0 0.95 x 202–498; class 0 0.37 x 194–247 | second box inside the first |
| energy drink alone | 1 drink | class 0 0.85 x 257–321 | |
| milk alone | milk | class 1 0.89 x 255–341; class 0 0.30 x 90–194 | second box on the background |
| 14:04 | milk, chips, 2 drinks overlapping | class 0 0.94 x 165–399; class 1 0.72 x 103–172 | one box spans the chips and both bottles |
| 14:05 | milk, chips, 2 drinks separated | class 0 0.92 x 163–477; class 1 0.80 x 103–171 | one box spans the chips and both bottles |
| 14:06 | milk, chips, 1 drink | class 0 0.94 x 164–338; class 1 0.83 x 103–171; class 0 0.52 x 413–463; class 2 0.33 x 413–465 | two boxes of different classes on the bottle |
| 14:12 | bar, chips, 1 drink | class 0 0.94 x 163–339; class 0 0.46 x 412–463; class 2 0.39 x 412–464 | **no box on the bar** (a missed object); two boxes on the bottle |

Few frames, not a test set; scores are not accuracy. Merged and missed objects are why the whole-image Kimi check
stays and why no local result is a count.

Live (30 s, app preview 480×360 JPEG, ~1 run/s, 2 threads): 29 runs, median 287.7 ms, max 328.3 ms, frame age at
use 82 ms, 0.86 cores, peak RSS 116.6 MiB; the app kept serving its preview (6.47 fps at the laptop) and pages
(65–175 ms). The camera was not on the shelf (venue view), so those detections were false positives; **no live
product assessment has been possible yet.**

## On the page (board app, `valio/demo-candidate`)

Panel "local model (experimental)" under "recognize shelf": *Run local model* (optional repeat every 2 s, stops
after 2 min) shows boxes, class numbers, scores, boxes per class, capture time, inference time, request id and
waits. No box is explicitly not evidence of an empty shelf. Each request has a 12 s timeout; a failure shows what
failed (no answer, HTTP error with the server's waits, or not reachable), a short health check that tells network
from service, failures in a row, the last good result and skipped repeat ticks.

*Ask Kimi about this region* (manual) posts one box (plus a 25 % margin, enlarged) and the whole frame with the box
outlined to the laptop service `/recognize/region`. The local class and score are not sent. Because Kimi sees the
whole frame, the answer is **not** an identification of the box alone: each item is listed with where it lies
(inside the box, around it, or no location), the answer carries no counts (`visible_units` and `inventory_counts`
are empty) and it is shown separately from the local results. The whole-image "Recognize shelf" is unchanged and
manual. No automatic hybrid: any later one must also handle missed objects and must not double-count.

## Service and start on boot

`serve.py` opens no camera: it listens to the app's own preview (socket.io on port 7000) only while detections are
requested and disconnects after 20 s without requests (listening costs ~0.3 of a core; idle ~0.01). Every
`/detect` answers within about 10 s (frame wait ≤ 4 s, queue wait ≤ 5 s, inference ~0.3 s) and logs one JSON line
(request id, status, waits, inference time). CORS allows only pages on port 7000. `GET /health` gives uptime and
request/error counts.

On the board it runs as the systemd **user** unit `valio-local-detect` (`valio-local-detect.service` here), started
at boot through lingering for the user `arduino`:

```
# once, on the board (files in ~/valio-onnx: venv/, yolox_local.py, serve.py, models/<name>/{model.onnx,model.json}, models/active)
python3 -m venv ~/valio-onnx/venv && ~/valio-onnx/venv/bin/pip install onnxruntime numpy pillow "python-socketio[client]"
mkdir -p ~/.config/systemd/user && cp valio-local-detect.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now valio-local-detect
loginctl show-user arduino -p Linger      # must be yes for start at boot without a login

systemctl --user status valio-local-detect          # state; "active (running)" only after READY=1
journalctl --user -u valio-local-detect -n 50       # one line per request
systemctl --user restart valio-local-detect         # or stop / disable
```

Behaviour: `Type=notify` (active only once the model is loaded and port 8200 is bound); `Restart=on-failure` after
15 s, at most 5 starts in 10 minutes, then it stays failed (no restart loop); a second copy cannot bind port 8200
and exits; `Nice=5` and `MemoryMax=400M` keep the camera app first.

This follows the board's own conventions: its services are systemd units (`arduino-app-cli`, `arduino-router`, …),
the user `arduino` already has lingering enabled and a running user manager, and there is no crontab. A user unit
needs no root.

Verified on the board 14 Nov 2026, 23:32–23:38 (systemd 257, no reboot):

| check | result |
|---|---|
| `enable --now` | enabled, active after 6.0 s (first start after boot), model init 0.32 s |
| idle | not connected to the preview, 0 TCP connections, 0.002 cores over 20 s, RSS 90 MiB |
| `systemctl --user restart` | ready again after 1.7 s |
| `kill -9` of the main process | `auto-restart`, active again 18 s later (RestartSec 15), restart counter 1 |
| second copy started by hand | exits with `Address already in use`; one service process |
| start limit (throwaway unit with the same limits, always failing, RestartSec 1) | 5 starts, then `Start request repeated too quickly`, stays failed; removed afterwards |
| `/detect` from the laptop | 200; frame wait 207 ms on the first request (preview connect), 0 ms after; inference 247–302 ms |
| idle disconnect | `preview disconnected (idle)` 20 s after the last request |
| CORS | allows `http://172.20.10.2:7000`, no header for other origins |

Manual runs (stop the unit first, so there is no second copy):

```
venv/bin/python bench.py model.onnx frames/ --threads 2 --repeats 10    # saved frames
venv/bin/python live.py model.onnx --seconds 30 --threads 2             # app preview, frames not saved
```

## Repeat-mode stall, 14 Nov 17:40 (finding)

One browser repeat run produced neither a result nor an error. The laptop's Wi-Fi log shows the hotspot link
degrading at 17:39:52 (no frames received, transmit failures) and dropping at 17:40:02.565 (`link_off`, involuntary;
it came back on another channel). The page's request had no timeout, and its busy flag then skipped every later
repeat tick silently. Fixed on the page (12 s timeout, error with diagnostics, ticks never blocked) and in the
service (bounded waits, request ids in the journal).

Re-run 14 Nov 23:37 on the board page (fresh load, repeat on), with the service paused (`kill -STOP`) for 22 s to
stand in for a request that never answers: after 12 s the page showed "no answer within 12 s (request cancelled)",
the request id, failures in a row, the last good result, the skipped ticks and "Health check now: board not
reachable on port 8200 (no answer in 3 s)"; repeat stayed on, and the first request after `kill -CONT` returned a
normal result. The service had dropped and re-opened its preview connection by itself; no restart was needed.
