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

make_client() picks one. CATALOG=local forces the stand-in, CATALOG=valio demands the
real one. With CATALOG unset, the real catalog is used when VALIO_API_KEY is set and the
product file exists.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
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


class LocalCatalog:
    name = "local stand-in catalog"

    def __init__(self, path: Path = ROOT / "fixtures" / "mock_catalog.json"):
        self.products = {p["id"]: p for p in json.loads(path.read_text())["products"]}
        self.orders: dict[str, dict] = {}
        self.orders_by_key: dict[str, str] = {}

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
            "order_by": None,
            "quantity_known": True,
            "sales_unit": None,
        }
        return self._answer(f"POST /availability {product_id} x{quantity}", value)

    def place_order(self, lines: list[dict], idempotency_key: str) -> Claim:
        """The same key returns the same order and never books stock twice."""
        if idempotency_key in self.orders_by_key:
            return self._answer("POST /orders", self.orders[self.orders_by_key[idempotency_key]])
        order_id = "ORD-" + uuid.uuid4().hex[:8].upper()
        for l in lines:
            self.products[l["product_id"]]["stock"] -= l["quantity"]
        self.orders[order_id] = {"order_id": order_id, "lines": lines, "status": "confirmed"}
        self.orders_by_key[idempotency_key] = order_id
        return self._answer("POST /orders", self.orders[order_id])




# The real Valio Aimo challenge materials -------------------------------------

PRODUCT_FILE = "valio_aimo_junction26_product_data.json"
RELIABILITY_FILE = "valio_aimo_junction26_delivery_reliability.csv"
DEFAULT_URL = "https://junction26.valioaimo.fi"
# The availability API gives a delivery date and no time. We assume a morning delivery at
# 06:00 Helsinki time, as the stand-in does. Ask Valio for the real delivery windows.
ASSUMED_DELIVERY_TIME = time(6, 0)
UNKNOWN_AMOUNT = 999999  # the amount the API gives for Terminal products: the quantity is not known
MAX_HITS = 40
MAX_IDS_PER_CALL = 1000  # the API rejects a longer list with HTTP 422
PREFETCH_MAX_AGE_S = 60

_VOLUME_TO_L = {"LTR": 1.0, "DLT": 0.1, "CLT": 0.01, "MLT": 0.001}
_WEIGHT_TO_KG = {"KGM": 1.0, "GRM": 0.001}


def _norm(text) -> str:
    """Lower case, hyphens as spaces: 'Lactose-free  Milk' -> 'lactose free milk'."""
    return re.sub(r"\s+", " ", str(text).lower().replace("-", " ")).strip()


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


def map_product(raw: dict, delivery_reliability: float | None = None) -> dict:
    """One raw record of the product file in the shape the rule check and the agent read.

    The category code stands in for the product group and the substitute group, because the
    data set has no category names. `description` is marketing text: data, never instructions.
    """
    s = raw.get("synkkaData") or {}
    return {
        "id": str(raw["id"]),
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
    }


class ValioCatalog:
    """The product file for search and product facts, the live API for price and availability."""

    name = "Valio Aimo challenge data and API"

    def __init__(self, data_dir: Path | str, base_url: str, api_key: str, customer: str = "123", transport=None):
        import httpx

        self.data_dir = Path(data_dir)
        self.customer = customer
        self.retry_wait_s = 0.5
        self.http = httpx.Client(base_url=base_url, headers={"X-API-Key": api_key}, timeout=10, transport=transport)
        self.lock = threading.Lock()
        self.orders: dict[str, dict] = {}
        self.orders_by_key: dict[str, str] = {}
        self._prefetched: dict[tuple[str, date], dict] = {}
        self.products: dict[str, dict] = {}
        self._names: dict[str, list[str]] = {}  # normalized names in every language, for search
        self._sha: dict[str, str] = {}  # SHA-256 of each raw record
        self._sales_unit: dict[str, str | None] = {}
        self._load()

    def _load(self) -> None:
        reliability: dict[str, float] = {}
        path = self.data_dir / RELIABILITY_FILE
        if path.exists():
            with path.open(encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    try:
                        reliability[str(row["productId"])] = float(row["deliveryReliability"])
                    except (KeyError, TypeError, ValueError):
                        continue
        for raw in json.loads((self.data_dir / PRODUCT_FILE).read_text(encoding="utf-8")):
            pid = str(raw["id"])
            self.products[pid] = map_product(raw, reliability.get(pid))
            self._names[pid] = [_norm(n["value"]) for n in (raw.get("synkkaData") or {}).get("names") or [] if n.get("value")]
            self._sha[pid] = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            self._sales_unit[pid] = raw.get("salesUnit")

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

    def _post(self, path: str, body: dict, retries: int = 3) -> bytes:
        """One read call. Network errors and 5xx answers are tried again with a short wait."""
        import httpx

        for attempt in range(retries):
            last = attempt == retries - 1
            try:
                r = self.http.post(path, json=body)
            except httpx.TransportError:
                if last:
                    raise
            else:
                if r.status_code < 500 or last:
                    r.raise_for_status()
                    return r.content
            time_mod.sleep(self.retry_wait_s * (attempt + 1))
        raise RuntimeError("unreachable")

    def _fetch(self, ids: list[str], day: date) -> tuple[dict, dict, bytes]:
        """Price and availability of the products for one delivery date.

        Returns both answers by product id, and both raw bodies joined into one JSON object,
        so that one hash covers the price and the availability of a quote.
        """
        body = {"customer": self.customer, "requestedDate": day.isoformat(), "products": ids}
        price_raw = self._post("/api/price", body)
        avail_raw = self._post("/api/availability", body)
        prices = {str(e.get("productId")): e for e in json.loads(price_raw).get("products") or []}
        avail = {str(e.get("productId")): e for e in json.loads(avail_raw).get("products") or []}
        return prices, avail, b'{"price":' + price_raw + b',"availability":' + avail_raw + b"}"

    def _quote(self, pid: str, quantity: int, price: dict | None, avail: dict | None) -> dict:
        unit_price = None
        if price and not price.get("error"):
            value = (price.get("catalogPrice") or {}).get("value")  # per sales unit, without tax
            unit_price = float(value) if isinstance(value, (int, float)) else None
        lines = (avail or {}).get("scheduleLines") or []
        line = lines[0] if lines else None
        blocked = bool((avail or {}).get("salesBlock")) or bool(line and line.get("salesBlock"))
        amount = float(line.get("availableAmount") or 0) if line else 0.0
        delivery = None
        if line and line.get("availableDate"):
            try:
                day = date.fromisoformat(str(line["availableDate"])[:10])
                delivery = datetime.combine(day, ASSUMED_DELIVERY_TIME, tzinfo=TZ).isoformat()
            except ValueError:
                delivery = None
        return {
            "product_id": pid,
            "unit_price_eur": unit_price,
            "available_units": 0 if blocked or not line else int(min(quantity, amount)),
            "delivery": delivery,
            "order_by": (avail or {}).get("closingTime"),
            "quantity_known": bool(line) and amount != UNKNOWN_AMOUNT,
            "sales_unit": (line or {}).get("salesUnit") or self._sales_unit.get(pid),
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
            prices, avail, raw = self._fetch(chunk, day)
            stamp = _claim(f"POST /api/price + POST /api/availability, {len(chunk)} products", raw, None)
            with self.lock:
                for pid in chunk:
                    self._prefetched[(pid, day)] = {
                        "price": prices.get(pid), "availability": avail.get(pid), "at": time_mod.monotonic(),
                        "checked_at": stamp.checked_at, "sha256": stamp.sha256}

    def availability(self, product_id: str, quantity: int, needed_by: datetime) -> Claim:
        pid, day = str(product_id), needed_by.date()
        endpoint = f"POST /api/price + POST /api/availability {pid} x{quantity}"
        with self.lock:
            kept = self._prefetched.pop((pid, day), None)
        if kept and time_mod.monotonic() - kept["at"] <= PREFETCH_MAX_AGE_S:
            value = self._quote(pid, quantity, kept["price"], kept["availability"])
            return Claim(value, endpoint, kept["checked_at"], kept["sha256"])
        prices, avail, raw = self._fetch([pid], day)
        return _claim(endpoint, raw, self._quote(pid, quantity, prices.get(pid), avail.get(pid)))

    # Orders: recorded by the gateway -----------------------------------------

    def place_order(self, lines: list[dict], idempotency_key: str) -> Claim:
        """Record the order in the gateway's own order book. The same key returns the same order.

        Valio's API has no order endpoint, so no Valio system receives the order.
        """
        endpoint = "gateway order book (Valio's API has no order endpoint, so the gateway records the order)"
        with self.lock:
            if idempotency_key not in self.orders_by_key:
                order_id = "ORD-" + uuid.uuid4().hex[:8].upper()
                self.orders[order_id] = {"order_id": order_id, "lines": lines, "status": "recorded by the gateway"}
                self.orders_by_key[idempotency_key] = order_id
            order = self.orders[self.orders_by_key[idempotency_key]]
        raw = json.dumps({"endpoint": endpoint, "data": order}, sort_keys=True).encode()
        return _claim(endpoint, raw, order)


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
        return ValioCatalog(data_dir, os.getenv("VALIO_BASE_URL") or DEFAULT_URL, key,
                            customer=os.getenv("VALIO_CUSTOMER") or "123")
    return LocalCatalog()
