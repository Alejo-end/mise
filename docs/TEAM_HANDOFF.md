# Valio: team handoff (14 Nov 2026, 10:50 EET)

For Alejo and the team. Start here, then `HANDBOOK.md` for running the demo. Three kinds of content, kept apart:
**implemented** (on `valio/integration-unattended`, tested), **proposals** (pending team agreement, nothing
built), **experiments** (separate `exp/*` branches, never merged into integration).

## 1. Implemented and tested

Canonical branch: `valio/integration-unattended` (contains `main` and Alejo's `review-14-nov`). Tests: **352 passed,
2 expected failures** at 3c3c7a2 (`cd valio-challenge && uv run pytest`).

| Area | What works | Evidence |
|---|---|---|
| Gateway | Lookup, Ed25519-signed quotes and answers, orders with idempotency, PIN approvals, audit log, Agent Driving Test | Core test suite; `backend/signing.py` uses Ed25519 (checked) |
| Purchasing safety | Duplicate orders and stale camera frames cannot trigger purchases (worker 1); recheck before ordering now applies the current rules to the current product data (3c3c7a2, a gap found by the benchmark: 0/8 to 8/8) | `tests/test_robustness.py`, `tests/test_duplicate_orders.py`, round-4 benchmark rerun |
| Real Valio data | Product file and recorded price/availability responses, real catalogue adapter, field labels (worker 3) | `tests/test_valio_*.py` |
| Shelf configuration | Slot products live in the signed rule set; `PUT /shelf/{slot}` signs a new version; a slot can be **unmapped** (no purchasing) | `tests/test_shelf_config.py` |
| Shelf box | Box editor with draft, atomic save, red conflicts, recalibration after a move; no purchase while editing or with an invalid layout | Tests, and checked on the real board page (07:20) |
| Recording | Recording mode (device events never reach the agent), guided recorder with typed counts and Enter marks, enrolment tool, clip check and upload | `tests/test_app.py`, `tests/tools/`; capture not yet performed |

No language model is in the live purchasing path: `backend/agent.py` is deterministic; a model is used only as an
agent under test in the Driving Test (`backend/model_agent.py`, when a key is set). Checked by the coordinator.

**Deployed (live now):** board files = 0d93b6a (checked over USB at 09:43). Since 11:11 the board runs the USB-camera variant `safe-kitchen-agent` through the replaced hub (camera OK, clean frames; see `measurements/camera-diagnosis-2026-11-14.md`); its default app is still the laptop-camera variant. Backend process started 08:40 from
851a992 code: it does **not** yet contain the recheck fix 3c3c7a2. It runs in **recording mode**, and signed rule
set v6 leaves all three slots unmapped, so the shelf cannot buy anything until someone ends recording mode and signs
purchasable products again.

**Recognition prototype (experimental, observational):** a "Recognize shelf" panel on the board page (deployed 12:22
to `safe-kitchen-agent`) calls a laptop service on `exp/recognition` (86c513e) that finds separated packages on the
surface and names enrolled products from the person's own photos, with unknown, ambiguous and obstructed outcomes and
visible-unit counts. Checked end to end in the real page; no products are enrolled yet, so no recognition result
exists. It never touches purchasing. How to use: `CAPTURE_GUIDE.md` section 6a.

## 2. Known failures and limits (say them out loud)

1. **Demo step 1: fixed by the merge of `review-14-nov` (19a24a8) on 14 Nov at 11:30.** The Driving Test passes 6 of 6
   with the real rule set, checked on the live API. `fix/sandbox-harness` (03608dd) fixes the same fault and adds
   argument validation. It is not merged, and it now overlaps this fix.
2. **Every live order goes to a person:** the mock API gives a delivery date without a time (decision G).
3. **"Stock is short" cannot happen live:** the mock API reports 9,999,999,999 available (decision I).
4. **Demo step 4** needs a new signed rule set (weekly limit and no allergen-free usual product).
5. **Escalation gap (reported by the benchmark, not yet fixed):** when every safe product is unavailable, the agent
   records "blocked" instead of asking the Head chef.
6. **Shelf detector:** estimates a fill level, not a count. Views between the two calibration photos (a hand, a
   half-covered zone, an unrelated scene) can read "low" (kept as strict expected-failure tests). A shift-tolerant
   rule exists but is off until real recordings set its threshold.
7. **No validated recordings of the real products** (chips, energy drink, milk; fitness bar as unfamiliar) exist yet.
   No recognition accuracy may be claimed.

## 3. Decisions awaiting the team

- Open implementation decisions A to I (`PARALLEL_WORK.md`), most urgent: **C** (may a camera "full" reading end a
  pending order?), **G** (date-only delivery), **I** (mock stock).
- Proposal: [continuous observation, scheduled replenishment](decisions/2026-11-14-continuous-observation-scheduled-replenishment.md).
- Proposal: [demo shelf setup, one camera and one visible row per product](decisions/2026-11-14-proposed-demo-capture-setup.md).

## 4. Experiments: what they showed, and what to do with them

Every conclusion below was challenged by an independent Kimi K3.1 Pro review on Verda (`review/2026-11-14-kimi/`,
75k tokens; the coordinator checked the code claims). Classes: **measured**, **synthetic**, **public data**,
**vendor claim**, **real shelf** (none yet).

| Direction | Finding (class) | Decision it informs | Recommendation |
|---|---|---|---|
| Agent benchmark (`exp/agent-bench`) | 736 scenarios with frozen dev/held-out splits. It found a real rule-change gap, now fixed, and an escalation gap (synthetic, deterministic scoring). Kimi results are smoke-level only | Gateway robustness and the Driving Test story | **Keep** as regression evidence; fix the escalation gap; report held-out results only after the running batch ends |
| Replenishment simulator (`exp/replenishment-sim`) | Partial sweep (9,816 of 55,296 episodes): a daily window halves supplier orders but raises stockout hours (57.7 to 177.4); urgent exceptions recover part (127.5) (synthetic) | Whether to adopt the proposal | **Defer** implementation: the trade-off is real, and the proposal is still pending. Finish the sweep, then decide window and urgent rules |
| Market research (`exp/market-research`) | Incumbents (Choco, Apicbase, MarketMan) already document approvals, signed webhooks, partial deliveries and suggested orders (vendor claims, 21 sources) | Pitch differentiation and pilot | **Keep:** differentiate on an open agent interface with permissions and verifiable transactions, not on "approvals exist". The pilot is a gateway shadow test with one kitchen, one wholesaler and one approver |
| Economics (`exp/sustainability`) | Measured Modal spend $36.06 today (research, not operations). Every operating cost is a projection; GPU time is paid even when weights are free; shared serving is needed before cloud recognition is affordable (assumptions) | Architecture choice | **Change:** cut to one central scenario and the labour-dominated stress case; replace assumptions with the recording results |
| VLM recognition (`exp/recognition`, `exp/vlm-models`) | The image reaches Kimi, but a candidate list makes it re-describe hard negatives as listed products. Qwen named no product on negatives but has too few positives. Gemma 4 loads; Nemotron failed to load (synthetic) | Whether a model may sit in front of purchasing | **Defer:** no model in the demo's purchasing path; rerun on the real recordings only |
| Shelf detector candidate rule (integration, off) | A shift-tolerant "between the photos" rule separates covered and unrelated views from genuine states (synthetic) | Enabling the rule on the board | **Change:** evaluate unchanged on the real recordings, then measure CPU on the UNO Q before enabling |
| Public retail vision (`exp/vlm-models`, SKU-110K) | Counting comparison on public shelf images; the dataset is academic and non-commercial only (public data, research-only) | None for the product | **Drop** from the team summary: it cannot be used commercially and says nothing about our products |

Removed from the team-facing summary as unsupported: any claim that the shelf recognises the team's products, that
the UNO Q runs a learned model, that a replenishment policy is proven better, that zero simulated violations prove
gateway robustness, that Qwen is "production-safe", or that Kimi has a general false-positive rate.

## 5. Recommended scope

- **Hackathon:** the gateway demo (fix step 1 by merging the sandbox fix, decide G and I), the shelf as one client
  with the one-row visibility constraint stated, a short real-recording evaluation (about 15 clips, normal light,
  `CAPTURE_GUIDE.md`), and the replenishment idea presented as a proposal with its simulated trade-off.
- **Pilot (after the hackathon):** the research report's shadow test: one kitchen, one wholesaler sandbox, one
  approver, a few packaged products. Gateway first; the camera stays optional and separate.

## 6. Where results live

Short economics and decision brief: `docs/team-decision-brief.md` on branch `exp/sustainability` (48df27c; six cost options with break-even conditions; every operating figure a projection).

Branches and owners: the table in `TEAM_BRIEF.md`. Cloud outputs: Modal Volumes `valio-agent-bench`,
`valio-recognition`, `valio-replenishment-sim`, `valio-retail-vision`, `valio-market-research`. Raw sponsor data, page
copies and private results stay out of git.
