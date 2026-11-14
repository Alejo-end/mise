# Worker 4: product recognition experiment on Modal (exploratory)

Worktree `/Users/bigboss/Ai/Claudecode/junction2026-wt-recognition`, branch `exp/recognition`. You
own `valio-challenge/experiments/recognition/` and nothing else. This must not replace or change the
working demo: the two-photo detector stays the baseline (DEMO_PLAN.md, TEAM_BRIEF.md).

## Compute and money

- Modal, workspace `muodikas`, already logged in (`~/.modal.toml`, never print it). The CLI is
  `~/.local/bin/modal`. The person set a workspace budget at or below the redeemed credits and has
  no card on file, so Modal stops at zero; we still bound use ourselves.
- Before launching anything: record `modal billing summary` and `modal billing rates` in your status
  file. Stop launching new batches if the metered cost of this experiment passes $30; check between
  batches. At most 2 containers at once, function timeout at most 30 minutes, at most 2 retries.
  Use the smallest hardware that fits each model (CPU or T4/L4 for embeddings, L4/A10G for a 7B
  vision-language model). Stop apps when a batch finishes (`modal app stop`); never leave a
  deployment running.
- Run detached (`modal run --detach`), with the batch driver itself running on Modal, so nothing
  depends on this laptop, its camera tab or a local waiting process. Persist inputs, model weights
  cache, checkpoints, predictions, annotated examples, latency and resource use to the Modal Volume
  `valio-recognition`. Copy small summaries back into `experiments/recognition/results/`.

## Data, honestly

- Recordings in the project: only `tests/device/sequences/steady-01`, a cup and a keyboard. That is
  not a shelf and not a product: do not claim product-recognition accuracy from it and do not train
  on it.
- Catalogue information: the official Valio product file and its schema. Worker 3 downloads them
  once into the shared, read-only folder `/Users/bigboss/Ai/Claudecode/valio-source-data/2026-11-14/`
  and writes verified identifier findings (image URLs, GTINs, other identifiers) to
  `/Users/bigboss/Ai/Claudecode/valio-source-data/FINDINGS-identifiers.md`. Start with the model setup
  and smoke tests; read those files when they exist; never modify them, never copy them into git.
  If the findings show image URLs, you may fetch read-only the images of the three demo products
  (rule set v4: `477032` rye bread, `464316` natural yoghurt 1 kg, `463335` lactose-free milk drink
  1 l) and of at most 20 look-alike products, as **reference** images. These are sponsor-provided
  challenge data: for this challenge only, kept in your Volume and git-ignored folders.
- Real shelf recordings, when a person makes them, are a **separate evaluation source**. Catalogue
  images are references only, never evaluation data.

## The comparison

1. Baseline: the current two-photo detector (`device/python/shelf_detect.py`, import read-only).
2. A lightweight reference-image approach: an open image embedding model (for example DINOv2-small or
   an OpenCLIP ViT-B/32) with nearest-neighbour matching to reference images and an "unknown"
   threshold.
3. A suitably sized open-weight vision-language model (for example Qwen2.5-VL-7B-Instruct), asked
   which catalogue product is in a slot, with "unknown" allowed.
4. A fine-tuned detector or classifier only if representative, correctly labelled shelf captures
   exist. They do not today: document that and stop there.

For every model record: exact version (repository and revision), licence (from the model card),
hardware, memory, latency per image, and cost per 1,000 images (from `modal billing rates`).

## Steps

1. Smoke test: one tiny detached run per model (2 or 3 images) that proves the pipeline end to end.
   Label it a smoke test, not a result.
2. Build the runnable evaluation pipeline with a data manifest that separates **reference captures**
   from **evaluation recordings**, and scores: product identity per slot, unfamiliar products ("unknown"),
   a product moved between slots (identity follows the product, no replenishment), obstruction
   (uncertain, no change), and downstream false replenishment decisions (feed predictions through
   `tests/device/replay.py` and the agent, read-only imports, as the replay tests do).
3. If representative product captures exist, run the evaluation batches. They will not exist unless a
   person records them: then write the exact capture list (product, slot, lighting, count, which are
   reference and which are evaluation, the recording names for `tools/record_sequence.py`) into
   `experiments/recognition/MISSING_DATA.md` and stop before claiming any accuracy.

Separate measured results from assumptions in everything you write. Handoff: `docs/handoff/w4-recognition.md`
(you may create that one file outside your folder).
