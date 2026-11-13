# Safe Kitchen Agent: demo plan

This file is the build brief for the Safe Kitchen Agent. Team Kindergarten Engineering builds it at Junction 2026 for the Valio Aimo challenge "Trusted Trade Agents". Any person or coding agent who joins the work starts here.

## Goal

A camera on an Arduino UNO Q watches a kitchen shelf. When a product runs low, a buying agent searches the Valio Aimo mock catalog. It picks a product or a safe substitute, applies the kitchen's rules, and orders within its limits. Larger or riskier orders go to the chef for approval. Every step goes into an audit log that a person can read.

The design rule: the language model searches, compares and explains. Plain code decides what is allowed. A model can be talked into a bad substitution. A rule check cannot.

## Deadlines

| When | What |
|---|---|
| Friday 13 November, 23:59 | Preliminary submission on the Junction platform |
| Saturday 14 November, 09:00 | First end-to-end run works (see "First milestone") |
| Saturday 14 November, 18:00 | Feature freeze for the core |
| Sunday 15 November, 08:00 | Final submission sent (the hard deadline is 10:00) |

## Constraints

1. Do not put secrets in the repository. Every submission becomes public under the MIT license. The Valio API key goes in `.env`, which `.gitignore` already excludes.
2. Get the Valio portal password and API key from the team. They are in the challenge brief on the Junction platform.
3. Use only the synthetic data from Valio. There is no real customer data.
4. Run a secret scan before each push, for example `gitleaks detect`.
5. Put the UNO Q, the laptop and the phone on one phone hotspot. Venue Wi-Fi often blocks traffic between devices.

## Hardware we have

| Part | Job in the demo |
|---|---|
| Arduino UNO Q (2 GB, Linux side and microcontroller side) | Runs shelf detection on the Linux side and reads the Modulino sensors on the microcontroller side |
| Arduino USB camera | Looks at the shelf |
| Modulino Knob | Sets the auto-order spend limit. A press marks the selected slot as low, as a fallback trigger |
| Modulino Thermo | Reads the cold-room temperature. Above the limit (for example +6 °C), the box sends an alarm event |
| Modulino Buzzer | If the chef must approve, it beeps. A temperature alarm plays a different tone |
| USB-C hub, 45 W power supply | Power and connections |
| 3D printer (queue at the hardware desk) | Shelf and camera mount |

Make sure that the Modulino code matches the `Arduino_Modulino` library examples in App Lab. Also make sure that the bridge between the Python side and the microcontroller side follows the App Lab examples.

## Architecture

```
[Shelf + USB camera] -> UNO Q Linux side (python/main.py)
                           |  slot state, temperature, knob value
[Modulinos] <-> UNO Q microcontroller side (sketch/sketch.ino)
                           |
                           v  HTTP POST /events
                 Backend on a laptop (FastAPI)
                   - valio.py   : adapter for the Valio mock API, caches every response
                   - rules.py   : rule check in plain code
                   - agent.py   : buying pipeline, later with a language model
                   - audit.py   : append-only log (SQLite first, Aiven Postgres later)
                   - app.py     : endpoints, approvals, serves the phone page
                           |
                           v
                 Phone page (web/approve.html), later Meta glasses
```

## Repository layout

```
device/          App Lab app: python/main.py, sketch/sketch.ino
backend/         app.py, valio.py, rules.py, agent.py, audit.py
rules/           school_kitchen.yaml
tests/           test_rules.py
web/             approve.html
fixtures/        cached Valio API responses (synthetic data only)
docs/valio-api/  API documentation from the Valio portal, without keys
```

## Components

### Device (UNO Q)

1. The Linux side reads a camera frame every 2 seconds.
2. The shelf uses fixed slots marked with high-contrast tape, with one product per slot.
3. The first version compares each slot area with a stored photo of the empty slot. No machine learning is needed for this.
4. A later version replaces the comparison with an Edge Impulse model that returns full, low or empty.
5. The microcontroller side reads the knob, the thermo and the knob button, and plays buzzer tones on request.
6. When a slot changes state, the board sends an event to the backend.

Event format:

```json
{
  "type": "slot_state",
  "slot": 3,
  "product_hint": "lactose-free milk 1 L",
  "state": "low",
  "estimated_units": 4,
  "temperature_c": 4.2,
  "auto_limit_eur": 150,
  "time": "2026-11-14T09:12:03+02:00"
}
```

The other event types are `temperature_alarm` and `manual_low` (the knob press).

### Kitchen rule set

The head chef signs the rule set once. Every decision cites its version.

```yaml
version: 3
signed_by: "Head chef, Espoo school kitchen"
site: school
banned_allergens: [nuts, peanuts, sesame]
must_have:
  - {product_group: milk, property: lactose_free}
targets:
  - {slot: 3, product: "lactose-free milk 1 L", target_units: 24, reorder_below: 6}
approved_substitute_groups:
  lactose_free_milk: [lactose_free_milk_any_brand]
limits:
  auto_order_eur: 150
  weekly_eur: 800
delivery:
  latest_needed: "next weekday 07:00"
approvers:
  default: "Head chef"
```

### Rule check

The rule check is plain Python. It takes one proposal and returns one of three results, always with a reason.

| Result | When |
|---|---|
| `allowed` | Same product as usual, within the target stock, under the auto limit |
| `needs_approval` | A substitute outside the approved groups, over the spend limit, delivery later than needed, or a standing order that grew more than 15% |
| `forbidden` | Breaks an allergen or a nutrition rule, or allergen data is missing |

Missing allergen data counts as unsafe. Nobody can approve a `forbidden` item. To allow it, the chef signs a new version of the rule set.

### Buying pipeline

1. Read the event and the rule set.
2. Search the catalog for the product and its substitutes.
3. Ask price and availability for the quantity and the delivery date.
4. If stock is partial, split the order between the usual product and substitutes.
5. Run the rule check on every candidate. Drop `forbidden` items.
6. Rank the remaining substitutes as best (closest match), better (close match at a better price or date) and good (cheapest safe option in time).
7. If everything is `allowed`, place the order. Otherwise, create an approval request.
8. Just before the order, ask price and availability again. If anything changed, run the rule check again.
9. Write every step to the audit log.

Build the first version as fixed code with no language model. Then add a language model to read voice or text requests and to write the plain-language explanations. If the model is slow, the demo still works.

### Trust protocol

Store each price, stock and origin claim from Valio with the endpoint, the time and a hash of the response. Show the time next to every number, for example "price checked 21:14".

### Approvals

1. The backend stores open approval requests.
2. The phone page asks for new requests every 2 seconds.
3. Each request shows up to three options with the trade-off in one sentence.
4. The chef taps approve, picks another option, or skips.
5. The backend tells the device to beep while a request is open.

Later, the Meta glasses read the request aloud and take a spoken answer. Build that with Meta's Mock Device Kit. Borrow real glasses only to test and record. Loans last 120 or 240 minutes.

### Audit log

The log is append-only. Each entry holds:

| Field | Example |
|---|---|
| `time` | 2026-11-14T09:12:05+02:00 |
| `step` | trigger, search, candidates, rule_check, approval, order |
| `detail` | The data for the step |
| `rule_set_version` | 3 |
| `actor` | agent, rule_check, or the approver's name |
| `order_id` | From the Valio mock API |

The chef view turns the log into a short story. The Valio view shows the same entries as JSON.

## Backend endpoints

| Method and path | Purpose |
|---|---|
| `POST /events` | Device events |
| `GET /approvals/open` | Open approval requests for the phone page |
| `POST /approvals/{id}` | Approve, choose another option, or skip |
| `GET /device/commands` | Buzzer commands for the device |
| `GET /log` | Audit log, as a story or as JSON |
| `POST /request` | A text request from the chef, for example "1,000 litres of lactose-free milk by Monday" |

## Tests

Write `tests/test_rules.py` before the agent code. It needs no API. Start with 10 cases and grow to 30. All forbidden cases must be blocked on every run.

Examples:

1. A product with nut traces in a school kitchen is `forbidden`.
2. A product with no allergen data is `forbidden`.
3. Regular milk in place of lactose-free milk is `forbidden`.
4. The same product, under the limit, is `allowed`.
5. An order over €150 is `needs_approval`.
6. A substitute from outside the approved groups is `needs_approval`.
7. Delivery after the needed time is `needs_approval`.
8. A standing order that grew 20% is `needs_approval`.

## Tonight's tasks by role

| Role | Task | Done when |
|---|---|---|
| Full stack | Download the API docs from the Valio portal into `docs/valio-api/`. Write `valio.py` with search and price-plus-availability. Cache every response in `fixtures/`. | One search returns a lactose-free milk product |
| Robotics | Set up the UNO Q in App Lab. Show a camera image. Read the knob and the thermo, and play a buzzer tone. | All three Modulinos work from one app |
| ML and testing | Write `rules/school_kitchen.yaml`, `backend/rules.py` and the first 10 tests. | All 10 tests pass |
| UX | Build the cardboard shelf with three taped slots. Sketch the approval card. Write the preliminary submission text. | Preliminary submission sent by 23:59 |

## First milestone

The milestone is done after this sequence works end to end:

1. A person takes the milk off the shelf.
2. The device sends a `slot_state` event with `state: empty`.
3. The backend creates a proposal.
4. The phone shows the proposal, and the box beeps.
5. The chef taps approve.
6. The order reaches the Valio mock API.
7. The audit log shows every step.

## Saturday

1. Morning: partial stock and substitutes, the knob sets the auto limit live, buzzer for approvals.
2. Afternoon: the log as a story, 30 forbidden-swap tests, log moved to Aiven Postgres, review with the Valio mentors.
3. 18:00: feature freeze for the core.
4. Evening: Meta glasses during a loan slot, three demo rehearsals, backup video.

## Demo script (three minutes)

1. Show the signed rule set of a school kitchen.
2. Take the lactose-free milk off the shelf. The box beeps.
3. The proposal appears on the phone or the glasses. The usual product is partly out of stock, and a safe substitute fills the gap. Approve it.
4. A judge tries to force a product with nuts. The agent blocks it and gives the reason.
5. Show the audit log as a story and as data.

## Still unknown

1. The exact endpoints and fields of the Valio mock API. The docs on the portal answer this.
2. Whether products carry allergen and nutrition fields. If not, ask the Valio mentors which source to use.
3. Whether one project can also enter the Meta challenge. Ask an organizer.

## Tech challenges to enter with the same project

1. Best use of Arduino UNO Q: the UNO Q and Modulinos are part of the project.
2. Aiven: if the audit log runs on Aiven Postgres.
