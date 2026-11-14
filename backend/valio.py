"""Catalog adapters. Each one answers search, product, availability and place_order.

Every answer comes back as a Claim: the value plus where it came from, the time and a
SHA-256 hash of the source (the trust protocol). Raw API responses and the answers of the
stand-in are cached in fixtures/cache/. Product records of the Valio file are never
written there.

Two catalogs:
  LocalCatalog  a stand-in that reads fixtures/mock_catalog.json. Written by the team,
                not Valio data.
  ValioCatalog  the real challenge materials. Search and product facts come from the
                product file on disk. Price and availability come from the live API
                (POST /api/price, POST /api/availability, header X-API-Key). Valio's API
                has no search endpoint and no order endpoint, so search runs here and the
                gateway records the order itself.

These are sponsor-provided challenge materials: a mock API and data files, not a Valio
production system. Every quote names that (SOURCE).

make_client() picks one. CATALOG=local forces the stand-in, CATALOG=valio demands the
real one. With CATALOG unset, the real catalog is used when VALIO_API_KEY is set and the
product file exists.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import re
import sys
import threading
import time as time_mod
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

from .allergens import read_allergens, text_allergens
from .rules import TZ

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "fixtures" / "cache"


@dataclass
class Claim:
    value: object
    endpoint: str
    checked_at: str
    sha256: str

    def to_dict(self) -> dict:
        return {"value": self.value, "endpoint": self.endpoint, "checked_at": self.checked_at, "sha256": self.sha256}


def _now() -> datetime:
    return datetime.now(TZ)


def _claim(endpoint: str, raw: bytes, value) -> Claim:
    digest = hashlib.sha256(raw).hexdigest()
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / f"{digest[:16]}.json").write_bytes(raw)
    return Claim(value, endpoint, _now().isoformat(timespec="seconds"), digest)


def delivery_after(now: datetime, lead_days: int) -> datetime:
    """Delivery at 06:00 on the lead_days-th weekday after now."""
    day, left = now.date(), lead_days
    while left > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            left -= 1
    return datetime.combine(day, time(6, 0), tzinfo=TZ)


def words(text) -> list[str]:
    """The words of a product phrase: lower case, no hyphens or punctuation. 'Lactose-free  Milk,' -> lactose, free, milk."""
    return re.sub(r"[^a-z0-9äöåéü%]+", " ", str(text).lower()).split()


class OrderBook:
    """Orders by idempotency key. The same key always returns the first order made with it."""

    def __init__(self):
        self.orders: dict[str, dict] = {}
        self.by_key: dict[str, str] = {}
        self.lock = threading.Lock()

    def place(self, lines: list[dict], key: str, status: str) -> tuple[dict, bool]:
        """Returns the order, and whether this call made it."""
        with self.lock:
            new = key not in self.by_key
            if new:
                order_id = "ORD-" + uuid.uuid4().hex[:8].upper()
                self.orders[order_id] = {"order_id": order_id, "lines": lines, "status": status}
                self.by_key[key] = order_id
            return self.orders[self.by_key[key]], new


class LocalCatalog:
    name = "local stand-in catalog"
    source = "the team's stand-in catalog (not Valio data)"

    def __init__(self, path: Path = ROOT / "fixtures" / "mock_catalog.json"):
        self.products = {p["id"]: p for p in json.loads(path.read_text())["products"]}
        self.book = OrderBook()
        self.orders = self.book.orders

    def _answer(self, endpoint: str, value) -> Claim:
        raw = json.dumps({"endpoint": endpoint, "data": value}, sort_keys=True).encode()
        return _claim(endpoint, raw, value)

    def search(self, query: str) -> Claim:
        q = query.lower().strip()
        hits = [p for p in self.products.values() if any(q in k or k in q for k in p["keywords"]) or q in p["name"].lower()]
        public = [{k: v for k, v in p.items() if k not in ("stock", "lead_days", "keywords")} for p in hits]
        return self._answer(f"GET /products?q={query}", public)

    def product(self, product_id: str) -> Claim:
        p = self.products.get(product_id)
        value = None if p is None else {k: v for k, v in p.items() if k not in ("stock", "lead_days", "keywords")}
        return self._answer(f"GET /products/{product_id}", value)

    def availability(self, product_id: str, quantity: int, needed_by: datetime) -> Claim:
        p = self.products[product_id]
        value = {
            "product_id": product_id,
            "unit_price_eur": p["price_eur"],
            "available_units": min(quantity, p["stock"]),
            "delivery": delivery_after(_now(), p["lead_days"]).isoformat(),
            "quantity_known": True,
        }
        return self._answer(f"POST /availability {product_id} x{quantity}", value)

    def place_order(self, lines: list[dict], idempotency_key: str) -> Claim:
        """The same key returns the same order and never books stock twice."""
        order, new = self.book.place(lines, idempotency_key, "confirmed")
        if new:
            for l in lines:
                self.products[l["product_id"]]["stock"] -= l["quantity"]
        return self._answer("POST /orders", order)




# The real Valio Aimo challenge materials -------------------------------------

PRODUCT_FILE = "valio_aimo_junction26_product_data.json"
RELIABILITY_FILE = "valio_aimo_junction26_delivery_reliability.csv"
CARBON_FILE = "valio_aimo_junction26_carbon_footprint.csv"
DEFAULT_URL = "https://junction26.valioaimo.fi"
# What these materials are. Every quote and approval names it: sponsor-provided challenge data and a
# mock API, not a Valio production system.
SOURCE = "Valio Aimo challenge data and mock API (sponsor-provided, not a Valio production system)"
# The availability API gives a delivery date (availableDate) and no time. We do not invent one: the
# delivery is that date, and the deadline check compares dates (rules.Line.delivery_time_known).
UNKNOWN_AMOUNT = 999999  # the amount the API gives for Terminal products: the quantity is not known
# Measured on 14 Nov: Stock products answer 9999999999.0, a value the documentation does not name. The
# documentation says availableAmount is the quantity available, so we read it as stated. Ask Valio.
MAX_HITS = 40
MAX_IDS_PER_CALL = 1000  # the API rejects a longer list with HTTP 422
PREFETCH_MAX_AGE_S = 60
MIN_CALL_GAP_S = 0.5  # at most 2 calls a second
MAX_RETRIES = 3  # after the first try, on 429, 5xx and network errors, with exponential backoff and jitter
DELIVERY_CHARGE = "unknown: the challenge API supplies no delivery charge"

_VOLUME_TO_L = {"LTR": 1.0, "DLT": 0.1, "CLT": 0.01, "MLT": 0.001}
_WEIGHT_TO_KG = {"KGM": 1.0, "GRM": 0.001}


def _norm(text) -> str:
    """A name or a query as its words with single spaces: 'Lactose-free  Milk' -> 'lactose free milk'."""
    return " ".join(words(text))


def _text(entries, languages=("en", "fi"), any_language=True) -> str | None:
    """The text in the first of `languages` that exists. Else the first text, if any_language."""
    entries = [e for e in entries or [] if e.get("value")]
    for lang in languages:
        for e in entries:
            if e.get("language") == lang:
                return e["value"]
    return entries[0]["value"] if entries and any_language else None


def _properties(raw: dict) -> list[str]:
    """Diet claims as properties: FREE_FROM_LACTOSE -> lactose_free, VEGAN -> vegan."""
    out = set()
    for group in (raw.get("synkkaData") or {}).get("classifications") or []:
        if group.get("name") != "nutritionalClaim":
            continue
        for v in group.get("values") or []:
            claim = str(v.get("id") or "")
            if claim:
                out.add(claim[len("FREE_FROM_"):].lower() + "_free" if claim.startswith("FREE_FROM_") else claim.lower())
    return sorted(out)


def _pack(raw: dict) -> dict | None:
    """The content of one sales unit, in litres or kilograms. None when the record does not say.

    The manufacturer record gives the content of the consumer unit. The `units` list says how
    many base units the consumer unit and the sales unit hold.
    """
    s = raw.get("synkkaData") or {}
    if s.get("variableUnit"):
        return None
    each = next((u for u in s.get("unitConversions") or [] if u.get("synkkaPackagingType") == "BASE_UNIT_OR_EACH"), None)
    units = raw.get("units") or []
    sales = next((u.get("sizeInBaseUnits") for u in units if u.get("unitId") == raw.get("salesUnit")), None)
    gtin = str(s.get("gtin") or "").lstrip("0")
    each_size = next((u.get("sizeInBaseUnits") for u in units
                      if gtin and str(u.get("gtin") or "").lstrip("0") == gtin), None)
    if each is None or not sales or not each_size:
        return None
    volume, weight = each.get("volume") or {}, each.get("netWeight") or {}
    if volume.get("unit") in _VOLUME_TO_L and volume.get("value"):
        amount, unit = volume["value"] * _VOLUME_TO_L[volume["unit"]], "l"
    elif weight.get("unit") in _WEIGHT_TO_KG and weight.get("value"):
        amount, unit = weight["value"] * _WEIGHT_TO_KG[weight["unit"]], "kg"
    elif volume.get("unit") in _WEIGHT_TO_KG and volume.get("value"):  # the volume field often holds a weight
        amount, unit = volume["value"] * _WEIGHT_TO_KG[volume["unit"]], "kg"
    else:
        return None
    return {"amount": round(amount * sales / each_size, 4), "unit": unit}


def _mass_kg(raw: dict) -> float | None:
    """The net mass of one sales unit in kilograms, where the file establishes it. Else None.

    A product sold by the KG weighs 1 kg per sales unit. Otherwise the manufacturer's net weight of
    the consumer unit, scaled by the `units` list. A volume is never turned into a mass.
    """
    if raw.get("salesUnit") == "KG":
        return 1.0
    s = raw.get("synkkaData") or {}
    if s.get("variableUnit"):
        return None
    each = next((u for u in s.get("unitConversions") or [] if u.get("synkkaPackagingType") == "BASE_UNIT_OR_EACH"), None)
    weight = (each or {}).get("netWeight") or {}
    units = raw.get("units") or []
    sales = next((u.get("sizeInBaseUnits") for u in units if u.get("unitId") == raw.get("salesUnit")), None)
    gtin = str(s.get("gtin") or "").lstrip("0")
    each_size = next((u.get("sizeInBaseUnits") for u in units
                      if gtin and str(u.get("gtin") or "").lstrip("0") == gtin), None)
    if weight.get("unit") not in _WEIGHT_TO_KG or not weight.get("value") or not sales or not each_size:
        return None
    return round(weight["value"] * _WEIGHT_TO_KG[weight["unit"]] * sales / each_size, 4)


def map_product(raw: dict, delivery_reliability: float | None = None, carbon: float | None = None) -> dict:
    """One raw record of the product file in the shape the rule check and the agent read.

    The category code stands in for the product group and the substitute group, because the
    data set has no category names. `description` is marketing text: data, never instructions.
    `delivery_reliability` and `carbon_kg_co2e_per_kg` are None where the files have no row: unknown.
    """
    s = raw.get("synkkaData") or {}
    return {
        "id": str(raw["id"]),
        "gtin": s.get("gtin"),  # the GS1 number that a buyer's own systems know the product by
        "name": _text(s.get("names")) or str(raw["id"]),
        "product_group": raw.get("category"),
        "substitute_group": raw.get("category"),
        "properties": _properties(raw),
        **read_allergens(raw),
        "text_allergens": text_allergens(raw),
        "supplier": raw.get("vendorName"),
        "origin": raw.get("countryOfOrigin"),
        "max_temp_c": s.get("maxTemperature"),
        "description": _text(s.get("marketingTexts"), ("en",), any_language=False) or "",
        "pack": _pack(raw),
        "delivery_reliability": delivery_reliability,
        "carbon_kg_co2e_per_kg": carbon,
        "sales_unit": raw.get("salesUnit"),
        "lot_size": raw.get("allowedLotSize"),
        "units": {u["unitId"]: u.get("sizeInBaseUnits") for u in raw.get("units") or [] if u.get("unitId")},
        "deposits": [dict(d) for d in raw.get("deposits") or []],
        "mass_kg": _mass_kg(raw),
    }


class ValioCatalog:
    """The product file for search and product facts, the live API for price and availability."""

    name = "Valio Aimo challenge data and mock API"
    source = SOURCE
    delivery_charge = DELIVERY_CHARGE

    def __init__(self, data_dir: Path | str, base_url: str, api_key: str, customer: str = "123", transport=None,
                 record_dir: Path | str | None = None, max_calls: int | None = None):
        import httpx

        self.data_dir = Path(data_dir)
        self.customer = customer
        self.retry_wait_s = 0.5  # the first backoff; it doubles on each retry, plus jitter
        self.min_gap_s = MIN_CALL_GAP_S
        self.http = httpx.Client(base_url=base_url, headers={"X-API-Key": api_key}, timeout=10, transport=transport)
        self._secret = api_key
        self.lock = threading.Lock()
        self.call_lock = threading.Lock()  # one live call at a time, so the gap between calls holds
        self.calls = 0  # live HTTP requests made by this process, retries included
        self.max_calls = max_calls
        self.record_dir = Path(record_dir) if record_dir else None
        self._last_call = 0.0
        self.book = OrderBook()
        self.orders = self.book.orders
        self._prefetched: dict[tuple[str, date], dict] = {}
        self.products: dict[str, dict] = {}
        self._names: dict[str, list[str]] = {}  # normalized names in every language, for search
        self._sha: dict[str, str] = {}  # SHA-256 of each raw record
        self._load()

    @staticmethod
    def _read_csv(path: Path, column: str) -> dict[str, float]:
        """productId -> value. Codes stay strings. A missing file or value is left out: unknown, never 0."""
        out: dict[str, float] = {}
        if path.exists():
            with path.open(encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    try:
                        out[str(row["productId"]).strip()] = float(row[column])
                    except (KeyError, TypeError, ValueError):
                        continue
        return out

    def _load(self) -> None:
        reliability = self._read_csv(self.data_dir / RELIABILITY_FILE, "deliveryReliability")
        carbon = self._read_csv(self.data_dir / CARBON_FILE, "AVG kg CO2-eq/kg")
        for raw in json.loads((self.data_dir / PRODUCT_FILE).read_text(encoding="utf-8")):
            pid = str(raw["id"])
            self.products[pid] = map_product(raw, reliability.get(pid), carbon.get(pid))
            self._names[pid] = [_norm(n["value"]) for n in (raw.get("synkkaData") or {}).get("names") or [] if n.get("value")]
            self._sha[pid] = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    # Product facts: from the file ------------------------------------------

    def search(self, query: str) -> Claim:
        """Products whose name, in one language, holds every word of the query. At most MAX_HITS, best first.

        Best means: more query words that are whole words of the name, then the shorter name.
        """
        tokens = _norm(query).split()
        hits = []
        for pid, names in self._names.items() if tokens else ():
            scores = [(-sum(t in set(re.findall(r"[\w%]+", n)) for t in tokens), len(n))
                      for n in names if all(t in n for t in tokens)]
            if scores:
                hits.append((min(scores), pid))
        ids = [pid for _, pid in sorted(hits)[:MAX_HITS]]
        digest = hashlib.sha256("".join(self._sha[i] for i in ids).encode()).hexdigest()
        return Claim([dict(self.products[i]) for i in ids], f'{PRODUCT_FILE}: search "{query}"',
                     _now().isoformat(timespec="seconds"), digest)

    def product(self, product_id: str) -> Claim:
        pid = str(product_id)
        p = self.products.get(pid)
        digest = self._sha.get(pid) or hashlib.sha256(b"null").hexdigest()
        return Claim(dict(p) if p else None, f"{PRODUCT_FILE}: product {pid}", _now().isoformat(timespec="seconds"), digest)

    # Price and availability: from the live API -------------------------------

    def _record(self, path: str, body: dict, status: int | None, response: bytes | None, attempt: int) -> None:
        """Keep one live request and its answer, without the key, read-only, for replays in tests."""
        if self.record_dir is None:
            return
        entry = {"recorded_at": _now().isoformat(timespec="seconds"), "attempt": attempt,
                 "request": {"method": "POST", "url": str(self.http.base_url).rstrip("/") + path,
                             "headers": {"X-API-Key": "<redacted>"}, "json": body},
                 "response": {"status": status}}
        if response is not None:
            try:
                entry["response"]["json"] = json.loads(response)
            except ValueError:
                entry["response"]["text_head"] = response[:500].decode("utf-8", "replace")
        text = json.dumps(entry, indent=1, ensure_ascii=False)
        if self._secret and self._secret in text:
            return  # never write the key
        name = f"{path.strip('/').replace('/', '-')}-{len(body.get('products') or [])}p.json"
        try:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            seq = len(list(self.record_dir.glob("*.json"))) + 1
            while list(self.record_dir.glob(f"{seq:03d}-*")):  # never overwrite a recording
                seq += 1
            out = self.record_dir / f"{seq:03d}-{name}"
            with out.open("x", encoding="utf-8") as f:
                f.write(text + "\n")
            out.chmod(0o444)
        except OSError as e:  # a recording must never break a live call; say so instead
            print(f"valio: could not record {name} in {self.record_dir}: {e}", file=sys.stderr)

    def _post(self, path: str, body: dict, retries: int = MAX_RETRIES) -> bytes:
        """One read call: at most 2 a second, then up to `retries` retries on 429, 5xx and network errors.

        The wait doubles each time, plus jitter, or follows Retry-After. Every attempt is counted and recorded.
        """
        import httpx

        for attempt in range(retries + 1):
            last = attempt == retries
            with self.call_lock:
                if self.max_calls is not None and self.calls >= self.max_calls:
                    raise RuntimeError(f"the live API call budget of this process ({self.max_calls}) is used up")
                gap = self.min_gap_s - (time_mod.monotonic() - self._last_call)
                if gap > 0:
                    time_mod.sleep(gap)
                self._last_call = time_mod.monotonic()
                self.calls += 1
                try:
                    r = self.http.post(path, json=body)
                except httpx.TransportError:
                    self._record(path, body, None, None, attempt + 1)
                    if last:
                        raise
                    r = None
                else:
                    self._record(path, body, r.status_code, r.content, attempt + 1)
            if r is not None and (r.status_code < 500 and r.status_code != 429 or last):
                r.raise_for_status()
                return r.content
            after = r.headers.get("retry-after") if r is not None else None
            wait = float(after) if after and after.isdigit() else self.retry_wait_s * 2 ** attempt
            time_mod.sleep(min(wait, 30) + random.uniform(0, self.retry_wait_s))
        raise RuntimeError("unreachable")

    def _fetch(self, ids: list[str], day: date) -> tuple[dict, dict, bytes, str | None]:
        """Price and availability of the products for one delivery date.

        Returns both answers by product id, both raw bodies joined into one JSON object (so that
        one hash covers the price and the availability of a quote), and the server time of the
        availability answer.
        """
        body = {"customer": self.customer, "requestedDate": day.isoformat(), "products": ids}
        price_raw = self._post("/api/price", body)
        avail_raw = self._post("/api/availability", body)
        price_doc, avail_doc = json.loads(price_raw), json.loads(avail_raw)
        currency = price_doc.get("currency")
        prices = {str(e.get("productId")): e | {"_currency": currency} for e in price_doc.get("products") or []}
        avail = {str(e.get("productId")): e for e in avail_doc.get("products") or []}
        return prices, avail, b'{"price":' + price_raw + b',"availability":' + avail_raw + b"}", avail_doc.get("timeNow")

    def _quote(self, pid: str, quantity: int, price: dict | None, avail: dict | None, server_now: str | None = None) -> dict:
        """The answer of the two calls for one product, in the shape the pipeline reads.

        `unit_price_eur` is catalogPrice.value: per sales unit, without tax. `delivery` is the
        availableDate (a date, no time). A passed closingTime means that date cannot be reached, so
        the delivery is None and `problem` says why. A missing value stays None.
        """
        p = self.products.get(pid) or {}
        unit_price = with_tax = valid_to = tax_rate = None
        problem = None
        if price and not price.get("error"):
            cp = price.get("catalogPrice") or {}
            if (cp.get("currency") or price.get("_currency")) == "EUR" and isinstance(cp.get("value"), (int, float)):
                unit_price = float(cp["value"])
                with_tax = float(cp["valueWithTax"]) if isinstance(cp.get("valueWithTax"), (int, float)) else None
                valid_to = _iso(cp.get("validTo"))
                tax_rate = (price.get("tax") or {}).get("taxRate")
            elif cp:
                problem = f"price is not in EUR ({cp.get('currency') or price.get('_currency')})"
        lines = (avail or {}).get("scheduleLines") or []
        line = lines[0] if lines else None
        blocked = bool((avail or {}).get("salesBlock")) or bool(line and line.get("salesBlock"))
        amount = float(line.get("availableAmount") or 0) if line else 0.0
        delivery = None
        if line and line.get("availableDate"):
            try:
                delivery = date.fromisoformat(str(line["availableDate"])[:10]).isoformat()
            except ValueError:
                delivery = None
        closing = _iso((avail or {}).get("closingTime"))
        now = _parse(server_now) or _now()
        if delivery and closing and _parse(closing) < now:
            problem = f"the order cut-off {closing} for delivery on {delivery} has passed"
            delivery = None
        unit = line.get("salesUnit") if line else None
        if unit and p.get("sales_unit") and unit != p["sales_unit"]:
            problem = f"the API counts in {unit}, the product file sells in {p['sales_unit']}"
            delivery = None
        if blocked:
            problem = "sales block: the product cannot be ordered now"
        return {
            "product_id": pid,
            "unit_price_eur": unit_price,
            "unit_price_with_tax_eur": with_tax,
            "tax_rate_pct": tax_rate,
            "price_valid_to": valid_to,
            "available_units": 0 if blocked or not line else int(min(quantity, amount)),
            "delivery": delivery,
            "delivery_time_known": False,
            "closing_time": closing,
            "sales_unit": unit or p.get("sales_unit"),
            "logistic_model": (avail or {}).get("logisticModel"),
            "quantity_known": bool(line) and amount != UNKNOWN_AMOUNT,
            "problem": problem,
            "source": SOURCE,
        }

    def prefetch(self, product_ids: list[str], needed_by: datetime) -> None:
        """Ask price and availability for many products in one call per endpoint.

        Each answer serves one later availability() call for that product and date. The call
        after that goes to the API again, so the recheck before an order is always fresh.
        Valio asks teams to narrow the products down first and not to query the whole catalog.
        """
        ids, day = list(dict.fromkeys(str(i) for i in product_ids)), needed_by.date()
        for i in range(0, len(ids), MAX_IDS_PER_CALL):
            chunk = ids[i:i + MAX_IDS_PER_CALL]
            prices, avail, raw, server_now = self._fetch(chunk, day)
            stamp = _claim(f"POST /api/price + POST /api/availability, {len(chunk)} products", raw, None)
            with self.lock:
                for pid in chunk:
                    self._prefetched[(pid, day)] = {
                        "price": prices.get(pid), "availability": avail.get(pid), "at": time_mod.monotonic(),
                        "server_now": server_now, "checked_at": stamp.checked_at, "sha256": stamp.sha256}

    def availability(self, product_id: str, quantity: int, needed_by: datetime) -> Claim:
        pid, day = str(product_id), needed_by.date()
        endpoint = f"POST /api/price + POST /api/availability {pid} x{quantity}"
        with self.lock:
            kept = self._prefetched.pop((pid, day), None)
        if kept and time_mod.monotonic() - kept["at"] <= PREFETCH_MAX_AGE_S:
            value = self._quote(pid, quantity, kept["price"], kept["availability"], kept["server_now"])
            return Claim(value, endpoint, kept["checked_at"], kept["sha256"])
        prices, avail, raw, server_now = self._fetch([pid], day)
        return _claim(endpoint, raw, self._quote(pid, quantity, prices.get(pid), avail.get(pid), server_now))

    # Orders: recorded by the gateway -----------------------------------------

    def place_order(self, lines: list[dict], idempotency_key: str) -> Claim:
        """Record the order in the gateway's own order book. The same key returns the same order.

        Valio's API has no order endpoint, so no Valio system receives the order.
        """
        endpoint = "gateway order book (recorded by the gateway, not sent to Valio: the challenge API has no order endpoint)"
        order, _ = self.book.place(lines, idempotency_key, "recorded by the gateway, not sent to Valio")
        raw = json.dumps({"endpoint": endpoint, "data": order}, sort_keys=True).encode()
        return _claim(endpoint, raw, order)


def _parse(value) -> datetime | None:
    """An API time such as '2026-11-15T17:00:00+0200'. A time with no zone is Helsinki time. None if unreadable."""
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=TZ)


def _iso(value) -> str | None:
    t = _parse(value)
    return t.isoformat(timespec="seconds") if t else None


def make_client():
    """The catalog the app runs on. See the module text for the rules."""
    mode = os.getenv("CATALOG", "").strip().lower()
    if mode not in ("", "local", "valio"):
        raise ValueError(f"CATALOG must be local or valio, not {mode!r}")
    if mode == "local":
        return LocalCatalog()
    key = os.getenv("VALIO_API_KEY", "").strip()
    data_dir = Path(os.getenv("VALIO_DATA_DIR") or "data/valio")
    data_dir = data_dir if data_dir.is_absolute() else ROOT / data_dir
    have_file = (data_dir / PRODUCT_FILE).exists()
    if mode == "valio":
        if not key:
            raise RuntimeError("CATALOG=valio needs VALIO_API_KEY. Put it in valio-challenge/.env.")
        if not have_file:
            raise RuntimeError(f"CATALOG=valio needs the product file {data_dir / PRODUCT_FILE}. "
                               "Download it from the Valio portal.")
    if key and have_file:
        # VALIO_RECORD_DIR keeps every live request and answer (without the key) for replays.
        # VALIO_MAX_CALLS caps the live calls of this process.
        cap = os.getenv("VALIO_MAX_CALLS", "").strip()
        return ValioCatalog(data_dir, os.getenv("VALIO_BASE_URL") or DEFAULT_URL, key,
                            customer=os.getenv("VALIO_CUSTOMER") or "123",
                            record_dir=os.getenv("VALIO_RECORD_DIR") or None, max_calls=int(cap) if cap else None)
    return LocalCatalog()
