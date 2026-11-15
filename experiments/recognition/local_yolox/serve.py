"""Local detection service on the board (port 8200), isolated from the app: it reads the app's own preview frames
(socket.io "frame" on localhost:7000; no second camera capture) and runs the YOLOX-nano ONNX model on request.
EXPERIMENTAL and observational: it never calls the purchasing backend.

Which products a model knows comes only from its manifest, model.json next to model.onnx (written from the trainer's
information, never inferred from predictions): the product set it was trained for, its class order as identities of
that set, and its preprocessing. Without a class order, classes stay numeric. A model trained for another product set
than the shelf's active one is reported as such (the page then uses it for diagnostics only, with no automatic cloud
fallback and no counts).

  ~/valio-onnx/venv/bin/python serve.py model.onnx --port 8200 --threads 2
GET /health, GET /detect?threshold=0.3&rid=<request id> (the newest preview frame), POST /detect with a JPEG body
(the box's recognition coordinator: exactly the frame it recognises). CORS allows only pages served on port 7000.
Starts idle: it listens to the preview only while detections are requested and disconnects after 20 s without
requests. Every /detect answers within about 10 s (frame wait <= 4 s, queue wait <= 5 s, inference ~0.3 s) and
logs one line (request id, status, waits, inference time) to stdout, i.e. the journal under systemd. Under systemd
(Type=notify) it reports READY=1 once the model is loaded and the port is bound.
"""
import argparse
import base64
import hashlib
import io
import json
import os
import re
import socket
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import socketio
from PIL import Image

import yolox_local as Y

ap = argparse.ArgumentParser()
ap.add_argument("model"); ap.add_argument("--port", type=int, default=8200); ap.add_argument("--threads", type=int, default=2)
a = ap.parse_args()
STARTED = time.time()
MODEL_PATH = os.path.realpath(a.model)
MODEL_HASH = hashlib.sha256(open(MODEL_PATH, "rb").read()).hexdigest()[:16]


def read_manifest(model_path: str) -> dict:
    """model.json beside the model. Missing or unreadable: nothing is known about classes or the product set."""
    p = os.path.join(os.path.dirname(model_path), "model.json")
    try:
        m = json.load(open(p))
    except (OSError, ValueError) as e:
        return {"error": f"no usable model.json ({type(e).__name__})", "classes": None, "product_set": None}
    return m if isinstance(m, dict) else {"error": "model.json is not an object", "classes": None, "product_set": None}


MANIFEST = read_manifest(MODEL_PATH)
_pre = MANIFEST.get("preprocessing") or {}
det = Y.Detector(MODEL_PATH, threads=a.threads, variant=_pre.get("variant") or "bgr255")
CLASSES = MANIFEST.get("classes")
if CLASSES is not None and (not isinstance(CLASSES, list) or len(CLASSES) != det.num_classes):
    MANIFEST["error"] = f"model.json lists {len(CLASSES) if isinstance(CLASSES, list) else CLASSES!r} classes, the model has {det.num_classes}"
    CLASSES = None
if MANIFEST.get("sha256") and not str(MANIFEST["sha256"]).startswith(MODEL_HASH):
    MANIFEST["error"] = "model.json describes another file (sha256 differs)"
    CLASSES, MANIFEST["product_set"] = None, None
MODEL_INFO = {"name": MANIFEST.get("name"), "product_set": MANIFEST.get("product_set"), "role": MANIFEST.get("role"),
              "classes": CLASSES, "classes_known": CLASSES is not None, "num_classes": det.num_classes,
              "class_map": "verified" if CLASSES is not None else MANIFEST.get("class_map_status", "unknown"),
              "output": MANIFEST.get("output"),
              "preprocessing": {"variant": det.variant, "status": _pre.get("status", "provisional"), "basis": _pre.get("basis")},
              "note": MANIFEST.get("note"), "error": MANIFEST.get("error"), "sha256_16": MODEL_HASH}
latest = {"jpg": None, "at": None}
lock, run_lock = threading.Lock(), threading.Lock()
PAGE_PORT = 7000
FRAME_WAIT_S, QUEUE_WAIT_S, FRESH_S = 4.0, 5.0, 2.0
IDLE_DISCONNECT_S = 20  # receiving the ~6 fps preview costs ~0.3 of a core, so listen only while asked
want, last_request = threading.Event(), {"t": 0.0}
stats = {"requests": 0, "succeeded": 0, "errors": 0, "last": None}


def log(**kw):
    print(json.dumps({"t": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), **kw}), flush=True)


def frames():
    while True:
        want.wait()
        sio = socketio.Client(reconnection=False)

        @sio.on("frame")
        def on_frame(m):
            with lock:
                latest.update(jpg=m["image"], at=time.time())
        try:
            sio.connect("http://localhost:7000", transports=["websocket", "polling"])
            log(event="preview connected")
            while sio.connected and time.time() - last_request["t"] < IDLE_DISCONNECT_S:
                time.sleep(0.5)
        except Exception as e:
            log(event="preview connection failed", error=str(e)[:160])
            time.sleep(2)
        finally:
            try:
                sio.disconnect()
            except Exception:
                pass
        if time.time() - last_request["t"] >= IDLE_DISCONNECT_S:
            want.clear()  # a request arriving now sets it again while it waits for a frame
            log(event="preview disconnected (idle)")


def fresh_frame(wait_s=FRAME_WAIT_S):
    """Newest preview frame not older than FRESH_S, connecting to the app's preview if needed."""
    end = time.time() + wait_s
    while True:
        last_request["t"] = time.time(); want.set()
        with lock:
            jpg, at = latest["jpg"], latest["at"]
        if jpg and time.time() - at <= FRESH_S:
            return jpg, at
        if time.time() > end:
            return None, None
        time.sleep(0.05)


def sd_notify(msg: str):
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.connect(addr); s.sendall(msg.encode())


class H(BaseHTTPRequestHandler):
    timeout = 15  # a stalled client connection cannot hold a handler thread for long

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin") or ""
        if urlparse(origin).port == PAGE_PORT:  # only the board page (port 7000): answers carry a camera picture
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            with lock:
                age = None if latest["at"] is None else round(time.time() - latest["at"], 2)
            return self._send(200, {"ok": True, "experimental": True, "model_sha256_16": MODEL_HASH, "model_info": MODEL_INFO,
                                    "versions": det.versions,
                                    "threads": a.threads, "variant": det.variant, "init_s": round(det.init_s, 3),
                                    "uptime_s": round(time.time() - STARTED), "listening_to_preview": want.is_set(),
                                    "latest_frame_age_s": age, **stats})
        if u.path != "/detect":
            return self._send(404, {"error": "not found"})
        t0 = time.perf_counter()
        q = parse_qs(u.query)
        rid = (q.get("rid") or [""])[0]
        rid = rid if re.fullmatch(r"[A-Za-z0-9._-]{1,40}", rid) else f"s{int(time.time() * 1000) % 10**8}"
        diag = {"rid": rid, "client": self.client_address[0]}
        stats["requests"] += 1
        try:
            thr = float(q.get("threshold", ["0.3"])[0])
        except ValueError:
            thr = -1
        if not 0.0 < thr <= 1.0:
            return self._fail(400, "threshold must be a number in (0, 1]", diag, t0)
        jpg, at = fresh_frame()
        diag["frame_wait_ms"] = round((time.perf_counter() - t0) * 1000)
        if not jpg:
            return self._fail(503, f"no fresh camera frame from the app within {FRAME_WAIT_S:.0f} s (is the camera app running?)", diag, t0)
        img = Image.open(io.BytesIO(base64.b64decode(jpg))); img.load()
        self._detect(img, thr, diag, t0, frame=jpg,
                     captured_at=datetime.fromtimestamp(at, timezone.utc).astimezone().isoformat(timespec="milliseconds"))

    def do_POST(self):
        """POST /detect?threshold=0.3&rid=<id> with a JPEG body: the box's recognition coordinator sends the exact frame
        it recognises (so the local model and the cloud check see the same picture; no frame is read here)."""
        u = urlparse(self.path)
        if u.path != "/detect":
            return self._send(404, {"error": "not found"})
        t0 = time.perf_counter()
        q = parse_qs(u.query)
        rid = (q.get("rid") or [""])[0]
        rid = rid if re.fullmatch(r"[A-Za-z0-9._-]{1,40}", rid) else f"s{int(time.time() * 1000) % 10**8}"
        diag = {"rid": rid, "client": self.client_address[0], "frame": "posted"}
        stats["requests"] += 1
        try:
            thr = float(q.get("threshold", ["0.3"])[0])
        except ValueError:
            thr = -1
        if not 0.0 < thr <= 1.0:
            return self._fail(400, "threshold must be a number in (0, 1]", diag, t0)
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= 4_000_000:
            return self._fail(413, "send one JPEG frame of at most 4 MB", diag, t0)
        body = self.rfile.read(n)
        if not body.startswith(b"\xff\xd8"):
            return self._fail(415, "the body is not a JPEG", diag, t0)
        try:
            img = Image.open(io.BytesIO(body)); img.load()
        except Exception as e:  # noqa: BLE001
            return self._fail(400, f"unreadable JPEG ({type(e).__name__})", diag, t0)
        self._detect(img, thr, diag, t0)

    def _detect(self, img, thr, diag, t0, frame=None, captured_at=None):
        t1 = time.perf_counter()
        if not run_lock.acquire(timeout=QUEUE_WAIT_S):  # one inference at a time: the camera app keeps its CPU
            diag["queue_wait_ms"] = round((time.perf_counter() - t1) * 1000)
            return self._fail(503, f"busy: another detection did not finish within {QUEUE_WAIT_S:.0f} s", diag, t0)
        try:
            diag["queue_wait_ms"] = round((time.perf_counter() - t1) * 1000)
            r = det(img, score_thr=thr)
        finally:
            run_lock.release()
        diag["server_total_ms"] = round((time.perf_counter() - t0) * 1000)
        for d in r["detections"]:  # identities only from the manifest's class order
            if CLASSES is not None:
                d["identity"] = CLASSES[d["class_id"]]
                d["label"] = CLASSES[d["class_id"]]
                d["class_scores"] = dict(zip(CLASSES, d["class_scores"]))
        r.update(model_info=MODEL_INFO, frame_size=list(img.size), runtime=f"onnxruntime {det.versions['onnxruntime']} CPU, {a.threads} threads",
                 model=MANIFEST.get("name") or os.path.basename(os.path.dirname(MODEL_PATH)), model_sha256_16=MODEL_HASH, experimental=True,
                 diagnostics=diag)
        if frame is not None:
            r.update(frame=frame, captured_at=captured_at)
        stats["succeeded"] += 1
        stats["last"] = {"rid": diag["rid"], "status": 200, "at": time.time()}
        log(event="detect", status=200, inference_ms=r["timing_ms"]["inference"], boxes=len(r["detections"]), **diag)
        self._send(200, r)

    def _fail(self, code, error, diag, t0):
        diag["server_total_ms"] = round((time.perf_counter() - t0) * 1000)
        stats["errors"] += 1
        stats["last"] = {"rid": diag["rid"], "status": code, "at": time.time()}
        log(event="detect", status=code, error=error, **diag)
        return self._send(code, {"error": error, "diagnostics": diag})


threading.Thread(target=frames, daemon=True).start()
server = ThreadingHTTPServer(("0.0.0.0", a.port), H)  # a second instance fails here (port in use) and exits
server.daemon_threads = True
log(event="ready", port=a.port, model_sha256_16=MODEL_HASH, model=MODEL_INFO["name"], product_set=MODEL_INFO["product_set"],
    classes_known=MODEL_INFO["classes_known"], manifest_error=MODEL_INFO["error"], threads=a.threads, init_s=round(det.init_s, 3),
    versions=det.versions)
sd_notify("READY=1")
server.serve_forever()
