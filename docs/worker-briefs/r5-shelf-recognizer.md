# Round 5: shelf recognition prototype (14 Nov, from 11:30 EET)

Rules: `r3-cloud-round.md` (patch delivery: do not commit, never work around read-only `.git`). Observational only:
nothing here may send events to the purchasing backend, change rule sets or orders, or touch the board. The
integration session wires the board UI (a "Recognize shelf" panel on the board page) and deploys; you build the
recognition service it calls.

## Writer R (Codex): recognition service. Worktree `junction2026-wt-recognition`, branch `exp/recognition`

Owns new `valio-challenge/experiments/recognition/shelf_service/` (+ its tests) only. Reuse, do not survey:
`recog/core.py` (`match`: cosine nearest neighbour per label, `accept` threshold, `margin`, returns "unknown"),
`recog/models.py` (pinned `facebook/dinov2-small` revision, Apache-2.0), the embedding code of `scale_app.py`
(`pooler_output`), on the **laptop CPU** (`device="cpu"`; use `mps` only if it works and is faster; record which).
No new model families. Products: `tools/recording_products.json` on branch `valio/integration-unattended`
(read it from `/Users/bigboss/Ai/Claudecode/junction2026-int/valio-challenge/tools/recording_products.json`):
identities `exp:chips`, `exp:energy-drink`, `exp:milk`; the fitness bar `exp:fitness-bar` is never enrolled.

### Service (FastAPI, `uv run uvicorn shelf_service.app:app --host 0.0.0.0 --port 8100` from `experiments/recognition`)

Storage under `experiments/recognition/shelf_service/store/` (gitignored): `background.jpg` (empty surface),
`enrolment/<identity>/reference/*.jpg` + `meta.json`, `enrolment/<identity>/heldout/*.jpg` (evaluation only, never in
the gallery), `off_cache/<barcode>.json` + images, `runs/<time>.json` (every recognition result with its frame).

- `POST /background` body `{"image_b64": JPEG, "surface": [x,y,w,h] normalised, "captured_at": iso}`: store the
  empty-surface photo for this surface. Returns `{"ok": true}`.
- `POST /recognize` body `{"image_b64", "surface", "captured_at"}` returns
  `{"captured_at", "surface", "objects": [{"box": [x,y,w,h] normalised to the full frame, "status": "known" |
  "unknown" | "ambiguous" | "obstructed", "identity": id or null, "label": text, "candidates": [{"identity",
  "similarity"}] (top 3), "area_px"}], "visible_units": {identity: n}, "uncertain": bool, "reasons": [text],
  "timing_ms": {"detect", "embed", "match", "total"}, "model": {"name", "revision", "device"},
  "gallery": {identity: n_reference_photos}, "note": "similarities are model scores, not measured accuracy"}`.
- `POST /enrol/photo` body `{"identity", "image_b64", "surface", "role": "reference" | "heldout", "captured_at"}`:
  find the single largest object on the surface (as in detection), save its crop (and the full frame) under the
  identity and role; refuse if zero or several objects are found (say so).
- `GET /enrolment`: per identity: photo counts per role, Open Food Facts data present or not, Valio mapping (from
  `recording_products.json` `catalogue_match`, read-only), unit/pack note.
- `POST /enrol/off/{barcode}`: optional read-only Open Food Facts lookup. Read the current OFF API documentation
  first (https://openfoodfacts.github.io/openfoodfacts-server/api/) and record what you read. Identify the app with a
  User-Agent `ValioShelfPrototype/0.1 (Junction 2026 hackathon prototype)`, plus a contact only if the environment
  variable `OFF_CONTACT` is set (never invent one). Read-only product endpoint, fields limited to what is needed
  (name, brands, quantity, image URLs). Cache every response; at most 10 requests per minute; on failure or missing
  product, return that and continue: missing OFF data never blocks manual enrolment. Keep source URL, retrieval time
  and the licence notes OFF publishes (database ODbL, images CC BY-SA) with each cached item; OFF images may be used
  as extra gallery references only when marked `source: open-food-facts` and can be switched off. OFF never replaces
  Valio prices, availability, SKU mapping or purchasing rules.
- `GET /health`: model loaded, gallery sizes, background present.

### Recognition pipeline

1. Detection on the monitored surface only: difference to `background.jpg` (blur, threshold, morphology), connected
   components above a minimum area. A component that touches the surface border on the side the camera's view
   enters, or is larger than a configurable maximum, is **obstructed** (a hand or arm); a frame-wide change
   (lighting) makes the whole result **uncertain**. Separated packages only: a component much larger than a single
   enrolled package is marked **ambiguous** ("possibly several touching items").
2. Identity: crop each component, embed with DINOv2-small, `match` against the reference gallery. **unknown** when
   below the accept threshold; **ambiguous** when the top two identities are within the margin. Thresholds come from
   the reference gallery only (leave-one-out among references plus background and non-product crops as negatives),
   recorded in the result; never tuned on held-out photos or on the fitness bar.
3. No priors: the slot or previous location, inventory membership and purchase history are **never** used for
   identity. A known product moved anywhere on the surface keeps its identity because only appearance is used.
4. Visible-unit counting = number of separated components identified as an identity. Never infer hidden stock.

### Evaluation (`shelf_service/evaluate.py`), separate tables

Identity (per crop, held-out photos and labelled recordings only: `data/recordings/evaluation/*/labels.json` of
the integration worktree when they exist) and visible-unit counting (per frame) are reported separately, plus the
four demonstrations: moved product keeps identity; the fitness bar is not labelled milk; removing one of two
visible units changes the count; a hand produces obstructed/uncertain. Until real photos exist, test with synthetic
composites built from the steady-01 frames and clearly labelled synthetic; never claim accuracy from them.

Tests for detection, matching, thresholds without held-out leakage, OFF caching/rate limit (mocked HTTP), and the
API contract. Record measured latency per stage on this laptop. Deliver: STATUS.md "Delivered changes" with the
exact run command.
