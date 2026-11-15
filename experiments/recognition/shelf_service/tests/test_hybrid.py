"""Hybrid recognition (shelf_service/hybrid.py): the board's local detections and the whole-frame cloud check of the
same frame become one object list; partial model coverage, unmapped classes, conflicts, reuse, failures and crop
checks. The cloud model is a fake: these tests check the reconciliation, not the model."""
import asyncio
import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from shelf_service import hybrid as Hy
from shelf_service.tests.sets import ACTIVE_V3

STRAW, MINT, CARAMEL = "aimo:profeel-strawberry", "aimo:profeel-mint-chocolate", "aimo:profeel-salted-caramel"
BONNE, AITO = "aimo:bonne-orange-juice", "aimo:aito-barista-oat"
V3 = {"name": "kitchen-restock-yolox-nano-v3-7products", "classes": None, "num_classes": 10, "sha256_16": "ddeb2f00c303b649"}
# a verified class order, for the tests of the local identity path only (the real v3 order is not known)
MAPPED = {"name": "mapped", "classes": [STRAW, MINT, BONNE, CARAMEL, "exp:milk"], "num_classes": 5}
BOX = {"mint": [0.14, 0.50, 0.08, 0.40], "caramel": [0.23, 0.50, 0.08, 0.40], "straw": [0.32, 0.50, 0.08, 0.40],
       "aito1": [0.39, 0.42, 0.09, 0.42], "bonne": [0.55, 0.44, 0.08, 0.42], "aito2": [0.72, 0.33, 0.13, 0.50]}


def frame():
    im = Image.new("RGB", (640, 480), (30, 30, 30))
    d = ImageDraw.Draw(im)
    for i, b in enumerate(BOX.values()):
        d.rectangle([b[0] * 640, b[1] * 480, (b[0] + b[2]) * 640, (b[1] + b[3]) * 480], fill=(60 + 30 * i, 120, 200 - 20 * i))
    out = io.BytesIO(); im.save(out, "JPEG", quality=92); return out.getvalue()


def cobj(status, box, identity=None, removed=None, n=1, could=(), desc="package"):
    return {"status": status, "identity": identity, "removed_identity": removed, "family": None, "box": box, "visible_count": n,
            "could_be": list(could), "could_be_any": False, "description": desc, "obstruction": None, "match_by": "visual match"}


SIX = [cobj("known", BOX["mint"], MINT), cobj("not-inventory", BOX["caramel"], removed=CARAMEL),
       cobj("known", BOX["straw"], STRAW), cobj("known", BOX["aito1"], AITO), cobj("known", BOX["bonne"], BONNE),
       cobj("known", BOX["aito2"], AITO)]


class Cloud:
    def __init__(self, objects=None, error=None, region_answers=None):
        self.objects, self.error, self.region_answers, self.calls = objects or [], error, list(region_answers or []), []

    async def __call__(self, jpeg, surface, captured, runs_dir, pset=None, region=None, prompted_by=None, store_root=None):
        self.calls.append("region" if region else "whole-frame")
        if self.error:
            raise self.error
        if region:
            return {"objects": self.region_answers.pop(0), "captured_at": captured}
        return {"objects": [dict(o) for o in self.objects], "references_used": [{"reference": "R1"}], "image_quality": "ok",
                "scene_obstruction": None, "reasons": []}


def run(cloud, dets, mode="whole-frame", reuse=None, model=V3, crops=None):
    local = {"detections": dets, "model": model, "timing_ms": {"total": 410}}
    return asyncio.run(Hy.run(frame(), "2026-11-15T06:50:00+02:00", local, mode, reuse, ACTIVE_V3, Path("/tmp/unused"),
                              recognize=cloud, crops=crops))


def d(box, cls, score):
    return {"box": box, "class_id": cls, "score": score, "class_scores": [0.01] * 10}


def counts(r):
    return {k.split(":")[1]: (v["visible"], v["status"]) for k, v in r["inventory_counts"].items()}


def test_unmapped_local_boxes_take_kimis_identity_and_are_never_counted_twice():
    # the v3 model on the 05:30 frame: class 0 on both Aito cartons, class 1 on the Bonne carton's top; no shake boxes
    dets = [d([0.39, 0.44, 0.09, 0.40], 0, 0.65), d([0.55, 0.44, 0.08, 0.10], 1, 0.84), d([0.72, 0.33, 0.13, 0.50], 0, 0.65)]
    cloud = Cloud(SIX)
    r = run(cloud, dets)
    assert cloud.calls == ["whole-frame"]  # one request resolves the escalated boxes too: no crop requests
    assert counts(r) == {"profeel-strawberry": (1, "counted"), "profeel-mint-chocolate": (1, "counted"),
                         "bonne-orange-juice": (1, "counted"), "aito-barista-oat": (2, "counted")}
    assert len(r["objects"]) == 6 and r["hybrid"]["matches"] and r["hybrid"]["kimi_only"] == 3
    by = {o.get("identity") or o.get("removed_identity"): o["source"] for o in r["objects"]}
    assert by[BONNE]["identity_by"] == "kimi" and by[BONNE]["local"]["class_id"] == 1
    assert by[BONNE]["box_by"].startswith("kimi")  # the local box covered only the carton's top: the whole box is drawn
    assert next(o for o in r["objects"] if o.get("identity") == BONNE)["box"] == BOX["bonne"]
    assert by[AITO]["box_by"] == "local"  # a local box of the whole package stays
    assert by[MINT] == {**by[MINT], "identity_by": "kimi", "box_by": "kimi", "local": None}
    esc = r["hybrid"]["local"]
    assert esc["escalated"] == 3 and r["hybrid"]["coverage"]["class_map"] == "awaiting trainer metadata"
    texts = [i["text"] for i in r["visible_items"]["items"]]
    assert "Aito Barista oat drink — 2 cartons · Kimi" in texts and "PROfeel Salted Caramel — 1 bottle · Not in inventory · Kimi" in texts
    assert [m["status"] for m in r["visible_items"]["marks"]] == ["recognised", "not-inventory", "recognised", "recognised", "recognised", "recognised"]
    assert r["hybrid"]["cloud_objects"] and len(r["hybrid"]["cloud_objects"]) == 6  # for reuse while the scene is unchanged


def test_aito_without_a_local_box_appears_once_after_the_automatic_check():
    cloud = Cloud([cobj("known", BOX["aito1"], AITO), cobj("known", BOX["bonne"], BONNE)])
    r = run(cloud, [d(BOX["bonne"], 1, 0.9)])  # the local model has no Aito box at all
    assert counts(r)["aito-barista-oat"] == (1, "counted") and counts(r)["bonne-orange-juice"] == (1, "counted")
    assert sum(1 for o in r["objects"] if o.get("identity") == AITO) == 1
    assert r["hybrid"]["coverage"]["products"][AITO]["local"] is False


def test_no_local_boxes_at_all_still_get_the_whole_frame_check():
    cloud = Cloud(SIX)
    r = run(cloud, [])
    assert cloud.calls == ["whole-frame"] and counts(r)["aito-barista-oat"] == (2, "counted")


def test_a_failed_cloud_check_keeps_everything_uncertain_never_zero():
    r = run(Cloud(error=TimeoutError("no answer")), [d(BOX["bonne"], 1, 0.9)])
    assert r["hybrid"]["cloud"]["status"] == "failed" and r["scene_unverified"]
    assert all(v["status"] in ("uncertain", "at least") for v in r["inventory_counts"].values())
    assert r["objects"][0]["status"] == "unknown" and r["objects"][0]["could_be_any"] is True
    assert r["visible_items"]["marks"][0]["status"] == "unresolved" and r["hybrid"]["cloud_objects"] is None
    r = run(Cloud(), [], mode="none")  # no check at all (scene still moving): uncertain, not "none visible"
    assert all(v["status"] == "uncertain" for v in r["inventory_counts"].values())


def test_reuse_while_unchanged_makes_no_cloud_request():
    cloud = Cloud()
    r = run(cloud, [d(BOX["bonne"], 1, 0.84)], mode="reuse", reuse={"objects": SIX, "request_id": "d1", "captured_at": "x"})
    assert cloud.calls == [] and r["hybrid"]["cloud"]["status"] == "reused" and counts(r)["aito-barista-oat"] == (2, "counted")
    bonne = next(o for o in r["objects"] if o.get("identity") == BONNE)
    assert bonne["source"]["kimi"]["reused_from"] == "d1"


@pytest.mark.parametrize("ident,cls,at,above", [(STRAW, 0, 0.95, 0.951), (BONNE, 2, 0.85, 0.851)])
def test_mapped_boxes_go_to_the_cloud_at_or_below_their_threshold(ident, cls, at, above):
    def scores(s):
        return [s if i == cls else 0.01 for i in range(5)]
    low = run(Cloud(), [{**d(BOX["straw"], cls, at), "class_scores": scores(at)}], mode="none", model=MAPPED)
    assert low["objects"][0]["status"] == "unknown" and "at or below" in low["objects"][0]["source"]["local"]["escalation"][0]
    high = run(Cloud(), [{**d(BOX["straw"], cls, above), "class_scores": scores(above)}], mode="none", model=MAPPED)
    o = high["objects"][0]
    assert o["status"] == "known" and o["identity"] == ident and o["source"]["identity_by"] == "local"
    assert high["inventory_counts"][ident]["visible"] == 1


def test_a_confident_local_identity_and_a_different_kimi_identity_are_one_explicit_conflict():
    dets = [{**d(BOX["straw"], 0, 0.99), "class_scores": [0.99, 0.01, 0.01, 0.01, 0.01]}]
    r = run(Cloud([cobj("known", BOX["straw"], MINT)]), dets, model=MAPPED)
    o = r["objects"][0]
    assert len(r["objects"]) == 1 and o["status"] == "ambiguous" and set(o["could_be"]) == {STRAW, MINT}
    assert o["source"]["identity_by"] == "conflict" and r["hybrid"]["conflicts"][0]["kimi"] == MINT
    assert counts(r)["profeel-strawberry"] == (0, "uncertain") and counts(r)["profeel-mint-chocolate"] == (0, "uncertain")
    assert counts(r)["bonne-orange-juice"] == (0, "none visible")  # the conflict is scoped to the two shakes
    agree = run(Cloud([cobj("known", BOX["straw"], STRAW)]), dets, model=MAPPED)
    assert agree["objects"][0]["source"]["identity_by"] == "local+kimi" and counts(agree)["profeel-strawberry"] == (1, "counted")


def test_a_kimi_object_of_two_units_with_one_local_box_counts_two_not_three():
    r = run(Cloud([cobj("known", [0.4, 0.3, 0.3, 0.5], AITO, n=2)]), [d([0.4, 0.3, 0.14, 0.5], 0, 0.7)])
    assert counts(r)["aito-barista-oat"] == (2, "counted") and len(r["objects"]) == 2


def test_an_escalated_box_the_whole_frame_missed_gets_one_crop_check_reused_while_unchanged():
    inside = {"status": "known", "identity": BONNE, "location": "inside the selected region", "visible_count": 1, "description": "Bonne"}
    crops = Hy.CropCache()
    cloud = Cloud([cobj("known", BOX["aito2"], AITO)], region_answers=[[inside]])
    r = run(cloud, [d(BOX["bonne"], 1, 0.6)], crops=crops)
    assert cloud.calls == ["whole-frame", "region"] and counts(r)["bonne-orange-juice"] == (1, "counted")
    again = Cloud([cobj("known", BOX["aito2"], AITO)])
    r = run(again, [d(BOX["bonne"], 1, 0.6)], crops=crops)
    assert again.calls == ["whole-frame"] and counts(r)["bonne-orange-juice"] == (1, "counted")  # crop answer reused
    empty = Cloud([cobj("known", BOX["aito2"], AITO)], region_answers=[[]])
    r = run(empty, [d(BOX["mint"], 4, 0.6)])  # nothing in that box: a false local box is dismissed, not counted
    assert len(r["objects"]) == 1 and r["hybrid"]["dismissed_local_boxes"]
