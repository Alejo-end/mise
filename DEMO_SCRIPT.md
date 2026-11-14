# The demo, step by step

Three minutes, five steps. Every step runs on the live Valio catalog. Nothing here needs the shelf camera.

The one sentence: Valio Aimo gets a gateway that lets an outside buying agent order under the kitchen's signed rules, and a person approves only when the rules require it.

## Before you start

1. Start the backend with an empty log, so the screens show no old test orders.

```
AUDIT_DB=data/judging.sqlite uv run uvicorn backend.app:app --host 0.0.0.0 --port 8001
```

2. Run the check. Every line must say PASS. A WARN line names what the audience will see.

```
uv run python tools/demo_preflight.py http://localhost:8001
```

3. Open three things. The laptop shows the page as Wholesaler (`/approve?as=valio`). The phone shows the page as Head chef. The touch screen of the box stands next to the knob.
4. Rehearse the five steps three times on this exact build. Do not change code after that.

## The steps

| Step | Where | What you do | What you say |
|---|---|---|---|
| 1. Admit an agent | Laptop, Wholesaler | Run the buying agent (the command below). It takes the Driving Test and passes 6 of 6. | "An outside agent must pass six scenarios before it gets a licence. One of them is a product text that tells the agent to order ten times more." |
| 2. The agent buys | Terminal | The agent searches the live catalog, compares four chicken products by price per kilogram and picks one. | "A model reads the request and picks among quoted options. Plain code decides what is allowed. The quote is signed." |
| 3. A person approves | Phone, Head chef | The ticket shows who asked, the product, the total and the reasons as short lines. Enter the PIN and approve. | "€389 is over the limit of €150, so the rules send it to the head chef. Below the limit, it goes out alone." |
| 4. The door | Touch screen of the box | The order appears with the air temperature against the product's limit. Tap "Reject", then "Yes, reject". | "The box knows what the phone does not: it is 25 degrees at the door and the limit is 6. The decision goes into the record with the temperature." |
| 5. The record and the stop | Laptop, Wholesaler | Open the order under "Each order, step by step". Then press "End" on the agent's licence. | "Every step has a time, a name and a rule set version. Valio can end an agent's licence in one click, and its next call is refused." |

The command for steps 1 and 2. It reads the PIN from the settings file, so the PIN does not appear on a screen.

```
uv run python -m backend.buyer_agent "We need 50 kg of chicken breast for Friday, frozen is fine" --url http://localhost:8001 --pin "$(grep '^APPROVER_PINS=' .env | cut -d= -f2 | cut -d: -f1)"
```

## If a judge asks for an exception

Each of these works on the live catalog.

- A forbidden product: on the Kitchen page, press the hint "Kinder Bueno, forbidden here", then "It arrived at the door". The answer names the allergen and the rule.
- A price that changes between the quote and the order: it is scenario 3 of the Driving Test, on the Wholesaler page.
- An agent that loses its licence in the middle of its work: step 5.
- A cold alarm: the alarm pill on the touch screen, "Cold alarm. Tap when seen".

Do not promise a partial delivery with substitutes on stage. Valio's test API reports unknown stock, so that case cannot happen live. It runs in the tests on the stand-in catalog.

## What to say about the limits, before anyone asks

- Orders are recorded by the gateway and are not sent to Valio. Valio's mentors confirmed that the challenge has no order endpoint.
- One buyer, one rule set. Customer prices, credit and minimum orders are not built.
- The PIN and the signing key are at demo grade.
- The shelf camera is a trigger prototype. We do not claim that it recognizes products reliably. An uncertain camera reading never starts an order.
- The box has no PIN, so its knob and its screen never approve an order.

## If something fails on stage

- The page shows a pink line at the top that names what failed: the network, or which address of the backend.
- The box is offline: steps 1, 2, 3 and 5 still work. Skip step 4 and show the order record.
- The model on Verda does not answer: the buying agent falls back to fixed rules and says so in its first line.
