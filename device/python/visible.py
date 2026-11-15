"""Shared shelf recognition on the box: one coordinator for every page, the latest result and continuous mode.

The box owns the camera. Every page asks through this coordinator: the camera setup page (socket messages) and the
demo page (through the backend's command channel: /device/commands answers "recognize" and "continuous"). Pages
never run timers of their own: continuous recognition is one loop here, so two open pages, a reload or Start pressed
twice never make a second loop, and it keeps running with only the demo page open (or none).

One scan (a cycle) at a time, never overlapping:
  1. Capture: the newest camera frame (never older than 2 s).
  2. Local: the board's local model (valio-local-detect, port 8200) on exactly that frame; its boxes are published at
     once ("identity pending", amber) when the scene differs from the shown result.
  3. Settled: a second frame a moment later must show the same scene; while it keeps changing the newer frame is taken
     (and its local boxes shown) for up to SETTLE_MAX_S. A scene that never settles gets no cloud check.
  4. Cloud: the whole-frame check (Kimi on the laptop's recognition service, with the reference photos) on the first
     scan and on every changed, settled scene; while the scene is unchanged since the last whole-frame check, that
     check's objects are reused (no cloud request). The service reconciles the local boxes and the cloud objects of the
     same frame into one object list. At most one cloud request is in flight.
  5. Publish the final result, or the failure.
A manual request while a scan runs joins it (its progress is shown; nothing is queued). In continuous mode the loop
waits the configured pause AFTER each complete scan (not after the local step, not on a fixed clock) and then starts
the next one; "Recognize now" during that wait starts the next scan early. After a failure the wait grows (retry
backoff, at most BACKOFF_MAX_S). Stop prevents further scans; the scan running at that moment is finished but its
result is discarded (unless someone asks for a scan while it finishes), so a late answer never replaces the last
observation, which stays with its own capture time. A scan that does not finish within CYCLE_TIMEOUT_S is released
and its late answer ignored. Every completion, error and timeout releases the busy state.

A failure never turns into zero: a failed cloud check leaves the local boxes and every count uncertain; a failed scan
with no new local picture keeps the previous result and records the error next to it. Every change is published to
the open setup pages and to the backend (POST /observations/visible). Observations only: a visible item is not
stock, not an order and not a purchasing decision; continuous mode grants no purchasing authority.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

STALE_AFTER_S = 300    # a result older than this is shown as stale
SEEN_IDS = 50
SETTLE_GAP_S = 0.8     # the second frame of the settled check
SETTLE_MAX_S = 8.0     # how long a moving scene is followed before giving up on a cloud check
REUSE_MAX_AGE_S = 600  # a whole-frame check is reused for an unchanged scene at most this long
CYCLE_TIMEOUT_S = 150.0  # a scan is released after this long (the cloud request itself times out at 110 s)
PAUSE_DEFAULT_S = 5.0
PAUSE_MIN_S, PAUSE_MAX_S = 1.0, 600.0
BACKOFF_MAX_S = 60.0
LOG_CYCLES = 8
PHASES = ("analysing locally", "checking with Kimi", "waiting for the next scan", "stopped", "error")


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).astimezone().isoformat(timespec="milliseconds")


def local_view(obs: dict, note: str) -> dict:
    """The local model's boxes of one frame as visible items, before (or without) the cloud check: every box is
    "identity pending" (amber); the local model's classes are numbers until its class order is verified."""
    loc = obs.get("local") or {}
    dets = sorted(loc.get("detections") or [], key=lambda d: d["box"][0] + d["box"][2] / 2)
    marks, items = [], []
    for i, d in enumerate(dets):
        n = i + 1
        marks.append({"n": n, "box": d["box"], "kind": "unidentified", "status": "unresolved", "source": "pending"})
        what = f"class {d.get('class_id')}" + (f" ({d['identity']})" if d.get("identity") else "")
        items.append({"kind": "unidentified", "status": "unresolved", "marks": [n],
                      "text": f"Local box: {what}, score {float(d.get('score', 0)):.2f} — identity pending"})
    if not dets:
        items.append({"kind": "note", "status": "unresolved", "marks": [],
                      "text": "The local model found no boxes here: not evidence of an empty shelf."})
    return {"items": items, "marks": marks, "uncertain": True, "reasons": [note], "captured_at": obs["captured"],
            "nothing_seen": False, "stage": "local", "references_used": [],
            "note": "Local boxes of the board's model: not counts. Kimi's whole-frame check identifies the products."}


def clamp_pause(v, lo: float = PAUSE_MIN_S) -> float:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return PAUSE_DEFAULT_S
    return PAUSE_DEFAULT_S if v != v else min(max(v, lo), PAUSE_MAX_S)


class Coordinator:
    def __init__(self, grab_frame, recognize, publish, clock=time.time, stale_after_s: float = STALE_AFTER_S,
                 run_async: bool = True, local_detect=None, scene=None, sleep=time.sleep,
                 settle_gap_s: float = SETTLE_GAP_S, settle_max_s: float = SETTLE_MAX_S,
                 reuse_max_age_s: float = REUSE_MAX_AGE_S, cycle_timeout_s: float = CYCLE_TIMEOUT_S,
                 backoff_max_s: float = BACKOFF_MAX_S, backoff_min_s: float = 5.0, pause_min_s: float = PAUSE_MIN_S):
        """grab_frame() -> (jpeg_b64, frame_id, captured_at_iso) or None when there is no fresh frame.
        recognize(jpeg_b64, captured_at_iso, **kw) -> the recognition service's result; raises on any failure. With a
          local model or a scene check: kw = request_id, frame_id, local (that frame's local answer), cloud
          ("whole-frame" or "reuse") and reuse (the cached whole-frame objects).
        local_detect(jpeg_b64, request_id) -> the board model's answer for exactly that frame; raises on failure.
        scene: module-like with sig(jpeg_b64) and same(a, b); None = no settled check and no reuse.
        publish(snapshot, frame_b64) -> None; frame_b64 is the picture of a newly shown (local or final) result."""
        self.grab_frame, self.recognize, self.publish = grab_frame, recognize, publish
        self.clock, self.stale_after_s, self.run_async = clock, stale_after_s, run_async
        self.local_detect, self.scene, self.sleep = local_detect, scene, sleep
        self.settle_gap_s, self.settle_max_s, self.reuse_max_age_s = settle_gap_s, settle_max_s, reuse_max_age_s
        self.cycle_timeout_s, self.backoff_max_s, self.backoff_min_s = cycle_timeout_s, backoff_max_s, backoff_min_s
        self.pause_min_s = pause_min_s
        self.lock = threading.Lock()
        self.current: dict | None = None
        self.current_frame: str | None = None
        self.current_sig = None
        self.last_final = None        # (entry, frame, sig) of the last final result: shown again if a scan is discarded
        self.last_error: dict | None = None
        self.seen: list[str] = []
        self.count = 0
        self.seq = 0                  # scan numbers; a result is shown only if no newer scan's result is shown
        self.applied_seq = 0
        self.cycle: dict | None = None  # the running scan
        self.cloud: dict | None = None  # the cloud request in flight
        self.cache: dict | None = None  # the last whole-frame check: its objects and its scene
        self.session: dict | None = None  # continuous mode
        self.sessions = 0
        self.wake = threading.Event()
        self.wake_req = None
        self.last_command_id = None
        self.discarded: dict | None = None
        self.last_end_s = None
        self.log: list[dict] = []
        self.stats = {"scans": 0, "local_ok": 0, "local_failed": 0, "settle_retakes": 0, "not_settled": 0,
                      "cloud_whole_frame": 0, "cloud_reused": 0, "cloud_failed": 0, "discarded": 0, "timed_out": 0,
                      "joined": 0}

    # snapshot -----------------------------------------------------------------------------------------------------
    def snapshot(self) -> dict:
        with self.lock:
            return self._snap()

    def _snap(self) -> dict:
        now = self.clock()
        cur, cy = self.current, self.cycle
        failed_after = bool(self.last_error and (cur is None or self.last_error["at_s"] >= cur["finished_at_s"]))
        status = "pending" if cy else "failed" if failed_after else "ok" if cur else "none"
        age = None if cur is None else round(now - cur["captured_at_s"])
        pend = None
        if cy:
            pend = {k: cy.get(k) for k in ("request_id", "requested_by", "kind", "frame_id", "captured_at", "started_at",
                                            "joined_by", "joined_ids")}
            pend.update(stage=cy.get("detail") or cy["phase"], phase=cy["phase"],
                        stage_s=round(now - cy.get("phase_since_s", cy["started_s"]), 1),
                        seconds=round(now - cy["started_s"], 1), discard=cy.get("discard", False))
        cloud = None if not self.cloud else {**self.cloud, "seconds": round(now - self.cloud["started_s"], 1)}
        return {"status": status, "pending": pend, "current": dict(cur) if cur else None,
                "last_error": dict(self.last_error) if failed_after else None, "cloud_in_flight": cloud,
                "continuous": self._cont(now), "cycles_log": list(self.log), "discarded": self.discarded,
                "age_s": age, "stale": age is not None and age > self.stale_after_s, "stale_after_s": self.stale_after_s,
                "at": _iso(now), "stats": dict(self.stats),
                "note": "Camera observation: not stock, not an order. Continuous mode grants no purchasing authority."}

    def _cont(self, now) -> dict:
        s, cy = self.session, self.cycle
        if s is None:
            return {"running": False, "phase": "stopped", "detail": "not started", "pause_s": PAUSE_DEFAULT_S,
                    "last_command_id": self.last_command_id}
        if s["active"]:
            if cy is not None:
                phase, detail = cy["phase"], cy.get("detail")
            else:
                phase, detail = s.get("phase", "analysing locally"), s.get("detail")
        else:
            phase = "stopped"
            detail = (f"stopped by the {s.get('stopped_by')}" +
                      ("; the scan that was running finishes and its result is discarded" if cy and cy.get("discard") else ""))
        nxt = s.get("next_at_s") if s["active"] and cy is None else None
        return {"running": s["active"], "session": s["id"], "phase": phase, "detail": detail, "pause_s": s["pause_s"],
                "started_at": s["started_at"], "started_by": s["started_by"], "cycles": s["cycles"],
                "failures": s["failures"], "next_scan_at": _iso(nxt) if nxt else None,
                "next_scan_in_s": round(max(0.0, nxt - now), 1) if nxt else None,
                "stopped_at": s.get("stopped_at"), "stopped_by": s.get("stopped_by"),
                "last_command_id": self.last_command_id}

    def _publish_now(self, frame=None):
        with self.lock:
            snap = self._snap()
        self.publish(snap, frame)

    # manual requests ----------------------------------------------------------------------------------------------
    def request(self, requested_by: str, request_id: str | None = None) -> dict:
        """One scan of the newest frame now. While a scan runs: joins it. In continuous mode while it waits: the next
        scan starts early. Answers at once (run_async) with the snapshot."""
        run = None
        with self.lock:
            if request_id and request_id in self.seen:
                return {"accepted": False, "reason": "this request was already taken", **self._snap()}
            self.count += 1
            rid = request_id or f"b{int(self.clock() * 1000) % 10**9}-{self.count}"
            self.seen = (self.seen + [rid])[-SEEN_IDS:]
            cy = self.cycle
            if cy is not None:
                cy["joined_by"] = sorted(set((cy.get("joined_by") or []) + [requested_by]))
                cy["joined_ids"] = ((cy.get("joined_ids") or []) + [rid])[-10:]
                cy["discard"] = False  # someone wants this scan's result (also after Stop)
                self.stats["joined"] += 1
                return {"accepted": False, "reason": f"joined the scan already running ({cy['request_id']})", **self._snap()}
            s = self.session
            if s and s["active"]:
                self.wake_req = (requested_by, rid)
                self.wake.set()
                snap = self._snap()
                out = {"accepted": True, "reason": "the next continuous scan starts now", **snap}
            else:
                frame = self.grab_frame()
                if frame is None:
                    self.last_error = {"request_id": rid, "requested_by": requested_by, "at": _iso(self.clock()),
                                       "at_s": self.clock(), "error": "no fresh camera picture (none, or older than 2 s)"}
                    snap = self._snap()
                    out = {"accepted": False, **snap}
                else:
                    run = self._new_cycle("manual", requested_by, rid, None, frame)
                    snap = self._snap()
                    out = {"accepted": True, **snap}
        self.publish(snap, None)
        if run:
            if self.run_async:
                threading.Thread(target=self._run_cycle, args=(run,), daemon=True).start()
            else:
                self._run_cycle(run)
        return out

    # continuous mode ----------------------------------------------------------------------------------------------
    def start_continuous(self, pause_s=None, requested_by: str = "page", command_id: str | None = None) -> dict:
        """Start the one continuous loop (idempotent: Start while running only updates the pause)."""
        with self.lock:
            if command_id:
                if command_id in self.seen:
                    return {"accepted": False, "reason": "this command was already taken", **self._snap()}
                self.seen = (self.seen + [command_id])[-SEEN_IDS:]
                self.last_command_id = command_id
            s = self.session
            if s and s["active"]:
                if pause_s is not None:
                    s["pause_s"] = clamp_pause(pause_s, self.pause_min_s)
                    if self.cycle is None and s.get("pause_started_s") is not None:
                        s["next_at_s"] = s["pause_started_s"] + s["pause_s"]
                        self.wake.set()  # re-arm the wait with the new pause
                snap = self._snap()
                out = {"accepted": False, "reason": "continuous recognition is already running", **snap}
                start = None
            else:
                self.sessions += 1
                now = self.clock()
                s = {"id": self.sessions, "active": True, "pause_s": clamp_pause(pause_s, self.pause_min_s), "started_by": requested_by,
                     "started_at": _iso(now), "started_s": now, "cycles": 0, "failures": 0,
                     "phase": "analysing locally", "detail": None, "next_at_s": None}
                self.session, self.wake_req = s, None
                self.wake.clear()
                start = s
                snap = self._snap()
                out = {"accepted": True, **snap}
        self.publish(snap, None)
        if start:
            threading.Thread(target=self._loop, args=(start,), daemon=True).start()
        return out

    def stop_continuous(self, requested_by: str = "page", command_id: str | None = None) -> dict:
        with self.lock:
            if command_id:
                if command_id in self.seen:
                    return {"accepted": False, "reason": "this command was already taken", **self._snap()}
                self.seen = (self.seen + [command_id])[-SEEN_IDS:]
                self.last_command_id = command_id
            s = self.session
            if not s or not s["active"]:
                return {"accepted": False, "reason": "continuous recognition is not running", **self._snap()}
            s["active"] = False
            s["stopped_at"], s["stopped_by"] = _iso(self.clock()), requested_by
            if self.cycle is not None and self.cycle.get("session") == s["id"]:
                self.cycle["discard"] = True  # its late answer must not replace the last observation
            self.wake.set()
            snap = self._snap()
        self.publish(snap, None)
        return {"accepted": True, **snap}

    def _loop(self, s: dict) -> None:
        try:
            while s["active"]:
                with self.lock:
                    other = self.cycle
                if other is not None:  # a manual scan (or a stopped session's last one) is still running
                    other["done"].wait(self.cycle_timeout_s + 5)
                    if not s["active"]:
                        break
                    if other.get("session") not in (None, s["id"]):
                        continue  # not this session's scan: start ours at once
                    ok = bool(other.get("ok"))
                else:
                    with self.lock:
                        if not s["active"]:
                            break
                        req, self.wake_req = self.wake_req, None
                        self.wake.clear()
                        by, rid = req if req else (f"continuous recognition (started by the {s['started_by']})", None)
                        s["cycles"] += 1
                        cy = self._new_cycle("continuous", by, rid or f"c{s['id']}-{s['cycles']}", s, None)
                    self.publish(self.snapshot(), None)
                    ok = self._run_cycle(cy)
                if not s["active"]:
                    break
                s["failures"] = 0 if ok else s["failures"] + 1
                wait = s["pause_s"] if ok else min(self.backoff_max_s, max(s["pause_s"], self.backoff_min_s) * 2 ** (s["failures"] - 1))
                with self.lock:
                    now = self.clock()
                    s["phase"] = "waiting for the next scan" if ok else "error"
                    s["detail"] = (None if ok else f"{(self.last_error or {}).get('error', 'scan failed')}; retrying in {wait:.0f} s")
                    s["pause_started_s"], s["next_at_s"] = now, now + wait
                self._publish_now()
                while s["active"]:  # the pause starts after the complete scan; Start may change it, a request ends it
                    with self.lock:
                        left = s["next_at_s"] - self.clock() if self.wake_req is None else 0
                    if left <= 0 or not s["active"]:
                        break
                    self.wake.wait(left)
                    self.wake.clear()
        finally:
            with self.lock:
                s["active"] = False
                s["next_at_s"] = None
            self._publish_now()

    # one scan -----------------------------------------------------------------------------------------------------
    def _new_cycle(self, kind, requested_by, rid, session, frame) -> dict:
        self.seq += 1
        now = self.clock()
        cy = {"seq": self.seq, "kind": kind, "request_id": rid, "requested_by": requested_by,
              "session": session["id"] if session else None, "started_s": now, "started_at": _iso(now),
              "phase": "analysing locally", "detail": "local model", "phase_since_s": now, "frame0": frame,
              "discard": False, "abandoned": False, "done": threading.Event(),
              "timings": {"pause_before_ms": round((now - self.last_end_s) * 1000) if self.last_end_s else None}}
        self.cycle = cy
        self.stats["scans"] += 1
        return cy

    def _run_cycle(self, cy: dict) -> bool:
        """Run one scan to its end; released after cycle_timeout_s even if a call hangs (its late answer ignored)."""
        if not self.run_async:
            self._body(cy)
            return bool(cy.get("ok"))
        t = threading.Thread(target=self._body, args=(cy,), daemon=True)
        t.start()
        t.join(self.cycle_timeout_s)
        if t.is_alive():
            with self.lock:
                if not cy.get("ended"):
                    cy["abandoned"], cy["ended"], cy["ok"] = True, True, False
                    self.stats["timed_out"] += 1
                    if self.cycle is cy:
                        self.cycle = None
                    self.last_error = {"request_id": cy["request_id"], "requested_by": cy["requested_by"],
                                       "at": _iso(self.clock()), "at_s": self.clock(),
                                       "error": f"the scan did not finish within {self.cycle_timeout_s:.0f} s: released, "
                                                "its late answer will be ignored"}
                    self._log(cy, False, self.last_error["error"], False)
                    cy["done"].set()
            self._publish_now()
        return bool(cy.get("ok"))

    def _phase(self, cy, phase, detail, obs=None):
        with self.lock:
            cy["phase"], cy["detail"], cy["phase_since_s"] = phase, detail, self.clock()
            if obs:
                cy["frame_id"], cy["captured_at"] = obs["frame_id"], obs["captured"]
            snap = self._snap()
        self.publish(snap, None)

    def _body(self, cy: dict) -> None:
        try:
            self._steps(cy)
        except Exception as e:  # noqa: BLE001 - never leave the coordinator busy
            self._end(cy, False, error=f"recognition failed: {str(e)[:200]}")
        finally:
            if not cy.get("ended"):
                self._end(cy, False, error="the scan ended without a result")

    def _steps(self, cy: dict) -> None:
        f = cy.pop("frame0", None) or self.grab_frame()
        if f is None:
            return self._end(cy, False, error="no fresh camera picture (none, or older than 2 s)")
        obs = {"rid": cy["request_id"], "requested_by": cy["requested_by"], "b64": f[0], "frame_id": f[1],
               "captured": f[2], "t0": cy["started_s"], "seq": cy["seq"]}
        self._phase(cy, "analysing locally", "local model", obs)
        t = self.clock()
        self._local(obs)
        cy["timings"]["local_ms"] = round((self.clock() - t) * 1000)
        n = len((obs.get("local") or {}).get("detections") or [])
        self._show_local(cy, obs, f"Local model: {n} box{'es' if n != 1 else ''} in {(obs.get('local') or {}).get('round_trip_ms')} ms; "
                                  "Kimi checks the whole picture next.")
        settled, t = True, self.clock()
        if self.scene:
            self._phase(cy, "analysing locally", "checking the scene is still", obs)
            end = self.clock() + self.settle_max_s
            while True:
                self.sleep(self.settle_gap_s)
                f2 = self.grab_frame()
                if f2 is None:
                    break  # no newer frame: the observed one stands
                s2 = self.scene.sig(f2[0])
                if self.scene.same(obs["sig"], s2):
                    break
                if self.clock() >= end:
                    settled = False
                    break
                self.stats["settle_retakes"] += 1  # the scene moved: the newer frame is the observation
                obs = {**obs, "b64": f2[0], "frame_id": f2[1], "captured": f2[2], "local": None, "summary": None}
                self._phase(cy, "analysing locally", "scene changing: following it", obs)
                self._local(obs)
                n = len((obs.get("local") or {}).get("detections") or [])
                self._show_local(cy, obs, f"Local model: {n} box{'es' if n != 1 else ''}; the scene is still changing.")
        cy["timings"]["settle_ms"] = round((self.clock() - t) * 1000)
        if not settled:
            self.stats["not_settled"] += 1
            return self._fallback(cy, obs, f"the scene kept changing for {self.settle_max_s:.0f} s: no Kimi check")
        if cy.get("discard"):
            return self._end(cy, False)  # stopped before the cloud check: no request is made
        with self.lock:
            if cy.get("abandoned"):
                return  # released after a timeout: it never sends a cloud request any more
            c = self.cache
            reuse = c if (c and self._same(c["sig"], obs["sig"]) and self.clock() - c["at_s"] <= self.reuse_max_age_s) else None
            busy = reuse is None and self.cloud is not None
            if not reuse and not busy:
                self.cloud = {"request_id": obs["rid"], "seq": cy["seq"], "frame_id": obs["frame_id"], "started_s": self.clock()}
        if busy:  # a released (timed-out) scan's request is still in flight: never a second one
            return self._fallback(cy, obs, "an earlier Kimi request is still in flight; no second one is sent")
        mode = "reuse" if reuse else "whole-frame"
        self._phase(cy, "checking with Kimi", "whole-frame check" if mode == "whole-frame" else
                    f"reusing the Kimi check of {_hms(reuse['captured_at'])} (scene unchanged)", obs)
        kw = {"request_id": obs["rid"], "frame_id": obs["frame_id"], "cloud": mode}
        if self.local_detect:
            kw["local"] = obs.get("local")
        if reuse:
            kw["reuse"] = {"objects": reuse["objects"], "request_id": reuse["request_id"],
                           "captured_at": reuse["captured_at"], **(reuse.get("extra") or {})}
        res, err, t = None, None, self.clock()
        try:
            res = self.recognize(obs["b64"], obs["captured"], **kw) if (self.local_detect or self.scene) else \
                self.recognize(obs["b64"], obs["captured"])
            if not isinstance(res, dict) or res.get("error") or not res.get("visible_items"):
                raise RuntimeError((res or {}).get("error") if isinstance(res, dict) else "no result")
        except Exception as e:  # noqa: BLE001
            res, err = None, str(e)[:240]
        finally:
            with self.lock:
                if self.cloud and self.cloud.get("seq") == cy["seq"]:
                    self.cloud = None
        cy["timings"].update(cloud_mode=mode, cloud_ms=round((self.clock() - t) * 1000))
        h = (res or {}).get("hybrid") or {}
        cloud_ok = res is not None and (h.get("cloud") or {}).get("status", "ok") in ("ok", "reused")
        with self.lock:
            self.stats["cloud_whole_frame" if mode == "whole-frame" else "cloud_reused"] += 1
            if not cloud_ok:
                self.stats["cloud_failed"] += 1
            if res is not None and mode == "whole-frame" and cloud_ok and h.get("cloud_objects") is not None:
                # a valid check of that scene, kept for reuse even if this scan's result is discarded
                self.cache = {"sig": obs["sig"], "objects": h["cloud_objects"], "extra": h.get("cloud_extra") or {},
                              "request_id": obs["rid"], "captured_at": obs["captured"], "at_s": self.clock()}
        if res is None:
            return self._fallback(cy, obs, f"the recognition service did not answer ({err})")
        obs["summary"] = _summary(obs, res, mode, reuse)
        entry = self._entry(obs, res["visible_items"], "final", res)
        error = None if cloud_ok else (f"Kimi check failed ({(h.get('cloud') or {}).get('error', 'no answer')}): "
                                       "identities and counts stay uncertain")
        self._end(cy, cloud_ok, entry, obs["b64"], obs["sig"], error)

    def _local(self, obs: dict) -> None:
        obs["sig"] = self.scene.sig(obs["b64"]) if self.scene else None
        if not self.local_detect:
            return
        t = self.clock()
        try:
            r = self.local_detect(obs["b64"], obs["rid"])
            if not isinstance(r, dict) or r.get("error"):
                raise RuntimeError((r or {}).get("error") if isinstance(r, dict) else "no answer")
            obs["local"] = {"detections": [{k: d.get(k) for k in ("box", "score", "class_id", "class_scores", "identity")}
                                           for d in r.get("detections") or []],
                            "model": r.get("model_info") or {"name": r.get("model")}, "timing_ms": r.get("timing_ms"),
                            "round_trip_ms": round((self.clock() - t) * 1000)}
            self.stats["local_ok"] += 1
        except Exception as e:  # noqa: BLE001 - without the local model the cloud check still runs
            obs["local"] = {"error": str(e)[:200], "detections": [], "round_trip_ms": round((self.clock() - t) * 1000)}
            self.stats["local_failed"] += 1

    def _same(self, a, b) -> bool:
        return bool(self.scene) and a is not None and b is not None and self.scene.same(a, b)

    def _entry(self, obs: dict, visible_items: dict, stage: str, result: dict | None = None) -> dict:
        done = self.clock()
        loc = obs.get("local") or {}
        return {"request_id": obs["rid"], "requested_by": obs["requested_by"], "frame_id": obs["frame_id"],
                "captured_at": obs["captured"], "captured_at_s": _parse(obs["captured"], obs["t0"]),
                "finished_at": _iso(done), "finished_at_s": done, "duration_ms": round((done - obs["t0"]) * 1000),
                "visible_items": visible_items, "stage": stage, "scan": obs.get("seq"),
                "local_ms": loc.get("round_trip_ms"), "local_boxes": len(loc.get("detections") or []),
                "local_error": loc.get("error"),
                "product_set": ((result or {}).get("product_set") or {}).get("set_id"), "model": (result or {}).get("model"),
                "summary": obs.get("summary"),
                "result": {k: v for k, v in (result or {"local": loc}).items() if k not in ("frame", "visible_items")}}

    def _show_local(self, cy, obs: dict, note: str) -> None:
        """Show the local boxes at once, unless the shown result is of the same scene (it stays until the merge)."""
        if not self.local_detect or (obs.get("local") or {}).get("error"):
            return
        with self.lock:
            if cy.get("discard") or cy.get("abandoned") or cy["seq"] < self.applied_seq:
                return
            same_as_shown = self.current is not None and self.current.get("stage") != "local" and self._same(self.current_sig, obs["sig"])
            if not same_as_shown:
                obs["summary"] = note
                self.current = self._entry(obs, local_view(obs, note), "local")
                self.current_frame, self.current_sig = obs["b64"], obs["sig"]
            snap = self._snap()
        self.publish(snap, None if same_as_shown else obs["b64"])

    def _fallback(self, cy, obs, why):
        """No usable cloud answer: the local boxes (if any) with every count uncertain, else the previous result stays;
        the error is recorded either way."""
        loc = obs.get("local") or {}
        entry = None
        if self.local_detect and not loc.get("error"):
            obs["summary"] = f"Local boxes only: {why}. Counts are uncertain, not zero."
            entry = self._entry(obs, local_view(obs, obs["summary"]), "local")
        self._end(cy, False, entry, obs["b64"], obs.get("sig"), why)

    def _end(self, cy, ok, entry=None, frame=None, sig=None, error=None):
        pub = None
        with self.lock:
            if cy.get("ended"):
                return  # a released scan's late end: ignored
            cy["ended"], cy["ok"] = True, ok
            discard = cy.get("discard") or cy["seq"] < self.applied_seq
            now = self.clock()
            if discard:
                self.stats["discarded"] += 1
                self.discarded = {"request_id": cy["request_id"], "scan": cy["seq"], "at": _iso(now),
                                  "why": "stopped: the result of the scan running at Stop is not shown"}
                if self.current is not None and self.current.get("scan") == cy["seq"]:  # its local boxes were shown
                    if self.last_final:
                        self.current, self.current_frame, self.current_sig = self.last_final
                        pub = self.current_frame
                    else:
                        self.current = self.current_frame = self.current_sig = None
            else:
                if entry is not None:
                    self.current, self.current_frame, self.current_sig = entry, frame, sig
                    self.applied_seq = cy["seq"]
                    if entry["stage"] == "final":
                        self.last_final = (entry, frame, sig)
                    pub = frame
                self.last_error = None if (ok or error is None) else {
                    "request_id": cy["request_id"], "requested_by": cy["requested_by"], "frame_id": cy.get("frame_id"),
                    "at": _iso(now), "at_s": now, "error": error}
            cy["timings"]["total_ms"] = round((now - cy["started_s"]) * 1000)
            self._log(cy, ok, error, discard)
            self.last_end_s = now
            if self.cycle is cy:
                self.cycle = None
            snap = self._snap()
        cy["done"].set()
        self.publish(snap, pub)

    def _log(self, cy, ok, error, discarded):
        t = cy["timings"]
        self.log = (self.log + [{"scan": cy["seq"], "kind": cy["kind"], "request_id": cy["request_id"],
                                 "requested_by": cy["requested_by"], "started_at": cy["started_at"],
                                 "finished_at": _iso(self.clock()), "ok": ok, "discarded": discarded,
                                 "error": error, "frame_id": cy.get("frame_id"), "captured_at": cy.get("captured_at"),
                                 **{k: t.get(k) for k in ("local_ms", "settle_ms", "cloud_mode", "cloud_ms", "total_ms",
                                                          "pause_before_ms")}}])[-LOG_CYCLES:]


def _summary(obs: dict, res: dict, mode: str, reuse) -> str:
    """One line for people: what the local model and Kimi each did for this result."""
    loc = obs.get("local")
    h = res.get("hybrid") or {}
    parts = []
    if loc is None:
        pass
    elif loc.get("error"):
        parts.append(f"Local model unavailable ({loc['error'][:80]})")
    else:
        n = len(loc.get("detections") or [])
        parts.append(f"Local model: {n} box{'es' if n != 1 else ''} in {loc.get('round_trip_ms')} ms")
    c = h.get("cloud") or {}
    if mode == "reuse":
        parts.append(f"Kimi: reused the check of {_hms(reuse['captured_at'])} (scene unchanged)")
    elif c.get("status") == "failed":
        parts.append("Kimi check failed: counts uncertain, not zero")
    elif h:
        parts.append(f"Kimi whole-frame check in {round((c.get('ms') or 0) / 1000)} s")
    if h.get("conflicts"):
        parts.append(f"{len(h['conflicts'])} conflict(s) between local model and Kimi")
    return "; ".join(parts) + "." if parts else ""


def _hms(iso) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return str(iso)


def _parse(iso: str, default: float) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return default


def for_backend(snap: dict) -> dict:
    """The snapshot without the raw recognition details (those stay on the setup page's diagnostics)."""
    out = dict(snap)
    if out.get("current"):
        out["current"] = {k: v for k, v in out["current"].items() if k != "result"}
    return out
