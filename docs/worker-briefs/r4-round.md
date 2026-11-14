# Round 4 (14 Nov, from 09:45 EET): independent work while the shelf recordings wait

Rules: `r3-cloud-round.md` in this folder, including **patch delivery** (do not commit, never work around the
read-only `.git`; list "Delivered changes" in your STATUS.md). The person is away. Nothing may touch the board,
the live backend on port 8000 (it runs in recording mode on purpose) or the demo behaviour. The replenishment
proposal (`docs/decisions/2026-11-14-continuous-observation-scheduled-replenishment.md`) is **pending team
agreement**: evaluate it in experiments only, never in `backend/` or `device/`.

Cloud: no agent-imposed monetary cap, but substantial *finite* batches with checkpoints and clear outputs; no idle
GPU reservations (scale-down windows of at most 60 s, no keep-warm, no watchers waiting for recordings); stop
completed or persistently broken jobs; durable inputs and outputs on Modal Volumes; nothing may depend on this
laptop, localhost or the board once launched. Check `modal app list` first and never duplicate a running app:
`ap-uhHkTNTDwsVWL7uNEY3d0J` (agent bench), `ap-XuvlLBjdH1mKYeQrxjsYfP` (recognition), `ap-OsB9gmOmNRHczDonqctmZC`
(conditioning), `ap-vGstsemYsWHuo9mI59cnIo` and `ap-EsoqsrKBRkKkOKbOU7sOLs` (VLM runtimes) keep running.

Verda (Kimi K3.1 Pro, about 40 requests and 3M tokens per 60 s shared by everyone; three existing lanes already use
about 35/min): new lanes at most **4 requests/min each**, honour `Retry-After`/`x-ratelimit-*`, at most 3 retries,
60 s timeout. DeepSeek is unavailable: never schedule or probe it. GPU: at most 4 new concurrent GPU containers in
total for writer V; CPU containers as needed (at most 50 per app).

## Writer E (Codex): replenishment-policy simulator. Worktree `junction2026-wt-replenishment-sim`, branch `exp/replenishment-sim`

Owns `valio-challenge/experiments/replenishment-sim/` and `docs/handoff/replenishment-sim.md`. Modal app
`valio-replenishment-sim`, Volume `valio-replenishment-sim` (CPU only).
- Isolated simulator (stdlib + numpy): **true inventory separate from noisy observations**. Model consumption,
  temporary removal and return, relocation to another slot, obstruction, missed observations, false low/empty
  readings, outstanding orders, approval delays, deliveries with lead times, partial deliveries.
- Policies on the **same seeded demand and observation traces**: (1) immediate replenishment (today's backend:
  a confirmed low/empty reading triggers an order; duplicate prevention while one is pending), (2) shared daily
  ordering window (candidates reassessed at the window), (3) scheduled window plus authorised urgent exceptions;
  plus straightforward baselines: fixed periodic order-up-to with perfect stock knowledge (oracle), fixed periodic
  ignoring the camera, and "never order automatically, person reviews everything".
- Sweep usage rates, thresholds, observation error rates, approval delays, lead times, window frequency, urgent
  authority on/off. Metrics: stockout time and events, over-ordering (units above target), duplicate orders,
  orders per week (fragmentation), review workload (approvals and uncertain reviews), policy violations (orders
  outside authority or window), units in pipeline. Show where the proposed policy performs **worse**.
- Start with hand-checked sanity cases (tests), then a substantial finite sweep: tens of thousands of reproducible
  episodes (seeds recorded), checkpointed on the Volume, summary tables and a short findings note.
- Assumptions are explicit and parameters, never presented as Valio data: delivery fees and timing are
  assumptions; Valio's historical delivery reliability (quantity fulfilment) must not be used as an on-time
  probability. State that this tests the proposal's trade-offs, not real demand or camera accuracy.

## Writer A2 (Codex): gateway benchmark extension. Worktree `junction2026-wt-bench`, branch `exp/agent-bench`

Owns `experiments/agent-bench/` (patch delivery). Reuse the 672-scenario benchmark and the running full run's
results (read them from Volume `valio-agent-bench:/runs/r3-full`, do not restart it). Add scenario families where
the replenishment proposal raises genuine gateway questions: a change of stock or rules before the order window,
an approval that arrives late (after the window or after prices change), a returned item while an order is
outstanding (must not clear it), a pending delivery when a new low reading arrives, and an urgent exception with and
without authority. Deterministic explicit-rule scoring; keep dev and held-out split fixed before running; isolated
databases; recorded Valio responses only (no Valio API). Validate with reference and naive agents, then run Kimi
on Modal at most 4 requests/min as a new run ID; DeepSeek stays unavailable. Report what the existing gateway
already handles and what it cannot express without the proposal.

## Writer V (Codex): generic retail detection/counting on public data. Worktree `junction2026-wt-vlm-models`, branch `exp/vlm-models`

Owns new `experiments/retail-vision/` (patch delivery); reuse `experiments/vlm-models/vlm_models` runtimes and the
shared `/hf-cache` read-only. Find an accessible, labelled retail dataset suitable for generic product detection and
counting (for example SKU-110K, Retail Product Checkout, Grocery Store, Open Images retail classes). Verify the
dataset terms and each model's licence separately from primary sources; record URL and retrieval date. Research-only
data or models go under `research-only/` subfolders and are never production candidates. Download data directly
inside Modal to a Volume (`valio-retail-vision`). Compare Qwen2.5-VL-7B and Gemma 4 (existing runtimes) with suitable
lightweight baselines (an open-vocabulary detector such as OWLv2 or Grounding DINO, and a small detector) on
accuracy, counting error, uncertainty/abstention, input resolution, batching, GPU memory, cold and warm latency. If
the training data allows, fine-tune a small detector in an isolated experiment and evaluate on an untouched test
split. Label everything as public-data or synthetic; never claim it shows recognition of the person's products or
performance on the UNO Q.

## Writer M (Codex retrieves, Kimi analyses): market and commercial research. Worktree `junction2026-wt-market-research`, branch `exp/market-research`

Owns `valio-challenge/research/market/`. Retrieval with real tools only (web search and fetching pages; model
memory is not a source). Save each source's text with URL and retrieval date under `research/market/sources/`.
Start with the official Choco OrderAgent documentation and the Apicbase supplier API documentation, then broaden
(ordering platforms, kitchen inventory tools, wholesaler APIs and agent-commerce standards). Then run the
**analysis on Kimi K3.1 Pro (Verda)** as a Modal job (`valio-market-research`, Secret `verda-inference`, at most 4
requests/min) over the saved sources: per source extraction (claims, evidence type), comparison, and a critique that
challenges our assumptions. Examine the wholesaler gateway and the kitchen inventory client separately: user, buyer
and payer; existing workflow and alternatives; evidence of pain and adoption barriers; differentiation through
interoperability, permissions and verifiable transactions; installation, integration and maintenance effort; a
realistic first pilot; questions for Valio mentors and kitchen operators. Distinguish vendor claims, independent
evidence, assumptions and our proposed advantages. Never invent interviews, willingness to pay or market sizes;
prepare interview questions, contact nobody. Output: `research/market/report.md` with every claim linked to a source.
