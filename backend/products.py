"""The shelf's active product set: one shared file, products/active.json.

It says what the camera and the recognition services look for (identities, labels, flavour families, the
local-score thresholds below which a detection goes to the cloud model) and how a counted unit relates to
the catalogue's ordering unit. It names products for recognition and display only: purchasing eligibility
stays with the signed rule set, and nothing here places, sizes or authorises an order.

Two quantities are never merged: the camera counts consumer units (a bottle, a carton); the catalogue sells
in its sales unit (a case of 15, a pack of 18, a single carton). count_vs_order() and sales_units_for() keep
them apart.

Earlier sets are archived under products/archive/ with their original identities. Historical stock, orders,
recordings and enrolment photos keep the identities they were made with; nothing here relabels them.

Removed products (excluded_products, e.g. PROfeel Salted Caramel since 15 Nov) are not candidates, enrolment
options or counts. They stay listed so that a model trained with them keeps its class indices (model_coverage)
and so that a readable removed flavour is reported as not inventory.
"""

from __future__ import annotations

import json
import math
import re
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACTIVE = ROOT / "products" / "active.json"
SCHEMA = "shelf-product-set/2"
# Valio's answer of 14 Nov (backend/valio.py): a lot size of 0.01 has a technical reason and means 1.
TECHNICAL_LOT = 0.01


def load(path: Path | str | None = None) -> dict:
    data = json.loads(Path(path or ACTIVE).read_text(encoding="utf-8"))
    validate(data)
    return data


def validate(data: dict) -> None:
    if data.get("schema") != SCHEMA:
        raise ValueError(f"product set schema must be {SCHEMA}")
    products = data.get("products") or []
    if not products:
        raise ValueError("the active product set has no products")
    ids = [p["identity"] for p in products]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate identity in the active product set")
    families = data.get("families") or {}
    archived = {i for s in data.get("archived_sets") or [] for i in (s.get("labels") or {})}
    active = set(ids)
    for x in data.get("excluded_products") or []:
        if x["identity"] in active:
            raise ValueError(f"{x['identity']} is both active and excluded")
        if x.get("family") and x["family"] not in families:
            raise ValueError(f"{x['identity']}: unknown family {x['family']}")
    for p in products:
        if p["identity"] in archived:
            raise ValueError(f"{p['identity']} is an archived identity; a new set gets new identities")
        thr = p.get("kimi_at_or_below")
        if not isinstance(thr, (int, float)) or not 0.0 < thr <= 1.0:
            raise ValueError(f"{p['identity']}: kimi_at_or_below must be a number in (0, 1]")
        if p.get("family") and p["family"] not in families:
            raise ValueError(f"{p['identity']}: unknown family {p['family']}")
        if not re.fullmatch(r"\d{8}|\d{12,14}", str(p.get("ean") or "")):
            raise ValueError(f"{p['identity']}: ean must be the owner's 8, 12, 13 or 14 digit catalogue selection")


def by_identity(data: dict) -> dict[str, dict]:
    return {p["identity"]: p for p in data["products"]}


def labels(data: dict, with_archived: bool = True) -> dict[str, str]:
    """identity -> label, for display. Archived and removed identities keep their original labels."""
    out = {}
    if with_archived:
        for s in data.get("archived_sets") or []:
            out.update(s.get("labels") or {})
    out.update({x["identity"]: x["label"] for x in data.get("excluded_products") or []})
    out.update({p["identity"]: p["label"] for p in data["products"]})
    return out


def model_coverage(data: dict, classes, num_classes: int | None = None) -> dict:
    """The explicit coverage map of a local model (since 15 Nov 06:40; it replaces the gate "every active product
    must be one class"). A model may cover only some products and is never refused for that.

    classes: the class names in index order from the model's manifest (the trainer's export), or None when unknown.
    Every class keeps its original index and is an active product, a recognised non-inventory product (removed, e.g.
    Salted Caramel, or old: never counted, never purchasable) or unmapped (its boxes keep their number; the cloud
    check establishes the identity). An active product is covered locally only through a verified class and where
    local_model_coverage allows it; every other active product is covered by the whole-frame cloud check."""
    active = {p["identity"] for p in data["products"]}
    excluded = {x["identity"] for x in data.get("excluded_products") or []}
    archived = {i for s in data.get("archived_sets") or [] for i in (s.get("labels") or {})}
    rules = (data.get("local_model_coverage") or {}).get("products") or {}
    known = isinstance(classes, list)
    n = num_classes if num_classes is not None else (len(classes) if known else 0)
    kinds = {}
    for k in range(n):
        c = classes[k] if known and k < len(classes) else None
        kinds[k] = ("active" if c in active else "excluded" if c in excluded else "archived" if c in archived else "unmapped") if c else "unmapped"
    products = {}
    for p in data["products"]:
        allowed = (rules.get(p["identity"]) or {}).get("coverage", "local-when-mapped") != "cloud"
        local = [k for k in range(n) if known and k < len(classes) and classes[k] == p["identity"]]
        products[p["identity"]] = {"local": allowed and bool(local), "local_classes": local if allowed else [],
                                   "covered_by": "local model + cloud check" if allowed and local else "cloud (whole-frame check)"}
    return {"class_map": "verified" if known else "awaiting trainer metadata", "products": products,
            "active_classes": {classes[k]: k for k, v in kinds.items() if v == "active"},
            "removed_classes": {k: classes[k] for k, v in kinds.items() if v == "excluded"},
            "old_classes": {k: classes[k] for k, v in kinds.items() if v == "archived"},
            "unmapped_classes": [k for k, v in kinds.items() if v == "unmapped"]}


def needs_cloud(product: dict, score: float) -> bool:
    """A local detection at or below the product's threshold goes to the cloud model. The threshold is a routing
    rule, separate from the minimum score for showing a box."""
    return float(score) <= float(product["kimi_at_or_below"])


def _lot(catalogue: dict) -> float | None:
    lot = catalogue.get("lot_size_sales_units")
    return None if lot is None else (1.0 if lot == TECHNICAL_LOT else float(lot))


def count_vs_order(product: dict, counted: int) -> dict:
    """The camera's count of consumer units beside the catalogue's ordering unit. Two quantities, never merged:
    7 cartons of an 18-pack is 7 cartons and 7/18 of a pack, not 7 packs."""
    c = product.get("catalogue") or {}
    per = c.get("count_units_per_sales_unit")
    out = {"counted": int(counted), "count_unit": product.get("count_unit"), "sales_unit": c.get("sales_unit"),
           "sales_unit_name": c.get("sales_unit_name"), "count_units_per_sales_unit": per}
    if per:
        full, rest = divmod(int(counted), int(per))
        out.update(full_sales_units=full, remainder_count_units=rest, as_sales_units=str(Fraction(int(counted), int(per))))
    else:
        out["note"] = "the catalogue entry's ordering unit is not known, so no conversion is made"
    return out


def sales_units_for(product: dict, count_units_needed: int) -> dict:
    """The smallest orderable quantity, in the catalogue's sales unit and lot size, that holds at least
    count_units_needed consumer units. Arithmetic only: it decides nothing about whether to order."""
    c = product.get("catalogue") or {}
    per, lot = c.get("count_units_per_sales_unit"), _lot(c)
    if not per or not lot:
        return {"sales_units": None, "sales_unit": c.get("sales_unit"),
                "reason": "the catalogue entry's sales unit or lot size is not known"}
    need = max(0, int(count_units_needed))
    sales = math.ceil(need / per)
    sales = math.ceil(sales / lot) * lot
    sales = int(sales) if float(sales).is_integer() else sales
    return {"sales_units": sales, "sales_unit": c.get("sales_unit"), "count_units": sales * per,
            "count_unit": product.get("count_unit"), "lot_size_sales_units": lot}


# Reading the selected entries from the catalogue product file (tools/catalogue_units.py) ----------------------

UNIT_NAMES = {"ST": "single unit", "KI": "case", "KRS": "layer", "PAL": "pallet", "KG": "kilogram"}
_SIZE_IN_NAME = re.compile(r"(\d+(?:[.,]\d+)?)\s?(ml|cl|dl|l|g|kg)\b", re.I)


def _digits(v) -> str:
    return str(v or "").lstrip("0")


def find_by_gtin(records, ean: str) -> list[tuple[dict, str | None]]:
    """Every (record, unit id) whose unit, product or unit-conversion GTIN equals the EAN exactly. An exact number
    only: nothing similar is ever taken instead. More than one record is reported, never resolved here."""
    want, hits = _digits(ean), []
    for raw in records:
        s = raw.get("synkkaData") or {}
        unit = next((u.get("unitId") for u in raw.get("units") or [] if _digits(u.get("gtin")) == want), None)
        if unit is None and _digits(s.get("gtin")) == want:
            unit = raw.get("baseUnit")
        if unit is None:
            unit = next((c.get("synkkaPackagingType") for c in s.get("unitConversions") or [] if _digits(c.get("gtin")) == want), None)
        if unit is not None:
            hits.append((raw, unit))
    return hits


def catalogue_entry(raw: dict, ean: str, matched_unit: str | None, source: dict) -> dict:
    """The ordering facts of one catalogue record: sales unit, units and their sizes, lot size, the consumer unit
    the camera counts and how many of them one sales unit holds."""
    s = raw.get("synkkaData") or {}
    units = {u["unitId"]: u.get("sizeInBaseUnits") for u in raw.get("units") or [] if u.get("unitId")}
    gtins = {u["unitId"]: u.get("gtin") for u in raw.get("units") or [] if u.get("unitId") and u.get("gtin")}
    consumer = next((uid for uid, g in gtins.items() if _digits(g) and _digits(g) == _digits(s.get("gtin"))), None)
    sales = raw.get("salesUnit")
    per = None
    if consumer and units.get(consumer) and units.get(sales):
        ratio = units[sales] / units[consumer]
        per = int(ratio) if float(ratio).is_integer() else ratio
    names = {n.get("language"): n.get("value") for n in s.get("names") or [] if n.get("value")}
    each = next((u for u in s.get("unitConversions") or [] if u.get("synkkaPackagingType") == "BASE_UNIT_OR_EACH"), None)
    size = None
    if each and (each.get("volume") or {}).get("value"):
        size = {"value": each["volume"]["value"], "unit": each["volume"]["unit"], "source": "unit conversion BASE_UNIT_OR_EACH"}
    else:
        m = _SIZE_IN_NAME.search(names.get("en") or names.get("fi") or "")
        if m:
            size = {"text": m.group(0), "source": "catalogue name (no unit conversion with a volume)"}
    lot = raw.get("allowedLotSize")
    return {
        "status": "found", "product_id": str(raw["id"]), "selected_ean": str(ean), "selected_ean_is_unit": matched_unit,
        "name_en": names.get("en"), "name_fi": names.get("fi"), "brand": s.get("brand"), "vendor": raw.get("vendorName"),
        "category": raw.get("category"), "sales_unit": sales, "sales_unit_name": UNIT_NAMES.get(sales, sales),
        "base_unit": raw.get("baseUnit"), "units": units, "unit_gtins": gtins,
        "count_unit_code": consumer, "count_unit_gtin": s.get("gtin"), "count_units_per_sales_unit": per,
        "consumer_unit_size": size, "lot_size_sales_units": 1 if lot == TECHNICAL_LOT else lot,
        "lot_size_raw": lot, "deleted": raw.get("deleted"), "source": source}
