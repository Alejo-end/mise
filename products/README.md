# Shelf products

`active.json` is the one shared list of what the shelf camera looks for. The board page (as `assets/products.json`,
copied at deploy and offered by the box update), the laptop recognition service (`SHELF_PRODUCTS`) and the backend
(`backend/products.py`) all read it. It grants **no purchasing authority**: the signed rule set decides what may be
ordered and is unchanged, and shelf events stay observe-only.

Active set `kitchen-20261115-b` (from 15 Nov 2026, 03:30): four products the owner selected by EAN directly in the
Valio Aimo catalogue, availability there confirmed by the owner. The exact selections are used; nothing is searched
for or substituted. PROfeel Salted Caramel (6408430401260) was removed by the owner; it is listed under
`excluded_products` only (see below).

| Product | Selected EAN | Catalogue entry | Counted per | Ordered per | Cloud check at local score |
|---|---|---|---|---|---|
| PROfeel Strawberry shake | 6408430101290 | 464893 | bottle (ST, 250 ml) | case KI = 15 bottles | ≤ 0.95 (provisional) |
| PROfeel Mint Chocolate shake | 6408430011629 | 464147 ("mint cocoa") | bottle (ST, 250 ml) | case KI = 15 bottles | ≤ 0.95 (provisional) |
| Bonne orange juice, 18-pack | 4740098003823 (the KI pack's own GTIN) | 475502 | carton (ST 4740098003793, 300 ml) | case KI = 18 cartons | ≤ 0.85 |
| Aito Barista oat drink | 6410222014305 | 475224 | carton (ST, 1 l) | single carton ST (a KI of 6 exists) | ≤ 0.85 |

Lot size: 0.01 in the file for all four, which Valio's answer of 14 Nov reads as 1 sales unit. The camera's count
(bottles, cartons) and the ordering unit are two quantities and are never merged: 7 Bonne cartons are 7 cartons and
7/18 of a case (`backend/products.py`: `count_vs_order`, `sales_units_for`).

The catalogue facts are copied from the challenge product file (sponsor data, read locally, never committed) by
`tools/catalogue_units.py --data-dir <folder> --write`.

Removed product: PROfeel Salted Caramel (`excluded_products`). It is not a recognition candidate, enrolment option or
count. It stays listed so that a readable "salted caramel" PROfeel is reported as not inventory (never as Strawberry or
Mint Chocolate), and so that a local model trained with a Salted Caramel class keeps that class as it is. (Its EAN was
not in the challenge product file either.)

Local model coverage (`local_model_coverage`, `backend/products.py` `model_coverage`, since 15 Nov 06:40; it
replaces `model_compatibility`, which required a class for every active product): an explicit map. A local model may
cover only some products and is never refused for that. An active product is counted from a local box only through
a verified class (the model manifest's class order, from the trainer's export) where the map allows it
(`local-when-mapped`) and above its `kimi_at_or_below`; Aito is `cloud` (the v3 model has no Aito class). Every other
product, and every unmapped, low-score or conflicting box, is resolved by the automatic whole-frame cloud check of
the recognition action. Classes keep their original indices and names: removed (Salted Caramel) and old products are
recognised as not in the inventory, never counted or purchasable; a class without a verified meaning keeps its
number. The current model, kitchen-restock-yolox-nano-v3-7products (10 classes), has no class order yet: all its
classes are unmapped until the trainer's metadata arrives.

Recording plan (`recording`, `tools/recording_plan.py`): labels and counts are per product, never per place. Where a
product stands does not affect its identity, recognition or count; any arrangement works (the left-to-right order in
`recording.arrangement_example` is only an example) and nothing asks to confirm or configure positions. Besides the
usual references and evaluations it has a rearrangement (expected: no change), a flavour-hidden view (expected:
flavour uncertain), a Bonne carton removal (one carton less, not one pack) and, if a bottle is at hand, the removed
Salted Caramel anywhere on the shelf (expected: not inventory).

Routing (`routing` in `active.json`): in detection/repeat mode a local detection at or below its product's
`kimi_at_or_below` goes to the cloud model automatically, once a local model trained for this set runs. This is
separate from `display_min_score`, the minimum score for showing a box. Conflicting classes and close shake flavours
(`flavour_margin`) are escalated too. The PROfeel shakes form the family `profeel-shake`: only readable flavour text
decides the product; an unreadable flavour stays "flavour uncertain" and is never a guessed product.

`archive/kitchen-20261115-a.json` is the five-product set of 02:42–03:30 on 15 Nov (laptop recognition service only,
never on the board), archived unchanged. `archive/kitchen-20261114-b.json` is the props set of 14 Nov (chips, energy drink, milk, the unfamiliar fitness
bar), moved here unchanged (sha256 recorded in `active.json`). Historical recordings, the 14 Nov regression cases,
enrolment photos, stock and audit entries keep those identities.
