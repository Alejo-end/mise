# The demo, step by step

Three minutes, one incident, told from start to end. Every step runs on the live Valio catalog.

The opening line: "mise is the kitchen system that refuses to make a bad purchase, and proves it."

Food safety and accountable decisions come first. The model on Verda and the gateway are the proof behind them, not the story.

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
4. Count the milk shelf low on the Kitchen page once, so the Stock view shows LOW.
5. Rehearse the seven steps three times on this exact build. Do not change code after that.

## The incident: the milk runs out

| Step | Where | What you do | What you say |
|---|---|---|---|
| 1. The shelf is empty | Touch screen, Stock | The lactose free milk shows LOW. Tap its row, then "Report low", then "Yes, report". | "A person reports it here. A camera reading does the same, but only after 5 minutes of the same reading, and never when the picture is unclear." |
| 2. The rules decide | Phone, Head chef | A ticket appears. Read its first line aloud: the verdict, the rule, who decides, the next safe step. | "Before anyone sees a product, the system answers one question: can this order proceed? Here it cannot go alone, and the line says why: for example, Valio does not report the stock, so a person must look." |
| 3. Unsafe options never reach the chef | Phone, the ticket | Point at "Why you decide". Products that break a rule are not on the ticket at all. | "Milk that is not lactose free, or a product with nuts, is dropped by plain code. A model can be talked into a bad substitute. A rule cannot." |
| 4. A person approves | Phone | Enter the PIN and approve. | "The approval carries a name, a time and the rule set version. The order is recorded by the gateway, ready for Valio's order interface." |
| 5. The delivery at the door | Touch screen, Door | The order appears with the air against the product's limit. Tap "Reject", then "Yes, reject". | "The box knows what the phone does not: 25 degrees at the door against a limit of 6. The decision goes into the record with the temperature." |
| 6. It fails safely | Laptop, any page | Switch the phone's hotspot off for ten seconds, or pull the camera's cable. | "No data, no automatic order. The page says so, and tells a person what to do. When the data returns, the notice goes away by itself." |
| 7. Trust, measured | Laptop, Wholesaler | Show "Trust, measured": the Driving Test result, unsafe orders blocked, cases sent to a person. | "Every outside agent passes six scenarios before it gets a licence. These numbers are counted from the audit log, not from slides." |

The touch screen and the LED face show different things on purpose. The face shows the spend limit and one symbol (a cold alarm, a delivery at the door, an order to approve). The screen shows the data and takes the decisions that need context.

## The same gateway for an outside agent (if there is time)

Run the buying agent. It takes the Driving Test over the network, searches the live catalog, compares four chicken products by price per kilogram, and sends its order to the head chef. It reads the PIN from the settings file, so the PIN does not appear on a screen.

```
uv run python -m backend.buyer_agent "We need 50 kg of chicken breast for Friday, frozen is fine" --url http://localhost:8001 --pin "$(grep '^APPROVER_PINS=' .env | cut -d= -f2 | cut -d: -f1)"
```

Then press "End" on its licence on the Wholesaler page: its next call is refused.

## If a judge asks for an exception

Each of these works on the live catalog.

- A forbidden product: on the Kitchen page, press the hint "Kinder Bueno, forbidden here", then "It arrived at the door". The answer names the allergen and the rule.
- A price that changes between the quote and the order: it is scenario 3 of the Driving Test, on the Wholesaler page.
- An agent that loses its licence in the middle of its work: the end of the outside agent part.
- A cold alarm: the Cold view of the touch screen shows the last 2 hours against the limit, and the alarm pill takes "Seen".

Do not promise a partial delivery with substitutes on stage. Valio's test API reports unknown stock, so that case cannot happen live. It runs in the tests on the stand-in catalog.

## What to say about the limits, before anyone asks

- Orders are recorded by the gateway and are not sent to Valio. Valio's mentors confirmed that the challenge has no order endpoint.
- One buyer, one rule set. Customer prices, credit and minimum orders are not built.
- The PIN and the signing key are at demo grade.
- The shelf camera is a trigger prototype. We do not claim that it recognizes products reliably. An uncertain camera reading never starts an order.
- The box has no PIN, so its knob and its screen never approve an order.

## If something fails on stage

- The page shows a pink line at the top that names what failed: the network, or which address of the backend.
- The box is offline: that is step 6, so say it. Steps 2, 3, 4 and 7 still work. Report the shortage on the Kitchen page instead of the touch screen.
- The model on Verda does not answer: the buying agent falls back to fixed rules and says so in its first line.
