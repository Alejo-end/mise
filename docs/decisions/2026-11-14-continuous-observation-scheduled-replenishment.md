# Continuous observation, scheduled replenishment

**Proposed by Anatoly, 14 November 2026 — pending team agreement.**

Nothing here is implemented, and the demo plan (`DEMO_PLAN.md`) is unchanged until the team agrees. The
Aimo Agent Gateway stays the main entry; the shelf camera is one example input among several.

## Why

Today one confirmed low or empty reading of a slot starts a purchase run at once (`Agent.handle_event` in
`backend/agent.py` calls `buy`). Every reading therefore becomes an order decision: many small orders, and a
product taken out for a moment can start one (the duplicate of 14 Nov behind decision C in
`docs/PARALLEL_WORK.md`). A kitchen does not buy that way: it watches its stock all day and orders at set times.

## Proposed operating model

- **Track approximate stock continuously.** A low-stock observation updates a *replenishment candidate* for
  that product; it does not create an order.
- **Keep three things separate:** stock estimation (what is there), purchasing permission (what may be bought,
  by whom, within which limits) and order timing (when an order goes out).
- **The owner configures per product:** minimum stock, restock quantity or target, automatic purchasing or
  human approval, and the normal ordering frequency.
- **Shared supplier ordering windows** consolidate eligible products into one order. A product's own frequency
  decides whether it is eligible in a given window.
- **Urgent orders outside the window** only through explicitly configured authority, under the existing
  controls.
- **Reassess at ordering time** with the latest stock evidence, outstanding orders, current prices and
  availability (looked up for the shortlisted candidates only).
- **Three kinds of stock, kept apart:** *physical* (units observed or counted), *usable* (physical minus units a
  person marked unusable) and *uncertain* (an old count, an obstructed or unresolved observation). The order
  quantity is `max(0, target - (usable + on order))`, never negative; *on order* is every outstanding order and open
  approval for the product. An uncertain count never orders by itself: it goes to a person.
- **Before commitment a proposal may change:** an item taken out and put back changes the candidate. A returned
  item is not proof of a supplier delivery and never clears an outstanding order by itself.
- **Keep everything that protects us today:** the explicit uncertain state, the signed rules, the spending
  controls, the approval requirements and duplicate prevention.
- **Approximate stock is enough for this scope.** Estimating the contents of every opened package is deferred.

| Concern | Decided by | Inputs | Output |
|---|---|---|---|
| Stock estimation | The board (or a person, or another input) | Camera with its uncertain state, knob press, manual entry | Approximate count or level per product, with its evidence and uncertainty |
| Replenishment candidate | The backend, continuously | Stock estimates, owner thresholds, outstanding orders | Product, proposed quantity, why, evidence age |
| Purchasing permission | The signed rule set | Owner's per-product settings, budgets, safety rules | Automatic, needs approval, or not allowed |
| Order timing | The ordering window (or a configured urgent authority) | Window schedule, product frequency | When the candidates are reassessed and committed |

## How it supports Valio's required flow and the rubric

At each window (or on "Run replenishment now") the kitchen agent makes one request through the Aimo Agent
Gateway: **lookup** (catalogue, then price and availability only for the shortlisted candidates, as Valio's API
guidance asks), a **signed quote**, then an **order within its authority or an approval request**. That is the
request → lookup → order/approval flow of the brief, with the shelf as one input.

| Criterion (weight) | What this adds |
|---|---|
| Working end-to-end demo (30%) | "Run replenishment now" runs the whole flow on demand, from stock evidence to an order or an approval, in one visible step |
| Matching the buyer's need (20%) | One consolidated order built from the owner's per-product settings, with fewer manual corrections than many single-product orders |
| Autonomy and controls (20%) | Per-product automatic or approval settings, urgent authority made explicit, every candidate, reassessment and decision logged with its rule set version |
| Robustness to exceptions (15%) | Partial availability, substitutions and delivery constraints are handled once, at reassessment, against current prices and availability |
| Openness and path to production (15%) | Separate, documented concerns and assumptions; the gateway stays a standard entry any buyer agent can use |

## Proposed hackathon scope

- One shared ordering window.
- Product-specific automatic or approval settings and stock thresholds.
- A "Run replenishment now" demo action that uses the same decision logic as the window.
- A small multi-item recognition and counting evaluation (the shelf recordings of 14 Nov: chips, an energy
  drink, milk, and an unfamiliar fitness bar; counts typed by a person, orientations with the barcode hidden).
- Future work: detailed hand tracking, continual learning, learned consumption rates, elaborate per-product
  scheduling.

## Relation to existing decisions (`docs/PARALLEL_WORK.md`)

- **C** (may the camera alone make a slot orderable again after an order?): this model answers it the strict
  way: a returned or "full" reading never clears an outstanding order; a delivery check or a person does.
- **E** (an older request approved later is checked against the newest rule set): reassessment at ordering
  time applies the newest rules, prices and availability.
- **G** (a delivery date alone is not proof of arrival before 07:00): unchanged; it limits which candidates a
  window may count as covered.
- **A and F** (who may change slot products and sign rule set versions): the owner's per-product settings would
  live in the same signed rule set.

## Decisions for the team

1. **Initial operating environment and what "stock" means.** Which kitchen (school kitchen of the demo, or a
   generic one)? Is stock a count of items, a level (empty / low / enough), or both? Per slot or per product?
2. **Default ordering window, and unapproved items at its cutoff.** One window per day at which time? When an
   approval is still open at the cutoff: carry it to the next window, drop it, or send the rest without it?
3. **Which products may order automatically**, and up to which amount or quantity, and which always need a
   person.
4. **Conditions for urgent exceptions:** who holds the authority, which products, which threshold (for example
   empty plus needed before the next window), and which existing controls still apply (spend limit, approval,
   duplicate prevention).

## If agreed (not started)

The trigger path would update a candidate instead of buying; a window runner and a "Run replenishment now"
endpoint would reassess and commit through the gateway; the owner settings would be new fields in the signed
rule set; regression tests would cover consolidation, the cutoff rule, the urgent path, returned items and
duplicate prevention.
