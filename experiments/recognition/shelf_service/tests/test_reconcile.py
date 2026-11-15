"""Automatic cloud fallback of detection/repeat mode: routing thresholds, conflicts, flavour ambiguity, one cloud
request at a time, reuse only while the scene is unchanged, late answers and failures (cloud answers mocked)."""
import asyncio
import io

import pytest
from PIL import Image, ImageDraw

from shelf_service.reconcile import RETRY_AFTER_S, Reconciler, Rejected
from shelf_service.tests.sets import ACTIVE, ACTIVE_V3, FIVE_CLASS, FOUR_CLASS

MODEL = {"product_set": "kitchen-20261115-b", "classes": FOUR_CLASS, "sha256_16": "test"}
FIVE = {"product_set": "kitchen-20261115-a", "classes": FIVE_CLASS, "name": "five-class"}
CARAMEL, STRAW, MINT = "aimo:profeel-salted-caramel", "aimo:profeel-strawberry", "aimo:profeel-mint-chocolate"
BONNE, AITO = "aimo:bonne-orange-juice", "aimo:aito-barista-oat"


def frame(shift=0, colour=(200, 60, 60)):
    im = Image.new("RGB", (480, 360), (40, 40, 40))
    d = ImageDraw.Draw(im)
    d.rectangle([60 + shift, 100, 120 + shift, 250], fill=colour)
    d.rectangle([300, 100, 360, 250], fill=(60, 60, 200))
    b = io.BytesIO(); im.save(b, "JPEG", quality=95); return b.getvalue()


def det(identity, score, box=(0.125, 0.28, 0.125, 0.42), **kw):
    return {"identity": identity, "score": score, "box": list(box), **kw}


def shake(identity, score, mine, other_id, other, box=(0.125, 0.28, 0.125, 0.42)):
    return det(identity, score, box, class_scores={identity: mine, other_id: other, AITO: 0.01})


class Cloud:
    """A controllable cloud model: each call waits until the test answers it."""

    def __init__(self):
        self.calls, self.gates = [], []

    async def __call__(self, frame_jpeg, box, captured_at):
        gate = {"event": asyncio.Event(), "answer": None, "error": None}
        self.calls.append({"box": box, "captured_at": captured_at, "frame": frame_jpeg}); self.gates.append(gate)
        await gate["event"].wait()
        if gate["error"]:
            raise gate["error"]
        return gate["answer"]

    def answer(self, i, units=None, status="resolved", family_units=None):
        g = self.gates[i]
        g["answer"] = {"status": status, "units": units or {}, "family_units": family_units or {}, "reason": "test",
                       "items_around_ignored": 2}
        g["event"].set()

    def fail(self, i, error):
        self.gates[i]["error"] = error; self.gates[i]["event"].set()


def run(test):
    asyncio.run(test())


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


@pytest.mark.parametrize("ident, at, above", [(STRAW, 0.95, 0.951), (MINT, 0.95, 0.951), (BONNE, 0.85, 0.851), (AITO, 0.85, 0.851)])
def test_threshold_boundary_at_goes_to_the_cloud_above_stays_local(ident, at, above):
    async def t():
        cs = {ident: 0.99, STRAW if ident != STRAW else MINT: 0.1}
        rec = Reconciler(Cloud(), ACTIVE)
        o = rec.observe(frame(), "t1", 1, [det(ident, at, class_scores=cs)], MODEL)["objects"][0]
        assert o["route"] == "cloud" and any("at or below" in w for w in o["escalation"])
        rec2 = Reconciler(Cloud(), ACTIVE)
        o = rec2.observe(frame(), "t1", 1, [det(ident, above, class_scores=cs)], MODEL)["objects"][0]
        assert o["route"] == "local" and o["units"] == {ident: 1}
    run(t)


def test_the_routing_threshold_is_not_the_display_minimum():
    async def t():
        rec = Reconciler(Cloud(), ACTIVE)
        r = rec.observe(frame(), "t", 1, [det(AITO, 0.31)], MODEL)  # shown at min score 0.3, still routed to the cloud
        assert r["objects"][0]["route"] == "cloud" and r["counts"][AITO]["status"] == "uncertain"
    run(t)


def test_overlapping_boxes_of_two_products_are_one_conflicting_object_never_two_units():
    async def t():
        cloud = Cloud(); rec = Reconciler(cloud, ACTIVE)
        r = rec.observe(frame(), "t", 1, [det(AITO, 0.99), det(BONNE, 0.97, (0.13, 0.3, 0.11, 0.38))], MODEL)
        assert len(r["objects"]) == 1 and r["objects"][0]["route"] == "cloud"
        assert r["objects"][0]["escalation"][0].startswith("conflicting classes")
        await settle()
        assert r["counts"][AITO]["counted"] == 0 and r["counts"][BONNE]["counted"] == 0 and len(cloud.calls) == 1
    run(t)


def test_close_shake_flavours_are_escalated_even_above_the_threshold():
    async def t():
        rec = Reconciler(Cloud(), ACTIVE)
        close = rec.observe(frame(), "t", 1, [shake(STRAW, 0.97, 0.98, MINT, 0.80)], MODEL)["objects"][0]
        assert close["route"] == "cloud" and "flavour uncertain" in close["escalation"][0]
        rec = Reconciler(Cloud(), ACTIVE)
        clear = rec.observe(frame(), "t", 1, [shake(STRAW, 0.97, 0.98, MINT, 0.20)], MODEL)["objects"][0]
        assert clear["route"] == "local" and clear["units"] == {STRAW: 1}
        rec = Reconciler(Cloud(), ACTIVE)
        missing = rec.observe(frame(), "t", 1, [det(STRAW, 0.99)], MODEL)["objects"][0]
        assert missing["route"] == "cloud" and "flavour scores not reported" in missing["escalation"][0]
    run(t)


def test_one_cloud_request_at_a_time_from_the_same_frame():
    async def t():
        cloud = Cloud(); rec = Reconciler(cloud, ACTIVE)
        f = frame()
        r = rec.observe(f, "t1", 1, [det(AITO, 0.5), det(BONNE, 0.6, (0.62, 0.28, 0.125, 0.42))], MODEL)
        await settle()
        assert sorted(o["status"] for o in r["objects"]) == ["asking cloud", "queued for cloud"]
        assert len(cloud.calls) == 1 and cloud.calls[0]["frame"] == f and cloud.calls[0]["captured_at"] == "t1"
        r2 = rec.observe(frame(), "t2", 2, [det(AITO, 0.5), det(BONNE, 0.6, (0.62, 0.28, 0.125, 0.42))], MODEL)
        await settle()
        assert len(cloud.calls) == 1 and {o["status"] for o in r2["objects"]} == {"awaiting cloud", "queued for cloud"}
        assert r2["counts"][AITO]["status"] == "uncertain" and r2["complete"] is False  # waiting is never zero
    run(t)


def test_an_answer_is_used_while_the_scene_is_unchanged_and_dropped_when_it_changes():
    async def t():
        cloud = Cloud(); rec = Reconciler(cloud, ACTIVE)
        rec.observe(frame(), "t1", 1, [det(AITO, 0.5)], MODEL)
        await settle(); cloud.answer(0, {AITO: 1}); await settle()
        r = rec.observe(frame(), "t2", 2, [det(AITO, 0.52)], MODEL)
        o = r["objects"][0]
        assert o["status"] == "resolved" and o["units"] == {AITO: 1} and o["cloud"]["reused"] is True
        assert o["cloud"]["answer_for_frame"] == 1 and o["cloud"]["items_around_ignored"] == 2
        assert r["counts"][AITO]["counted"] == 1 and r["counts"][AITO]["from_cloud"] == 1
        assert r["counts"][AITO]["status"] == "counted"
        moved = (0.25, 0.28, 0.125, 0.42)  # the object moved: its box and crop differ
        r = rec.observe(frame(shift=60), "t3", 3, [det(AITO, 0.5, moved)], MODEL)
        await settle()
        assert r["objects"][0]["status"] == "asking cloud" and len(cloud.calls) == 2
        r = rec.observe(frame(colour=(230, 230, 230)), "t4", 4, [det(AITO, 0.5)], MODEL)  # same box, new look
        assert r["objects"][0]["status"] != "resolved"
    run(t)


def test_a_late_answer_never_replaces_a_newer_one():
    async def t():
        cloud = Cloud(); rec = Reconciler(cloud, ACTIVE)
        rec.observe(frame(), "t1", 1, [det(AITO, 0.5)], MODEL); await settle()
        job1 = rec.in_flight
        cloud.fail(0, RuntimeError("HTTP 500")); await settle()
        # a newer frame of the same object gets its own answer first ...
        rec.failures.clear()
        rec.observe(frame(), "t2", 2, [det(AITO, 0.5)], MODEL); await settle()
        cloud.answer(1, {AITO: 1}); await settle()
        # ... then an answer for the older frame 1 arrives late
        rec._store({**job1, "seq": 1}, {"status": "no-product", "units": {}, "family_units": {}}, ACTIVE)
        assert rec.stats["late_dropped"] == 1
        r = rec.observe(frame(), "t3", 3, [det(AITO, 0.5)], MODEL)
        assert r["objects"][0]["status"] == "resolved" and r["objects"][0]["cloud"]["answer_for_frame"] == 2
    run(t)


def test_an_older_frame_is_rejected_and_never_overwrites_a_newer_observation():
    async def t():
        rec = Reconciler(Cloud(), ACTIVE)
        rec.observe(frame(), "t5", 5, [], MODEL, session="page-a")
        with pytest.raises(Rejected) as e:
            rec.observe(frame(), "t4", 4, [], MODEL, session="page-a")
        assert e.value.code == 409 and rec.stats["rejected_stale"] == 1
        rec.observe(frame(), "t1", 1, [], MODEL, session="page-b")  # a reloaded page starts again
    run(t)


def test_failures_and_timeouts_stay_uncertain_and_are_retried_later():
    async def t():
        now = [1000.0]
        cloud = Cloud(); rec = Reconciler(cloud, ACTIVE, clock=lambda: now[0])
        rec.observe(frame(), "t1", 1, [det(BONNE, 0.4)], MODEL); await settle()
        cloud.fail(0, RuntimeError("vision API error (describe step): HTTP 429")); await settle()
        r = rec.observe(frame(), "t2", 2, [det(BONNE, 0.4)], MODEL); await settle()
        o = r["objects"][0]
        assert o["status"] == "uncertain" and "HTTP 429" in o["reason"] and o["units"] == {}
        assert r["counts"][BONNE]["status"] == "uncertain" and len(cloud.calls) == 1  # no immediate retry
        now[0] += RETRY_AFTER_S + 1
        r = rec.observe(frame(), "t3", 3, [det(BONNE, 0.4)], MODEL); await settle()
        assert r["objects"][0]["status"] == "asking cloud" and len(cloud.calls) == 2
    run(t)


def test_the_cloud_timeout_is_enforced():
    async def t():
        never = Cloud()
        rec = Reconciler(never, ACTIVE)
        rec.pset().routing["cloud_timeout_s"] = 0.05
        try:
            rec.observe(frame(), "t1", 1, [det(BONNE, 0.4)], MODEL)
            await asyncio.sleep(0.2)
            assert rec.stats["timed_out"] == 1 and rec.in_flight is None
            r = rec.observe(frame(), "t2", 2, [det(BONNE, 0.4)], MODEL)
            assert "no answer within" in r["objects"][0]["reason"]
        finally:
            rec.pset().routing["cloud_timeout_s"] = 60
    run(t)


def test_an_unreadable_flavour_from_the_cloud_stays_a_family_count():
    async def t():
        cloud = Cloud(); rec = Reconciler(cloud, ACTIVE)
        rec.observe(frame(), "t1", 1, [shake(MINT, 0.9, 0.9, STRAW, 0.85)], MODEL); await settle()
        cloud.answer(0, status="uncertain", family_units={"profeel-shake": 1}); await settle()
        r = rec.observe(frame(), "t2", 2, [shake(MINT, 0.9, 0.9, STRAW, 0.85)], MODEL)
        assert r["objects"][0]["units"] == {} and r["family_units"]["profeel-shake"]["units"] == 1
        assert all(r["counts"][s]["counted"] == 0 and r["counts"][s]["status"] == "uncertain" for s in (STRAW, MINT))
        assert CARAMEL not in r["counts"]
    run(t)


def test_local_counts_keep_cartons_and_packs_apart():
    async def t():
        rec = Reconciler(Cloud(), ACTIVE)
        boxes = [(0.05, 0.2, 0.08, 0.3), (0.2, 0.2, 0.08, 0.3), (0.35, 0.2, 0.08, 0.3)]
        r = rec.observe(frame(), "t", 1, [det(BONNE, 0.97, b) for b in boxes], MODEL)
        c = r["counts"][BONNE]
        assert (c["counted"], c["from_local"], c["count_unit"], c["status"]) == (3, 3, "carton", "counted")
        assert (c["order_units"]["sales_unit"], c["order_units"]["count_units_per_sales_unit"], c["order_units"]["full_sales_units"]) == ("KI", 18, 0)
        assert r["authority"].startswith("observational") and "Recognize shelf" in r["missed_objects_note"]
    run(t)


def test_partial_coverage_is_accepted_unmapped_classes_and_cloud_covered_products_escalate():
    async def t():
        rec = Reconciler(Cloud(), ACTIVE)
        # a model without a class order (no trainer metadata): accepted, its boxes keep their number and go to the cloud
        r = rec.observe(frame(), "t", 1, [det(None, 0.99, class_id=7)], {"name": "v3", "classes": None, "num_classes": 10})
        o = r["objects"][0]
        assert o["route"] == "cloud" and "no verified meaning" in o["escalation"][0] and o["local"][0]["label"] == "class 7"
        assert r["coverage"]["class_map"] == "awaiting trainer metadata" and r["coverage"]["unmapped_classes"] == list(range(10))
        assert not any(p["local"] for p in r["coverage"]["products"].values())
        # Aito missing from the model: it is cloud-covered; the other products stay local above their thresholds
        rec = Reconciler(Cloud(), ACTIVE)
        r = rec.observe(frame(), "t", 1, [det(BONNE, 0.99, class_id=2)], {"classes": FIVE_CLASS[:4]})
        assert r["objects"][0]["route"] == "local" and r["counts"][BONNE]["counted"] == 1
        assert r["coverage"]["products"][AITO]["local"] is False and r["coverage"]["products"][BONNE]["local"] is True
        # the product file says Aito is cloud-covered: even a mapped, confident Aito box goes to the cloud
        rec = Reconciler(Cloud(), ACTIVE_V3)
        o = rec.observe(frame(), "t", 1, [det(AITO, 0.99, class_id=4)], FIVE)["objects"][0]
        assert o["route"] == "cloud" and "cloud-covered" in o["escalation"][0]
    run(t)


def test_a_five_class_model_keeps_its_caramel_class_which_is_never_counted():
    async def t():
        cloud = Cloud(); rec = Reconciler(cloud, ACTIVE)
        car = det(CARAMEL, 0.99, (0.05, 0.2, 0.08, 0.3), class_id=0, class_scores={CARAMEL: 0.99, STRAW: 0.02, MINT: 0.01})
        aito = det(AITO, 0.99, (0.62, 0.28, 0.125, 0.42), class_id=4)
        r = rec.observe(frame(), "t1", 1, [car, aito], FIVE)  # partial coverage is fine; caramel keeps index 0
        o_car = next(o for o in r["objects"] if o["local"][0]["identity"] == CARAMEL)
        assert o_car["route"] == "cloud" and "removed from the inventory" in o_car["escalation"][0]
        assert o_car["local"][0]["label"] == "PROfeel Salted Caramel shake"  # its own name, never renamed
        assert CARAMEL not in r["counts"] and r["counts"][AITO]["counted"] == 1
        await settle()
        cloud.answer(0, status="no-product")  # the cloud read Salted Caramel: not inventory
        await settle()
        r = rec.observe(frame(), "t2", 2, [car, aito], FIVE)
        assert next(o for o in r["objects"] if o["route"] == "cloud")["units"] == {} and CARAMEL not in r["counts"]
        # a strawberry with a close caramel score is a flavour doubt, though caramel is not inventory
        rec = Reconciler(Cloud(), ACTIVE)
        o = rec.observe(frame(), "t", 1, [shake(STRAW, 0.97, 0.97, CARAMEL, 0.9)], FIVE)["objects"][0]
        assert o["route"] == "cloud" and "Salted Caramel 0.90" in o["escalation"][0]
    run(t)
