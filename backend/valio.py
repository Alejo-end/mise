"""Adapter for the Valio Aimo mock API.

Every answer comes back as a Claim: the value plus the endpoint, the time and a
SHA-256 hash of the raw response (the trust protocol). Every raw response is
cached in fixtures/cache/.

Two back ends:
  LocalCatalog  a stand-in that reads fixtures/mock_catalog.json. Used when
                VALIO_BASE_URL is not set.
  ValioHttp     the real mock API. The paths below are placeholders until the
                docs from the Valio portal are in docs/valio-api/.
"""

from __future__ import annotations

import hashlib
import json
import os
import time as time_mod
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path

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


class ValioHttp:
    """The real Valio Aimo mock API. Fill in the paths from docs/valio-api/."""

    name = "Valio Aimo mock API"

    def __init__(self, base_url: str, api_key: str):
        import httpx

        self.http = httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=10)

    def _call(self, method: str, path: str, retries: int = 3, **kw) -> tuple[bytes, object]:
        """Retry network errors and 5xx answers with a short wait. Callers decide whether a call is safe to retry."""
        import httpx

        for attempt in range(retries):
            try:
                r = self.http.request(method, path, **kw)
                if r.status_code < 500:
                    r.raise_for_status()
                    return r.content, r.json()
            except httpx.TransportError:
                if attempt == retries - 1:
                    raise
            time_mod.sleep(0.5 * (attempt + 1))
        r.raise_for_status()
        return r.content, r.json()

    def search(self, query: str) -> Claim:
        raw, data = self._call("GET", "/products", params={"q": query})  # TODO: real path
        return _claim(f"GET /products?q={query}", raw, data)

    def product(self, product_id: str) -> Claim:
        raw, data = self._call("GET", f"/products/{product_id}")  # TODO: real path
        return _claim(f"GET /products/{product_id}", raw, data)

    def availability(self, product_id: str, quantity: int, needed_by: datetime) -> Claim:
        body = {"product_id": product_id, "quantity": quantity, "needed_by": needed_by.isoformat()}
        raw, data = self._call("POST", "/availability", json=body)  # TODO: real path
        return _claim(f"POST /availability {product_id} x{quantity}", raw, data)

    def place_order(self, lines: list[dict], idempotency_key: str) -> Claim:
        # Safe to retry only because the key makes a repeated call return the first order.
        raw, data = self._call("POST", "/orders", json={"lines": lines},
                               headers={"Idempotency-Key": idempotency_key})  # TODO: real path
        return _claim("POST /orders", raw, data)


def make_client():
    url, key = os.getenv("VALIO_BASE_URL"), os.getenv("VALIO_API_KEY")
    if url and key:
        return ValioHttp(url, key)
    return LocalCatalog()
