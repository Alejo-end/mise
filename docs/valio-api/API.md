# Valio Aimo challenge: API and data reference

This file describes the real materials of the Valio Aimo challenge, read from the resource portal (https://junction26.valioaimo.fi/) on 14 November 2026. The portal needs the event password. The API needs a key in the `X-API-Key` header. Both are on the challenge page of the Junction platform. Keep the key in `valio-challenge/.env`.

The data files are in `valio-challenge/data/valio/`, a link to the shared read-only folder `valio-source-data/2026-11-14/` (downloaded once on 14 Nov, sizes and SHA-256 in its `MANIFEST.txt`). Git ignores that folder. Valio says the dataset "must not be used for anything outside of this challenge", so never commit it. Our repository becomes public.

## What these materials are

Valio's materials are **sponsor-provided challenge data and a mock API**, not a Valio production
system. Every quote, approval option and order answer of our gateway names that in its `source` field.
Prices and availability are customer-specific in the real world and change; the mock API's answers are
true only for the time and the requested date of the call.

## Field status (measured 14 Nov 2026, worker 3)

Status: **sponsor file** = Valio's challenge data file (static, downloaded once); **sponsor mock API** =
the live challenge API (`POST /api/price`, `POST /api/availability`); **unavailable** = no Valio source
exists; **simulated** = our own code decides or records it. The schema source is the OpenAPI file in
this folder (`openapi.json`, from the portal; the live copy needs the portal login).

| Field the pipeline uses | Status | Source | How our code uses it |
|---|---|---|---|
| Price | sponsor mock API | `catalogPrice.value`: per sales unit, without tax, EUR; also `valueWithTax`, `tax.taxRate` (13.5 % measured), `comparisonPrice` per `comparisonUnit` (KG or L), `listPrice`, `netPriceFound` | `unit_price_eur` = `catalogPrice.value`; totals are goods without tax. A price not in EUR, or `error: true`, is no price (a person must look) |
| Price validity | sponsor mock API | `catalogPrice.validFrom`, `validTo` (measured: net prices to 2026-12-31, catalog prices to 9999-12-31; offsets like `+0200`) | A gateway quote never outlives `validTo` |
| Availability | sponsor mock API | `scheduleLines[0].availableAmount` in the sales unit; `logisticModel` Stock or Terminal | 999999 (Terminal) = quantity unknown, goes to a person. Measured: most Stock products answer 9999999999, a value the documentation does not name; we read it as stated (the quantity available). Ask Valio |
| Available date | sponsor mock API | `scheduleLines[0].availableDate`, a bare date (`2026-11-16`) | The delivery **date**. No arrival time is invented: the deadline check compares dates and says "in time by date only" |
| Arrival time or window | unavailable | none | Quotes say "not supplied: the source gives a delivery date only" |
| Closing time (order cut-off) | sponsor mock API | `closingTime` per product | A passed cut-off makes the available date unreachable: not orderable, with the reason. A quote ends at the cut-off. Measured: Terminal products (477032 rye bread, 462921, 463038) had a cut-off of 12 Nov for delivery on 16 Nov |
| Sales block | sponsor mock API | `salesBlock` (product and schedule line) | No stock, not orderable |
| Sales unit | sponsor file + sponsor mock API | file `salesUnit` (KI 8,005, ST 5,607, KG 1,068, ...); API `scheduleLines[].salesUnit` | Quantities are in sales units. If the API's unit differs from the file's, the product is not orderable |
| Pack size | sponsor file | `units[]` (`sizeInBaseUnits` per packaging level) and `synkkaData.unitConversions` (`BASE_UNIT_OR_EACH` volume or net weight) | `pack` = content of one sales unit in l or kg, or None. A substitute must have the usual product's pack size |
| Lot size | sponsor file | `allowedLotSize`, in sales units: 0.01 for 16,872 products; 0.15 to 25 for 674 (672 sold by the KG, 2 by the KI: 462952 lot 1, 464997 lot 2) | A gateway quote off the lot size gets HTTP 422; the pipeline leaves out products whose lot does not divide one unit |
| Deposits | sponsor file, no price | `deposits[]`: `material`, `unit`, `quantity` on 506 products; no price anywhere, and the price API has no deposit field | "deposit charge unknown", never 0, never in a total |
| Allergens | sponsor file | `synkkaData.classifications` (`allergen`, `nonAllergen`, `nutritionalClaim`) | The rule check; missing data goes to a person (rule set v4) |
| Delivery reliability | sponsor file | reliability CSV, 4,425 rows, all matched; 13,121 products have no row | Information only (quote `comparison`, option lines): historical share of the ordered quantity delivered, **not an on-time probability**. Missing = unknown. It no longer ranks substitutes |
| Carbon footprint | sponsor file | carbon CSV `AVG kg CO2-eq/kg`, product-group averages, 11,033 rows, all matched; 6,513 products have no row | Information only. An order estimate only when the file establishes the ordered mass (net weight, or sold by the KG); else unknown |
| Delivery charge | unavailable | none in the file or the API | "delivery charge unknown" in quotes, approval options and order log; never 0 in a total. Totals are goods only |
| Order placement | simulated | the API has no order endpoint | The gateway records the order (idempotency key); answers and the log say "recorded by the gateway, not sent to Valio" |
| Order confirmation | simulated | none | The gateway's own order id (`ORD-...`); no Valio confirmation exists |
| Quote expiry | simulated | our gateway, bounded by sponsor data | The earliest of 15 minutes, the cut-off and the price's `validTo`; the quote says which (`valid_until_set_by`) |
| Customer | sponsor mock API | `customer` in the request | The documentation says it does not change the answer; we send `123` |

Call discipline (Valio: "query the APIs only when you have narrowed down the products"): the pipeline
narrows by the local product file first (search, pack size, lot size) and asks price and availability
for that shortlist in one call per endpoint; the backend makes at most 2 calls a second, retries at most
3 times with exponential backoff and jitter on 429 and 5xx, can cap its calls (`VALIO_MAX_CALLS`) and
records every call without the key (`VALIO_RECORD_DIR`). On 14 Nov worker 3 made 31 calls in total; the
recordings and their README are in `valio-source-data/2026-11-14/api-recordings/` next to the data files.

## What the materials contain

| Material | Where | Size |
|---|---|---|
| Product data | `valio_aimo_junction26_product_data.json` | 87 MB, 17,546 products |
| Product data schema | `valio_aimo_junction26_product_data_schema.json` | 30 KB |
| Delivery reliability | `valio_aimo_junction26_delivery_reliability.csv` | 4,425 products |
| Carbon footprint | `valio_aimo_junction26_carbon_footprint.csv` | 11,033 products |
| Pricing API | `POST /api/price` | Live |
| Availability API | `POST /api/availability` | Live |
| API documentation | `/docs` (Swagger), `/redoc`, `/openapi.json` on the portal | Needs the portal login |

## What the API does not have

The API has no search endpoint and no order endpoint. This changes our design:

1. Product search runs on our side, over the product data file. The file does not change during the challenge.
2. The order step is ours to design. That is the brief: "prototype the interface and operating model". Our gateway records the order, and no Valio endpoint receives it.
3. Valio asks teams not to fetch prices or availability for all products. Narrow the products down from the static file first, then call the API. Undisclosed rate limits apply.

## Endpoints

Base URL: `https://junction26.valioaimo.fi`. Every `/api` call needs the header `X-API-Key`.

```
curl -H "X-API-Key: $VALIO_API_KEY" https://junction26.valioaimo.fi/api/ping
```

### POST /api/availability

Request body, the same for the price endpoint:

```json
{"customer": "123", "requestedDate": "2026-11-16", "products": ["460013", "460063"]}
```

`products` holds 1 to 1,000 product codes. A longer list gives HTTP 422. The customer number does not change the answer in this challenge.

Response fields:

| Field | Meaning |
|---|---|
| `timeNow` | Server time of the answer, Helsinki time |
| `requestedDate` | The date from the request, at midnight |
| `products[]` | One entry per requested product, in request order |
| `productId` | The product code |
| `closingTime` | The order cut-off: order by this time to receive the goods on `availableDate` |
| `salesBlock` | True: the product cannot be ordered now |
| `logisticModel` | Stock products: Valio knows the quantity. Terminal products ship from the supplier, and the quantity is unknown |
| `scheduleLines[]` | Exactly one entry for an available product. An empty array means no delivery on any date |
| `scheduleLines[].availableAmount` | Quantity in `salesUnit`. Always 999999 for Terminal products. 0 means not available. Measured: 9999999999 for most Stock products |
| `scheduleLines[].availableDate` | Earliest date for that amount. It can differ from the requested date. A bare date, no time |
| `scheduleLines[].salesUnit` | Technical code of the sales unit |
| `scheduleLines[].salesBlock` | True: the product cannot be ordered now |

### POST /api/price

Response fields:

| Field | Meaning |
|---|---|
| `customerId`, `currency`, `pricingDate` | Echo of the request, and the currency of all prices |
| `products[]` | One entry per requested product |
| `quantity` | Always 1.0 |
| `catalogPrice` | Price per sales unit: `value` without tax, `valueWithTax` with tax, plus `validFrom`, `validTo`, `currency` |
| `priceWithTax` | Price with tax and its validity period |
| `comparisonPrice`, `comparisonUnit` | Price per KG or L, to compare products of different size |
| `tax` | `taxRate` in percent and `taxValue` |
| `listPrice` | Price before a customer-specific price or discount |
| `netPriceFound` | True: a customer-specific net price was found |
| `error` | True: no price was found for the product |

### File download endpoints

`GET /api/products/download`, `GET /api/delivery-reliability/download` and `GET /api/carbon-footprint/download` redirect (307) to the files on the same host (`/files/<hash>/<name>`). Use `curl -L`. The product JSON schema has no API endpoint; it is on the password-protected portal.

## Product data

Each product has these top-level fields:

| Field | Meaning |
|---|---|
| `id` | Product code, used in the API and in the other files |
| `salesUnit`, `baseUnit` | Technical unit codes, for example KI, ST, KG. The file gives no translations |
| `units[]` | The packaging levels of the product: `unitId` and `sizeInBaseUnits`. Example: for product `463335`, one KI holds 5 base units |
| `allowedLotSize` | Smallest order step, in sales units |
| `category` | Category code. The names are not in the data |
| `vendorName` | Supplier. The data has 648 vendors |
| `countryOfOrigin` | ISO code, for example `fi` |
| `temperatureCondition` | A code from 1 to 9. The meaning is not in the data. Our reading from the storage temperatures of the products: 7 is frozen, 8 is room temperature, and 1 to 4 are chilled |
| `salesUnitGtin` | Barcode of the sales unit |
| `substitutions[]` | Valio's own substitute list: `product` and `substituteProduct`. Only 189 products have one (250 pairs) |
| `deposits[]` | Returnable packaging charged with the product |
| `synkkaData` | Product information from the manufacturer, through the GS1 Synkka service |

`synkkaData` holds names, marketing texts and key ingredients in Finnish, Swedish and English. It also holds structured ingredients, storage instructions, `minTemperature` and `maxTemperature`, nutrition, and `classifications`.

### Allergens

Allergens are in `synkkaData.classifications`, in two groups:

1. `allergen`: a list of codes, each with a `unit` of `CONTAINS`, `MAY_CONTAIN` or `UNDECLARED`.
2. `nonAllergen`: codes that the product is free from.

The codes are GS1 allergen codes. The most frequent are AM, AW, UW, AY, AE and AN. Our reading of the GS1 code list, from memory: AM is milk, AW is cereals with gluten, and UW is wheat. AY is soy, AE is egg, AN is tree nuts, AP is peanuts and AS is sesame. Ask the Valio mentors for the official code list. The full map is in `backend/allergens.py`.

Three facts about the codes matter for the rule check:

1. Nut kinds have their own codes, for example SA for almond and SH for hazelnut. 69 products list a nut kind without AN. So every nut kind must count as nuts.
2. The code X99 stands alone on 120 products, for example crisps and juice. We read it as "no allergens that must be declared".
3. The `nonAllergen` group uses the unit `FREE_FROM`. Two products list one allergen in both groups.

Diet claims are in the group `nutritionalClaim`, for example `FREE_FROM_LACTOSE` (4,178 products), `LOW_LACTOSE` (321), `FREE_FROM_GLUTEN` (3,049) and `VEGAN` (2,959).

### Coverage: how much is missing

| Measure | Products | Share of 17,546 |
|---|---|---|
| A manufacturer record exists | 14,478 | 83% |
| The `allergen` group lists at least one code | 7,205 | 41% |
| `allergen` or `nonAllergen` has content | 9,436 | 54% |
| Neither group has content | 8,110 | 46% |
| Structured ingredients exist | 3,746 | 21% |
| `maxTemperature` exists | 10,266 | 59% |
| A delivery reliability value exists | 4,425 | 25% |
| A carbon footprint value exists | 11,033 | 63% |

Valio explains the gaps: factory-made meals usually have exact allergen data, and fruit and vegetables often have none. An empty allergen group does not mean "no allergens". Our rule check treats it as unknown.

Allergen units across the data: 15,610 `CONTAINS`, 9,956 `MAY_CONTAIN` and 303 `UNDECLARED`.

### Delivery reliability

A value from 0 to 1 per product: the share of ordered quantity that was delivered in the past. The median is 1.0, and 291 of the 4,425 products are below 0.9.

### Carbon footprint

The column `AVG kg CO2-eq/kg` is an average for the product group, not a measurement of the product.

## Valio's example request in the real data

"1,000 litres of lactose-free milk by Monday": 80 products carry a lactose-free claim and "milk" in the English name. Examples are `463335` (Valio Eila 3 % milk drink 1 l lactose free), `462921` (the organic 1 l version) and `463038` (a 10 l box). 40 of the 80 are sold by the KI and 31 by the ST. Our reading: ST is one piece and KI is a crate. The `units` list of each product says how many pieces one KI holds: 5 cartons for `462921`, and 2 boxes of 10 l for `463038`. So 1,000 litres is 1,000 pieces of `463335`, or 200 KI of `462921`, or 50 KI of `463038`.

## What this means for our code

Items 1 to 3, 6 and 7 are done in `backend/valio.py`, `backend/allergens.py`, `backend/agent.py` and `backend/gateway.py`, with tests (`tests/test_valio_catalog.py`, `tests/test_valio_quotes.py`). Item 4 is open. Item 5 was changed on 14 Nov: reliability informs (quotes and options), it does not rank. Item 7: `closingTime`, `salesBlock`, `allowedLotSize` and the 999999 amount are respected (see the field status table).

1. `backend/valio.py` calls `POST /api/price` and `POST /api/availability` with the `X-API-Key` header. It no longer calls `/products` or `/orders`.
2. It builds a local product index from the data file for search.
3. It maps allergens from `classifications`. `MAY_CONTAIN` counts as traces. An empty group is unknown.
4. Use `substitutions` first for substitutes, then the same category and the diet claims.
5. Use `deliveryReliability` in the substitute ranking. It is the real version of the supplier score in Cold Chain Proof.
6. Use `maxTemperature` of the product for the delivery temperature limit.
7. Respect `closingTime`, `salesBlock`, `allowedLotSize` and the 999999 amount of Terminal products.
