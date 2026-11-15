"""Brief live run on the board: reuse the app's own preview frames (socket.io "frame" on localhost:7000; no second
camera capture), run the local detector on the newest frame about once a second, record runtime only.

  ~/valio-onnx/venv/bin/python live.py model.onnx --seconds 30 --threads 2
Frames are not saved (people may be in view). Detections are reported as counts per class id only.
"""
import argparse
import base64
import io
import json
import os
import resource
import statistics
import threading
import time

import socketio
from PIL import Image

import yolox_local as Y

ap = argparse.ArgumentParser()
ap.add_argument("model"); ap.add_argument("--seconds", type=float, default=30); ap.add_argument("--threads", type=int, default=2)
a = ap.parse_args()
det = Y.Detector(a.model, threads=a.threads)
latest = {"jpg": None, "at": 0.0, "n": 0}
lock = threading.Lock()
sio = socketio.Client(reconnection=False)


@sio.on("frame")
def on_frame(m):
    with lock:
        latest.update(jpg=m["image"], at=time.time(), n=latest["n"] + 1)


sio.connect("http://localhost:7000", transports=["websocket", "polling"])
t_end, lat, ages, counts, cpu0, w0 = time.time() + a.seconds, [], [], {}, os.times(), time.perf_counter()
while time.time() < t_end:
    with lock:
        jpg, at = latest["jpg"], latest["at"]
    if jpg:
        img = Image.open(io.BytesIO(base64.b64decode(jpg))); img.load()
        ages.append(time.time() - at)
        r = det(img)
        lat.append(r["timing_ms"]["total"])
        for k, v in r["counts"].items():
            counts[k] = counts.get(k, 0) + v
    time.sleep(max(0.0, 1.0 - (lat[-1] / 1000 if lat else 0)))
c1, wall = os.times(), time.perf_counter() - w0
sio.disconnect()
lat.sort()
print(json.dumps({"runs": len(lat), "preview_frames_received": latest["n"], "frame_size": list(img.size) if lat else None,
                  "latency_ms": {"median": round(statistics.median(lat), 1), "max": round(lat[-1], 1)} if lat else None,
                  "frame_age_at_use_ms": round(1000 * statistics.median(ages), 1) if ages else None,
                  "avg_cpu_cores": round(((c1.user - cpu0.user) + (c1.system - cpu0.system)) / wall, 2),
                  "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                  "detections_by_class_total": counts}, indent=1))
