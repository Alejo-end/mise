# Worker 6: recognition experiments at scale on Modal and Verda

Worktree `/Users/bigboss/Ai/Claudecode/junction2026-wt-recognition`, branch `exp/recognition` (worker 4's,
finished). You own `valio-challenge/experiments/recognition/`. Read `README.md` here, worker 4's handoff
`docs/handoff/w4-recognition.md`, `experiments/recognition/MISSING_DATA.md`, and its results. The
recognition work stays experimental: never change the demo, the core code or the board.

Claude coordinates lightly: you build, validate, launch detached cloud jobs, confirm first results,
write the handoff and stop. The jobs then run without this laptop or Claude.

## 1. First: the false milk identifications

On three crops of a hand, a cup and a keyboard, Kimi K3.1 Pro named lactose-free milk (463335) twice;
Qwen2.5-VL-7B said "unknown" 3 of 3. Find out whether the candidate labels or the prompt bias the answer:
vary the candidate list (with and without milk, shuffled order, more distractors, no list, "unknown" first
or last, describe-then-match), the wording, image size and crop context, and repeat each configuration
many times. Run the same configurations on Qwen (Modal, parallel GPU containers) and Kimi (Verda). The
measured quantity is how often a model names a catalogue product when none is in view. These inputs
contain no product, so this is a **synthetic stress test**, labelled as such.

## 2. Comparisons, prepared and run where inputs exist

- Approaches: the two-photo template baseline, image embeddings (DINOv2, CLIP; nearest neighbour plus an
  unknown threshold), barcode and OCR where a code or text is visible (the catalogue has GTINs for nearly
  every product; the three demo consumer GTINs are in worker 4's handoff), and vision-language models
  (Qwen on Modal; Kimi on Verda; DeepSeek on Verda only when it answers 200, it answered 403
  `subscription_required` at 07:05).
- Configuration sweeps on Modal: thresholds, crop sizes, prompts, models, hardware, in parallel.
- Synthetic stress inputs are allowed and must be labelled synthetic: the recorded frames with
  augmentations (blur, brightness, occlusion patches, rotations), and images with rendered barcodes and
  text built from catalogue GTINs and names. They test robustness and the unknown or uncertain behaviour,
  never product-recognition accuracy.
- The evaluation harness for real recordings: identity per slot, misplaced products, unknown items,
  occlusion, and the downstream false replenishment decision. Split by recording session, never by
  adjacent frames. It runs automatically when recordings named in `MISSING_DATA.md` appear on the Volume.
- No product-recognition accuracy claim and no product-specific training without suitable labelled real
  data. Keep infrastructure tests, synthetic stress tests and real shelf accuracy in separate result
  folders and separate tables.

## 3. Execution, detached and resumable

- Modal app `valio-recognition-exp`, Volume `valio-recognition` (worker 4's). Upload everything jobs need.
  Verda credentials come only from the Modal Secret `verda-inference`; never put a key in code, images,
  logs or results.
- A resumable driver on Modal that skips finished work items and writes results, checkpoints and
  summaries to the Volume as they finish. Scale concurrency toward account limits: parallel GPU
  containers on Modal; on Verda read `x-ratelimit-*` headers, honour `Retry-After`, back off with jitter.
  Request timeout 60 s, at most 3 retries, then record an infrastructure failure. Stop persistently broken
  jobs and finished apps.
- Never call the Valio APIs. No agent-imposed monetary cap; report usage (`modal billing summary`, Verda
  tokens) before and after each batch.
- Validate small, then launch the full batches detached. Confirm results are arriving, then stop
  watching. Leave `experiments/recognition/summarize.py` for integration.

Handoff: append a section "Scale-up (worker 6)" to `docs/handoff/w4-recognition.md` (or write
`docs/handoff/w6-recognition-scale.md`): commits, every Modal app ID, dashboard link and function call ID,
what is still running, Volume paths, the bias findings with their counts, usage, and blockers.
