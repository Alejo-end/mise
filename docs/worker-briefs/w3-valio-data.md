# Worker 3: real Valio data and quotes (priority 1, the 12:00 milestone)

Worktree `/Users/bigboss/Ai/Claudecode/junction2026-wt-valio`, branch `wt/valio-data`, backend port
8301 on 127.0.0.1 only, audit database from `valio-challenge/.env`. Your files are in the
contract (`docs/PARALLEL_WORK.md`, worktree 3).

Goal (TEAM_BRIEF.md, Saturday 12:00): the Valio entry runs on the real Valio data and the live
price and availability API, and the HANDBOOK demo steps 2 to 4 work on it.

## What you have

- `VALIO_API_KEY` is in your `valio-challenge/.env` and works (`GET /api/ping` answered 200 at 05:44).
  Never print it. Read it from the environment.
- `docs/valio-api/API.md` (Alejo) describes the portal, the files, `POST /api/price`,
  `POST /api/availability` and the download endpoints. The API has no search and no order endpoint.
- `backend/valio.py` has the real-catalog adapter (`ValioCatalog`, `make_client`), tested only with
  a fake API. Six tests in `tests/test_valio_catalog.py` skip because the product file is missing.

- Official resources: the portal https://junction26.valioaimo.fi/ and its OpenAPI description
  https://junction26.valioaimo.fi/openapi.json (pricing API, availability API for a requested
  delivery date, product JSON and its JSON schema, delivery-reliability CSV, carbon-footprint CSV).
  Read the actual schemas and endpoint documentation; where they disagree with API.md, they win.
  Authenticate every `/api` request with the `X-API-Key` header.

## Semantics you must keep (from the person, 14 Nov 05:50)

- These are **sponsor-provided challenge data and mock APIs**. Label them so everywhere (field table,
  quote answers, approval text, handbook notes), never as Valio production systems.
- Availability for a requested date does **not** establish a precise arrival time. Do not invent an
  arrival time or window.
- Delivery reliability is historical fulfilment against ordered quantities. It is **not** an on-time
  delivery probability; never call it one.
- Missing reliability and carbon values stay **unknown** (not 0, not average).
- Carbon values are product-group averages in kg CO2-eq per kg. Estimate an order's footprint only
  when the ordered mass is established correctly (from the file's units and sizes); otherwise unknown.
- Inspect whether delivery fees, cut-off times and arrival windows are actually supplied. Unsupported
  fields stay explicit ("not supplied by the challenge API"), never invented.

Priority inside this task: catalogue, pricing and date-specific availability first. Reliability and
carbon come after, as transparent comparison information in quotes and approval options, only where
compatible with the plan and the rule check (they inform, they do not decide).

## Tasks, in order

1. Download the official files once, with the documented read-only endpoints (`curl -L -H
   "X-API-Key: ..."`; the download endpoints in API.md and the OpenAPI file, plus the product JSON
   schema) into the shared source folder `/Users/bigboss/Ai/Claudecode/valio-source-data/2026-11-14/`,
   with the names in API.md. Check sizes and record a SHA-256 of each file in `MANIFEST.txt` there.
   Then make the folder read-only (`chmod -R a-w`) and link it as `valio-challenge/data/valio`
   (`ln -s`). Other workers read the same files; each keeps its own database. Never commit the files
   (Valio's terms).
   Join the supplementary CSVs to the products by product code: keep codes as strings (leading
   zeros), and count and report unmatched records in both directions.
   Check the product schema for image URLs, GTINs and other identifiers useful for recognition.
   Write verified findings (field names, counts, an example per field, whether image URLs resolve,
   without fetching more than 3 images) to
   `/Users/bigboss/Ai/Claudecode/valio-source-data/FINDINGS-identifiers.md` for worker 4.
2. Run Alejo's skipped tests. Report the exact counts. Do not fabricate data to make a test pass;
   if a test fails on the real file, find out whether the test or the code is wrong and say which.
3. Start your backend with `CATALOG=valio` and `RULES_FILE=rules/school_kitchen_valio.yaml` and run
   demo steps 2 to 4 (HANDBOOK.md) against the live API. Production is read-only: price and
   availability calls only. Valio asks teams not to fetch prices for all products: narrow first,
   at most 300 API calls in total, at most 2 per second, back off on 429, at most 3 retries.
4. Write a field status table into `docs/valio-api/API.md`: for every field the pipeline uses
   (price, validity, availability, available date, closing time, sales block, sales unit, pack size,
   lot size, deposits, allergens, delivery reliability, delivery charge, order placement, order
   confirmation, quote expiry) say whether it is **real** (live API or Valio file), **sandboxed**
   (the challenge's mock environment), **unavailable** (Valio has no source), or **simulated** (our
   code), with the source.
5. In the buying path and the gateway quotes: use arrival information where the source has it
   (`availableDate`, `closingTime`) for the deadline check; include charges where the source has
   them (deposits, if the file prices them). A missing value stays **unknown**: never treat a
   missing delivery charge as zero in a total; say "delivery charge unknown" in the quote and the
   approval text. Respect pack sizes and lot sizes, quote expiry (the price `validTo` and our
   15-minute quote), spending limits and deadlines. Add tests in `tests/test_valio_quotes.py`;
   tests that need the Valio file skip when it is absent, like Alejo's.
6. Purchases stay dry: our gateway records orders, no Valio endpoint receives one. Make that
   explicit in the order answer and the audit log ("recorded by the gateway, not sent to Valio").
7. Review: run one Codex review on your final diff of quote validation and the purchasing states:
   `codex exec -s read-only -C /Users/bigboss/Ai/Claudecode/junction2026-wt-valio "Review the diff
   of branch wt/valio-data against valio/hw-checkpoint for correctness bugs in quote validation,
   pack sizes, expiry, limits, deadlines and order states. List findings with file and line."`
   with a 15-minute timeout, output to `valio-challenge/data/codex-review-w3.md`. Fix real findings,
   note the rest in the handoff.

Leave `handle_event` and the pending-slot code in `agent.py` alone (worktree 1), and the rule set
loading and signing in `rules.py` (worktree 2). Handoff: `docs/handoff/w3-valio-data.md`. It must
report: which endpoints were called successfully (with counts), which data was imported (files,
sizes, record counts, unmatched joins), which tests now run and their results, and which purchasing
fields remain simulated or unavailable.
