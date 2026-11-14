"""The real-catalog adapter, on synthetic records in the shape of the Valio product file.

No real Valio record is in this file: the dataset is for the challenge only. Two tests read
the real file when it is on this machine and are skipped when it is not.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest

from backend import valio as V
from backend.allergens import read_allergens
from backend.rules import FORBIDDEN, NEEDS_APPROVAL, TZ, Context, Line, check_line, load_rules

REAL = Path(__file__).resolve().parent.parent / "data" / "valio" / V.PRODUCT_FILE
NEEDED = datetime.now(TZ) + timedelta(days=2)


def record(pid, name, allergen=(), free=(), claims=(), category="10400", sales="ST", litres=1.0):
    enum = lambda group, values: {"type": "ENUM", "name": group, "values": [{"id": i, "unit": u} for i, u in values]}
    return {
        "id": pid, "salesUnit": sales, "baseUnit": "ST", "category": category, "allowedLotSize": 0.01,
        "vendorName": "TEST DAIRY", "countryOfOrigin": "fi",
        "units": [{"unitId": "ST", "gtin": "0000000000017", "sizeInBaseUnits": 1.0},  # the consumer unit
                  {"unitId": "KI", "sizeInBaseUnits": 5.0}],
        "synkkaData": {
            "gtin": "0000000000017", "names": [{"value": name, "language": "en"}], "maxTemperature": 6.0,
            "variableUnit": False,
            "unitConversions": [{"synkkaPackagingType": "BASE_UNIT_OR_EACH", "volume": {"unit": "LTR", "value": litres}}],
            "classifications": [enum("allergen", allergen), enum("nonAllergen", [(c, "FREE_FROM") for c in free]),
                                enum("nutritionalClaim", [(c, None) for c in claims])],
        },
    }


RECORDS = [
    record("1", "Test milk drink 1 l lactose free", [("AM", "CONTAINS")], claims=["FREE_FROM_LACTOSE"]),
    record("2", "Test almond bar", [("AM", "CONTAINS"), ("SA", "CONTAINS")], category="30000"),  # a nut kind, no AN
    record("3", "Test biscuit", [("AW", "CONTAINS"), ("AS", "MAY_CONTAIN")], category="30000"),
    record("4", "Test tomato", category="20000"),  # no allergen data at all
    record("5", "Test juice 1 l", [("X99", "CONTAINS")], category="50000"),  # declares no obligatory allergens
    record("6", "Test milk drink 1 l lactose free crate", [("AM", "CONTAINS")], claims=["FREE_FROM_LACTOSE"], sales="KI"),
]


def api(request: httpx.Request) -> httpx.Response:
    api.calls.append((request.url.path, request.headers.get("x-api-key")))
    ids = json.loads(request.content)["products"]
    if request.url.path == "/api/price":
        return httpx.Response(200, json={"customerId": "123", "currency": "EUR", "pricingDate": "x", "products": [
            {"productId": i, "quantity": 1.0, "netPriceFound": False, "error": i == "3",
             "catalogPrice": None if i == "3" else {"value": 1.5, "valueWithTax": 1.7, "validFrom": "a", "validTo": "b",
                                                    "currency": "EUR"}} for i in ids]})
    lines = {"1": [{"salesBlock": False, "availableAmount": 10.0, "availableDate": "2026-11-16T00:00:00", "salesUnit": "ST"}],
             "2": [{"salesBlock": False, "availableAmount": 999999.0, "availableDate": "2026-11-17T00:00:00", "salesUnit": "ST"}],
             "3": []}
    return httpx.Response(200, json={"timeNow": "x", "requestedDate": "x", "products": [
        # The cut-off lies in the future, relative to now: a passed closingTime now makes the date unreachable.
        {"productId": i, "closingTime": (datetime.now(TZ) + timedelta(days=1)).strftime("%Y-%m-%dT12:00:00"),
         "salesBlock": i == "5", "logisticModel": "Stock",
         "scheduleLines": lines.get(i, lines["1"])} for i in ids]})


@pytest.fixture
def catalog(tmp_path):
    (tmp_path / V.PRODUCT_FILE).write_text(json.dumps(RECORDS))
    api.calls = []
    return V.ValioCatalog(tmp_path, "https://example.test", "key-123", transport=httpx.MockTransport(api))


def test_allergen_codes_map_to_the_kitchen_vocabulary(catalog):
    p = {pid: catalog.product(pid).value for pid in "12345"}
    assert p["1"]["allergens"] == ["milk"] and "lactose_free" in p["1"]["properties"]
    assert "nuts" in p["2"]["allergens"]  # almond alone still counts as nuts
    assert p["3"]["traces"] == ["sesame"]
    assert p["4"]["allergens"] is None and p["4"]["traces"] is None  # an empty group is unknown, not "none"
    assert p["5"]["allergens"] == [] and p["5"]["traces"] == []
    assert p["1"]["product_group"] == "10400" and p["1"]["supplier"] == "TEST DAIRY"


def test_pack_is_the_content_of_one_sales_unit(catalog):
    assert catalog.product("1").value["pack"] == {"amount": 1.0, "unit": "l"}
    assert catalog.product("6").value["pack"] == {"amount": 5.0, "unit": "l"}  # a crate of five


def test_search_needs_every_word_and_ignores_hyphens(catalog):
    assert {p["id"] for p in catalog.search("lactose-free milk").value} == {"1", "6"}
    assert catalog.search("oat drink").value == []
    assert catalog.product("999").value is None


def test_price_and_availability_come_from_the_two_real_endpoints(catalog):
    a = catalog.availability("1", 24, NEEDED).value
    assert (a["unit_price_eur"], a["available_units"], a["quantity_known"]) == (1.5, 10, True)
    # The API gives a date and no time. Changed 14 Nov (worker 3): no arrival time is invented any more,
    # the delivery is the bare availableDate and the deadline check compares dates.
    assert a["delivery"] == "2026-11-16" and a["delivery_time_known"] is False
    assert {c[0] for c in api.calls} == {"/api/price", "/api/availability"}
    assert {c[1] for c in api.calls} == {"key-123"}


def test_supplier_stock_blocked_sales_and_missing_price(catalog):
    assert catalog.availability("2", 24, NEEDED).value["quantity_known"] is False  # 999999 means unknown
    assert catalog.availability("2", 24, NEEDED).value["available_units"] == 24
    no_line = catalog.availability("3", 24, NEEDED).value
    assert (no_line["unit_price_eur"], no_line["available_units"], no_line["delivery"]) == (None, 0, None)
    assert catalog.availability("5", 24, NEEDED).value["available_units"] == 0  # sales block


def test_prefetch_serves_each_product_once(catalog):
    catalog.prefetch(["1", "2"], NEEDED)
    n = len(api.calls)
    catalog.availability("1", 5, NEEDED)
    assert len(api.calls) == n  # served from the batch
    catalog.availability("1", 5, NEEDED)
    assert len(api.calls) > n  # the recheck asks the API again


def test_orders_live_in_the_gateway_and_one_key_is_one_order(catalog):
    lines = [{"product_id": "1", "quantity": 2}]
    a, b = catalog.place_order(lines, "run1"), catalog.place_order(lines, "run1")
    assert a.value["order_id"] == b.value["order_id"] and "no order endpoint" in a.endpoint
    assert not [c for c in api.calls if "order" in c[0]]


def test_make_client_picks_the_catalog(monkeypatch, tmp_path):
    monkeypatch.setenv("CATALOG", "local")
    assert isinstance(V.make_client(), V.LocalCatalog)
    monkeypatch.setenv("CATALOG", "valio")
    monkeypatch.delenv("VALIO_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        V.make_client()
    monkeypatch.setenv("CATALOG", "nonsense")
    with pytest.raises(ValueError):
        V.make_client()


def test_unknown_allergen_data_follows_the_rule_set(catalog):
    rules, ctx = load_rules(), Context(slot_group="x", needed_by=NEEDED)
    tomato, bar = catalog.product("4").value, catalog.product("2").value
    assert check_line(Line(tomato, 1, 1.0, NEEDED), rules, ctx).result == FORBIDDEN  # version 3 behaviour
    to_person = dict(rules, unknown_allergens="needs_approval")
    d = check_line(Line(tomato, 1, 1.0, NEEDED), to_person, ctx)
    assert d.result == NEEDS_APPROVAL and "no allergen data" in d.reasons[0]
    assert check_line(Line(bar, 1, 1.0, NEEDED), to_person, ctx).result == FORBIDDEN  # nuts stay forbidden


def test_an_exact_product_id_can_be_an_approved_substitute(catalog):
    rules = dict(load_rules(), approved_substitute_groups={"milk": ["6"]})
    ctx = Context(slot_group="milk", needed_by=NEEDED)
    crate = catalog.product("6").value
    assert check_line(Line(crate, 1, 1.0, NEEDED, is_usual=False), rules, ctx).result != NEEDS_APPROVAL


def test_conflicting_fields_are_reported():
    raw = record("7", "x", [("AM", "CONTAINS")], free=["AM"])
    assert read_allergens(raw)["allergen_conflicts"] == ["milk"]


@pytest.mark.skipif(not REAL.exists(), reason="the Valio product file is not on this machine")
def test_real_file_maps_the_demo_products():
    c = V.ValioCatalog(REAL.parent, "https://example.test", "k", transport=httpx.MockTransport(api))
    milk = c.product("463335").value
    assert milk["allergens"] == ["milk"] and "lactose_free" in milk["properties"]
    assert milk["pack"] == {"amount": 1.0, "unit": "l"} and milk["product_group"] == "10400"
    rules, ctx = load_rules(), Context(slot_group="x", needed_by=NEEDED)
    daim = c.product("460835").value  # lists almond without the general nut code
    assert check_line(Line(daim, 1, 1.0, NEEDED), rules, ctx).result == FORBIDDEN


# ---------- the whole pipeline on the real product file, with a stand-in for the live API ----------

VALIO_RULES = Path(__file__).resolve().parent.parent / "rules" / "school_kitchen_valio.yaml"


def stock_of(amounts, price=1.2):
    """A stand-in for the two live calls: a fixed price, and the stock given per product (default 500)."""
    def handler(request: httpx.Request) -> httpx.Response:
        ids = json.loads(request.content)["products"]
        if request.url.path == "/api/price":
            return httpx.Response(200, json={"customerId": "123", "currency": "EUR", "pricingDate": "x", "products": [
                {"productId": i, "quantity": 1.0, "netPriceFound": False, "error": False,
                 "catalogPrice": {"value": price, "valueWithTax": price, "validFrom": "a", "validTo": "b", "currency": "EUR"}}
                for i in ids]})
        # Today, so the date is before every "next weekday 07:00" deadline. Changed 14 Nov (worker 3): a delivery
        # on the needed day with no arrival time now goes to a person, and "tomorrow" is the needed day Sun-Thu.
        day = datetime.now(TZ).strftime("%Y-%m-%dT00:00:00")
        return httpx.Response(200, json={"timeNow": "x", "requestedDate": "x", "products": [
            {"productId": i, "closingTime": None, "salesBlock": False, "logisticModel": "Stock",
             "scheduleLines": [{"salesBlock": False, "availableAmount": float(amounts.get(i, 500)), "availableDate": day,
                                "salesUnit": "ST"}]} for i in ids]})
    return handler


def real_agent(tmp_path, amounts):
    from backend.agent import Agent
    from backend.audit import AuditLog
    catalog = V.ValioCatalog(REAL.parent, "https://example.test", "k", transport=httpx.MockTransport(stock_of(amounts)))
    return Agent(catalog, load_rules(VALIO_RULES), AuditLog(tmp_path / "a.sqlite"))


@pytest.mark.skipif(not REAL.exists(), reason="the Valio product file is not on this machine")
def test_real_catalog_the_usual_milk_is_ordered_alone(tmp_path):
    agent = real_agent(tmp_path, {})
    r = agent.handle_event({"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0})
    assert r["status"] == "ordered" and r["total_eur"] == 28.8  # 24 cartons at 1.20
    (order,) = agent.orders.values()
    assert [(l["product_id"], l["quantity"]) for l in order["lines"]] == [("463335", 24)]


@pytest.mark.skipif(not REAL.exists(), reason="the Valio product file is not on this machine")
def test_real_catalog_short_stock_is_filled_with_an_approved_carton_of_the_same_size(tmp_path):
    agent = real_agent(tmp_path, {"463335": 10})
    r = agent.handle_event({"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0})
    assert r["status"] == "ordered"  # an approved substitute within the limits needs no person
    (order,) = agent.orders.values()
    lines = {l["product_id"]: l["quantity"] for l in order["lines"]}
    assert lines["463335"] == 10 and sum(lines.values()) == 24
    assert set(lines) - {"463335"} <= {"464306", "464401", "464398"}
    left_out = next(e for e in agent.log.entries() if e["step"] == "candidates")["detail"]["left_out"]
    assert left_out["other_pack_size"]  # crates and 10 l boxes are not mixed with 1 l cartons


@pytest.mark.skipif(not REAL.exists(), reason="the Valio product file is not on this machine")
def test_real_catalog_valios_example_request_goes_to_the_chef(tmp_path):
    agent = real_agent(tmp_path, {})
    r = agent.handle_request("1,000 litres of lactose-free milk by Monday")
    assert r["status"] == "needs_approval"  # 1,000 cartons are far over the 150 euro limit
    for option in agent.open_approvals()[0]["options"]:
        assert sum(l["quantity"] for l in option["lines"]) == 1000


@pytest.mark.skipif(not REAL.exists(), reason="the Valio product file is not on this machine")
def test_real_catalog_a_nut_product_is_refused_and_an_unknown_one_goes_to_a_person(tmp_path):
    agent = real_agent(tmp_path, {})
    assert agent.handle_request("20 forced item", "460835")["status"] == "forbidden"  # almonds, no general nut code
    unknown = next(p["id"] for p in agent.catalog.products.values() if p["allergens"] is None)
    assert agent.handle_request("20 forced item", unknown)["status"] == "needs_approval"


# ---------- the ingredient text scan ----------

def texts(*lines):
    return {"synkkaData": {"keyIngredients": [{"value": line, "language": "en"} for line in lines]}}


@pytest.mark.parametrize("text,found", [
    ("May contain traces of nuts.", ["nuts"]),
    ("Saattaa sisältää pähkinää ja seesamia", ["nuts", "sesame"]),
    ("hasselnötter, jordnötter", ["nuts", "peanuts"]),
    ("pine nuts, tahini", ["nuts", "sesame"]),
    ("coconut, nutmeg, peanut oil", ["peanuts"]),  # coconut and nutmeg are not tree nuts
    ("Nut free. Without sesame.", []),             # the simplest denials
    ("ei sisällä pähkinää", []),
    ("pähkinätön", []),
    ("MILK, cream", []),                           # the scan knows nuts, peanuts and sesame only
])
def test_text_scan(text, found):
    from backend.allergens import text_allergens
    assert text_allergens(texts(text)) == found


def test_the_scan_says_which_banned_allergens_it_cannot_find():
    from backend.allergens import unscanned
    assert unscanned(["Nuts", "sesame"]) == [] and unscanned(["milk", "nuts"]) == ["milk"]


@pytest.mark.skipif(not REAL.exists(), reason="the Valio product file is not on this machine")
def test_real_catalog_counts_under_the_school_bans():
    """The numbers in the handbook. They change only when the data, the code map or the scan changes."""
    from backend.dirty_data import count
    c = count(V.ValioCatalog(REAL.parent, "https://example.test", "k"), load_rules(VALIO_RULES))
    assert c["products"] == 17546
    assert c["results"] == {"forbidden": 1962, "needs_approval": 8149, "allowed": 7435}
    assert c["to_a_person"] == {"no allergen data": 8110, "undeclared": 22, "only the ingredient text names it": 17}
    assert c["no_data"] == {"non_food": 3974, "food": 4136, "suppliers": 648, "suppliers_with_a_gap": 459,
                            "suppliers_for_half": 29, "suppliers_with_no_data_at_all": 264, "their_products": 4983}


def test_a_product_in_a_non_food_group_needs_no_allergen_data():
    """A paper towel has no allergen data. The rule set can name its group, so nobody reads its label."""
    from backend import rules as R
    rules = {"banned_allergens": ["nuts"], "unknown_allergens": "needs_approval", "non_food_groups": ["63"]}
    towel = {"id": "t", "name": "Test paper towel", "product_group": "63010", "allergens": None, "traces": None}
    soup = towel | {"name": "Test soup", "product_group": "17020"}
    assert R.safety(towel, rules) == ([], [])
    assert "no allergen data" in R.safety(soup, rules)[1][0]
    assert R.safety(towel | {"allergens": ["nuts"], "traces": []}, rules)[0]  # a declared ban still forbids
    assert R.safety(towel, rules | {"non_food_groups": []})[1]
