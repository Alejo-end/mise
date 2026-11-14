"""Quotes and orders on the Valio adapter: dates without times, cut-offs, lot sizes, unknown charges,
quote expiry, dry orders, retries and recordings.

The first part uses synthetic records and a stand-in API whose answers have the shape measured on
14 Nov 2026 (offsets like +0200, a bare availableDate, 9999999999 for Stock amounts). No real Valio
record is in this file. The last part replays recorded answers of the sponsor's mock API; it is
skipped when the product file or the recordings are not on this machine.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import valio as V
from backend.agent import Agent, delivery_at, in_time, lot_ok
from backend.audit import AuditLog
from backend.gateway import comparison, make_router
from backend.rules import ALLOWED, NEEDS_APPROVAL, TZ, Context, Line, check, load_rules
from backend.signing import Signer, verify

VALIO_RULES = Path(__file__).resolve().parent.parent / "rules" / "school_kitchen_valio.yaml"
REAL = Path(__file__).resolve().parent.parent / "data" / "valio" / V.PRODUCT_FILE
RECORDINGS = REAL.parent / "api-recordings"


def record(pid, name, sales="ST", lot=0.01, litres=None, grams=None, deposits=(), allergen=(("AM", "CONTAINS"),)):
    each = {"synkkaPackagingType": "BASE_UNIT_OR_EACH"}
    if litres:
        each["volume"] = {"unit": "LTR", "value": litres}
    if grams:
        each["netWeight"] = {"unit": "GRM", "value": grams}
    return {
        "id": pid, "salesUnit": sales, "baseUnit": "ST", "category": "10400", "allowedLotSize": lot,
        "vendorName": "TEST DAIRY", "countryOfOrigin": "fi", "deposits": list(deposits),
        "units": [{"unitId": "ST", "gtin": "0000000000017", "sizeInBaseUnits": 1.0},
                  {"unitId": "KI", "sizeInBaseUnits": 5.0}],
        "synkkaData": {
            "gtin": "0000000000017", "names": [{"value": name, "language": "en"}], "variableUnit": False,
            "unitConversions": [each],
            "classifications": [{"type": "ENUM", "name": "allergen", "values": [{"id": c, "unit": u} for c, u in allergen]}],
        },
    }


RECORDS = [
    record("1", "Test milk 1 l", litres=1.0, grams=1025.0),
    record("2", "Test bread 500 g", grams=500.0),
    record("3", "Test steak", sales="KG", lot=2.8),
    record("4", "Test water 12 x 0.5 l crate", sales="KI", litres=0.5, deposits=[{"material": "3", "unit": "ST", "quantity": 12.0}]),
    record("5", "Test juice 1 l", litres=1.0),  # a volume only: no established mass
    record("6", "Test milk 1 l pairs", litres=1.0, grams=1025.0, lot=2.0),  # sold in steps of two
]


class FakeApi:
    """A stand-in for POST /api/price and POST /api/availability in the measured shape."""

    def __init__(self):
        self.calls = []
        self.now = datetime.now(TZ)
        self.day = self.now.date().isoformat()  # today: before every deadline the tests use
        self.amounts = {}  # product -> availableAmount; default 9999999999 (measured for Stock products)
        self.closing = {}  # product -> closingTime; default: tomorrow 17:00
        self.units = {}  # product -> salesUnit the API answers; default: the file's
        self.status = []  # HTTP status codes to answer before the real answer, e.g. [429]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.url.path, request.headers.get("x-api-key")))
        if self.status:
            return httpx.Response(self.status.pop(0), json={"detail": "slow down"})
        ids = json.loads(request.content)["products"]
        fmt = "%Y-%m-%dT%H:%M:%S%z"
        if request.url.path == "/api/price":
            price = lambda v: {"value": v, "valueWithTax": round(v * 1.135, 2), "validFrom": "2026-09-01T00:00:00+0300",
                               "validTo": "2026-12-31T00:00:00+0200", "currency": "EUR"}
            values = {"1": 1.4, "2": 2.78, "3": 13.11, "4": 24.69, "5": 1.2, "6": 1.5}
            return httpx.Response(200, json={"customerId": "123", "currency": "EUR", "pricingDate": "x", "products": [
                {"productId": i, "quantity": 1.0, "catalogPrice": price(values[i]), "tax": {"taxRate": 13.5, "taxValue": 0.1},
                 "netPriceFound": True, "error": False} for i in ids]})
        tomorrow = (self.now + timedelta(days=1)).replace(hour=17, minute=0, second=0, microsecond=0).strftime(fmt)
        sales = {r["id"]: r["salesUnit"] for r in RECORDS}
        return httpx.Response(200, json={"timeNow": self.now.strftime(fmt), "requestedDate": "x", "products": [
            {"productId": i, "closingTime": self.closing.get(i, tomorrow), "salesBlock": False, "logisticModel": "Stock",
             "scheduleLines": [{"salesBlock": False, "availableAmount": float(self.amounts.get(i, 9999999999)),
                                "availableDate": self.day,
                                "salesUnit": self.units.get(i, sales[i])}]} for i in ids]})


@pytest.fixture
def api():
    return FakeApi()


@pytest.fixture
def catalog(tmp_path, api):
    (tmp_path / V.PRODUCT_FILE).write_text(json.dumps(RECORDS))
    (tmp_path / V.RELIABILITY_FILE).write_text("productId,deliveryReliability\n1,0.97\n")
    (tmp_path / V.CARBON_FILE).write_text("productId,AVG kg CO2-eq/kg\n1,1.44\n5,0.9\n")
    c = V.ValioCatalog(tmp_path, "https://example.test", "key-123", transport=httpx.MockTransport(api),
                       record_dir=tmp_path / "rec")
    c.min_gap_s = c.retry_wait_s = 0
    return c


@pytest.fixture
def agent(tmp_path, catalog):
    rules = dict(load_rules(VALIO_RULES), unknown_allergens="needs_approval", must_have=[],
                 targets=[{"slot": 3, "product": "test milk 1 l", "product_id": "1", "group": "milk",
                           "target_units": 24, "reorder_below": 6}])
    return Agent(catalog, rules, AuditLog(tmp_path / "a.sqlite"), restock_min_s=0)


def needed(api, days=2):
    return datetime.combine((api.now + timedelta(days=days)).date(), datetime.min.time(), tzinfo=TZ).replace(hour=7)


# ---------- the adapter ----------

def test_the_delivery_is_the_available_date_and_no_arrival_time_is_invented(catalog, api):
    a = catalog.availability("1", 24, needed(api)).value
    assert a["delivery"] == api.day and a["delivery_time_known"] is False
    assert a["closing_time"].endswith("17:00:00+02:00")
    assert (a["unit_price_eur"], a["unit_price_with_tax_eur"], a["tax_rate_pct"]) == (1.4, 1.59, 13.5)
    assert a["price_valid_to"] == "2026-12-31T00:00:00+02:00"
    assert a["available_units"] == 24 and a["quantity_known"] is True  # 9999999999 read as stated
    assert "mock API" in a["source"] and "not a Valio production system" in a["source"]


def test_a_passed_cutoff_makes_the_date_unreachable(catalog, api):
    api.closing["2"] = (api.now - timedelta(days=2)).strftime("%Y-%m-%dT12:30:00+0200")
    a = catalog.availability("2", 10, needed(api)).value
    assert a["delivery"] is None and "cut-off" in a["problem"] and "has passed" in a["problem"]


def test_a_sales_unit_other_than_the_files_is_not_orderable(catalog, api):
    api.units["1"] = "KI"
    a = catalog.availability("1", 10, needed(api)).value
    assert a["delivery"] is None and "KI" in a["problem"]


def test_mass_and_charges_come_only_from_the_file(catalog):
    assert catalog.product("1").value["mass_kg"] == 1.025
    assert catalog.product("3").value["mass_kg"] == 1.0  # sold by the KG
    assert catalog.product("5").value["mass_kg"] is None  # a litre is not turned into a kilogram
    assert catalog.product("4").value["deposits"] == [{"material": "3", "unit": "ST", "quantity": 12.0}]
    assert catalog.product("1").value["delivery_reliability"] == 0.97
    assert catalog.product("2").value["delivery_reliability"] is None  # no row: unknown, not 0 or 1
    assert catalog.product("2").value["carbon_kg_co2e_per_kg"] is None


def test_comparison_informs_and_keeps_unknowns_unknown(catalog):
    c = comparison(catalog.product("1").value, 10)
    assert c["carbon"]["estimate_kg_co2e"] == round(10.25 * 1.44, 2) and c["delivery_reliability"]["value"] == 0.97
    assert "not an on-time delivery probability" in c["delivery_reliability"]["meaning"]
    juice = comparison(catalog.product("5").value, 10)
    assert juice["carbon"]["estimate_kg_co2e"] is None and "mass" in juice["carbon"]["status"]
    bread = comparison(catalog.product("2").value, 10)
    assert bread["delivery_reliability"]["value"] is None and bread["delivery_reliability"]["status"] == "unknown"
    assert bread["carbon"]["estimate_kg_co2e"] is None


def test_429_is_retried_with_backoff_and_at_most_three_times(catalog, api):
    api.status = [429, 503]
    assert catalog.availability("1", 1, needed(api)).value["unit_price_eur"] == 1.4
    assert len(api.calls) == 4 and catalog.calls == 4  # 2 failed tries, then price and availability
    api.status = [429] * 4
    with pytest.raises(httpx.HTTPStatusError):
        catalog.availability("1", 1, needed(api))
    assert len(api.calls) == 8  # one try and three retries, then it gives up


def test_every_live_call_is_recorded_without_the_key(catalog, api, tmp_path):
    catalog.availability("1", 1, needed(api))
    files = sorted((tmp_path / "rec").glob("*.json"))
    assert [f.name.split("-", 1)[1] for f in files] == ["api-price-1p.json", "api-availability-1p.json"]
    for f in files:
        assert "key-123" not in f.read_text() and not f.stat().st_mode & 0o222  # read-only
        assert json.loads(f.read_text())["response"]["status"] == 200


def test_the_call_budget_stops_live_calls(catalog, api):
    catalog.max_calls = 1
    with pytest.raises(RuntimeError, match="budget"):
        catalog.availability("1", 1, needed(api))
    assert len(api.calls) == 1


# ---------- the rule check and the buying path ----------

def test_the_deadline_compares_dates_when_the_source_has_no_time(agent, api):
    rules, d = agent.rules, needed(api)
    ctx = Context(slot_group="milk", needed_by=d)
    milk = agent.catalog.product("1").value
    on_day, known = delivery_at(d.date().isoformat())
    assert known is False
    day_before = check([Line(milk, 1, 1.4, on_day - timedelta(days=1), True, True, False)], rules, ctx)
    assert day_before.result == ALLOWED and any("by date only" in r for r in day_before.reasons)
    # The needed day itself: the source has no hour, so it cannot show "by 07:00". A person confirms it,
    same_day = check([Line(milk, 1, 1.4, on_day, True, True, False)], rules, ctx)
    assert same_day.result == NEEDS_APPROVAL and any("no arrival time" in r and "07:00" in r for r in same_day.reasons)
    # unless the signed rule set says a delivery on the needed day is in time.
    permitted = dict(rules, delivery=dict(rules["delivery"], date_only_same_day="allowed"))
    assert check([Line(milk, 1, 1.4, on_day, True, True, False)], permitted, ctx).result == ALLOWED
    late = check([Line(milk, 1, 1.4, on_day + timedelta(days=1), True, True, False)], rules, ctx)
    assert late.result == NEEDS_APPROVAL and any("after the needed day" in r for r in late.reasons)
    assert not any("in time by date only" in r for r in late.reasons)
    assert in_time(d.date().isoformat(), d) and not in_time((d + timedelta(days=1)).date().isoformat(), d)


def test_a_quantity_off_the_lot_size_is_refused(agent, api):
    q = agent.quote_exact("3", 3, needed(api))
    assert q["status"] == "invalid_quantity" and "2.8" in q["reason"]
    assert agent.quote_exact("3", 28, needed(api))["status"] == "quote"  # 10 lots of 2.8 KG
    assert lot_ok(24, 0.01) and lot_ok(28, 2.8) and not lot_ok(3, 2.8)


def test_a_passed_cutoff_is_not_orderable_and_says_why(agent, api):
    api.closing["1"] = (api.now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S+0200")
    q = agent.quote_exact("1", 5, needed(api))
    assert q["status"] == "not_orderable" and "cut-off" in q["reason"]


def test_charges_stay_unknown_and_out_of_the_total(agent, api):
    q = agent.quote_exact("4", 2, needed(api))
    assert q["total_eur"] == 49.38  # goods only: 2 x 24.69
    assert q["charges"]["delivery"].startswith("unknown")
    assert q["charges"]["deposits"]["4"].startswith("unknown") and "material 3" in q["charges"]["deposits"]["4"]
    r = agent.buy_exact("4", 2, "run4", deadline=needed(api))
    assert r["status"] == "needs_approval"  # not the usual product of any slot
    option = agent.open_approvals()[0]["options"][0]
    assert "Delivery charge unknown" in option["trade_off"] and "Deposit charge unknown" in option["trade_off"]
    assert "mock API" in option["source"]


def test_an_order_is_recorded_by_the_gateway_and_never_sent_to_valio(agent, api):
    r = agent.handle_event({"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0})
    assert r["status"] == "ordered" and r["sent_to_valio"] is False and "not sent to Valio" in r["note"]
    entry = next(e for e in agent.log.entries() if e["step"] == "order")
    assert "not sent to Valio" in entry["detail"]["summary"] and entry["detail"]["sent_to_valio"] is False
    assert "Delivery charge unknown" in entry["detail"]["summary"]
    assert {path for path, _ in api.calls} == {"/api/price", "/api/availability"}
    (order,) = agent.orders.values()
    assert order["lines"][0]["delivery"] == api.day and order["lines"][0]["closing_time"]


def test_a_moved_delivery_date_at_the_recheck_goes_to_a_person(agent, api):
    q = agent.quote_exact("1", 5, needed(api))
    opt_line = q["line"]
    api.day = (api.now + timedelta(days=5)).date().isoformat()  # the date moved after the quote
    r = agent._order([opt_line], q["ctx"], "run-moved", None, approved_by=None, approved_total=q["total_eur"])
    assert r["status"] == "needs_approval"
    entry = next(e for e in agent.log.entries() if e["step"] == "recheck")
    assert any("delivery" in c for c in entry["detail"]["changes"])


# ---------- the gateway on the Valio adapter ----------

class Licences:
    def valid(self, token):
        return {"id": "L1", "agent": "test agent", "scope": {"spend_limit_eur": 150.0, "product_groups": ["milk"]}} \
            if token == "ok" else None


@pytest.fixture
def gateway(tmp_path, agent):
    app = FastAPI()
    signer = Signer(tmp_path / "key.pem")
    app.include_router(make_router(agent, agent.catalog, Licences(), agent.log, signer))
    return TestClient(app), signer


def test_a_gateway_quote_ends_at_the_cutoff_and_names_what_is_unknown(gateway, api):
    client, signer = gateway
    cutoff = api.now + timedelta(minutes=5)
    api.closing["1"] = cutoff.strftime("%Y-%m-%dT%H:%M:%S+0200")
    q = client.post("/gateway/quotes", json={"product_id": "1", "quantity": 10, "needed_by": needed(api).date().isoformat()},
                    headers={"Authorization": "Bearer ok"}).json()
    assert q["valid_until_set_by"] == "the order cut-off (closingTime)"
    assert datetime.fromisoformat(q["valid_until"]) <= cutoff
    assert q["delivery"] == api.day and q["delivery_time"].startswith("not supplied")
    assert q["delivery_charge"].startswith("unknown") and q["total_covers"] == "goods only, without tax"
    assert q["price_basis"].startswith("per sales unit, without tax") and q["sales_unit"] == "ST"
    assert "mock API" in q["source"] and verify(q, signer.public_key)
    assert q["comparison"]["delivery_reliability"]["value"] == 0.97


def test_a_gateway_quote_off_the_lot_size_gets_422(gateway, api):
    client, _ = gateway
    r = client.post("/gateway/quotes", json={"product_id": "3", "quantity": 3}, headers={"Authorization": "Bearer ok"})
    assert r.status_code == 422 and "lot size" in r.json()["detail"]


def test_a_gateway_order_answer_says_it_was_not_sent_to_valio(gateway, api):
    client, signer = gateway
    auth = {"Authorization": "Bearer ok"}
    q = client.post("/gateway/quotes", json={"product_id": "1", "quantity": 10, "needed_by": needed(api).date().isoformat()},
                    headers=auth).json()
    r = client.post("/gateway/orders", json={"quote_id": q["quote_id"], "idempotency_key": "k1"}, headers=auth)
    assert r.status_code == 201 and r.json()["sent_to_valio"] is False and verify(r.json(), signer.public_key)


def test_an_expired_gateway_quote_is_refused(gateway, api):
    client, _ = gateway
    auth = {"Authorization": "Bearer ok"}
    api.closing["1"] = (api.now + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S+0200")
    q = client.post("/gateway/quotes", json={"product_id": "1", "quantity": 10, "needed_by": needed(api).date().isoformat()},
                    headers=auth).json()
    import time
    time.sleep(1.5)
    r = client.post("/gateway/orders", json={"quote_id": q["quote_id"], "idempotency_key": "k2"}, headers=auth)
    assert r.status_code == 410


# ---------- replays of the recorded answers of the sponsor's mock API ----------

def recorded(path: str, product: str, day: str = "2026-11-16") -> dict | None:
    """The first recorded 200 answer of `path` for delivery `day` that covers `product`.

    The first, so that recordings made later (other dates, other times) never change these replays.
    """
    if not RECORDINGS.is_dir():
        return None
    for f in sorted(RECORDINGS.glob("*.json")):
        e = json.loads(f.read_text())
        req, res = e.get("request") or {}, e.get("response") or {}
        body = req.get("json") or {}
        if (req.get("url", "").endswith(path) and res.get("status") == 200 and body.get("requestedDate") == day
                and product in (body.get("products") or [])):
            return e
    return None


def replay(request: httpx.Request) -> httpx.Response:
    ids = json.loads(request.content)["products"]
    e = recorded(request.url.path, ids[0])
    body = dict(e["response"]["json"])
    body["products"] = [p for p in body["products"] if p["productId"] in ids]
    return httpx.Response(200, json=body)


needs_replay = pytest.mark.skipif(not REAL.exists() or recorded("/api/availability", "463335") is None,
                                  reason="the Valio product file or the recorded API answers are not on this machine")


@needs_replay
def test_replay_the_recorded_answers_for_the_demo_products():
    c = V.ValioCatalog(REAL.parent, "https://example.test", "k", transport=httpx.MockTransport(replay))
    c.min_gap_s = 0
    monday = datetime(2026, 11, 16, 7, 0, tzinfo=TZ)
    milk = c.availability("463335", 24, monday).value
    assert (milk["unit_price_eur"], milk["delivery"], milk["sales_unit"]) == (1.4, "2026-11-16", "ST")
    assert milk["quantity_known"] and milk["problem"] is None
    crate = c.availability("462921", 200, monday).value
    assert crate["sales_unit"] == "KI" and crate["unit_price_eur"] == 10.97
    # Recorded 14 Nov 05:49: the rye bread's cut-off was 12 Nov 12:30, so 16 Nov cannot be reached.
    bread = c.availability("477032", 30, monday).value
    assert bread["delivery"] is None and "cut-off" in bread["problem"]


def test_a_moved_delivery_date_after_an_approval_goes_back_to_a_person(agent, api):
    q = agent.quote_exact("1", 5, needed(api))
    api.day = (api.now + timedelta(days=1)).date().isoformat()  # earlier, still in time, but not what was approved
    r = agent._order([q["line"]], q["ctx"], "run-approved", None, approved_by="Head chef", approved_total=q["total_eur"])
    assert r["status"] == "needs_approval" and not agent.orders
    reasons = agent.open_approvals()[0]["options"][0]["reasons"]
    assert reasons == ["the delivery date changed after the quote, so a person must look again"]


def test_a_late_line_is_not_called_in_time_by_date(agent, api):
    d = needed(api)
    milk = agent.catalog.product("1").value
    late = check([Line(milk, 1, 1.4, delivery_at((d + timedelta(days=1)).date().isoformat())[0], True, True, False)],
                 agent.rules, Context(slot_group="milk", needed_by=d))
    assert not any("in time by date only" in r for r in late.reasons)


def test_a_recording_never_overwrites_and_never_breaks_a_call(catalog, api, tmp_path):
    rec = tmp_path / "rec"
    rec.mkdir()
    (rec / "001-old.json").write_text("{}")
    (rec / "001-old.json").chmod(0o444)
    (rec / "003-other.json").write_text("{}")  # a gap in the numbering
    catalog.availability("1", 1, needed(api))
    names = sorted(p.name for p in rec.glob("*.json"))
    assert len(names) == 4 and len({n[:3] for n in names}) == 4
    rec.chmod(0o555)  # read-only folder: the call still works
    try:
        assert catalog.availability("1", 1, needed(api)).value["unit_price_eur"] == 1.4
    finally:
        rec.chmod(0o755)


def test_a_split_order_respects_each_lines_lot_size(agent, api):
    """Codex finding, 14 Nov: 1 usual unit + 2 units of a substitute sold in pairs fill a need of 3."""
    agent.rules["approved_substitute_groups"] = {"milk": ["6"]}
    api.amounts = {"1": 1}
    target = agent.rules["targets"][0]
    r = agent.buy(target, 0, 3, "run-split", standing=False)
    assert r["status"] == "ordered"
    (order,) = agent.orders.values()
    assert {l["product_id"]: l["quantity"] for l in order["lines"]} == {"1": 1, "6": 2}
    api.amounts = {"1": 2}  # a need of 3 with 2 usual units: one pair would overshoot, so 1 short
    agent.buy(target, 0, 3, "run-short", standing=False)
    option = agent.open_approvals()[0]["options"][0]
    assert option["label"] == "partial" and {l["product_id"]: l["quantity"] for l in option["lines"]} == {"1": 2}


def test_the_order_state_keeps_the_dry_run_note_after_a_restart(gateway, api, agent):
    client, _ = gateway
    auth = {"Authorization": "Bearer ok"}
    q = client.post("/gateway/quotes", json={"product_id": "1", "quantity": 10, "needed_by": needed(api).date().isoformat()},
                    headers=auth).json()
    run = client.post("/gateway/orders", json={"quote_id": q["quote_id"], "idempotency_key": "k3"}, headers=auth).json()["run_id"]
    agent.placed_runs[run] = {k: v for k, v in agent.placed_runs[run].items() if k in ("status", "order_id", "total_eur")}
    state = client.get(f"/gateway/orders/{run}", headers=auth).json()  # as Agent.restore() rebuilds it
    assert state["status"] == "ordered" and state["sent_to_valio"] is False and "not sent to Valio" in state["note"]
