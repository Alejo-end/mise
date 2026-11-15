"""Board benchmark on saved raw camera frames: init time, latency, peak memory, CPU use, detections per frame.

  ~/valio-onnx/venv/bin/python bench.py model.onnx frames/ --threads 4 --repeats 10 --variant bgr255
Runtime figures only; detections are observations on a handful of frames, not accuracy.
"""
import argparse
import glob
import json
import os
import resource
import statistics
import time

from PIL import Image

import yolox_local as Y

ap = argparse.ArgumentParser()
ap.add_argument("model"); ap.add_argument("frames"); ap.add_argument("--threads", type=int, default=4)
ap.add_argument("--repeats", type=int, default=10); ap.add_argument("--variant", default="bgr255")
a = ap.parse_args()
wall0, cpu0 = time.perf_counter(), os.times()
det = Y.Detector(a.model, threads=a.threads, variant=a.variant)
frames = sorted(glob.glob(os.path.join(a.frames, "*.jpg")))
for f in frames[:2]:
    det(Image.open(f))  # warm-up
lat, per_frame = [], {}
bench_wall0, bench_cpu0 = time.perf_counter(), os.times()
for f in frames:
    img = Image.open(f); img.load()
    runs = [det(img) for _ in range(a.repeats)]
    lat += [r["timing_ms"]["total"] for r in runs]
    r = runs[-1]
    per_frame[os.path.basename(f)] = {"counts": r["counts"], "detections": [(d["label"], d["score"], d["box_px"]) for d in r["detections"]],
                                      "max_raw_score": r["max_raw_score"], "timing_ms": r["timing_ms"]}
bench_wall, c1 = time.perf_counter() - bench_wall0, os.times()
cpu_cores = ((c1.user - bench_cpu0.user) + (c1.system - bench_cpu0.system)) / bench_wall
lat.sort()
print(json.dumps({"model_bytes": os.path.getsize(a.model), "versions": det.versions, "threads": a.threads, "variant": a.variant,
                  "init_s": round(det.init_s, 3), "runs": len(lat), "latency_ms": {
                      "median": round(statistics.median(lat), 1), "p90": round(lat[int(0.9 * (len(lat) - 1))], 1),
                      "max": round(lat[-1], 1), "min": round(lat[0], 1)},
                  "avg_cpu_cores_during_benchmark": round(cpu_cores, 2),
                  "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                  "frames": per_frame}, indent=1))
