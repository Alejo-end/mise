"""Allergens of a product in the real Valio Aimo data, in the names our rule sets use.

The product file holds GS1 allergen codes in `synkkaData.classifications`, in two groups:
`allergen` (each code with CONTAINS, MAY_CONTAIN or UNDECLARED) and `nonAllergen` (FREE_FROM).

CODES is our reading of the GS1 allergen code list. The data set gives no translations.
Confirm the map with the Valio mentors before a kitchen depends on it.

An empty group never means "no allergens". 46% of the products have no value in either
group, and for those the allergens are unknown.
"""

from __future__ import annotations

import re

# The tree nut kinds. 69 real products list a kind (for example almond) without the general code AN.
NUT_KINDS = ("SA", "SH", "SW", "SC", "SP", "SM", "SR", "ST", "SQ")

CODES = {
    "AM": "milk", "ML": "lactose", "AE": "egg", "AF": "fish", "AC": "crustaceans", "UM": "molluscs",
    "AP": "peanuts", "AN": "nuts", "AS": "sesame", "AY": "soy", "BC": "celery", "BM": "mustard",
    "NL": "lupin", "AU": "sulphites", "AW": "gluten", "UW": "wheat", "GB": "barley", "GO": "oats",
    "NR": "rye", "GS": "spelt", "GK": "kamut", **{code: "nuts" for code in NUT_KINDS},
}
# X99 in the allergen group says that the product has no allergen that must be declared.
# It counts as known data and adds no allergen.
NO_ALLERGENS = "X99"

# A diet claim that contradicts a declared allergen: claim id -> allergen name.
CLAIM_CONTRADICTS = {"FREE_FROM_LACTOSE": "lactose", "FREE_FROM_GLUTEN": "gluten", "FREE_FROM_EGGS": "egg",
                     "FREE_FROM_SOY": "soy", "FREE_FROM_WHEAT": "wheat"}


def allergen_name(code: str) -> str:
    """Our name for a GS1 code. A code we do not know stays as its lower-case code."""
    return CODES.get(code, str(code).lower())


def _groups(raw: dict) -> dict[str, list[dict]]:
    groups = (raw.get("synkkaData") or {}).get("classifications") or []
    return {g.get("name"): g.get("values") or [] for g in groups}


def read_allergens(raw: dict) -> dict:
    """The allergen facts of one raw product record.

    allergens           names with CONTAINS, or None when both groups are empty (unknown)
    traces              names with MAY_CONTAIN, or None when both groups are empty
    undeclared          names the manufacturer marked UNDECLARED
    free_from           names in the nonAllergen group
    allergen_conflicts  names where two fields disagree: declared and free-from at the same time,
                        or a "free from" diet claim for an allergen the product contains
    """
    groups = _groups(raw)
    declared, free = groups.get("allergen", []), groups.get("nonAllergen", [])
    if not declared and not free:
        return {"allergens": None, "traces": None, "undeclared": [], "free_from": [], "allergen_conflicts": []}

    contains, traces, undeclared = set(), set(), set()
    for v in declared:
        code = v.get("id")
        if code == NO_ALLERGENS:
            continue
        name, unit = allergen_name(code), v.get("unit")
        if unit == "CONTAINS":
            contains.add(name)
        elif unit == "MAY_CONTAIN":
            traces.add(name)
        else:  # UNDECLARED, and any unit we do not know: not a statement that the allergen is absent
            undeclared.add(name)
    free_from = {allergen_name(v.get("id")) for v in free if v.get("id") != NO_ALLERGENS}

    conflicts = (contains | traces) & free_from
    claims = {v.get("id") for v in groups.get("nutritionalClaim", [])}
    conflicts |= {name for claim, name in CLAIM_CONTRADICTS.items() if claim in claims and name in contains}
    return {"allergens": sorted(contains), "traces": sorted(traces), "undeclared": sorted(undeclared),
            "free_from": sorted(free_from), "allergen_conflicts": sorted(conflicts)}


# Text scan -----------------------------------------------------------------
#
# A heuristic. It reads the ingredient texts for words that name nuts, peanuts or sesame,
# in English, Finnish and Swedish. It finds words, not meaning: it does not know every
# spelling, and it reads only the simplest denials ("nut free", "without nuts"). A hit
# means "a person must look", never "the product contains it". No hit proves nothing.

TEXT_SCAN_NAMES = ("nuts", "peanuts", "sesame")
_WORD = re.compile(r"[a-zåäöéèü]+")

_PEANUT = ("peanut", "groundnut", "arachis", "maapähkin", "jordnöt")
_SESAME = ("sesam", "seesam", "tahini")
# Words that hold a nut word and are not tree nuts: nutmeg, coconut, butternut, doughnut,
# chestnut and the almond potato.
_NOT_NUTS = ("nutmeg", "muskottipähkin", "muskotnöt", "coconut", "kookos", "kokos", "butternut", "doughnut", "donut",
             "chestnut", "mandelpotatis")
_NUT_KIND_WORDS = ("almond", "hazel", "walnut", "cashew", "pecan", "pistachio", "macadamia", "marzipan",
                   "pähkin", "mantel", "pistaasi", "pekaani", "makadamia", "marsipaani",
                   "nötter", "mandel", "pistasch", "pekan", "marsipan")
# A quick test: no text without one of these pieces can name nuts, peanuts or sesame.
_MAYBE = re.compile(r"nut|nöt|pähkin|arachis|sesam|seesam|tahini|" + "|".join(_NUT_KIND_WORDS))
# One word that denies: nutfree, pähkinätön, seesamiton, nötfri.
_FREE_WORD = re.compile(r"(tön|ton|töntä|tonta|free|fri|fria|fritt)$|ttöm|ttom")
# Words before the allergen that deny it: "without nuts", "free from nuts", "ei sisällä pähkinää".
_DENIALS = (("without",), ("utan",), ("ilman",), ("no",), ("free", "from"), ("fri", "från"), ("ei", "sisällä"))


def _names_nuts(word: str) -> bool:
    if any(x in word for x in _NOT_NUTS) or any(x in word for x in _PEANUT):
        return False
    if any(x in word for x in _NUT_KIND_WORDS):
        return True
    # English "nut" and "nuts" alone or at the end of a word (pine nuts, brazilnut).
    # Swedish "nöt" only at the end of a longer word (hasselnöt): "nöt" alone is beef.
    return bool(re.fullmatch(r"[a-z]*nuts?", word)) or (word.endswith("nöt") and len(word) > 3)


def _allergen_in(word: str) -> str | None:
    if any(x in word for x in _PEANUT):
        return "peanuts"
    if any(x in word for x in _SESAME):
        return "sesame"
    return "nuts" if _names_nuts(word) else None


def _denied(words: list[str], i: int) -> bool:
    """True when the word at i is denied by itself, by the word after it or by the words before it."""
    if _FREE_WORD.search(words[i]) or (i + 1 < len(words) and words[i + 1] in ("free", "fri", "fria")):
        return True
    return any(tuple(words[max(0, i - len(d)):i]) == d for d in _DENIALS)


def _texts(raw: dict) -> list[str]:
    s = raw.get("synkkaData") or {}
    out = [t.get("value") or "" for t in (s.get("keyIngredients") or []) + (s.get("materialAdditionalDescriptions") or [])]
    for ingredient in s.get("ingredients") or []:
        out += [n.get("value") or "" for n in ingredient.get("names") or []]
    return out


def text_allergens(raw: dict, names=TEXT_SCAN_NAMES) -> list[str]:
    """The allergens in `names` that the ingredient texts of the raw record name. A heuristic, see above."""
    found = set()
    for text in _texts(raw):
        text = text.lower()
        if not _MAYBE.search(text):  # most texts name none of the three, so skip the word loop
            continue
        words = _WORD.findall(text)
        for i, word in enumerate(words):
            name = _allergen_in(word)
            if name and not _denied(words, i):
                found.add(name)
    return sorted(found & set(names))
