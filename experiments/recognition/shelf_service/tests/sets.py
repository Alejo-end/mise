"""Product sets for the tests, in the shapes of the shared file: ACTIVE mirrors products/active.json of 15 Nov 03:30
(four products, Salted Caramel removed; families, thresholds, the ordering units of the selected entries), ARCHIVED mirrors the props set of
14 Nov with its original identities. Test data only; the live service reads the shared file (SHELF_PRODUCTS)."""
import dataclasses

from shelf_service.product_set import ProductSet

ROUTING = {"default_kimi_at_or_below": 0.85, "display_min_score": 0.3, "conflict_overlap": 0.5, "flavour_margin": 0.25,
           "scene_unchanged_mean_abs_diff": 8.0, "scene_unchanged_box_iou": 0.7, "cloud_timeout_s": 60,
           "cloud_in_flight": 1, "cloud_answer_max_age_s": 600}


def _shake(slug, label, short, variants):
    return {"identity": f"aimo:profeel-{slug}", "label": label, "display_name": "PROfeel " + short, "short_label": short,
            "family": "profeel-shake",
            "count_unit": "bottle", "kimi_at_or_below": 0.95, "match_text": ["profeel"], "variant_text": variants,
            "catalogue": {"status": "found", "sales_unit": "KI", "sales_unit_name": "case", "count_units_per_sales_unit": 15}}


TEXT_MATCHING = {"generic_words": ["premium", "täysmehu", "taysmehu", "mehu", "juice", "juoma", "drink", "high", "protein", "proteiinijuoma", "proteindryck", "100", "25", "g", "ml", "l", "dl", "1l", "uht", "lactose", "free", "laktoositon", "laktosfri", "no", "added", "sugar", "sokeroimaton", "osockrad", "professional", "original", "classic", "valio", "fazer", "shake", "oat", "kaura", "kaurajuoma", "havredryck", "gluten", "gluteeniton", "glutenfri"],
                 "flavour_words": ["strawberry", "mansikka", "mansikkainen", "jordgubb", "jordgubbsmak", "mint", "minttu", "minttukaakao", "mintchoklad", "chocolate", "suklaa", "choklad", "cocoa", "kaakao", "caramel", "karamelli", "kinuski", "saltkaramell", "salted", "vanilla", "vanilja", "banana", "banaani", "coffee", "kahvi", "apple", "omena", "äpple", "mango", "pineapple", "ananas", "grapefruit", "greippi", "multivitamin", "multivitamiini", "peach", "persikka", "raspberry", "vadelma", "blueberry", "mustikka", "lemon", "sitruuna", "lime", "orange", "appelsiini", "apelsin", "apelsinjuice", "appelsiinitäysmehu", "pear", "päärynä", "cherry", "kirsikka", "tropical", "trooppinen", "berry", "marja", "barista"]}
CUES = {"aimo:profeel-strawberry": "pink PROfeel packaging with the matching strawberry graphics", "aimo:profeel-mint-chocolate": "green/mint PROfeel packaging with the matching mint-chocolate graphics", "aimo:bonne-orange-juice": "Bonne branding, orange-fruit illustrations and the white carton with a black front label", "aimo:aito-barista-oat": "Aito branding (turquoise logo) on the dark Barista oat drink carton with a coffee illustration", "aimo:profeel-salted-caramel": "orange/caramel PROfeel packaging"}

COVERAGE = {"products": {"aimo:aito-barista-oat": {"coverage": "cloud", "basis": "owner, 15 Nov: the local model has no Aito class"}}}

ACTIVE = ProductSet.from_dict({
    "schema": "shelf-product-set/2", "set_id": "kitchen-20261115-b", "routing": ROUTING, "text_matching": TEXT_MATCHING,
    "families": {"profeel-shake": {"label": "PROfeel protein shake", "display_name": "PROfeel shake", "flavour_required": True,
                                   "brand_text": ["profeel"]}},
    "products": [
        _shake("strawberry", "PROfeel Strawberry shake", "Strawberry", ["strawberry", "mansikka", "mansikkainen"]),
        _shake("mint-chocolate", "PROfeel Mint Chocolate shake", "Mint Chocolate", ["mint chocolate", "mint", "minttukaakao"]),
        {"identity": "aimo:bonne-orange-juice", "label": "Bonne orange juice (18-pack)", "display_name": "Bonne orange juice",
         "short_label": "Bonne orange",
         "family": None, "count_unit": "carton", "kimi_at_or_below": 0.85, "match_text": ["bonne"],
         "variant_text": ["orange", "appelsiini", "appelsiinitäysmehu"],
         "catalogue": {"status": "found", "sales_unit": "KI", "sales_unit_name": "case", "count_units_per_sales_unit": 18}},
        {"identity": "aimo:aito-barista-oat", "label": "Aito Barista oat drink", "display_name": "Aito Barista oat drink",
         "short_label": "Aito Barista",
         "family": None, "count_unit": "carton", "kimi_at_or_below": 0.85, "match_text": ["aito"], "variant_text": ["barista"],
         "catalogue": {"status": "found", "sales_unit": "ST", "sales_unit_name": "single unit", "count_units_per_sales_unit": 1}}],
    "excluded_products": [{"identity": "aimo:profeel-salted-caramel", "label": "PROfeel Salted Caramel shake",
                           "display_name": "PROfeel Salted Caramel",
                           "short_label": "Salted Caramel", "family": "profeel-shake", "ean": "6408430401260",
                           "variant_text": ["salted caramel"]}],
    "archived_sets": [{"set_id": "kitchen-20261115-a", "labels": {"aimo:profeel-salted-caramel": "PROfeel Salted Caramel shake"}},
                      {"set_id": "kitchen-20261114-b", "labels": {"exp:chips": "chips", "exp:milk": "milk",
                                                                  "exp:energy-drink": "energy drink"}}]})

# the shared file of 15 Nov 06:40: the v3 local model has no Aito class, so Aito is cloud-covered
ACTIVE_V3 = dataclasses.replace(ACTIVE, coverage_rules=COVERAGE)

FOUR_CLASS = ["aimo:profeel-strawberry", "aimo:profeel-mint-chocolate", "aimo:bonne-orange-juice", "aimo:aito-barista-oat"]
FIVE_CLASS = ["aimo:profeel-salted-caramel", *FOUR_CLASS]  # a trainer's five-class order: index 0 stays caramel

ARCHIVED = ProductSet.from_dict({
    "schema": "shelf-product-set/1", "set_id": "kitchen-20261114-b",
    "slots": {"1": {"identity": "exp:chips", "label": "chips",
                    "owner_confirmed": {"brand": "Estrella", "variant": "Spicy Cheddar", "match_text": ["estrella"],
                                        "variant_text": ["spicy cheddar"]}},
              "2": {"identity": "exp:energy-drink", "label": "energy drink",
                    "owner_confirmed": {"brand": "Funky Monkey", "variant": "Sunny Peach", "match_text": ["funky monkey"],
                                        "variant_text": ["sunny peach"]}},
              "3": {"identity": "exp:milk", "label": "milk"}}})

for _p in ACTIVE.products + ACTIVE.excluded:  # the owner's descriptions of the packages, as in the shared file
    _p["visual_cue"] = CUES[_p["identity"]]
