"""Enrolment links a physical product to a catalogue item only by exact GTIN or by the owner's choice."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import enrol_product as e  # noqa: E402

ITEMS = [  # synthetic catalogue records in the downloaded file's shape (no sponsor data in tests)
    {"id": "900001", "salesUnit": "KI", "salesUnitGtin": "16400000000019",
     "synkkaData": {"gtin": "6400000000019", "names": [{"language": "fi", "value": "Testisipsi 150 g"}]},
     "units": [{"unitId": "BAG", "gtin": "6400000000019"}]},
    {"id": "900002", "salesUnit": "ST", "salesUnitGtin": "6400000000026",
     "synkkaData": {"gtin": "6400000000026", "names": [{"language": "fi", "value": "Testisipsi 175 g"}]}, "units": []},
]


def test_check_digit():
    assert e.gtin_ok("6400000000019") and not e.gtin_ok("6400000000018") and not e.gtin_ok("12345")


def test_only_an_exact_gtin_matches_never_a_similar_product():
    m = e.gtin_matches("6400000000019", ITEMS)
    assert [x["code"] for x in m] == ["900001"] and "BAG" in m[0]["level"]
    assert e.gtin_matches("6400000000030", ITEMS) == []  # no match: nothing similar is offered
    assert [x["code"] for x in e.gtin_matches("16400000000019", ITEMS)] == ["900001"]  # case GTIN, same product
