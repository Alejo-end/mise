# Handoff, worker 3: real Valio data and quotes (14 Nov 2026, 05:42 to 06:40)

Branch `wt/valio-data`, base `eca4100` (`valio/hw-checkpoint`). Not pushed, not merged.
Valio's materials are **sponsor-provided challenge data and a mock API**, not a Valio production system.

## Commits

| Commit | What |
|---|---|
| `9317466` | Adapter reads the measured answers (bare `availableDate`, `closingTime`, sales unit, price validity, tax); live calls rate-limited, retried with backoff and jitter, capped, recorded without the key. Quotes and approvals: source label, unknown delivery and deposit charges, quote expiry, lot sizes (422), dry orders. `tests/test_valio_quotes.py` |
| `ed4ce9d` | Field status table and measured API facts in `docs/valio-api/API.md` |
| `0130062` | A moved delivery date after an approval goes back to a person; "in time by date only" never on a late line; recordings never overwrite and never break a call |
| `3864ad6` | The three Codex review findings fixed (see "Codex review") |
| `c8e3418` | No "in time by date only" note on a same-day line that needs a person (seen in the live smoke test) |
| (this file) | Handoff, and the call count in `API.md` |

## Measured: data imported

Shared read-only folder `/Users/bigboss/Ai/Claudecode/valio-source-data/2026-11-14/`, linked as
`valio-challenge/data/valio` (git ignores it). Sizes and SHA-256 in its `MANIFEST.txt`.

| File | Bytes | Records | Join to products by code |
|---|---|---|---|
| `valio_aimo_junction26_product_data.json` | 87,077,467 | 17,546 products, ids 6-digit strings, unique, `deleted` false on all | |
| `valio_aimo_junction26_delivery_reliability.csv` | 56,741 | 4,425 rows (`productId`, `deliveryReliability`, 0.0 to 1.0) | 0 codes unmatched; 13,121 products have no row (unknown) |
| `valio_aimo_junction26_carbon_footprint.csv` | 156,990 | 11,033 rows (`productId`, `AVG kg CO2-eq/kg`, 0.13 to 30.46) | 0 codes unmatched; 6,513 products have no row (unknown) |

Codes stay strings; none has a leading zero, so stripping zeros changes nothing (checked).
The **product JSON schema is not downloaded**: it is on the password-protected portal only (no API
endpoint; `/openapi.json` also answers 303 to `/login`, with or without the API key). Needs a person with
the event password. The schema used is the repository's `docs/valio-api/openapi.json` (from the portal).

## Measured: API calls

35 calls in total (budget 300), all recorded (secrets removed, read-only) in
`valio-source-data/2026-11-14/api-recordings/` with a README (per-call table). No 429 and no 5xx happened.
The folder is locked read-only (`chmod -R a-w`) since 06:36.

| Endpoint | Calls | Answers |
|---|---|---|
| `GET /openapi.json` | 2 | 303 to the portal login (without and with the key) |
| `GET /api/ping` | 1 | 200 |
| `GET /api/{products,delivery-reliability,carbon-footprint}/download` + same-host `/files/...` | 6 | 307 then 200 each |
| `POST /api/price` | 13 | 200 (1 wasted: a shell quoting error sent one joined string, `error: true`) |
| `POST /api/availability` | 13 | 200 (1 wasted, same cause) |

Shortlists (Valio: never price the whole catalogue; we never did): exploration 11 products (rule set v4
targets 477032, 464316, 463335; approved substitutes 464306, 464401, 464398; Valio's example crates
462921, 463038; deposit product 460123; no-allergen product 460585; KG lot-size product 461411); demo
quotes 1 product each (463335, 460585); text request and slot 3: the pipeline's 6-product shortlist
(32 local search hits, 26 left out for another pack size); slot 1: 3 products; smoke test after the
Codex fixes: 463335 and 477032, 1 product each.

Facts the answers showed (14 Nov 05:49 to 06:25): `availableAmount` 9999999999 for most Stock products
(undocumented; read as stated), 999999 for Terminal; `closingTime` already passed for Terminal products
(477032 rye bread: 12 Nov 12:30 for delivery on 16 Nov); `availableDate` a bare date; price per sales
unit without tax, VAT 13.5 %; no delivery charge, no deposit price anywhere.

## Measured: tests

| Run | Result |
|---|---|
| Baseline at `eca4100`, `uv run pytest -q` | 2 failed, 160 passed, 6 skipped. The 2 failures come from this worktree's `.env` (`RESTOCK_MIN_S=120`), which `backend/app.py` loads into the environment during tests. With `RESTOCK_MIN_S=0`: 162 passed, 6 skipped |
| After linking the real file, no code change | 168 passed, 0 skipped: Alejo's 6 skipped tests now run and pass (`test_real_file_maps_the_demo_products`, `test_real_catalog_the_usual_milk_is_ordered_alone`, `..._short_stock_is_filled_with_an_approved_carton_of_the_same_size`, `..._valios_example_request_goes_to_the_chef`, `..._a_nut_product_is_refused_and_an_unknown_one_goes_to_a_person`, `test_real_catalog_counts_under_the_school_bans`) |
| `9317466` | 187 passed |
| `0130062` | 190 passed |
| `3864ad6`, `c8e3418` (final) | **192 passed, 0 skipped, 0 failed** (24 in `tests/test_valio_quotes.py`, one replays the recorded API answers; 27 in `tests/test_valio_catalog.py`) |

All runs with `RESTOCK_MIN_S=0` in the shell. Without it, the same 2 tests fail in this worktree only.

Alejo's tests changed, intended and documented (three edits in `tests/test_valio_catalog.py`):
`test_price_and_availability_come_from_the_two_real_endpoints` expects the bare date instead of the
invented 06:00; its fake API's fixed `closingTime` (2026-11-15 12:00) became "tomorrow 12:00", because a
passed cut-off now makes a product not orderable (the fixed date would have failed from 15 Nov noon on);
the `stock_of` stand-in delivers today instead of tomorrow, because "tomorrow" is the needed weekday from
Sunday to Thursday, and a same-day delivery with no arrival time now goes to a person.

## Measured: demo steps 2 to 4 on the live mock API (127.0.0.1:8301, `CATALOG=valio`, rule set v4)

| Step | Result |
|---|---|
| 1, Driving Test | **Blocked as committed**: with rule set v4 our agent passes 5 of 6 (`api_error`: 0 orders) and gets no licence. Also at base `eca4100` (checked on an exported copy). Cause: the sandbox runs the stand-in catalog but takes the backend's rule set; v4's usual milk 463335 is not in the stand-in, so the agent asks a person instead of ordering. Workaround for this run only: the Driving Test with the stand-in rule set v3 (same product groups and €150 limit) written into my audit log; the backend restored the licence at restart |
| 2, quote | 1,000 × 463335 for Mon 16.11.: €1.40 per carton without tax, total €1,400.00 (goods only), delivery 2026-11-16 (time not supplied), cut-off 15.11. 17:00, delivery charge unknown, decision needs approval. Signature verifies; one digit changed fails |
| 3, order and approval | HTTP 202, approved with the PIN, `ORD-6A036266` "recorded by the gateway, not sent to Valio"; the same key returns the same order |
| 4, catalogue and label | Counts 1,962 forbidden (11 %), 8,149 to a person (46 %), 7,435 clear (42 %). First order of 460585 (no allergen data) needed approval, approved, `ORD-794D2491`; the label memory works ("Test chef checked the label"). **The second order still needed a person**: 460585 is not a usual product or an approved substitute in rule set v4, and step 3's €1,400 put the week over the €800 limit |

Live smoke test on the final code (06:35, calls 32 to 35): 24 × 463335 for Monday now says "available on
the needed day Mon 16.11., but the source gives no arrival time, so a person must confirm it arrives by
07:00"; 477032 answers HTTP 409 "the order cut-off 2026-11-12T12:30 for delivery on 2026-11-16 has passed";
the step 3 order, restored after a restart, still says "not sent to Valio". **Consequence: on the live
mock API with rule set v4, every order now goes to a person** (the API answers the requested Monday, and
the rule set needs Monday 07:00), until a signed rule set sets `delivery.date_only_same_day: allowed`.

Also seen (before the Codex fixes): the text request "1,000 litres of lactose-free milk by Monday" gives one option (the usual
milk, €1,400). Slot 3 empty: the milk is fully available (9999999999), so **no substitute appears**: demo
step 5's "stock is short" does not happen on the live mock API. Slot 1 empty: the usual rye bread is left
out ("the order cut-off 2026-11-12 12:30 for delivery on 2026-11-16 has passed"); an unapproved substitute
goes to a person. All approvals except steps 3 and 4 were skipped.

## Behaviour changes (all in worktree 3 files)

1. No invented arrival time: a Valio delivery is the `availableDate` (`rules.Line.delivery_time_known`).
   An earlier date is in time ("in time by date only"); a later date is late; the needed day itself goes
   to a person ("no arrival time, a person must confirm it arrives by 07:00"), unless the signed rule set
   says `delivery.date_only_same_day: allowed`. The stand-in catalog keeps its times.
2. A passed `closingTime`, a sales block, or a sales unit other than the file's makes a product not
   orderable, with the reason (gateway 409 with the reason). A forbidden product stays forbidden.
3. Lot sizes: a gateway quote off `allowedLotSize` gets 422; the pipeline gives every allocated line a
   whole multiple of its lot size (`allot`), so splits stay valid (only 672 KG and 2 KI products have lots
   other than 0.01).
4. Charges: delivery charge always "unknown", deposit charge "unknown" where the file lists deposits;
   never 0, never in a total. `total_eur` stays the goods total without tax (the approval page needs a
   number), with `total_covers` and `charges` next to it. Trade-off texts say "Delivery charge unknown".
5. Quote expiry: earliest of 15 minutes, the cut-off, the price's `validTo` (`valid_until_set_by`).
6. Recheck before an order: a moved delivery date is a change; after an approval it goes back to a person.
7. Orders: answers and the audit log say "recorded by the gateway, not sent to Valio" (`sent_to_valio: false`).
8. Valio's delivery reliability no longer ranks substitutes (it treated unknown as 1.0); it is shown as
   information with its meaning. Carbon: an estimate only when the file establishes the ordered mass.
9. Live calls: at most 2 a second, at most 3 retries (429, 5xx, network) with exponential backoff and
   jitter or Retry-After, optional cap `VALIO_MAX_CALLS`, recording with `VALIO_RECORD_DIR`.
10. One compatibility change outside the buying path: `Agent.check_delivery` reads a bare-date delivery
    (late only from the next day on); before, it would have crashed on a Valio order.

## Assumptions (not measured)

- 9999999999 means "this much is available" (documentation: `availableAmount` is the quantity available).
- The spend limits apply to the goods total without tax; the unknown delivery charge does not send every
  order to a person. Open decision below.
- None about arrival: the needed day goes to a person by default (see decision 5).

## Not tested

- Any 429 or 5xx from the real API (none happened; the retry is tested against a stand-in only).
- The approval page (`web/approve.html`) with the new fields; it reads `total_eur`, `trade_off` and the
  line prices, which keep their types.
- The board, the camera and step 5 on the shelf. Demo steps 1 to 4 three times in a row.

## Needs a person or another worktree

1. **Driving Test on rule set v4** (blocks demo step 1 on the real catalog, pre-existing): `app.py`
   builds `DrivingTest(ruleset, log)` with the backend's rule set, but the sandbox uses the stand-in
   catalog. Proposed: give the sandbox the stand-in rule set `rules/school_kitchen.yaml` (same groups and
   limit). `app.py`/`sandbox.py` are not worktree 3's files.
2. **Step 4's "second order goes through alone"** needs a no-allergen product that is a usual product or
   an approved substitute in a new signed rule set (worktree 2 / Alejo), and a week that is under €800:
   run step 4 before approving step 3's €1,400, or use a fresh audit database per rehearsal.
3. **Step 5's "stock is short"** cannot happen with the live mock API for 463335 (9999999999 available).
   The shelf story needs another product, a smaller limit, or a scripted shortage, decided by the owner.
4. The product JSON schema (portal password).
5. Decisions: may the kitchen's signed rule set accept a delivery on the needed day without an arrival
   time (`delivery.date_only_same_day: allowed`; until then every live order goes to a person); should an
   unknown delivery charge send orders to a person; should 9999999999 count as known stock.
6. Handbook notes (Alejo's file): label the Valio materials as sponsor-provided mock data and API; the
   06:00 delivery is gone; the run settings need `VALIO_RECORD_DIR` and optionally `VALIO_MAX_CALLS`.
7. The worktree `.env` sets `RESTOCK_MIN_S=120`, which makes 2 committed tests fail in this worktree.

## Codex review

One run, as briefed: `codex exec -s read-only` (model gpt-6-astra), 06:27 to 06:31, exit 0, output in
`valio-challenge/data/codex-review-w3.md` (git-ignored; checked: it holds no key). It reviewed commit
`ed4ce9d` against `valio/hw-checkpoint` and found three P2 issues. All three were real and are fixed, with
tests, in `3864ad6`:

1. `rules.py`: a date-only delivery on the needed day was `allowed`, although the signed rule needs it by
   07:00. Fixed: a person confirms it unless the rule set permits date-only delivery.
2. `agent.py`: lot sizes were checked per candidate against the whole need, which dropped valid splits
   (Codex reproduced 1 usual + 2 of a substitute with lot 2). Fixed: checked per allocated line.
3. `gateway.py`: `GET /gateway/orders/{run_id}` lost "not sent to Valio" for orders restored after a
   restart. Fixed, and confirmed live on the restored order `ORD-6A036266`.

No finding was left open. My own review before Codex fixed three more (`0130062`).

## Not written: findings for worker 4

The brief asks for `valio-source-data/FINDINGS-identifiers.md`. The agent harness refused to let this
worker write that report file, so its content went to the integration session in the final report. Short
version: the product file has **no image URLs** (`synkkaData.medias[]` holds only `id` and `primary`; no
documented media endpoint; 0 images fetched); GTINs are the strong identifier (`salesUnitGtin` 17,387
products, `units[].gtin` 17,540, `synkkaData.gtin` 14,478); names fi 17,546, en 17,544, sv 16,100.
