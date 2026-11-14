# Worker 5: sandbox fixes and the agent benchmark on Modal and Verda

Worktree `/Users/bigboss/Ai/Claudecode/junction2026-wt-bench`, branch `exp/agent-bench` (from
`valio/integration-unattended`). You own `valio-challenge/experiments/agent-bench/` and the separate
branch `fix/sandbox-harness`. Read `README.md` here (rules for every worker), `../PARALLEL_WORK.md`
(contract, decisions A to I, demo blockers), `../../HANDBOOK.md`, `backend/sandbox.py`,
`backend/model_agent.py`, `experiments/driving-test-verda/` on branch `exp/recognition` (worker 4's
Kimi adapter; read it with `git show exp/recognition:<path>`), and worker 4's and worker 3's handoffs.

Claude coordinates lightly: you build, validate, launch detached cloud jobs, confirm the first results,
write the handoff and stop. The jobs then run without this laptop, its backend, or Claude.

## 1. Sandbox fixes, reversible, on `fix/sandbox-harness` (branch it from `valio/integration-unattended`)

- Rule set and catalogue mismatch: the sandbox gets the backend's rule set (v4, real product codes) but
  runs the stand-in catalogue, so a correct agent fails 5 of 6 (`api_error`) and demo step 1 breaks.
  Make the sandbox use a rule set that matches the catalogue it runs (the stand-in rule set for the
  stand-in catalogue), and keep the policy the scenarios intend. Prove it: our reference agent passes
  6 of 6 and the naive agent still fails.
- String quantity crash: a model sent the quantity `"24"`, the sandbox accepted it, and `judge()`
  crashed on number plus string. Validate tool arguments at the sandbox boundary (the agent gets a clear
  error and may retry), and make `judge()` never crash: a broken episode is scored as failed with a reason.
- Each fix gets a regression test that fails without it. Keep the diff small, in Alejo's files only
  where the fix lives; describe it so Alejo can review it. Do not merge it into integration or main.
  Then merge `fix/sandbox-harness` into `exp/agent-bench`.

## 2. The benchmark

- Scenario generator: parameterised families, each scenario with an explicit expected outcome written
  as rules (order placed with exactly these lines, approval requested from this role, refused with this
  reason, no order). Families: duplicate and concurrent requests, stale quotes, malformed quantities,
  pack units and lot sizes, budgets and weekly limits, product and rule set changes, approval
  permissions, unavailable products, missing delivery times (a delivery date alone is never proof of
  arrival before 07:00), substitutions, misleading product text. Hundreds of distinct scenarios.
- Split development and held-out evaluation cases by scenario seed and family parameters before running
  anything; never tune prompts or the adapter on held-out cases. Record the split in the results.
- Decisions that matter (C refill policy, G same-day date-only delivery) run as separate, labelled
  configurations, never mixed.
- Agents: Kimi K3.1 Pro on Verda (`kimi-k3-1-pro`, works), DeepSeek V4.1 Flash on Verda
  (`deepseek-4-1-flash-1017`: at 07:05 it answered 403 `subscription_required`; probe it once at start
  and once before each batch, and include it automatically when it answers 200), plus our reference agent
  and the naive agent as baselines (no model calls). Repeat each scenario several times per model;
  expand to thousands of episodes where the per-scenario variance makes it useful.
- Isolation: every episode runs the backend in-process with its own temporary SQLite audit database and
  rule-version folder, the stand-in catalogue or recorded Valio responses only. No network except Verda.
  No call to any Valio API, no external order: the gateway records orders only.
- Scoring: explicit rule checks plus observed outcomes in the episode's audit log. Never use another
  model's opinion as ground truth. Report per agent and family: task completion, policy violations,
  unnecessary refusals, crashes, latency, tokens.
- Reproducible bugs found in the gateway or harness become regression tests (in your branches).

## 3. Execution on Modal, detached and resumable

- Modal app `valio-agent-bench`, Volume `valio-agent-bench`. Upload everything the jobs need (code,
  scenarios, recorded responses, catalogue files used) to the Volume or the image. Verda credentials
  come only from the Modal Secret `verda-inference` (`VERDA_INFERENCE_API_KEY`,
  `VERDA_INFERENCE_BASE_URL`); never put a key in code, images, logs or results.
- A driver function on Modal reads a work list, skips episodes that already have a result file, and
  runs the rest with `.map` / `.spawn`; results, checkpoints and summaries go to the Volume as each
  episode finishes. Relaunching resumes.
- Throughput: scale concurrency toward what the accounts allow. Read Verda's `x-ratelimit-*` headers,
  honour `Retry-After` on 429, back off with jitter, and lower concurrency while throttled. Request
  timeout 60 s (Verda's non-streaming limit), at most 3 retries per call, then the episode is recorded
  as an infrastructure failure. Stop a job that fails persistently; stop apps that finished.
- No agent-imposed monetary cap (the person's instruction). Report usage: `modal billing summary` and
  Verda token totals from the responses, before and after each batch.
- Launch: validate on a small development batch first, then launch the full batches detached
  (`modal run --detach`). Confirm that results are arriving, then stop watching: no continuous polling.
  Leave `experiments/agent-bench/summarize.py`, which reads the Volume and prints the current totals, so
  integration can inspect summaries and failures later.

Handoff `docs/handoff/w5-agent-bench.md` must list: the commits on both branches, the sandbox fixes and
their tests, every Modal app ID and dashboard link and function call ID you launched, which are still
running, Volume paths, scenario counts per family and split, episodes done so far, first results with
their status (development or held-out), usage, and blockers.
