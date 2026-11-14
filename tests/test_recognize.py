"""Recognition from a picture: the model reads, plain code matches. The model is a stand-in here."""
import pytest
from fastapi.testclient import TestClient

from backend import recognize

PRODUCTS = [
    {"id": "1", "name": "Lactose free milk drink 1 l", "supplier": "Northfarm", "gtin": "06400000000017"},
    {"id": "2", "name": "Lactose free milk drink 1 l organic", "supplier": "Northfarm", "gtin": "6400000000024"},
    {"id": "3", "name": "Rye bread 500 g", "supplier": "Bakers", "gtin": "6400000000031"},
]


def answers(monkeypatch, text):
    monkeypatch.setattr(recognize, "ask_model", lambda image: text)


def test_a_field_that_fails_its_check_is_none(monkeypatch):
    answers(monkeypatch, 'Here: {"brand": "Northfarm", "name": ["x"], "size": "1 l", "barcode": "12 3", "best_before": "soon"}')
    assert recognize.read_pack("x") == {"brand": "Northfarm", "name": None, "size": "1 l", "barcode": None, "best_before": None}


def test_an_answer_without_json_reads_nothing(monkeypatch):
    answers(monkeypatch, "I cannot see a pack.")
    assert recognize.match(recognize.read_pack("x"), PRODUCTS) == ("none", [])


def test_the_barcode_number_wins_over_the_name():
    how, found = recognize.match({"name": "Rye bread 500 g", "barcode": "6400000000017"}, PRODUCTS)
    assert how == "barcode" and [p["id"] for p, _ in found] == ["1"]


def test_an_unknown_barcode_falls_back_to_the_name():
    how, found = recognize.match({"brand": "Bakers", "name": "rye bread", "size": "500 g", "barcode": "99999999"}, PRODUCTS)
    assert how == "name" and found[0][0]["id"] == "3"


def test_the_shorter_catalog_name_comes_first_on_an_equal_score():
    how, found = recognize.match({"name": "lactose free milk drink", "size": "1 l"}, PRODUCTS)
    assert how == "name" and [p["id"] for p, _ in found] == ["1", "2"] and found[0][1] == 1.0


def test_one_word_is_not_enough_to_name_a_product():
    assert recognize.match({"name": "milk"}, PRODUCTS) == ("none", [])


def test_the_endpoint_answers_with_the_rules_of_this_kitchen_and_records_nothing(monkeypatch):
    from backend import app as A
    product = next(p for p in A.catalog.products.values() if p.get("gtin"))
    answers(monkeypatch, '{"brand": null, "name": null, "size": null, "barcode": "%s", "best_before": "2026-12-01"}' % product["gtin"])
    client = TestClient(A.app)
    before = len(client.get("/log?format=json&limit=500").json())
    out = client.post("/recognize", json={"image_b64": "A" * 200}).json()
    assert out["how"] == "barcode" and out["read"]["best_before"] == "2026-12-01"
    assert out["matches"][0]["product"]["id"] == product["id"] and out["matches"][0]["verdict"] in ("allowed", "read_label", "forbidden")
    assert len(client.get("/log?format=json&limit=500").json()) == before


def test_no_key_gives_a_clear_refusal(monkeypatch):
    from backend import app as A

    def no_key(image):
        raise RuntimeError("VERDA_INFERENCE_API_KEY is not set")
    monkeypatch.setattr(recognize, "ask_model", no_key)
    r = TestClient(A.app).post("/recognize", json={"image_b64": "A" * 200})
    assert r.status_code == 503 and "not set" in r.json()["detail"]
