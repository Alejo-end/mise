# Round 3b follow-ups (14 Nov, 08:30 EET)

Rules: `r3-cloud-round.md` in this folder, including **patch delivery** (no commits, no git workarounds).
Every running Modal app keeps running: `ap-XuvlLBjdH1mKYeQrxjsYfP` (recognition), `ap-vGstsemYsWHuo9mI59cnIo`
(model runtimes), `ap-uhHkTNTDwsVWL7uNEY3d0J` (agent bench). Never stop, restart or relaunch them; new work is a
new run ID in a new app. DeepSeek is unavailable (provider capacity): do not schedule or probe it.

## Writer B2 (Codex): candidate conditioning, with positives. Worktree `junction2026-wt-recognition`

Owns new files only: `experiments/recognition/conditioning_app.py`, `recog/conditioning.py`,
`tests/test_conditioning.py`, `results/*/conditioning-*`. Read
`results/infrastructure/kimi-payload-20261114/README.md` first (integration's diagnostic: the image reaches Kimi;
with the candidate list the same crop is re-described as a yoghurt tub or milk carton; synthetic label cards are
named correctly). The earlier reading "shuffled order rules out list bias" was wrong: shuffling keeps the list.
Build and launch (detached, new app run, Kimi on Verda at most 8 requests/min, Qwen2.5-VL-7B on Modal at most 2
GPUs) a study that scores **negatives and positives together**:
- Prompts: candidate-free (describe, then a separate text-only match step against the list); candidate-conditioned
  (list in the prompt, as now); list with a mandatory "none of these" option; verify-one-candidate yes/no.
- Negatives: steady-01 crops (hard negatives: cups, patterned surfaces), synthetic controls. Positives
  (SYNTHETIC until recordings exist): rendered package-like cards from catalogue names of the demo products and
  look-alikes (464306, 464401, 464398 must not be called 463335), at several sizes and blur levels, plus partial
  occlusion. Real positives and negatives come from `/inputs/recordings/` once person-validated (the same
  `real_eval.discover` rules); until then the real table says idle.
- Report per model x prompt: false product rate on negatives, correct identification and look-alike confusion
  on positives, "unknown" rate on positives (a model that always says unknown must score zero recall), with
  repeats and Wilson intervals labelled descriptive. Separate tables for synthetic and real.

## Writer C2 (Codex): Gemma 4 E4B retry. Worktree `junction2026-wt-vlm-models`

Owns `experiments/vlm-models/` (patch delivery). Gemma 4 E4B stopped: its audio submodule rejects SDPA
(`evidence/current-errors.json`). Retry E4B alone in a new run ID (for example `r3-e4b-eager`): eager attention
(or the library's supported setting for the audio tower only), same pinned revision, same plans, one A100-80GB.
Do not touch run `r3-20261114-v6` or its app. If it loads, run the same smoke gate, then its full plan; record
load time, memory and first results; if it fails, record the exact error and stop.

## Writer D3 (Codex on Kimi via Verda): sustainability deployment options. Worktree `junction2026-wt-sustainability`

Owns `experiments/sustainability/` and `docs/sustainability.md` (patch delivery). Revise:
1. Label every operating figure as a **projection** until recognition accuracy, escalation share and review
   rates are measured on real recordings; say which measurement replaces which assumption.
2. Add deployment options: dedicated (one warm GPU per kitchen), scale-to-zero (per-burst cold start, load,
   inference, scale-down window), and **shared cloud serving across N kitchens** (one warm or autoscaled service
   whose fixed cost is divided by N; show N = 1, 10, 50, 200; include queueing headroom and that cold starts
   vanish only while traffic keeps the service warm). Keep GPU execution as paid time in every option.
3. Keep measured inputs (rates, billing, board) as they are; tests must pass.

## Recognition requirements from 14 Nov 08:50 (the person's instructions; apply in every recognition writer)

- The shelf now holds chips (slot 1), an energy drink (slot 2) and milk (slot 3); a small fitness bar is the
  unfamiliar item, never in the reference gallery (label value "unknown"). Product set and observed identities:
  `tools/recording_products.json` (integration branch). Labels: `labels.json` of each recording (counts typed by
  the person, orientation and barcode visibility per view).
- Initial identification (enrolment: barcode, owner-confirmed catalogue choice, previous orders) is separate from
  runtime recognition. Runtime recognition must work without a visible barcode, from reference images and context.
- Score barcode-hidden views separately (`barcode_visible: false` in the label windows).
- Context (expected slot, purchase history) may inform but must never force an identity; uncertain observations stay
  explicitly uncertain. The swap and move recordings test exactly this.
- All slots are unmapped for purchasing (signed rule set v6): recognition results never lead to orders.
