# Worker 1: purchasing reliability and reproducible configuration

Worktree `/Users/bigboss/Ai/Claudecode/junction2026-wt-detection`, branch `wt/detection-reliability`,
backend port 8101 on 127.0.0.1 only. Your files are in the contract (worktree 1). The stale-frame
and duplicate-order fixes are done and merged (commit "Valio worktree 1: stale frames and
duplicate orders fixed"); keep them and their tests.

## 1. The restock policy

Today: `RESTOCK_MIN_S` defaults to 0 in the code, the live backend runs 120 from a local `.env`,
and Alejo's `tests/test_requests.py::test_one_empty_shelf_makes_one_order` expects an immediate
reorder after a single "full" reading. That sequence is exactly how the live duplicate happened
(orders `ORD-68CE0E08` and `ORD-592FF708`, 26 s apart).

- Write down the business meaning of a refill: after an order, "full" can be the delivery, stock
  from a back room, the same product put back, or a false reading (a hand, a recalibration). Only
  a delivery fulfils the order. A delivery cannot arrive before the order's earliest arrival.
- Implement one consistent candidate policy with explicit configuration. Proposal to evaluate:
  the slot stays pending until (a) a delivery check confirms the order, or (b) the slot reads full
  continuously for `RESTOCK_CONFIRM_S` (use the `health` status of interface 7, which arrives every
  10 s), and at least `RESTOCK_MIN_S` after the trigger. One full reading never clears it. The
  window is a mitigation, not proof of a refill: say so in the code and the docs.
- Tests (`tests/test_restock_policy.py`): a false full inside the window, a false full outside the
  window (a single reading), a genuine early refill (sustained full inside the window), outstanding
  orders and open approvals, a backend restart in each state, repeated observations, and the code
  default reproducing the live configuration.
- Alejo's test specifies the old policy. Keep it meaningful: pin it to the immediate policy
  explicitly (`restock_min_s=0`, sustain off) with a comment that names decision C. That is the one
  edit you may make in his test file. If the business meaning stays unresolved, the committed
  default is the conservative policy, and decision C in `docs/PARALLEL_WORK.md` states exactly
  what Alejo must choose.

## 2. Reproducible configuration

A fresh checkout must reproduce the tested configuration without any local override: put every
backend setting the tests rely on (`RESTOCK_MIN_S`, `RESTOCK_CONFIRM_S`, the offline time) into a
new `backend/settings.py` with documented defaults, read by the agent and the app, and a committed
`.env.example` listing every key with its default and meaning. Add a test that the defaults are the
tested ones. The live `.env` may only hold secrets and site values (PINs, keys, addresses).

## 3. Health and uncertainty (interfaces 7, 8, 9)

- Device: send `health` in the `status` event (interface 7), from the trackers' `uncertain`,
  `fill`, camera state, frame age and config validity. Put the pure parts (building the health
  dict, the calibration check of interface 9) into `shelf_detect.py` and test them on the laptop.
- Device, interface 9: store the `assignment_id` with each calibration photo (`data/slotN_meta.json`);
  fetch `GET /shelf` from the backend every minute; a different id makes the slot `stale` and
  uncertain. Until worktree 2's `/shelf` exists the call fails: treat that as "unknown", not stale.
- Backend: the `Status` model accepts the optional `health`; `GET /device` returns the last one and
  its age. Health never orders and never cancels.
- Backend, interface 8: `Agent.pending_for_slot(slot)`, rebuilt from the audit log.
- Document in `docs/PARALLEL_WORK.md`'s table where each check is enforced (integration owns that
  file: write your changes to it in your handoff instead).

## 4. Review and bounds

Run one Codex review of the restock policy and the pending-state changes:
`codex exec -s read-only -C /Users/bigboss/Ai/Claudecode/junction2026-wt-detection "Review the diff
of branch wt/detection-reliability against valio/hw-checkpoint for correctness bugs in purchasing
state transitions: pending slots, restock window, sustained full, restart restore, locking."`,
15-minute timeout, output to `valio-challenge/data/codex-review-w1.md`. Fix real findings.

Do not deploy to the board; the integration session does that later. Device code cannot run on the
laptop, so everything testable must live in `shelf_detect.py`, and `main.py` must at least compile
(`python3 -m py_compile`). Handoff: `docs/handoff/w1-reliability.md`.
