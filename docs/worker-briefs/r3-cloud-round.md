# Round 3 (14 Nov, from 07:45 EET): cloud workloads after workers 5 and 6 stopped on the Claude limit

Claude (integration session, `junction2026-int`) only coordinates, deploys and integrates. The writers below
are Codex sessions; each owns one worktree and the paths named for it, nothing else. Results must outlive the
writer: detached Modal jobs, durable files on Modal Volumes, resumable from checkpoints.

## Rules for every writer

- Read first: `docs/worker-briefs/README.md`, `docs/PARALLEL_WORK.md` (contract) in
  `/Users/bigboss/Ai/Claudecode/junction2026-int/valio-challenge/`, then your own brief section below.
- Write only inside your worktree, only in your owned paths and your `STATUS.md`. Never touch the board, the
  live backend (port 8000), `junction2026-int`, `junction2026` (main checkout) or another worktree. Read-only
  access elsewhere is fine. Commit to your branch with the repository's configured identity (do not change
  git config). Never push, never merge, never rebase other branches.
- No Valio API calls, no orders, no price or availability requests. Valio data files
  (`/Users/bigboss/Ai/Claudecode/valio-source-data/2026-11-14/`, read-only) are sponsor challenge data: use them
  only for this challenge, may be copied to a private Modal Volume, never committed.
- Secrets: Verda credentials only through the Modal Secret `verda-inference` (`VERDA_INFERENCE_API_KEY`,
  `VERDA_INFERENCE_BASE_URL`). Never print environment variables or keys, never write them to code, images,
  logs, results or commits. Do not commit `.env`.
- Cloud inventory before launching anything: `~/.local/bin/modal app list` and `modal volume ls`. Never start a
  second copy of an app that is running; resume instead. Write every app ID, dashboard link
  (`https://modal.com/apps/muodikas/main/<app id>`) and function call ID into your `STATUS.md` immediately after
  launch, before doing anything else.
- Jobs: `modal run --detach` (or `.spawn` from a detached entrypoint); a driver that skips work items that
  already have a result file; results and checkpoints written to the Volume as each item finishes; relaunching
  resumes. Request timeout 60 s for Verda, at most 3 retries with jittered backoff, honour `Retry-After` and
  `x-ratelimit-*`; after that record an infrastructure failure for the item. Stop an app that keeps failing.
- Shared provider budgets (the Verda key is shared by everyone, about 40 requests and 3M tokens per 60 s):
  agent bench at most 15 requests/min, recognition bias at most 12 requests/min, the rest is left for the
  Verda coding agent. GPU containers on Modal: recognition (writer B) at most 3 at once, model runtimes
  (writer C) at most 6 at once. CPU containers: as needed, at most 20 per app.
- No agent-imposed monetary cap (the person's instruction). Report usage before and after each batch:
  `modal billing` output for the app, and Verda token totals summed from responses.
- Labelling: keep infrastructure tests, synthetic stress tests and real-shelf results in separate folders
  and separate tables. No product-recognition accuracy claim without labelled real shelf recordings.
  Automated labels (from any model) are proposals with `"validated": false` until a person confirms them;
  never use another model's answer as ground truth; never train or score on unvalidated labels.
- Validate small first (unit tests locally, then a tiny cloud batch), then launch the full batches detached.
  Confirm the first results arrived on the Volume, write the handoff, commit, and end your session. Do not
  keep polling; the jobs run on without you.
- `uv run pytest` must pass for your own tests. Keep the diff inside your paths.

## Writer A: agent benchmark (Codex). Worktree `junction2026-wt-bench`, branch `exp/agent-bench`

Owns `valio-challenge/experiments/agent-bench/`, `valio-challenge/docs/handoff/w5-agent-bench.md`, STATUS at
`valio-challenge/experiments/agent-bench/STATUS.md`. Full brief: `docs/worker-briefs/w5-agent-bench.md`
(in this worktree). Already done (do not redo): section 1, the sandbox fixes on `fix/sandbox-harness`
(03608dd, merged into `exp/agent-bench` as d087e9c; 341 tests passed), and the Verda probe
(`experiments/agent-bench/probe.py`; kimi-k3-1-pro 200, ratelimit about 40 req / 3M tokens per 60 s;
deepseek-4-1-flash-1017 403 `subscription_required`). Status history: `valio-challenge/data/WORKER_STATUS.md`.
Do now: section 2 (scenario generator with dev and held-out split fixed before any run, explicit-rule
expected outcomes, reference and naive baselines, decision variants C and G as separate labelled
configurations) and section 3 (Modal app `valio-agent-bench`, Volume `valio-agent-bench`, detached resumable
driver, Kimi on Verda now, DeepSeek probed at each batch start and included automatically when it answers
200). Hundreds of scenarios; repeats per model where the variance makes it useful. Leave `summarize.py`.

## Writer B: recognition at scale (Codex). Worktree `junction2026-wt-recognition`, branch `exp/recognition`

Owns `valio-challenge/experiments/recognition/` (except nothing else), `docs/handoff/w6-recognition-scale.md`,
STATUS at `valio-challenge/experiments/recognition/STATUS.md`. Full brief: `docs/worker-briefs/w6-recognition-scale.md`
in `junction2026-int`. Recovered checkpoint 1c77e45: `recog/bias.py` (configurations, prompts, aggregation,
Wilson intervals), `recog/verda.py` (rate gate, chat), `build_catalogue_subset.py` (Volume
`/inputs/catalogue/subset.json` exists), `verda_probe.py` (DeepSeek 403; Kimi accepts reasoning_effort "none"
and temperature 0). Not launched yet: nothing on Modal for this code. Status history in
`valio-challenge/data/WORKER_STATUS.md`. Worker 4's `modal_app.py` limits (2 containers, 1 retry) were for the
earlier budget; raise them within the GPU budget above. Do now, in order:
1. The false-milk bias study (synthetic stress test, labelled so) on Qwen2.5-VL-7B (Modal, transformers path
   that already works, weights in `/hf-cache`) and Kimi K3.1 Pro (Verda), many repeats per configuration.
2. Template and embedding baselines (DINOv2, CLIP; nearest neighbour plus unknown threshold) on synthetic
   augmentations of the recorded frames (blur, brightness, occlusion patches, rotation), labelled synthetic.
3. The real-recording evaluation harness, split by recording session, that runs when recordings and a person
   -validated label file appear under `/inputs/recordings/<name>/` (integration uploads them; layout =
   `tests/device/sequences/<name>/`). Include a small specialised candidate (for example a linear probe or
   k-NN on frozen embeddings per slot) trained only on validated reference sessions and scored only on other
   sessions; it must stay idle (and say so) until validated labels exist.
Leave `summarize.py` that prints all three tables separately.

## Writer C: new vision-language model runtimes (Codex). Worktree `junction2026-wt-vlm-models`, branch `exp/vlm-models`

Owns `valio-challenge/experiments/vlm-models/` only (new folder), STATUS at
`valio-challenge/experiments/vlm-models/STATUS.md`, handoff `docs/handoff/vlm-models.md`. May import
`experiments/recognition/recog/*` read-only (bias configurations, image loading); never edit it. Modal app
`valio-vlm-models`; Volume `valio-recognition`, writing only under `/runs/vlm-models/`; weights under the shared
`/hf-cache` (reuse what is there, download directly inside Modal, never through the laptop).
Models, verified on Hugging Face at 07:40 (all ungated; record the full revision SHA you pin):
- `google/gemma-4-E4B-it` (Apache-2.0, `Gemma4ForConditionalGeneration`, about 16 GB bf16, revision 4ecc2ba63bca...)
- `google/gemma-4-12B-it` (Apache-2.0, `Gemma4UnifiedForConditionalGeneration`, about 24 GB, revision 43c9d014b231...)
- `nvidia/NVIDIA-Nemotron-Nano-12B-v2-VL-BF16` (NVIDIA Open Model License, custom code, about 26 GB, revision ca9543b126e8...)
- `Qwen/Qwen2.5-VL-7B-Instruct` (Apache-2.0, already cached, revision cc594898137f..., the existing baseline)
Choose a runtime per model that actually loads it (vLLM pinned version if it supports the architecture,
otherwise transformers); record versions, GPU type, load time, peak GPU memory. If a model cannot load,
record the exact error and move on; do not block the others. Run each model as its own parallel job.
Experiments (each result labelled infrastructure, synthetic or real):
1. Unknown rejection: the bias configurations from `recog/bias.py` (no product in view): how often each
   model names a catalogue product.
2. Image resolution: the same inputs at several sizes (for example 224, 448, 896 px long side, crop and wider
   context).
3. Reference-image strategy: text candidates only; one reference image per candidate in the prompt; empty and
   full slot photos as references. Real references exist only after the recordings (`ref-*`); until then run it
   on synthetic rendered product cards (catalogue names and GTINs drawn as text), labelled synthetic, and make
   the real variant start when `/inputs/recordings/ref-*` appears.
4. Latency and cost: cold start, warm per-image p50 and p95, throughput, GPU-seconds and $ per 1,000 images.
Leave `summarize.py` with one table per experiment and per label.

## Writer D: production sustainability (Codex driven by Kimi K3.1 Pro on Verda). Worktree `junction2026-wt-sustainability`, branch `exp/sustainability`

Owns `valio-challenge/experiments/sustainability/` (new folder) and `valio-challenge/docs/sustainability.md`.
No cloud jobs, no network calls. Build:
1. `cost_model.py` (stdlib only) with `assumptions.json`: per-kitchen workload (slots, frames per second
   processed, stock-change events per day, share of events escalated, reorder attempts per day), prices
   (GPU $/s by type, Verda tokens, idle hosting of the gateway, review minutes and labour cost per minute,
   board power and amortisation) and their sources; every unverified number marked `"source": "ASSUMPTION"`.
   It computes, for three architectures (local only on the board; local with cloud escalation of uncertain
   events; cloud recognition of every event), cost per event and monthly cost per kitchen, separating
   development expenditure (the research runs, read from result files when present) from projected operating
   cost. Unit tests in `experiments/sustainability/tests/`.
2. `docs/sustainability.md`: the comparison and the measurement plan: product identification errors, false
   stock changes, unnecessary reorder attempts, unknown and occluded handling, latency, memory, network
   dependence, cost per event; what is measured versus assumed; the rule that optimisation or quantisation
   comes only after accuracy is established, that optimised variants are validated again and measured on the
   UNO Q before any local-feasibility claim; that purchasing stays behind the deterministic gateway; and the
   explicit uncertain state plus manual path when recognition or connectivity fails (describe the existing
   ones in `device/python/shelf_detect.py` and the board page, read-only).
Inputs you may read: result folders in the other worktrees (read-only), `device/` code. Integration adds the
board measurements and real prices later.
