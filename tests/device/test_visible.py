"""The box's shared recognition coordinator (device/python/visible.py): one scan at a time for every page, a failed
scan never replaces the last result, request ids are taken once; the hybrid flow (local model, settled scene,
whole-frame cloud check or its reuse); continuous mode (one loop, the pause after each complete scan, Stop, timeouts,
backoff, manual requests while it runs)."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "device" / "python"))
import visible as V  # noqa: E402

ITEMS = {"items": [{"kind": "product", "text": "Bonne orange juice — 1 carton"}], "uncertain": False}


class Box:
    def __init__(self):
        self.now = 1_000_000.0
        self.frame = ("JPEG64", 7, "2026-11-15T04:00:00+02:00")
        self.answers, self.calls, self.published = [], [], []

    def grab(self):
        return self.frame

    def recognize(self, b64, captured):
        self.calls.append((b64, captured))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a

    def publish(self, snap, frame):
        self.published.append((snap["status"], frame))


def coordinator(box, run_async=False):
    return V.Coordinator(box.grab, box.recognize, box.publish, clock=lambda: box.now, run_async=run_async)


def test_a_result_is_shared_with_its_frame_identity_and_source():
    box = Box(); c = coordinator(box)
    box.answers = [{"visible_items": ITEMS, "product_set": {"set_id": "kitchen-20261115-b"}, "objects": []}]
    r = c.request("demo page", "d1")
    assert r["accepted"] is True and box.calls == [("JPEG64", "2026-11-15T04:00:00+02:00")]
    s = c.snapshot()
    assert s["status"] == "ok" and s["current"]["request_id"] == "d1" and s["current"]["requested_by"] == "demo page"
    assert s["current"]["frame_id"] == 7 and s["current"]["visible_items"] == ITEMS
    assert box.published[-1] == ("ok", "JPEG64") and all(p == ("pending", None) for p in box.published[:-1])
    assert "result" not in V.for_backend(s)["current"]  # raw details stay on the setup page


def test_a_failure_keeps_the_previous_result_and_says_so():
    box = Box(); c = coordinator(box)
    box.answers = [{"visible_items": ITEMS}, RuntimeError("recognition service not reachable")]
    c.request("camera setup page"); box.now += 30
    c.request("demo page")
    s = c.snapshot()
    assert s["status"] == "failed" and "not reachable" in s["last_error"]["error"]
    assert s["current"]["visible_items"] == ITEMS  # never replaced by zero counts
    box.answers = [{"error": "HTTP 502"}]
    c.request("demo page")
    assert c.snapshot()["current"]["visible_items"] == ITEMS and c.cycle is None  # busy state released


def test_no_fresh_frame_is_an_error_not_an_empty_shelf():
    box = Box(); box.frame = None; c = coordinator(box)
    r = c.request("demo page")
    assert r["accepted"] is False and r["status"] == "failed" and "no fresh camera picture" in r["last_error"]["error"]
    assert r["current"] is None and box.calls == []


def test_one_scan_at_a_time_and_a_request_id_is_taken_once():
    box = Box(); c = coordinator(box, run_async=False)
    c._new_cycle("manual", "camera setup page", "b1", None, None)  # one is running
    r = c.request("demo page", "d2")
    assert r["accepted"] is False and "joined" in r["reason"] and box.calls == []
    assert r["pending"]["joined_by"] == ["demo page"] and r["pending"]["joined_ids"] == ["d2"]  # the backend sees it taken
    c.cycle = None
    box.answers = [{"visible_items": ITEMS}]
    assert c.request("demo page", "d3")["accepted"] is True
    again = c.request("demo page", "d3")  # the backend repeats a command until it hears back
    assert again["accepted"] is False and "already taken" in again["reason"] and len(box.calls) == 1


def test_an_old_result_is_stale():
    box = Box(); c = coordinator(box)
    box.answers = [{"visible_items": ITEMS}]
    c.request("demo page")
    box.now = V._parse("2026-11-15T04:00:00+02:00", 0) + V.STALE_AFTER_S + 5
    s = c.snapshot()
    assert s["stale"] is True and s["status"] == "ok" and s["age_s"] > V.STALE_AFTER_S


# Hybrid flow ---------------------------------------------------------------------------------------------------------
# Frames are strings "<scene><n>" (e.g. "A1"): a fake scene module compares only the scene letter.
class Scene:
    @staticmethod
    def sig(b64):
        return b64[0]

    @staticmethod
    def same(a, b):
        return a == b


def merged(scene, n_aito=2, status="ok"):
    vi = {"items": [{"kind": "product", "status": "recognised", "marks": [1], "text": f"Aito — {n_aito} cartons · Kimi"}],
          "marks": [{"n": 1, "box": [0.4, 0.3, 0.1, 0.4], "status": "recognised", "source": "kimi"}], "uncertain": status != "ok"}
    return {"visible_items": vi, "objects": [], "product_set": {"set_id": "kitchen-20261115-b"},
            "hybrid": {"cloud": {"status": status, "ms": 18000, **({"error": "timeout"} if status == "failed" else {})},
                       "cloud_objects": [{"scene": scene}] if status == "ok" else None, "conflicts": []}}


class HybridBox(Box):
    def __init__(self, frames):
        super().__init__()
        self.frames, self.local_calls, self.on_cloud = list(frames), [], None
        self.local_answer = {"detections": [{"box": [0.4, 0.3, 0.1, 0.4], "score": 0.65, "class_id": 0}],
                             "model_info": {"name": "v3", "classes": None, "num_classes": 10}, "timing_ms": {"total": 300}}

    def grab(self):
        f = self.frames.pop(0) if len(self.frames) > 1 else self.frames[0]
        self.now += 0.05
        return (f, int(f[1:]), f"2026-11-15T06:5{f[1:]}:00+02:00") if f else None

    def local(self, b64, rid):
        self.local_calls.append(b64)
        if isinstance(self.local_answer, Exception):
            raise self.local_answer
        return self.local_answer

    def recognize(self, b64, captured, **kw):
        self.calls.append((b64, kw.get("cloud"), (kw.get("reuse") or {}).get("request_id")))
        if self.on_cloud:
            hook, self.on_cloud = self.on_cloud, None
            hook()
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a(b64[0]) if callable(a) else a

    def sleep(self, s):
        self.now += s


def hybrid(box):
    return V.Coordinator(box.grab, box.recognize, box.publish, clock=lambda: box.now, run_async=False,
                         local_detect=box.local, scene=Scene, sleep=box.sleep)


def test_local_boxes_show_first_then_one_whole_frame_check_on_the_settled_frame():
    box = HybridBox(["A1", "A2"]); c = hybrid(box)
    box.answers = [merged]
    c.request("demo page", "d1")
    assert box.local_calls == ["A1"] and box.calls == [("A1", "whole-frame", None)]  # automatic: no manual cloud click
    stages = [(st, f) for st, f in box.published]
    assert ("pending", "A1") in stages and stages[-1] == ("ok", "A1")  # local result published with its frame, then final
    s = c.snapshot()
    assert s["current"]["stage"] == "final" and s["current"]["local_boxes"] == 1 and "Kimi whole-frame check" in s["current"]["summary"]
    assert s["stats"]["cloud_whole_frame"] == 1 and s["pending"] is None
    log = s["cycles_log"][-1]
    assert log["ok"] and log["cloud_mode"] == "whole-frame" and log["local_ms"] is not None and log["cloud_ms"] is not None


def test_the_local_stage_is_amber_pending_never_counts():
    box = HybridBox(["A1", "A2"])
    shown = []
    box.publish = lambda snap, frame: shown.append(snap["current"]) if frame else None
    c = hybrid(box)
    box.answers = [merged]
    c.request("demo page")
    local = shown[0]
    assert local["stage"] == "local" and all(m["status"] == "unresolved" for m in local["visible_items"]["marks"])
    assert "identity pending" in local["visible_items"]["items"][0]["text"] and local["visible_items"]["uncertain"]
    box.local_answer = {**box.local_answer, "detections": []}
    box.frames = ["B1", "B2"]; box.answers = [merged]; shown.clear()
    c.request("demo page")
    assert "not evidence of an empty shelf" in shown[0]["visible_items"]["items"][0]["text"]


def test_an_unchanged_scene_reuses_the_check_and_a_changed_one_gets_a_new_check():
    box = HybridBox(["A1", "A2"]); c = hybrid(box)
    box.answers = [merged, merged, merged]
    c.request("demo page", "d1")
    box.frames = ["A3", "A4"]
    c.request("demo page", "d2")
    assert box.calls[-1] == ("A3", "reuse", "d1")  # no cloud model request: the check of d1 is reused
    assert c.snapshot()["current"]["captured_at"].startswith("2026-11-15T06:53")  # the new picture's own time
    box.frames = ["B5", "B6"]
    c.request("demo page", "d3")
    assert box.calls[-1] == ("B5", "whole-frame", None)
    assert c.snapshot()["stats"]["cloud_reused"] == 1 and c.snapshot()["stats"]["cloud_whole_frame"] == 2


def test_a_moving_scene_is_followed_until_it_settles_and_a_scene_that_never_settles_gets_no_cloud_check():
    box = HybridBox(["A1", "B2", "B3"]); c = hybrid(box)
    box.answers = [merged]
    c.request("demo page")
    assert box.local_calls == ["A1", "B2"] and box.calls == [("B2", "whole-frame", None)]
    assert c.snapshot()["stats"]["settle_retakes"] == 1
    moving = ["C%d" % i if i % 2 else "D%d" % i for i in range(1, 40)]
    box.frames = moving; box.calls.clear()
    c.request("demo page")
    s = c.snapshot()
    assert box.calls == [] and s["stats"]["not_settled"] == 1 and "kept changing" in s["last_error"]["error"]
    assert s["current"]["stage"] == "local" and s["current"]["visible_items"]["uncertain"]


def test_a_request_during_the_cloud_check_joins_it_and_starts_nothing():
    box = HybridBox(["A1", "A2"]); c = hybrid(box)
    box.answers = [merged]
    seen = {}

    def press_during_kimi():
        box.frames = ["B3", "B4"]
        seen["r"] = c.request("camera setup page", "b2")
    box.on_cloud = press_during_kimi
    c.request("demo page", "d1")
    assert seen["r"]["accepted"] is False and "joined" in seen["r"]["reason"]
    assert seen["r"]["pending"]["phase"] == "checking with Kimi"  # its progress is shown
    assert box.local_calls == ["A1"] and len(box.calls) == 1 and c.snapshot()["current"]["request_id"] == "d1"


def test_a_failed_cloud_check_keeps_the_local_boxes_uncertain_and_never_zero():
    box = HybridBox(["A1", "A2"]); c = hybrid(box)
    box.answers = [RuntimeError("recognition service not reachable")]
    c.request("demo page")
    s = c.snapshot()
    assert s["status"] == "failed" and "not reachable" in s["last_error"]["error"] and c.cycle is None
    assert s["current"]["stage"] == "local" and "uncertain, not zero" in s["current"]["summary"]
    box.frames = ["B1", "B2"]; box.answers = [lambda sc: merged(sc, status="failed")]
    c.request("demo page")
    s = c.snapshot()  # the service answered but its Kimi check failed: its uncertain result, no cache to reuse
    assert s["current"]["stage"] == "final" and "Kimi check failed" in s["last_error"]["error"] and c.cache is None


def test_without_the_local_model_the_whole_frame_check_still_runs():
    box = HybridBox(["A1", "A2"]); c = hybrid(box)
    box.local_answer = RuntimeError("connection refused")
    box.answers = [merged]
    c.request("demo page")
    s = c.snapshot()
    assert box.calls == [("A1", "whole-frame", None)] and s["status"] == "ok" and "Local model unavailable" in s["current"]["summary"]


# Continuous mode (real threads, short times) --------------------------------------------------------------------------
class LiveBox:
    """Thread-safe fake box: frames of one scene, a local model and a cloud whose delay and answers the test sets."""

    def __init__(self, cloud_s=0.15, scene="A"):
        self.lock = threading.Lock()
        self.scene, self.n, self.cloud_s, self.fail, self.gate = scene, 0, cloud_s, None, None
        self.spans, self.running, self.max_running, self.calls, self.published = [], 0, 0, [], []

    def grab(self):
        with self.lock:
            self.n += 1
            return (f"{self.scene}{self.n}", self.n, time.strftime("%Y-%m-%dT%H:%M:%S+02:00"))

    def local(self, b64, rid):
        time.sleep(0.02)
        return {"detections": [], "model_info": {"name": "v3", "classes": None}, "timing_ms": {"total": 20}}

    def recognize(self, b64, captured, **kw):
        t0 = time.monotonic()
        with self.lock:
            self.running += 1; self.max_running = max(self.max_running, self.running)
            self.calls.append((b64, kw.get("cloud")))
        try:
            if self.gate:
                self.gate.wait(5)
            time.sleep(self.cloud_s)
            if self.fail:
                raise self.fail
            return merged(b64[0])
        finally:
            with self.lock:
                self.running -= 1

    def publish(self, snap, frame):
        with self.lock:
            self.published.append(snap)


def live(box, **kw):
    args = dict(local_detect=box.local, scene=Scene, settle_gap_s=0.01, pause_min_s=0.05, backoff_min_s=0.1)
    args.update(kw)
    return V.Coordinator(box.grab, box.recognize, box.publish, run_async=True, **args)


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def scans(c):
    return [x for x in c.snapshot()["cycles_log"]]


def test_continuous_scans_never_overlap_and_the_pause_follows_each_complete_scan():
    box = LiveBox(cloud_s=0.2); c = live(box)
    box.scene = "A"
    r = c.start_continuous(0.3, "demo page")
    assert r["accepted"] is True
    assert wait_for(lambda: len(scans(c)) >= 3, 8)
    c.stop_continuous("demo page")
    log = scans(c)
    assert box.max_running == 1  # never two cloud calls at once
    for prev, nxt in zip(log, log[1:]):
        assert nxt["started_at"] >= prev["finished_at"]  # never overlapping
        assert nxt["pause_before_ms"] >= 300 - 15  # the pause starts after the complete scan, cloud step included
    assert log[0]["cloud_mode"] == "whole-frame" and log[1]["cloud_mode"] == "reuse"  # unchanged scene: reused
    assert sum(1 for x in box.calls if x[1] == "whole-frame") == 1


def test_start_twice_and_two_pages_make_one_loop_and_stop_ends_it():
    box = LiveBox(cloud_s=0.05); c = live(box)
    assert c.start_continuous(0.1, "demo page")["accepted"] is True
    again = c.start_continuous(0.2, "camera setup page")  # the other page, or a reload, presses Start again
    assert again["accepted"] is False and "already running" in again["reason"] and again["continuous"]["pause_s"] == 0.2
    assert c.start_continuous(None, "demo page", "k1")["accepted"] is False
    assert c.start_continuous(None, "demo page", "k1")["reason"] == "this command was already taken"
    assert wait_for(lambda: len(scans(c)) >= 3, 6)
    assert c.stop_continuous("camera setup page")["accepted"] is True
    n = len(scans(c)); time.sleep(0.6)
    assert len(scans(c)) <= n + 1 and box.max_running == 1  # at most the running scan ends; no further scans
    s = c.snapshot()["continuous"]
    assert s["running"] is False and s["phase"] == "stopped" and s["stopped_by"] == "camera setup page"


def test_stop_during_the_cloud_check_discards_the_late_answer_and_keeps_the_last_observation():
    box = LiveBox(cloud_s=0.01); c = live(box)
    c.start_continuous(5, "demo page")
    assert wait_for(lambda: len(scans(c)) == 1)
    first = c.snapshot()["current"]
    box.scene, box.gate = "B", threading.Event()  # the scene changes: the next scan needs Kimi, which hangs
    assert c.request("demo page", "d9")["reason"] == "the next continuous scan starts now"  # early, during the pause
    assert wait_for(lambda: (c.snapshot()["pending"] or {}).get("phase") == "checking with Kimi")
    c.stop_continuous("demo page")
    assert c.snapshot()["continuous"]["phase"] == "stopped"
    box.gate.set()  # the late answer arrives after Stop
    assert wait_for(lambda: c.cycle is None)
    s = c.snapshot()
    assert s["current"]["request_id"] == first["request_id"] and s["current"]["captured_at"] == first["captured_at"]
    assert s["discarded"]["request_id"] == "d9" and s["status"] == "ok"
    time.sleep(0.3)
    assert len(scans(c)) == 2  # no scan after Stop


def test_recognize_now_while_waiting_starts_the_next_scan_early_and_while_scanning_joins():
    box = LiveBox(cloud_s=0.3); c = live(box)
    c.start_continuous(30, "demo page")  # a long pause
    assert wait_for(lambda: (c.snapshot()["pending"] or {}).get("phase") == "checking with Kimi")
    j = c.request("camera setup page", "b1")
    assert j["accepted"] is False and "joined" in j["reason"]
    assert wait_for(lambda: c.snapshot()["continuous"]["phase"] == "waiting for the next scan")
    assert c.snapshot()["continuous"]["next_scan_in_s"] > 20
    t = time.monotonic()
    assert c.request("demo page", "d1")["accepted"] is True
    assert wait_for(lambda: len(scans(c)) == 2, 3) and time.monotonic() - t < 2  # not 30 s later
    assert scans(c)[-1]["requested_by"] == "demo page" and scans(c)[-1]["request_id"] == "d1"
    c.stop_continuous("demo page")


def test_a_hanging_scan_is_released_its_late_answer_ignored_and_no_second_cloud_request_is_sent():
    box = LiveBox(cloud_s=0.6); c = live(box, cycle_timeout_s=0.25)
    c.request("demo page", "d1")
    assert wait_for(lambda: "did not finish" in ((c.snapshot()["last_error"] or {}).get("error") or ""), 3)
    assert c.cycle is None and c.snapshot()["status"] == "failed"  # busy state released at the timeout
    box.scene = "B"
    c.request("demo page", "d2")  # the hung cloud request is still in flight: no second one
    assert wait_for(lambda: len(scans(c)) == 2, 3)
    assert "still in flight" in scans(c)[-1]["error"] and box.max_running == 1
    time.sleep(0.6)  # the late answer of d1 arrives
    assert c.snapshot()["current"]["request_id"] == "d2" and c.cloud is None


def test_failures_back_off_and_recover():
    box = LiveBox(cloud_s=0.01); box.fail = RuntimeError("recognition service not reachable")
    c = live(box, backoff_min_s=0.2)
    c.start_continuous(0.05, "demo page")
    assert wait_for(lambda: len(scans(c)) >= 2, 4)
    cont = c.snapshot()["continuous"]
    assert cont["failures"] >= 1 and (cont["phase"] == "error" and "retrying" in cont["detail"] or cont["phase"] != "error")
    log = scans(c)
    assert log[1]["pause_before_ms"] >= 190  # backoff (0.2 s), not the 0.05 s pause
    box.fail = None
    assert wait_for(lambda: scans(c)[-1]["ok"], 4)
    assert wait_for(lambda: c.snapshot()["continuous"]["failures"] == 0, 2)  # reset after a good scan
    c.stop_continuous("demo page")
