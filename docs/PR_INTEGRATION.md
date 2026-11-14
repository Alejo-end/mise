# Draft PR: Valio integration into main

**Title:** Valio: safe shelf client, real data, signed shelf configuration, recording tools (integration of 14 Nov)

**Draft. Do not merge before Alejo's review.** Branch `valio/integration-unattended` (56 commits ahead of `main`, which
it contains together with `review-14-nov`). Experiments stay on their own `exp/*` branches and are not part of this PR.

## What changes

- **Purchasing safety.** Stale camera frames and repeated low readings cannot create duplicate orders (worker 1).
  The recheck before an order now applies the current rules to the current product data (3c3c7a2). Before this,
  a product that lost a required property, or a rule change with unchanged price and stock, could still be ordered;
  the agent benchmark found it, and its rule-change family goes from 0/8 to 8/8.
- **Real Valio data.** Product file import, real catalogue adapter, recorded price and availability responses,
  dated deliveries, cut-offs, lot sizes, unknown charges and field labels (worker 3).
- **Shelf configuration in the signed rule model.** `GET/PUT /shelf`; every change signs a new rule set version.
  A slot can be unmapped for purchasing (49fb733). Observations are kept apart from assignments (worker 2).
- **Shelf box.** A box editor with a draft, atomic save, red conflicts and recalibration after a move. There is no
  purchase while editing or with an invalid layout. Checked on the real board page. Calibration validity is per
  assignment; board health is reported on `/device`.
- **Recordings.** A recording mode in which device events never reach the agent (23ea6a4); a guided recorder with
  typed item counts and Enter marks; product enrolment by exact barcode or by owner confirmation; a clip check and
  upload; `docs/CAPTURE_GUIDE.md` (draft, not yet performed).
- **Detector investigation.** Views between the two calibration photos can read "low": kept as strict
  expected-failure tests. A shift-tolerant candidate rule is added but **off** by default.
- **Documentation.** `docs/TEAM_HANDOFF.md` (start here), `docs/PARALLEL_WORK.md` (contract and open decisions A
  to I), two proposals pending agreement in `docs/decisions/`, and board measurements.

## Validation

- `cd valio-challenge && uv run pytest`: **352 passed, 2 expected failures** (the known detector gap).
- Box editor driven in a browser on the real board page (draft kept through board updates, refused save, valid
  save, recalibration, reload).
- Round-4 benchmark cases rerun against this branch's backend: rule-change family 8/8 (0/8 before the fix).
- Deployed: board 0d93b6a; the backend runs this code without 3c3c7a2, in recording mode.

## Remaining limitations

- Demo step 1 fails (Driving Test 5/6) until `fix/sandbox-harness` (03608dd) is merged. It touches Alejo's
  `sandbox.py` and `app.py` start-up, so it is left for Alejo.
- Decisions G (date-only delivery), I (mock stock) and C (camera "full" ending a pending order) are open; the
  defaults are documented.
- Escalation gap: with every safe product unavailable, the agent records "blocked" instead of asking the Head chef.
- The shelf detector estimates a fill level and is not a counter. There are no validated recordings of the real
  products yet, so no recognition claim can be made.
- The live backend is in recording mode with all slots unmapped. A demo of shelf purchasing needs that ended and
  products signed deliberately.

## Related experiments (not in this PR)

Findings with their evidence class, and a keep, change, defer or drop recommendation, are in
`docs/TEAM_HANDOFF.md` section 4. The agent benchmark (`exp/agent-bench`) found the recheck gap fixed here; the
replenishment simulator (`exp/replenishment-sim`) and the market research (`exp/market-research`) inform the
pending proposal and the pitch.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
