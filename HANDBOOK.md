# Handbook: Aimo Agent Gateway and the Safe Kitchen Agent

This handbook explains the Valio Aimo entry to anyone on the team. The build brief with the work order is `DEMO_PLAN.md`. The Valio data and API are described in `docs/valio-api/API.md`.

## What it is

Valio Aimo asks how a wholesaler can let outside buying agents order safely. Our answer has two parts.

1. The Aimo Agent Gateway is the interface on Valio's side, and it is the entry. It gives lookup, quote, order, approval and an audit log to any outside agent that holds a licence.
2. The Safe Kitchen Agent is one client of the gateway. A camera on an Arduino UNO Q watches a kitchen shelf. If a product runs low, the agent orders it inside the kitchen's rules.

The design rule: a language model can search, compare and explain. Plain code decides what is allowed.

## The gateway for an outside agent

An outside agent does these steps. The calls are in `docs/valio-api/gateway-openapi.json`, an OpenAPI file that any agent builder can read.

1. Take the Agent Driving Test: `POST /sandbox/test`. An agent that passes all six scenarios gets a licence with a spend limit, product groups and an end time.
2. Look up products: `GET /gateway/products?q=`.
3. Ask for a quote: `POST /gateway/quotes`. The answer has the price, the stock, the delivery date and the rule decision. It is valid for 15 minutes.
4. Order the quote: `POST /gateway/orders` with an idempotency key (a name for this order, so a repeated call never orders twice). The order is placed, sent to the chef, or refused with the reason.
5. Read the state and the log of the order: `GET /gateway/orders/{run_id}` and `GET /gateway/audit?run_id=`.

The gateway signs every quote and every order answer with an Ed25519 key. The public key is at `GET /gateway/key`. So the agent can keep an answer and prove later what the wholesaler told it. A hash alone cannot do that, because the server can make any hash. In this prototype the gateway makes its own key. In production the key belongs to Valio.

A product outside the product groups of the licence goes to the chef. A restart of the backend keeps licences, placed orders and open approvals, because they come back from the audit log. It forgets open quotes.

## The map

![The kitchen, our gateway and Valio's challenge materials](../docs/handbook/valio-system.svg)

Valio's challenge API has only two live calls: price and availability. It has no search and no order. So search runs in our gateway over the product file, and our gateway records the orders.

## The shelf box

![The parts of the shelf box, pulled apart](enclosure/renders/exploded.png)

The case holds the UNO Q, the Knob and the Buzzer. The Thermo sits in its own small pod on a long cable, away from the heat of the board. `enclosure/README.md` has the print settings, the screws and the assembly steps. `enclosure/cad/sk1_assembly.FCStd` opens in FreeCAD.

## How it decides

![Allowed, needs approval, forbidden](../docs/handbook/valio-decision.svg)

The head chef signs a rule set once (`rules/school_kitchen.yaml`). Every decision names the version of that rule set. "Signed" means that the app records a hash of the file and refuses a changed file with the same version. It is not a cryptographic signature.

One order goes through these steps, and each step goes into the audit log:

1. A trigger arrives: the camera, a knob press or a text request.
2. The pipeline searches the catalog and asks price and stock for each candidate.
3. The rule check drops every forbidden product.
4. If the usual product is short, the pipeline fills the gap with substitutes and ranks up to three options.
5. If the first option is allowed, the agent orders. If not, the chef gets the options on the phone, and the box beeps.
6. Just before the order, the pipeline asks price and stock again.
7. The order is placed with one key per run, so a repeated call does not order twice.

Two cases always go to the chef. The first is a product that the wholesaler cannot count, because the supplier ships it. The second is a product whose label a person must read, for example a product with no allergen data. The chef reads that label once. The agent keeps the answer for the product. If the product data or the rule set changes, the agent asks again.

## What the real data looks like

![The real catalog under the kitchen's allergen rules](../docs/handbook/valio-allergen-data.svg)

| Result under the bans (nuts, peanuts, sesame) | Products | Share of 17,546 |
|---|---|---|
| Forbidden by the allergen codes | 1,962 | 11% |
| To a person: no allergen data | 8,110 | 46% |
| To a person: a banned allergen is marked undeclared | 22 | under 1% |
| To a person: only the ingredient text names it | 17 | under 1% |
| Clear | 7,435 | 42% |

Almost half of the catalog has no allergen data. So on the real catalog, "unknown" must go to a person, not to "forbidden". The rule-set key `unknown_allergens` sets this. The stand-in rule set keeps the strict value.

To count again, run `uv run python -m backend.dirty_data`. A test holds the same numbers. The command also prints 20 clear products for a hand check.

46% does not mean that the chef approves every second order. The chef reads the label of each such product once, on its first order. We do not know how many of these products a real kitchen orders in a week. Ask the Valio mentors.

The ingredient text scan knows nuts, peanuts and sesame only. If a rule set bans another allergen, the backend writes a notice in the log at the start, and `/device/state` lists the gap. For that allergen, only the allergen codes protect.

## Run it

Run every command from `valio-challenge/`. You need `uv`.

1. Put the approver PINs in `.env`, for example `APPROVER_PINS=4821:Head chef`.
2. Start the backend: `uv run uvicorn backend.app:app --host 0.0.0.0 --port 8000`.
3. Open `http://localhost:8000/approve`. On a phone, use the laptop's address on the same hotspot.
4. Run the tests: `uv run pytest`. 147 tests pass.

To run on the real Valio catalog, add three lines to `.env` and start the backend again:

```
VALIO_API_KEY=<the key from the challenge page>
CATALOG=valio
RULES_FILE=rules/school_kitchen_valio.yaml
```

The product file must be in `data/valio/`. Rule set version 4 names real product codes. If the rule set and the catalog do not match, the app refuses to start.

To let a real language model take the Driving Test, add `ANTHROPIC_API_KEY=<key>` to `.env`. Then send `{"agent": "claude"}` to `POST /sandbox/test`. The model gets the tool descriptions, the request and the kitchen's rules, and no hint about the scenarios. One run makes about 30 model calls.

The shelf box is the App Lab app in `device/`. Its own page is on port 7000 of the board. In "Settings" on that page, enter the laptop address. Then do the setup steps in order: place the three boxes, save the empty photo, save the full photo.

To put a new version on the board over USB, use `adb`:

1. Copy the changed files: `adb push device/python/main.py /home/arduino/ArduinoApps/safe-kitchen-agent/python/main.py`. Do the same for `sketch/sketch.ino` and `assets/index.html`.
2. Open a shell on the board with `adb shell`. Then run `export TMPDIR=/tmp`. Without it, the start fails with "Stat /Data/Local/Tmp".
3. Run `arduino-app-cli app restart user:safe-kitchen-agent`. The board builds the sketch, flashes it and starts the app. This takes about two minutes.
4. Read the result: `curl -s localhost:7000/state` and `arduino-app-cli app logs user:safe-kitchen-agent`.

The board holds a copy of the version before 14 November in `~/safe-kitchen-agent.backup-20261114`.

In a warm room, the cold room alarm is on. Then the buzzer plays the alarm, and every order waits for the chef. For a demo, raise the alarm limit in "Settings", or put the Thermo pod in a cool box.

## Demo in six steps

1. Start with the gateway. Run the Agent Driving Test on the naive agent and on our agent. Only our agent gets a licence.
2. With that licence, an outside agent asks for a quote for "1,000 litres of lactose-free milk by Monday". Show the price, the time of the price and the signature. Change one digit of the answer, and the signature fails.
3. The agent orders the quote. The order is over its limit, so it waits for the chef. Approve it on the phone with the PIN.
4. Show the real catalog under the school's bans: 11% forbidden, 46% to a person, 42% clear. Then order one product with no allergen data. The chef reads the label once, and the second order goes through alone.
5. Now the kitchen as one client. Take the milk off the shelf. The camera triggers the agent, and the box beeps. Stock is short, so a safe substitute needs approval.
6. Show the audit log as a story and as data.

## How the judges score

| Criterion | Weight | What it means for us |
|---|---|---|
| Working end-to-end demo in the mocked environment | 30% | The demo must run on Valio's real data and API, not on our stand-in catalog. This is the first job |
| Solving the problem and user need | 20% | The order must match what the buyer asked for: the right product, pack size and amount |
| Autonomy and controls | 20% | The three results of the rule check, the approval on the phone and the audit log |
| Exception situations | 15% | Partial stock, substitutes, late delivery, a price that changes before the order |
| Openness and a path to production | 15% | Open, documented calls. FastAPI gives the interface as OpenAPI at `/docs`. The assumptions are in `../ASSUMPTIONS.md` |

## What is true today

| Part | State |
|---|---|
| Rule check, buying pipeline, approvals, audit log | Work on the stand-in catalog, with tests |
| Shelf box app and enclosure | Built, and the version of 14 November runs on the board. The camera count is a rough estimate from two photos, not a real count |
| Shelf box on 14 November at 02:40 | The app talks to the backend. No Modulino answered on the Qwiic port, and no camera was plugged in. So the knob, the Thermo, the buzzer and the camera are not tested with this version |
| Real catalog adapter (`backend/valio.py`, `backend/allergens.py`) | Built and tested on the real product file with a fake API. Not tested against the live API: no API key is on this laptop |
| Buying pipeline on the real catalog | Works on the real product file with rule set version 4. Tests use a stand-in for the two live calls. Nobody ran it against the live API yet |
| Gateway calls for an outside agent | Built, with tests: lookup, quote, order, order state and audit under `/gateway`. Each call needs the licence of the Driving Test. Quotes and order answers are signed. Licences and orders survive a restart |
| Agent Driving Test | Works. The "naive agent" is a script, not a language model. An agent that does nothing fails. A real model can take the test (`backend/model_agent.py`), but nobody ran it yet: no model key is on this laptop |
| Delivery check (Cold Chain Proof) | Works on the stand-in catalog. The Thermo measures air, not food |

## Limits to say out loud

1. The allergen code map is our reading of the GS1 list. Ask the Valio mentors for the official list.
2. "Missed" risky products cannot be counted without a sample that a person checked by hand.
3. The PIN has four digits. Five wrong tries lock approvals for 30 seconds. It is for the demo network only.
4. The box updates its own code from the laptop with no login. Use it on our own hotspot only.
5. The signature shows that an answer came from the gateway's key. In this prototype the gateway holds that key, not Valio.
6. We wrote the six scenarios of the Driving Test. A pass shows that an agent handles these six cases, not that it is safe in general.
7. A substitute must have the same pack size as the usual product. So a 1.5 l carton is not offered for a request in litres. The approval request says how many products the agent did not consider for this reason.
8. A label check is kept until the name, the allergen data or the rule set changes. A recipe change with no data change is not seen.

## Where things are

| Path | Content |
|---|---|
| `backend/rules.py` | The rule check and the rule-set signature |
| `backend/agent.py` | The buying pipeline, approvals, delivery check |
| `backend/valio.py` | The stand-in catalog and the real catalog adapter |
| `backend/allergens.py` | GS1 allergen codes to our names, and the ingredient text scan |
| `backend/audit.py` | The append-only log |
| `backend/gateway.py`, `backend/signing.py` | The calls for an outside agent, and the signature on their answers |
| `docs/valio-api/gateway-openapi.json` | The published description of those calls. `uv run python -m backend.export_openapi` writes it |
| `backend/sandbox.py`, `backend/model_agent.py` | The Agent Driving Test, and a real language model as the agent under test |
| `backend/dirty_data.py` | The count of the real catalog under a rule set |
| `backend/app.py`, `web/approve.html` | Endpoints and the phone page |
| `device/` | The shelf box app for the UNO Q |
| `enclosure/` | The printed case. See `enclosure/README.md` |
