"""Open Food Facts as a second source: it adds a picture and can only add an allergen warning."""
import pytest

from backend import openfoodfacts as off
from backend import rules as R

RULES = {"version": 1, "banned_allergens": ["nuts"], "unknown_allergens": "needs_approval"}


@pytest.fixture(autouse=True)
def empty_cache():
    off._cache.clear()


def answer(allergens=(), traces=(), status=1):
    calls = []

    def get(url):
        calls.append(url)
        return {"status": status, "product": {"product_name": "Wafer", "brands": "B", "allergens_tags": list(allergens),
                                              "traces_tags": list(traces), "image_front_small_url": "https://example.org/p.jpg"}}
    get.calls = calls
    return get


def product(**more):
    return {"id": "1", "name": "Wafer", "gtin": "06400000000017", "allergens": [], "traces": []} | more


def test_an_allergen_that_only_the_second_source_lists_goes_to_a_person_and_never_forbids():
    p = off.enrich(product(), answer(allergens=["en:nuts", "en:milk", "en:unknown-thing"]))
    assert p["second_source_allergens"] == ["milk", "nuts"] and p["picture"] == "https://example.org/p.jpg"
    forbidden, person = R.safety(p, RULES)
    assert forbidden == [] and len(person) == 1 and "Open Food Facts lists nuts" in person[0]


def test_an_empty_list_in_the_second_source_clears_nothing():
    p = off.enrich(product(allergens=["nuts"]), answer())
    forbidden, person = R.safety(p, RULES)
    assert len(forbidden) == 1 and person == []


def test_an_allergen_that_both_sources_list_adds_no_second_warning():
    p = off.enrich(product(traces=["nuts"]), answer(traces=["en:nuts"]))
    forbidden, person = R.safety(p, RULES)
    assert len(forbidden) == 1 and person == []


def test_a_product_that_the_database_does_not_have_stays_as_it_is():
    p = off.enrich(product(), answer(status=0))
    assert "picture" not in p and R.safety(p, RULES) == ([], [])


def test_a_lookup_is_kept_and_a_failed_one_changes_nothing():
    get = answer()
    off.lookup("6400000000017", get); off.lookup("06400000000017", get)
    assert len(get.calls) == 1

    def down(url):
        raise OSError("no network")
    p = off.enrich(product(gtin="6400000000024"), down)
    assert "picture" not in p and R.safety(p, RULES) == ([], [])


def test_a_short_or_missing_number_is_never_sent():
    get = answer()
    assert off.lookup("", get) is None and off.lookup("1234", get) is None and get.calls == []


def test_the_label_check_is_asked_again_when_the_second_source_changes():
    assert R.label_key(product()) != R.label_key(product(second_source_allergens=["nuts"]))
