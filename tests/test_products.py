"""The shared active product set (products/active.json): identities, routing thresholds, the count/order units and
the archived set. Catalogue records here are small made-up records in the product file's shape, not Valio data."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from backend import products as P

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = P.load()
BY = P.by_identity(ACTIVE)
SHAKES = ["aimo:profeel-strawberry", "aimo:profeel-mint-chocolate"]
CARAMEL = "aimo:profeel-salted-caramel"
FIVE_CLASS = [CARAMEL, *SHAKES, "aimo:bonne-orange-juice", "aimo:aito-barista-oat"]  # a trainer's possible class order
BONNE, AITO = BY["aimo:bonne-orange-juice"], BY["aimo:aito-barista-oat"]


def test_the_active_set_is_the_owners_four_catalogue_selections():
    assert ACTIVE["set_id"] == "kitchen-20261115-b" and ACTIVE["replaces"] == "kitchen-20261115-a"
    assert {p["identity"]: p["ean"] for p in ACTIVE["products"]} == {
        "aimo:profeel-strawberry": "6408430101290", "aimo:profeel-mint-chocolate": "6408430011629",
        "aimo:bonne-orange-juice": "4740098003823", "aimo:aito-barista-oat": "6410222014305"}
    assert all(BY[s]["family"] == "profeel-shake" for s in SHAKES)
    assert ACTIVE["families"]["profeel-shake"]["flavour_required"] is True


@pytest.mark.parametrize("ident, at, above", [(s, 0.95, 0.9501) for s in SHAKES] +
                         [("aimo:bonne-orange-juice", 0.85, 0.8501), ("aimo:aito-barista-oat", 0.85, 0.8501)])
def test_routing_threshold_boundaries_are_inclusive(ident, at, above):
    p = BY[ident]
    assert p["kimi_at_or_below"] == at
    assert P.needs_cloud(p, at) is True  # "at or below" goes to the cloud model
    assert P.needs_cloud(p, at - 0.0001) is True
    assert P.needs_cloud(p, above) is False


def test_the_routing_threshold_is_separate_from_the_display_minimum():
    r = ACTIVE["routing"]
    assert r["display_min_score"] == 0.3 and r["default_kimi_at_or_below"] == 0.85
    assert all(p["kimi_at_or_below"] != r["display_min_score"] for p in ACTIVE["products"])
    assert all("provisional" in BY[s]["threshold_status"] for s in SHAKES)


def test_bonne_counts_cartons_and_orders_packs_of_18():
    c = BONNE["catalogue"]
    assert c["status"] == "found" and c["selected_ean_is_unit"] == "KI" and c["sales_unit"] == "KI"
    assert c["count_unit_code"] == "ST" and c["count_units_per_sales_unit"] == 18 and c["lot_size_sales_units"] == 1
    seven = P.count_vs_order(BONNE, 7)
    assert seven == {"counted": 7, "count_unit": "carton", "sales_unit": "KI", "sales_unit_name": "case",
                     "count_units_per_sales_unit": 18, "full_sales_units": 0, "remainder_count_units": 7,
                     "as_sales_units": "7/18"}
    assert P.count_vs_order(BONNE, 18)["full_sales_units"] == 1 and P.count_vs_order(BONNE, 18)["as_sales_units"] == "1"
    t25 = P.count_vs_order(BONNE, 25)
    assert (t25["counted"], t25["full_sales_units"], t25["remainder_count_units"], t25["as_sales_units"]) == (25, 1, 7, "25/18")
    assert P.sales_units_for(BONNE, 1) == {"sales_units": 1, "sales_unit": "KI", "count_units": 18, "count_unit": "carton",
                                           "lot_size_sales_units": 1.0}
    assert P.sales_units_for(BONNE, 18)["sales_units"] == 1 and P.sales_units_for(BONNE, 19)["sales_units"] == 2
    assert P.sales_units_for(BONNE, 0)["sales_units"] == 0


def test_the_other_products_ordering_units():
    for s in ("aimo:profeel-strawberry", "aimo:profeel-mint-chocolate"):
        c = BY[s]["catalogue"]
        assert (c["sales_unit"], c["count_units_per_sales_unit"], c["lot_size_sales_units"]) == ("KI", 15, 1)
        assert P.sales_units_for(BY[s], 16)["count_units"] == 30
    c = AITO["catalogue"]
    assert (c["sales_unit"], c["count_units_per_sales_unit"]) == ("ST", 1)
    assert P.sales_units_for(AITO, 5) == {"sales_units": 5, "sales_unit": "ST", "count_units": 5, "count_unit": "carton",
                                          "lot_size_sales_units": 1.0}


def test_salted_caramel_is_removed_but_known_as_not_inventory():
    assert CARAMEL not in BY and all(p["catalogue"]["status"] == "found" for p in ACTIVE["products"])
    x = ACTIVE["excluded_products"][0]
    assert (x["identity"], x["ean"], x["family"]) == (CARAMEL, "6408430401260", "profeel-shake")
    assert P.labels(ACTIVE)[CARAMEL] == "PROfeel Salted Caramel shake"
    assert "positions" not in ACTIVE["recording"] and "any arrangement" in ACTIVE["recording"]["arrangement_example"]


def test_an_entry_without_ordering_facts_gets_no_conversion():
    p = {"identity": "x", "count_unit": "bottle", "catalogue": {"status": "not found", "product_id": None}}
    assert "note" in P.count_vs_order(p, 3) and P.count_vs_order(p, 3)["counted"] == 3
    assert P.sales_units_for(p, 3)["sales_units"] is None


def test_a_five_class_model_keeps_its_indices_and_its_caramel_class_is_not_counted():
    c = P.model_coverage(ACTIVE, FIVE_CLASS)
    assert c["class_map"] == "verified" and c["removed_classes"] == {0: CARAMEL} and c["unmapped_classes"] == []
    assert c["active_classes"] == {"aimo:profeel-strawberry": 1, "aimo:profeel-mint-chocolate": 2,
                                   "aimo:bonne-orange-juice": 3, "aimo:aito-barista-oat": 4}  # original indices
    # the product file covers Aito by the cloud check: even a model with an Aito class does not count it locally
    assert c["products"]["aimo:aito-barista-oat"]["local"] is False and c["products"]["aimo:bonne-orange-juice"]["local"] is True


def test_partial_coverage_is_accepted_and_an_unknown_class_order_keeps_every_class_numbered():
    v3 = P.model_coverage(ACTIVE, None, num_classes=10)  # the v3 model: ten classes, no trainer metadata yet
    assert v3["class_map"] == "awaiting trainer metadata" and v3["unmapped_classes"] == list(range(10))
    assert not any(p["local"] for p in v3["products"].values())
    three = P.model_coverage(ACTIVE, FIVE_CLASS[:3] + ["exp:milk"])  # caramel, strawberry, mint, an old product
    assert three["products"]["aimo:profeel-strawberry"]["local"] is True and three["products"]["aimo:bonne-orange-juice"]["local"] is False
    assert three["old_classes"] == {3: "exp:milk"}
    assert ACTIVE["local_model_coverage"]["products"]["aimo:aito-barista-oat"]["coverage"] == "cloud"
    assert "model_compatibility" not in ACTIVE


def _record(pid="900001", consumer="0000000000017", case="0000000000024", per=18.0, lot=0.01):
    return {"id": pid, "salesUnit": "KI", "baseUnit": "ST", "allowedLotSize": lot, "category": "1", "vendorName": "X",
            "units": [{"unitId": "ST", "gtin": consumer, "sizeInBaseUnits": 1.0},
                      {"unitId": "KI", "gtin": case, "sizeInBaseUnits": per}],
            "synkkaData": {"gtin": consumer, "names": [{"language": "en", "value": "18x Test juice 300ml"}],
                           "unitConversions": [{"synkkaPackagingType": "CASE"}]}}


def test_catalogue_lookup_is_by_exact_gtin_and_reads_the_pack_conversion():
    recs = [_record(), _record("900002", "0000000000031", "0000000000048")]
    hits = P.find_by_gtin(recs, "24")
    assert [(r["id"], u) for r, u in hits] == [("900001", "KI")]
    assert P.find_by_gtin(recs, "17")[0][1] == "ST"
    assert P.find_by_gtin(recs, "2") == [] and P.find_by_gtin(recs, "240") == []  # exact digits only
    e = P.catalogue_entry(recs[0], "24", "KI", {"file": "made-up"})
    assert (e["product_id"], e["sales_unit"], e["count_unit_code"], e["count_units_per_sales_unit"]) == ("900001", "KI", "ST", 18)
    assert e["lot_size_sales_units"] == 1 and e["consumer_unit_size"] == {"text": "300ml", "source": "catalogue name (no unit conversion with a volume)"}
    assert len(P.find_by_gtin(recs + [_record("900003")], "24")) == 2  # reported, never resolved


def test_the_archived_sets_keep_their_original_identities():
    five = ACTIVE["archived_sets"][0]
    blob5 = (ROOT / five["file"]).read_bytes()
    assert hashlib.sha256(blob5).hexdigest() == five["sha256"] and json.loads(blob5)["set_id"] == "kitchen-20261115-a"
    assert [p["identity"] for p in json.loads(blob5)["products"]][0] == CARAMEL  # history unchanged
    arch = ACTIVE["archived_sets"][1]
    blob = (ROOT / arch["file"]).read_bytes()
    assert hashlib.sha256(blob).hexdigest() == arch["sha256"]  # archived unchanged
    old = json.loads(blob)
    assert old["set_id"] == "kitchen-20261114-b"
    assert [old["slots"][s]["identity"] for s in ("1", "2", "3")] == ["exp:chips", "exp:energy-drink", "exp:milk"]
    labels = P.labels(ACTIVE)
    assert labels["exp:chips"] == "chips" and labels["exp:milk"] == "milk"
    assert labels["aimo:bonne-orange-juice"] == "Bonne orange juice (18-pack)"
    assert not set(arch["labels"]) & set(BY)


def test_a_new_set_cannot_reuse_an_archived_identity_or_a_bad_threshold():
    bad = json.loads(json.dumps(ACTIVE))
    bad["products"][0]["identity"] = "exp:milk"
    with pytest.raises(ValueError, match="archived identity"):
        P.validate(bad)
    bad = json.loads(json.dumps(ACTIVE))
    bad["products"][1]["kimi_at_or_below"] = 0
    with pytest.raises(ValueError, match="kimi_at_or_below"):
        P.validate(bad)


def test_the_recording_plan_follows_the_active_set():
    import sys
    sys.path.insert(0, str(ROOT / "tools"))
    import recording_plan as rp
    assert rp.SET_ID == ACTIVE["set_id"] and set(rp.IDENTITY.values()) == set(BY)


def test_the_product_set_grants_no_purchasing_authority():
    """No active product is in a signed rule set: naming a product for the camera does not make it orderable."""
    ids = {p["catalogue"].get("product_id") for p in ACTIVE["products"]} - {None}
    assert ids == {"464893", "464147", "475502", "475224"}
    assert "rye bread" in (ROOT / "rules" / "school_kitchen_valio.yaml").read_text()  # purchasing settings as they were
    for f in (ROOT / "rules").glob("*.yaml"):
        rules = yaml.safe_load(f.read_text())
        listed = {str(t.get("product_id")) for t in rules.get("targets", [])}
        listed |= {str(x) for g in (rules.get("approved_substitute_groups") or {}).values() for x in g}
        assert not ids & listed, f.name
    assert "none" in ACTIVE["purchasing_authority"]


def test_the_box_update_delivers_the_shared_product_set():
    from backend import app as A
    files = {f["path"]: f["sha256"] for f in TestClient(A.app).get("/device/files").json()}
    assert files["assets/products.json"] == hashlib.sha256((ROOT / "products" / "active.json").read_bytes()).hexdigest()
    body = TestClient(A.app).get("/device/files/assets/products.json")
    assert body.status_code == 200 and body.json()["set_id"] == "kitchen-20261115-b"
