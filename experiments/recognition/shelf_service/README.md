# Observational shelf recognizer — Writer R, round 5

This service detects separated foreground components on one fixed-camera surface,
embeds their appearance with the existing pinned DINOv2-small `pooler_output`, and
compares against manual reference photos using `recog.core.match`. Device is **CPU**,
with four Torch threads. It never calls the board, purchasing backend, Valio APIs,
orders, prices or availability. Slot, inventory and purchase-history priors are absent.

## Run

From `valio-challenge/experiments/recognition`:

```sh
export UV_PROJECT=shelf_service
uv sync --locked
uv run uvicorn shelf_service.app:app --host 0.0.0.0 --port 8100
```

The project selection is necessary because only `shelf_service/` is owned by this
writer; the shared project's dependency files are unchanged. The local project and
lockfile install Torch, Transformers, OpenCV, FastAPI, NumPy, Pillow and HTTPX.
Use **one Uvicorn worker** for the prototype's in-process operation lock and OFF
rate gate. Integration owns deployment and proxy/UI wiring. No server is left running
by this writer. No credentials are needed for the ungated pinned model.

First start downloads model weights into the ignored `store/model_cache`; the model
is loaded at startup. `/health` remains available with `model_loaded: false` and a
reason if loading fails. Non-product paths (background capture and manual photo
storage) remain usable. The model revision is
`ed25f3a31f01632728cabb09d1542f84ab7b0056`, `facebook/dinov2-small`, Apache-2.0.
MPS was not used or claimed faster.

Configuration, all optional:

| Setting | Default | Purpose |
|---|---|---|
| `SHELF_STORE` | `shelf_service/store` | Private photos, metadata, cache and run results |
| `SHELF_PRODUCTS` | `valio-challenge/products/active.json` of this checkout (absent on this branch: set it to the demo branch's file) | The active product set (shared file, schema shelf-product-set/2): identities, labels, flavour families, routing thresholds, ordering units. Required; no built-in list. Re-read when it changes |
| `SHELF_ENTRY_SIDE` | `top` | Camera entry border: top/bottom/left/right/any |
| `SHELF_MAX_AREA` | `0.45` | Maximum component area / monitored surface area |
| `SHELF_USE_OFF_IMAGES` | `0` | Enables explicitly associated OFF image references |
| `OFF_CONTACT` | unset | App-contact addition to OFF User-Agent; not logged or stored |

Other detection parameters are in `DetectionConfig`. They are engineering defaults,
not measured product accuracy: Gaussian blur 5 px, max-channel difference >25,
3 px opening/closing, minimum area max(16 px, 0.2% of surface), global change >=70%,
entry-border tolerance 3 px. Package touching warning starts above 1.65 times the
largest reference package's foreground area for that background. Unknown camera
entry direction must be configured by integration. A hand entirely inside the view
and smaller than the maximum is not guaranteed to be detected as a hand. Touching
small items may be smaller than one large reference package. These are limitations
of a component detector, requiring real-scene tests.

## Products, automatic cloud fallback (15 Nov)

The products come only from the shared product-set file (`SHELF_PRODUCTS`, `product_set.py`). The 14 Nov
regression replays, the synthetic evaluation and the enrolment pipeline's tests use the archived props set with its
original identities. PROfeel flavours are told apart only by readable flavour text; an unreadable flavour stays
`flavour-uncertain` (counted per family in `family_units`, never as a product). Counts are consumer units with the
catalogue's ordering unit beside them (`order_units`).

- `GET /products`: the active set as this service reads it.
- `POST /recognize/object`: one selected box resolved for counting (`vision.resolve_selected`): only items inside the
  box count, items around it are ignored; dark, obstructed or unclear answers are `uncertain`, never zero.
- `POST /reconcile` (`reconcile.py`): one frame of a compatible local model (its class order covers every active
  product; extra classes such as a removed Salted Caramel keep their index and name, go to the cloud check and are
  never counted), with its detections.
  Overlapping boxes are one object; an object is counted locally only above its product's `kimi_at_or_below`, with
  agreeing classes and no close shake flavour; every other object goes to the cloud model with a crop and context
  of the same frame, one request at a time, with a timeout (`routing.cloud_timeout_s`). Answers are reused only while
  the object's box and crop are unchanged; late answers never replace newer ones; failures stay uncertain and are
  retried after 30 s. Answers at once (409 for an older frame of the same page session or a model for another set).
  `GET /reconcile/state` shows the queue. Objects the local model missed are not here: "Recognize shelf" stays.
  Observational only: no stock events, no orders.

### Visual matching (15 Nov, 05:20)

Step 2 is visual for active sets: every object not settled by its readable text is compared with the kitchen's
labelled reference photos (the two most recent manual reference crops per product from `store/enrolment`, plus the
owner's description of each package in the product file, `visual_cue`) using the object's own crop from the same
frame. Readable text is evidence, not a condition: a brand with its own flavour phrase decides alone (PROfeel
MANSIKKA, aito BARISTA); generic words (PREMIUM, TÄYSMEHU, 100%, HIGH PROTEIN, DRINK; `text_matching.generic_words`)
neither support nor contradict; a flavour or fruit word of another product (`flavour_words`) rules that product out.
A visual answer is not accepted on colour alone, against contradicting text, or against another brand read on the
package. Uncertainty is kept to the products an open object could be (`could_be`); a recognised object keeps its
own status. Archived (v1) sets keep the text-only step of 14 Nov. `python -m shelf_service.replay <frame.jpg>`
replays saved raw frames with real calls (never adding them to the references).

## API and enrolment

1. `POST /background` with `{image_b64, surface:[x,y,w,h], captured_at}`. Use a JPEG
   of an empty surface from the same camera. Coordinates are normalised to the full
   image; capture time must be ISO8601 with a timezone. Raw base64 and the
   `data:image/jpeg;base64,` prefix both work. Limits: 12 MB encoded JPEG, 16 MP.
2. `POST /enrol/photo` adds `identity`, `role: reference|heldout`, and optional
   `session_id`. Only `exp:chips`, `exp:energy-drink`, `exp:milk` are accepted.
   **The fitness bar cannot be enrolled.** Supply at least two distinct captures
   per enrolled identity. Exactly one separated unobstructed component is required.
   Zero/multiple/obstructed components, duplicate photos and cross-split sessions
   return 409. Bad JPEG/schema/identity returns 422.
3. `POST /recognize` takes the same photo body as `/background`. It returns the
   requested objects, top-three identity scores, full-frame normalised boxes,
   per-identity visible-unit counts, uncertainty and reasons, model revision/device,
   gallery photo counts, stage timing and the exact thresholds/provenance used.
   Unknown, ambiguous and obstructed components have `identity: null`. Only known
   separated components count; counts never imply hidden stock. An empty matching
   background may confidently report zero. Missing background, changed surface,
   frame size, global lighting change or model failure yields uncertainty.
4. `GET /enrolment` lists counts by role, OFF association, current read-only
   `catalogue_match`, and the visible-package/unit note. Catalogue mapping never
   grants purchasing eligibility. `GET /health` reports startup/model and storage state.

Reference photos alone calibrate the model, with per-photo leave-one-out positives,
other-identity reference impostors, and five empty-background/non-product crops.
Acceptance is max(hardest negative + 0.01, weakest positive - 0.02), capped at
1.0001 to permit rejecting everything. Margin is max(0.01, half the smallest
same-vs-other identity LOO gap). These fixed guard bands are provisional. Every
result records reference fingerprints, counts, negatives and calibration status.
An incomplete gallery returns unknown until all enrolled identities have at least
two references. Heldout photos and the fitness bar never enter calibration, area
estimation, cached embeddings or the gallery. No auto-enrolment occurs.

`store/enrolment/<identity>/<role>/*.jpg` contains component crops;
`<role>/frames/*.jpg` contains full frames, and `meta.json` carries source, role,
position, timestamps, fingerprints and optional recording session. `background.jpg`
and `background.json` identify the monitored surface. Each recognition writes
`runs/<id>.json` and the corresponding full-frame JPEG. Treat `uncertain` as a
manual-review state. The service has no route for publishing a stock event.

## Optional Open Food Facts

`POST /enrol/off/{barcode}` works with no body for cached metadata only. An optional
body `{identity:"exp:milk", use_image:false}` explicitly associates the result.
Names never imply an identity or Valio SKU. To use its cached front image, the
association needs `use_image:true` **and** service setting `SHELF_USE_OFF_IMAGES=1`.
Set that setting to 0 to disable all OFF gallery images. Manual references and
backgrounds still set thresholds; OFF images do not fit thresholds or package size.

Only GET product lookups (v3.6, fields `product_name,brands,quantity,image_front_url`)
and optional GETs from `images.openfoodfacts.org` occur. The app identifies itself
as `ValioShelfPrototype/0.1 (Junction 2026 hackathon prototype)`, adding a contact
only if configured. At most 10 network requests in any 60 seconds, including images;
timestamps persist across restart. Product responses including errors/missing items
are cached under `off_cache/<barcode>.json`; images and image-fetch status are cached
beside them, with source URL, retrieval time and licence metadata. No retries or
live OFF requests were made in development; tests use mocked HTTP. A cached failure
is returned on repeated lookup; integration can remove that barcode cache file to
retry later. Manual enrolment never depends on OFF availability.

Documentation read on 2026-11-14, before implementation:

- [API introduction](https://openfoodfacts.github.io/openfoodfacts-server/api/):
  current v3.6, custom User-Agent, read product endpoint, request limits and caching;
  database ODbL, individual contents Database Contents License, images CC BY-SA.
- [API tutorial](https://openfoodfacts.github.io/openfoodfacts-server/api/tutorial-off-api/):
  barcode lookup, `fields` projection, missing-product responses (older v2 examples).
- [v3 reference](https://openfoodfacts.github.io/openfoodfacts-server/api/ref-v3/).
- [OFF licence guide](https://openfoodfacts.github.io/openfoodfacts-server/api/tutorials/license-be-on-the-legal-side/)
  and the introduction's [CC BY-SA 3.0 image licence link](https://creativecommons.org/licenses/by-sa/3.0/deed.en).

OFF data and photos retain their source/licences and do not replace Valio prices,
availability, mappings or signed purchasing rules.

## Evaluate and test

From `experiments/recognition`, with `UV_PROJECT=shelf_service` exported:

```sh
uv run pytest -q shelf_service/tests
uv run python -m shelf_service.evaluate --synthetic
uv run python -m shelf_service.evaluate --recordings /Users/bigboss/Ai/Claudecode/junction2026-int/valio-challenge/data/recordings/evaluation
```

Synthetic runs make a fresh, isolated `store/synthetic/<id>/` gallery from **steady-01
cup/desk texture composites** and print separate crop-identity, frame-count,
demonstration and latency tables. Invented identities are proxies, not real chips,
milk, drink or fitness-bar ground truth. Four checks exercise movement, unfamiliar
rejection, removal from two to one visible unit, and a border-entering hand proxy.
Images and raw runs stay ignored. `results/synthetic/` contains the shareable
aggregate report. The real table is separate and idle until labelled data exists.

Real evaluation accepts manual heldout crops and human-validated recordings
(`validated:true`, `validation_source:"human"`, nonempty `validated_by`,
`role:"evaluation"`, distinct `session_id`). Use session IDs for manual captures too.
Model-generated label proposals are refused. Duplicate reference frames and reference
sessions are excluded. The integration `shelf-recording-labels/1` format is supported:
`config.json` slot rectangles and `meta.jsonl` frames, plus labelled time windows
with `slots`, `identities`, `counts`. Only explicitly single-item windows enter crop
identity scoring. Explicit counts for every slot are required for frame counting;
slot inventory/presence is never converted into a guessed count. Actual observed
identity, including the fitness bar, is read from the human labels, not assignment.

A more precise native object-label format is also supported:

```json
{
  "validated": true, "validation_source": "human", "validated_by": "reviewer",
  "role": "evaluation", "session_id": "eval-session-2", "scenario": "moved-product",
  "surface": [0,0,1,1],
  "frames": [{"file": "frames/000001.jpg",
    "objects": [{"identity":"exp:milk", "box":[0.2,0.3,0.1,0.3]}],
    "visible_units":{"exp:milk":1,"exp:chips":0,"exp:energy-drink":0},
    "obstructed":false}]
}
```

Keep the fixed camera and background appropriate to each evaluation capture.
Recording `scenario` identifies demonstrations; movement needs at least two distinct
positions, removal needs explicit 2 then 1 counts in the same recording, and hand
checks require an explicitly obstructed frame/window. Missing evidence stays idle.
The service does not tune thresholds from these results. Stage timings measure
service detection, embedding (including gallery rebuild when necessary), matching,
and total through frame persistence; total excludes HTTP decoding/queue time and
final JSON write. Reports include cached-model load and first-gallery recognition
separately from warm calls. No local-board feasibility or real-shelf accuracy claim
is made from laptop timings or synthetic fixtures.

## Hybrid recognition (15 Nov, 06:40)

`POST /recognize/hybrid` is what the box's recognition action calls (both pages, one coordinator on the box,
`device/python/visible.py` on the demo branch). The box runs the local model on the board on its newest frame and
shows its boxes at once; once the scene is settled it sends that frame with the local detections and `cloud`:
`whole-frame` on the first observation and on every changed, settled scene, `reuse` (the previous whole-frame
objects, no cloud request) while the scene is unchanged. `hybrid.py` reconciles the local objects and the cloud
objects of the same frame into one object list: a local box and a cloud object at the same place are one object; a
confident, verified, locally covered product keeps its local identity (and an explicit conflict with a different
cloud identity: one ambiguous object that could be either, never two counts); unmapped (no trainer metadata),
low-score (at or below `kimi_at_or_below`), removed/old and conflicting boxes take the cloud's decision; cloud objects
without a local box (Aito, which the local model does not cover) are added once. An escalated local box the
whole-frame check did not cover gets one crop check (at most two per frame, answers reused while the box and its
picture stay the same); a crop check that finds no package dismisses that local box. A failed or skipped cloud check
leaves every product "uncertain"/"at least" (`scene_unverified`), never zero. Visible items say who identified each
object (local vision, Kimi, both, or a conflict). One cloud model request at a time (`cloud_gate`); merging never
waits for it. Replay: `python -m shelf_service.replay <frame.jpg> --local-json <that frame's local answer>`.
