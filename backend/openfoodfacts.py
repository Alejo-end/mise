"""A second source for a product: Open Food Facts, by the number under the barcode.

It gives two things that the wholesaler's data does not: a picture of the pack, and a second
opinion on allergens. The second opinion can only add a warning. Shoppers fill this database in,
and many records list no allergens at all (the Valio milk drink is one), so an empty list there
means "nobody filled it in". It never clears a product.

It is an outside service. Each lookup has a short timeout and is kept in memory, and every caller
works without it. Only the barcode number leaves the backend. Data: Open Food Facts, under the
Open Database License.
"""
from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

NAME = "Open Food Facts"
URL = "https://world.openfoodfacts.org/api/v2/product/{gtin}?fields=product_name,brands,allergens_tags,traces_tags,image_front_small_url,image_front_url"
TIMEOUT_S = 3.0
RETRY_AFTER_S = 120.0  # a lookup that failed is tried again after this time

# Open Food Facts tag -> the allergen name that the rule sets use.
TAGS = {"en:milk": "milk", "en:nuts": "nuts", "en:peanuts": "peanuts", "en:sesame-seeds": "sesame", "en:soybeans": "soy",
        "en:eggs": "egg", "en:fish": "fish", "en:crustaceans": "crustaceans", "en:molluscs": "molluscs",
        "en:celery": "celery", "en:mustard": "mustard", "en:lupin": "lupin", "en:gluten": "gluten",
        "en:sulphur-dioxide-and-sulphites": "sulphites"}

_cache: dict[str, tuple[float, dict | None, bool]] = {}  # gtin -> (time, record, the lookup worked)
_lock = threading.Lock()


def enabled() -> bool:
    return os.getenv("OPEN_FOOD_FACTS", "").strip() == "1"


def _get(url: str) -> dict:
    import httpx
    r = httpx.get(url, timeout=TIMEOUT_S, headers={"User-Agent": "kitchen-ordering-demo/0.1"})
    return r.json() if r.status_code == 200 else {"status": 0}


def lookup(gtin: str, get=None) -> dict | None:
    """The record for one barcode number, or None if the database does not have it or did not answer."""
    code = re.sub(r"\D", "", str(gtin or "")).lstrip("0")
    if len(code) < 8:
        return None
    with _lock:
        hit = _cache.get(code)
    if hit and (hit[2] or time.time() - hit[0] < RETRY_AFTER_S):
        return hit[1]
    try:
        raw = (get or _get)(URL.format(gtin=code.zfill(13)))
        p = raw.get("product") if raw.get("status") == 1 else None
        record = p and {"name": p.get("product_name") or None, "brand": p.get("brands") or None,
                        "picture": p.get("image_front_small_url") or None,
                        "picture_large": p.get("image_front_url") or p.get("image_front_small_url") or None,
                        "allergens": sorted({TAGS[t] for t in (p.get("allergens_tags") or []) + (p.get("traces_tags") or []) if t in TAGS})}
        worked = True
    except Exception:
        record, worked = None, False
    with _lock:
        _cache[code] = (time.time(), record, worked)
    return record


def enrich(product: dict, get=None) -> dict:
    """Add the picture and the second allergen opinion to a catalog product. It changes the product in place."""
    record = lookup(product.get("gtin"), get)
    if record:
        product["picture"], product["picture_large"] = record["picture"], record["picture_large"]
        product["second_source"] = NAME
        product["second_source_allergens"] = record["allergens"]
    return product


def enrich_all(products: list[dict]) -> None:
    """The same for several products at once, so a slow answer costs one timeout and not one for each product."""
    if products:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(enrich, products))
