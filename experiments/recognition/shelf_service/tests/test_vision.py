"""Vision-model mode: validated, never forced into the inventory, clear errors (Verda calls mocked)."""
import asyncio
import base64
import io
import json

import pytest
from PIL import Image

from shelf_service import vision
from shelf_service.tests.sets import ACTIVE, ARCHIVED


def jpeg():
    b = io.BytesIO(); Image.new("RGB", (64, 48), (90, 100, 120)).save(b, "JPEG"); return b.getvalue()


def fake_chat(answers):
    calls = []

    async def chat(client, gate, body):
        calls.append(body)
        a = answers[len(calls) - 1]
        if isinstance(a, Exception):
            raise a
        return a
    return chat, calls


def run(tmp_path, monkeypatch, answers):
    from recog import verda
    chat, calls = fake_chat(answers)
    monkeypatch.setattr(verda, "chat", chat)
    # the 14 Nov behaviour, with the props set of that day (archived)
    return asyncio.run(vision.recognize(jpeg(), [0, 0, 1, 1], "2026-11-14T13:00:00+02:00", tmp_path, pset=ARCHIVED)), calls


def ok(content):
    return {"content": json.dumps(content), "usage": {"prompt_tokens": 10}}


def test_described_items_are_matched_only_when_the_text_step_supports_it(tmp_path, monkeypatch):
    items = {"items": [
        {"box": [0.1, 0.2, 0.2, 0.3], "visible_count": 2, "description": "white carton", "readable_text": "MAITO 1 l",
         "evidence": "word MAITO", "barcode": "6408430000296", "valio_code": "463335"},
        {"box": [0.5, 0.2, 0.2, 0.3], "visible_count": 1, "description": "protein bar wrapper", "evidence": "wrapper"}],
        "scene_obstruction": None, "image_quality": "ok"}
    match = {"matches": [{"index": 0, "match": "milk", "reason": "readable MAITO"}, {"index": 1, "match": "unknown"}]}
    r, calls = run(tmp_path, monkeypatch, [ok(items), ok(match)])
    assert [o["status"] for o in r["objects"]] == ["known", "unknown"]
    assert r["visible_units"] == {"exp:milk": 2}
    assert all("barcode" not in o and "valio_code" not in o for o in r["objects"])  # never passed through
    assert "image_url" not in json.dumps(calls[1])  # the matching step never sees the image
    assert "chips" not in calls[0]["messages"][0]["content"][1]["text"]  # the image step has no candidate list


def test_an_empty_scene_returns_no_objects_and_no_second_call(tmp_path, monkeypatch):
    r, calls = run(tmp_path, monkeypatch, [ok({"items": [], "scene_obstruction": None, "image_quality": "ok"})])
    assert r["objects"] == [] and r["visible_units"] == {} and len(calls) == 1


def test_bad_answers_and_timeouts_are_clear_errors(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="could not be used"):
        run(tmp_path, monkeypatch, [{"content": "I think it is milk"}])
    with pytest.raises(RuntimeError, match="describe step"):
        run(tmp_path, monkeypatch, [asyncio.TimeoutError()])
    with pytest.raises(RuntimeError, match="describe step"):
        run(tmp_path, monkeypatch, [{"error": "HTTP 429", "infra_failure": True}])


def test_boxes_are_clipped_and_pixel_boxes_dropped(tmp_path, monkeypatch):
    items = {"items": [{"box": [0.9, 0.9, 0.5, 0.5], "description": "a"}, {"box": [100, 50, 40, 40], "description": "b"}]}
    r, _ = run(tmp_path, monkeypatch, [ok(items), ok({"matches": []})])
    assert r["objects"][0]["box"] == [0.9, 0.9, 0.1, 0.1] and r["objects"][1]["box"] is None
    assert [o["status"] for o in r["objects"]] == ["unknown", "unknown"]


def run_confirmed(tmp_path, monkeypatch, items, matches=None, pset=ARCHIVED):
    from recog import verda
    answers = [ok({"items": items, "scene_obstruction": None, "image_quality": "ok"})] + ([ok(matches)] if matches else [])
    chat, calls = fake_chat(answers)
    monkeypatch.setattr(verda, "chat", chat)
    r = asyncio.run(vision.recognize(jpeg(), [0, 0, 1, 1], "t", tmp_path, save=False, pset=pset))
    return r, calls


def test_confirmed_brand_text_decides_without_asking_the_model_again(tmp_path, monkeypatch):
    items = [{"box": [0.5, 0.4, 0.1, 0.4], "visible_count": 1, "description": "orange bottle", "brand": "FUNKY MONKEY",
              "readable_text": "FUNKY MONKEY"},
             {"box": [0.6, 0.4, 0.1, 0.4], "visible_count": 1, "description": "orange bottle", "brand": "FUNKY MONKEY",
              "readable_text": "FUNKY MONKEY", "obstruction": "partly overlapped by the bottle in front"}]
    r, calls = run_confirmed(tmp_path, monkeypatch, items)
    assert len(calls) == 1  # no text-model reinterpretation of a confirmed brand
    assert [o["status"] for o in r["objects"]] == ["known", "known"]
    assert [o["visibility"] for o in r["objects"]] == ["visible", "partial"]  # partly hidden still counted
    assert r["visible_units"] == {"exp:energy-drink": 2}
    assert all(o["match_level"] == "brand" and "variant was not read" in o["match_reason"] for o in r["objects"])


def test_brand_and_variant_is_an_exact_match_and_another_variant_is_ambiguous(tmp_path, monkeypatch):
    items = [{"description": "bag", "brand": "Estrella", "variant": "SPICY Cheddar", "readable_text": "Estrella SPICY Cheddar"},
             {"description": "bag", "brand": "Estrella", "variant": "Sour Cream", "readable_text": "Estrella Sour Cream"}]
    r, calls = run_confirmed(tmp_path, monkeypatch, items)
    assert [(o["status"], o["match_level"]) for o in r["objects"]] == [("known", "brand+variant"), ("ambiguous", None)]
    assert "variant 'Sour Cream'" in r["objects"][1]["match_reason"]
    assert r["visible_units"] == {"exp:chips": 1}


def test_unresolved_items_make_counts_lower_bounds_never_zero(tmp_path, monkeypatch):
    items = [{"description": "orange bottle, label turned away"}, {"description": "black cable"}]
    r, calls = run_confirmed(tmp_path, monkeypatch, items,
                             {"matches": [{"index": 0, "match": "unknown"}, {"index": 1, "match": "not inventory"}]})
    assert [o["status"] for o in r["objects"]] == ["unknown", "not-inventory"]
    assert r["inventory_counts"]["exp:energy-drink"]["status"] == "uncertain"  # not "none visible", not 0 stock
    assert r["uncertain"] is True and r["visible_units"] == {}


def test_only_unrelated_objects_give_none_visible(tmp_path, monkeypatch):
    r, _ = run_confirmed(tmp_path, monkeypatch, [{"description": "black cable"}], {"matches": [{"index": 0, "match": "not inventory"}]})
    assert r["inventory_counts"]["exp:milk"]["status"] == "none visible" and r["uncertain"] is False


def test_a_region_check_sends_context_and_crop_and_maps_boxes_back(tmp_path, monkeypatch):
    from recog import verda
    b = io.BytesIO(); Image.new("RGB", (480, 360), (90, 100, 120)).save(b, "JPEG")
    items = {"items": [{"box": [0.0, 0.0, 1.0, 1.0], "visible_count": 1, "description": "orange bottle"},
                       {"box": [0.3, 0.3, 0.4, 0.4], "visible_count": 1, "description": "white carton", "readable_text": "MAITO"}],
             "scene_obstruction": "a hand", "image_quality": "ok"}
    chat, calls = fake_chat([ok(items), ok({"matches": [{"index": 0, "match": "unknown"}, {"index": 1, "match": "milk"}]})])
    monkeypatch.setattr(verda, "chat", chat)
    r = asyncio.run(vision.recognize(b.getvalue(), [0, 0, 1, 1], "t", tmp_path, region=[0.4, 0.4, 0.2, 0.2],
                                     prompted_by={"local": "L2", "class_id": 1, "score": 0.41}, pset=ARCHIVED))
    content = calls[0]["messages"][0]["content"]
    assert [c["type"] for c in content] == ["image_url", "image_url", "text"]  # whole frame (outlined), then the crop
    text = content[2]["text"]
    assert "second photo" in text and "chips" not in text and "class" not in text  # no candidates, no local label
    assert "0.41" not in json.dumps(calls)  # the local model's score is echoed to the page only
    assert r["mode"] == "vision-model region" and r["crop"] == [0.35, 0.35, 0.3, 0.3]
    assert r["objects"][0]["box"] == [0.35, 0.35, 0.3, 0.3]  # crop fractions -> whole-frame fractions
    assert r["objects"][1]["box"] == [0.44, 0.44, 0.12, 0.12]
    # the whole crop-sized box lies mostly outside the selected region; the carton lies inside it
    assert [o["location"] for o in r["objects"]] == ["around the selected region", "inside the selected region"]
    assert r["reported_by_location"]["inside the selected region"] == {"exp:milk": 1}
    # no counts at all, so a region answer cannot be added to the local or whole-image counts
    assert r["visible_units"] == {} and r["inventory_counts"] == {}
    assert "not an identification of the selected region alone" in r["counting_note"]
    assert any("may refer to the whole frame" in x for x in r["reasons"])
    assert r["prompted_by"]["local"] == "L2"
    saved = sorted(p.name for p in tmp_path.iterdir())
    assert any(n.endswith("-region-crop.jpg") for n in saved) and any(n.endswith("-region-context.jpg") for n in saved)


def test_region_crop_is_enlarged_and_clamped_to_the_frame():
    b = io.BytesIO(); Image.new("RGB", (480, 360), (0, 0, 0)).save(b, "JPEG")
    ctx, crop, rect = vision.region_payload(b.getvalue(), [0.9, 0.9, 0.1, 0.1])
    assert rect == [0.875, 0.875, 0.125, 0.125]  # the margin stops at the frame edge
    assert max(Image.open(io.BytesIO(crop)).size) == vision.REGION_MIN_SIDE
    assert Image.open(io.BytesIO(ctx)).size == (480, 360)


# The active set of 15 Nov: five products, three PROfeel flavours told apart only by readable flavour text ------------

def run_active(tmp_path, monkeypatch, items, matches=None):
    return run_confirmed(tmp_path, monkeypatch, items, matches, pset=ACTIVE)






def test_bonne_counts_cartons_beside_its_pack_of_18(tmp_path, monkeypatch):
    items = [{"description": "juice carton", "brand": "Bonne", "variant": "Appelsiinitäysmehu", "visible_count": 7}]
    r, _ = run_active(tmp_path, monkeypatch, items)
    c = r["inventory_counts"]["aimo:bonne-orange-juice"]
    assert (c["visible"], c["count_unit"], c["status"]) == (7, "carton", "counted")
    assert c["order_units"] == {"count_unit": "carton", "sales_unit": "KI", "sales_unit_name": "case",
                                "count_units_per_sales_unit": 18, "full_sales_units": 0, "remainder_count_units": 7}
    assert r["inventory_counts"]["aimo:aito-barista-oat"]["order_units"]["count_units_per_sales_unit"] == 1


def _region_result(objects, quality="ok", obstruction=None):
    r = {"objects": objects, "reasons": [], "image_quality": quality, "scene_obstruction": obstruction}
    vision.region_result(r, [0.4, 0.4, 0.2, 0.2], 0)
    return r


def obj(box, status, identity=None, family=None, n=1):
    return {"box": box, "status": status, "identity": identity, "family": family, "visible_count": n}


def test_the_selected_object_is_resolved_without_the_objects_around_it():
    r = _region_result([obj([0.42, 0.42, 0.15, 0.15], "known", "aimo:aito-barista-oat"),
                        obj([0.0, 0.0, 0.2, 0.2], "known", "aimo:bonne-orange-juice", n=3)])
    s = vision.resolve_selected(r)
    assert s["status"] == "resolved" and s["units"] == {"aimo:aito-barista-oat": 1} and s["items_around_ignored"] == 1


def test_an_unclear_selected_object_is_uncertain_not_zero():
    assert vision.resolve_selected(_region_result([obj([0.42, 0.42, 0.15, 0.15], "known", "aimo:aito-barista-oat")],
                                                  quality="dark"))["status"] == "uncertain"
    s = vision.resolve_selected(_region_result([obj([0.42, 0.42, 0.15, 0.15], "flavour-uncertain", family="profeel-shake")]))
    assert s["status"] == "uncertain" and s["units"] == {} and s["family_units"] == {"profeel-shake": 1}
    assert vision.resolve_selected(_region_result([obj(None, "known", "aimo:aito-barista-oat")]))["status"] == "uncertain"
    assert vision.resolve_selected(_region_result([]))["status"] == "no-product"
    assert vision.resolve_selected(_region_result([obj([0.42, 0.42, 0.15, 0.15], "not-inventory")]))["status"] == "no-product"



def test_a_clear_view_gives_plain_counts_and_an_empty_shelf_says_so(tmp_path, monkeypatch):
    r, _ = run_active(tmp_path, monkeypatch, [{"description": "carton", "brand": "Bonne", "variant": "orange", "visible_count": 3}])
    assert [i["text"] for i in r["visible_items"]["items"]] == ["Bonne orange juice — 3 cartons"]
    r, _ = run(tmp_path, monkeypatch, [ok({"items": [], "scene_obstruction": None, "image_quality": "ok"})])
    assert r["visible_items"]["nothing_seen"] is True and r["visible_items"]["items"] == []



# Visual matching with the labelled reference photos (active sets, 15 Nov) -------------------------------------------

STRAW, MINT, BONNE, AITO, CARAMEL = ("aimo:profeel-strawberry", "aimo:profeel-mint-chocolate", "aimo:bonne-orange-juice",
                                     "aimo:aito-barista-oat", "aimo:profeel-salted-caramel")
REFS = {STRAW: (230, 60, 140), MINT: (60, 180, 120), BONNE: (240, 240, 235), AITO: (20, 40, 70)}  # made-up crops


def ref_store(tmp_path):
    root = tmp_path / "refstore"
    for ident, colour in REFS.items():
        d = root / "enrolment" / ident / "reference"
        d.mkdir(parents=True)
        photos = []
        for k in range(3):
            Image.new("RGB", (120, 200), colour).save(d / f"r{k}.jpg", "JPEG")
            photos.append({"role": "reference", "source": "manual", "captured_at": f"2026-11-15T03:4{k}:00",
                           "path": f"enrolment/{ident}/reference/r{k}.jpg"})
        (root / "enrolment" / ident / "meta.json").write_text(json.dumps({"identity": ident, "photos": photos}))
    return root


def frame_jpeg():
    b = io.BytesIO(); Image.new("RGB", (640, 480), (30, 30, 30)).save(b, "JPEG"); return b.getvalue()


def run_visual(tmp_path, monkeypatch, items, matches=None):
    from recog import verda
    answers = [ok({"items": items, "scene_obstruction": None, "image_quality": "ok"})] + ([ok({"matches": matches})] if matches is not None else [])
    chat, calls = fake_chat(answers)
    monkeypatch.setattr(verda, "chat", chat)
    r = asyncio.run(vision.recognize(frame_jpeg(), [0, 0, 1, 1], "t", tmp_path, save=False, pset=ACTIVE, store_root=ref_store(tmp_path)))
    return r, calls


def box(k):
    return [0.05 + 0.15 * k, 0.3, 0.1, 0.4]


GENERIC_PROFEEL = {"brand": "Profeel", "readable_text": "Profeel, HIGH PROTEIN, DRINK, 25 g"}


def test_the_visual_step_gets_the_labelled_references_and_each_objects_own_crop(tmp_path, monkeypatch):
    items = [{"box": box(0), "description": "small green drink carton", **GENERIC_PROFEEL},
             {"box": box(1), "description": "white juice carton", "brand": "Bonne", "variant": "PREMIUM TÄYSMEHU"}]
    r, calls = run_visual(tmp_path, monkeypatch, items, [
        {"index": 0, "match": "PROfeel Mint Chocolate shake", "basis": ["branding", "graphics"], "evidence": "green PROfeel"},
        {"index": 1, "match": "Bonne orange juice (18-pack)", "basis": ["branding", "graphics", "layout"], "evidence": "orange fruit"}])
    content = calls[1]["messages"][0]["content"]
    texts = " ".join(c["text"] for c in content if c["type"] == "text")
    images = [c for c in content if c["type"] == "image_url"]
    assert len(images) == 4 * 2 + 2  # two references per product, one crop per object
    assert "R1 = PROfeel Strawberry shake" in texts and "pink PROfeel packaging" in texts and "Colour alone is never enough" in texts
    assert "PROfeel Salted Caramel shake — NOT in the inventory" in texts and "orange/caramel PROfeel packaging" in texts
    assert "Object 1: text read on it: brand: Bonne; variant: PREMIUM TÄYSMEHU" in texts
    assert [x["path"].split("/")[-1] for x in r["references_used"]] == ["r1.jpg", "r2.jpg"] * 4  # the two most recent
    assert [o["identity"] for o in r["objects"]] == [MINT, BONNE] and r["visible_units"] == {MINT: 1, BONNE: 1}


def test_unreadable_flavours_are_decided_visually_and_caramel_stays_outside_the_inventory(tmp_path, monkeypatch):
    items = [{"box": box(k), "description": d, **GENERIC_PROFEEL} for k, d in enumerate(["green carton", "orange carton", "pink carton"])]
    r, calls = run_visual(tmp_path, monkeypatch, items, [
        {"index": 0, "match": "PROfeel Mint Chocolate shake", "basis": ["branding", "graphics", "colour"]},
        {"index": 1, "match": "PROfeel Salted Caramel shake", "basis": ["branding", "graphics", "colour"]},
        {"index": 2, "match": "PROfeel Strawberry shake", "basis": ["branding", "graphics", "colour"]}])
    assert len(calls) == 2  # the generic text decided nothing: the visual step did
    assert [o["status"] for o in r["objects"]] == ["known", "not-inventory", "known"]
    assert r["objects"][1]["removed_identity"] == CARAMEL and r["visible_units"] == {MINT: 1, STRAW: 1}
    assert r["family_units"] == {} and r["inventory_counts"][STRAW]["status"] == "counted"


def test_generic_words_are_not_a_contradiction_but_another_flavour_is():
    ev = vision.text_evidence({"brand": "Bonne", "variant": "PREMIUM TÄYSMEHU", "readable_text": "Bonne, PREMIUM, TÄYSMEHU, 100%"}, ACTIVE)
    assert ev["contradicted"] == [] and ev["supported"] == [] and BONNE in ev["brand"]
    assert vision.text_decision(ev, ACTIVE) is None  # missing APPELSIINI: verified visually, not rejected
    ev = vision.text_evidence({"brand": "PROfeel", "variant": "CHOCOLATE DRINK"}, ACTIVE)
    assert MINT not in ev["contradicted"] and STRAW in ev["contradicted"] and CARAMEL in ev["contradicted"]
    ev = vision.text_evidence({"brand": "PROfeel", "variant": "CARAMEL DRINK"}, ACTIVE)
    assert {MINT, STRAW} <= set(ev["contradicted"]) and CARAMEL not in ev["contradicted"]
    ev = vision.text_evidence({"brand": "Bonne", "readable_text": "Bonne OMENA täysmehu"}, ACTIVE)
    assert BONNE in ev["contradicted"]  # an apple juice is not the selected orange juice
    assert vision.text_decision(vision.text_evidence({"brand": "PROfeel", "readable_text": "PROfeel MANSIKKA"}, ACTIVE), ACTIVE)["identity"] == STRAW
    assert vision.text_decision(vision.text_evidence({"brand": "PROfeel", "variant": "Salted Caramel"}, ACTIVE), ACTIVE)["status"] == "not-inventory"


def test_an_explicit_other_flavour_or_colour_alone_prevents_the_match(tmp_path, monkeypatch):
    items = [{"box": box(0), "description": "white juice carton", "brand": "Bonne", "readable_text": "Bonne OMENA"},
             {"box": box(1), "description": "pink carton, label hidden"},
             {"box": box(2), "description": "dark oat carton", "brand": "aito", "variant": "BARISTA"}]
    r, _ = run_visual(tmp_path, monkeypatch, items, [
        {"index": 0, "match": "Bonne orange juice (18-pack)", "basis": ["branding", "layout"]},
        {"index": 1, "match": "PROfeel Strawberry shake", "basis": ["colour"]}])
    assert [o["status"] for o in r["objects"]] == ["ambiguous", "unknown", "known"]
    assert "contradicts" in r["objects"][0]["match_reason"] and r["objects"][1]["could_be"] == [STRAW]
    ic = r["inventory_counts"]
    assert ic[AITO]["status"] == "counted"  # a recognised Aito is not made uncertain by the open objects
    assert ic[STRAW]["status"] == "uncertain" and ic[BONNE]["status"] == "uncertain" and ic[MINT]["status"] == "none visible"
    assert r["objects"][0]["could_be"] == [BONNE]  # the picture said Bonne, the text said apple: Bonne stays open


def test_marks_carry_each_objects_own_status_and_the_list_its_numbers(tmp_path, monkeypatch):
    items = [{"box": box(0), "description": "green carton", **GENERIC_PROFEEL},
             {"box": box(1), "description": "orange carton", **GENERIC_PROFEEL},
             {"box": box(2), "description": "pink carton", **GENERIC_PROFEEL},
             {"box": box(3), "description": "dark oat carton", "brand": "aito", "variant": "PROFESSIONAL BARISTA OAT DRINK"},
             {"box": box(4), "description": "white juice carton", "brand": "Bonne", "variant": "PREMIUM TÄYSMEHU"},
             {"box": box(5), "description": "large dark oat carton", "brand": "aito", "variant": "PROFESSIONAL BARISTA OAT DRINK"},
             {"box": None, "description": "small grey box"}]
    r, _ = run_visual(tmp_path, monkeypatch, items, [
        {"index": 0, "match": "PROfeel Mint Chocolate shake", "basis": ["branding", "graphics"]},
        {"index": 1, "match": "PROfeel Salted Caramel shake", "basis": ["branding", "graphics"]},
        {"index": 2, "match": "PROfeel Strawberry shake", "basis": ["branding", "graphics"]},
        {"index": 4, "match": "Bonne orange juice (18-pack)", "basis": ["branding", "graphics", "layout"]},
        {"index": 6, "match": "unknown", "could_be": []}])
    v = r["visible_items"]
    assert [(m["n"], m["status"]) for m in v["marks"]] == [(1, "recognised"), (2, "not-inventory"), (3, "recognised"),
                                                           (4, "recognised"), (5, "recognised"), (6, "recognised"), (7, "unresolved")]
    assert {i["text"]: (i["marks"], i["status"]) for i in v["items"]} == {
        "PROfeel Strawberry — at least 1 bottle": ([3], "recognised"), "PROfeel Mint Chocolate — at least 1 bottle": ([1], "recognised"),
        "Bonne orange juice — at least 1 carton": ([5], "recognised"), "Aito Barista oat drink — at least 2 cartons": ([4, 6], "recognised"),
        "PROfeel Salted Caramel — 1 bottle · Not in inventory": ([2], "not-inventory"),
        "Unidentified: small grey box — 1 item (not identified)": ([7], "unresolved")}
    assert "case" not in repr(v) and r["visible_units"] == {MINT: 1, STRAW: 1, BONNE: 1, AITO: 2}


def test_a_profeel_package_is_not_dismissed_as_not_inventory(tmp_path, monkeypatch):
    items = [{"box": box(0), "description": "carton", **GENERIC_PROFEEL}]
    r, _ = run_visual(tmp_path, monkeypatch, items, [{"index": 0, "match": "not inventory", "basis": ["colour"]}])
    o = r["objects"][0]
    assert o["status"] == "flavour-uncertain" and set(o["could_be"]) == {STRAW, MINT} and r["family_units"] == {"profeel-shake": 1}
    assert r["inventory_counts"][BONNE]["status"] == "none visible"  # only the shakes are affected


def test_conflicting_evidence_stays_unresolved_but_only_for_the_products_it_could_be(tmp_path, monkeypatch):
    # the live case of 05:22: an orange carton read as "PROfeel CHOCOLATE DRINK", matched visually to Salted Caramel
    items = [{"box": box(0), "description": "small orange and brown carton", "brand": "Valio", "variant": "CHOCOLATE DRINK",
              "readable_text": "PROfeel CHOCOLATE DRINK"},
             {"box": box(1), "description": "pink carton", **GENERIC_PROFEEL},
             {"box": box(2), "description": "dark oat carton", "brand": "aito", "variant": "BARISTA OAT DRINK"}]
    r, _ = run_visual(tmp_path, monkeypatch, items, [
        {"index": 0, "match": "PROfeel Salted Caramel shake", "basis": ["branding", "graphics", "colour"]},
        {"index": 1, "match": "PROfeel Strawberry shake", "basis": ["branding", "graphics"]}])
    o = r["objects"][0]
    assert o["status"] == "ambiguous" and "contradicts" in o["match_reason"] and o["could_be"] == [MINT]  # chocolate fits only Mint
    assert o["could_be_any"] is False
    ic = r["inventory_counts"]
    assert ic[MINT]["status"] == "uncertain"  # the one product it could be
    assert ic[STRAW]["status"] == "counted" and ic[AITO]["status"] == "counted" and ic[BONNE]["status"] == "none visible"
    v = r["visible_items"]
    assert [m["status"] for m in v["marks"]] == ["unresolved", "recognised", "recognised"]
    assert any(i["text"].endswith("(could be PROfeel Mint Chocolate)") for i in v["items"])
