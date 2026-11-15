"""Local YOLOX-nano detector for the shelf props (kitchen-restock-yolox-nano-v1, ONNX opset 11). Observational only.

The ONNX graph already decodes YOLOX boxes: output [1, 3549, 8] = [cx, cy, w, h] in 416 x 416 input pixels
((xy + grid) * stride, exp(wh) * stride, strides 8/16/32), then objectness and 3 class values that already passed a
sigmoid. This module therefore only letterboxes the input, multiplies objectness by the class value, filters by
score, suppresses duplicates (class-aware NMS) and maps boxes back to the original frame. It never decodes twice.

Not in the export and therefore NOT known: the class names in index order and the training preprocessing. Classes
stay numeric ("class 0/1/2") until the training or export files say otherwise (serve.py reads them from the model's
manifest, model.json, written from the trainer's information). Each box also carries the raw class values
(class_scores, in class order) so that a close second class (a similar flavour) can be seen. Preprocessing defaults to the standard
YOLOX one (letterbox top-left, pad 114, BGR, 0-255, no mean/std); the variant is a parameter and is reported.
Stdlib + numpy + Pillow + onnxruntime only (the board has no OpenCV).
"""

from __future__ import annotations

import time

import numpy as np
from PIL import Image

INPUT = 416
VARIANTS = ("bgr255", "rgb255", "rgb_imagenet")
CLASS_NAMES: dict[int, str] = {}  # empty on purpose: the class order is not in the export


def letterbox(img: Image.Image, size: int = INPUT, variant: str = "bgr255"):
    """YOLOX preproc: resize keeping the aspect ratio, place top-left on a 114-grey square. Returns (tensor, ratio)."""
    w, h = img.size
    r = min(size / w, size / h)
    nw, nh = int(w * r), int(h * r)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[:nh, :nw] = np.asarray(img.convert("RGB").resize((nw, nh), Image.BILINEAR))
    x = canvas.astype(np.float32)
    if variant == "bgr255":
        x = x[:, :, ::-1]
    elif variant == "rgb_imagenet":
        x = (x / 255.0 - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    elif variant != "rgb255":
        raise ValueError(variant)
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None]).astype(np.float32), r


def nms(boxes: np.ndarray, scores: np.ndarray, thr: float) -> list[int]:
    order, keep = scores.argsort()[::-1], []
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0]); yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2]); yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        area = lambda b: (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
        iou = inter / (area(boxes[i:i + 1]) + area(boxes[order[1:]]) - inter + 1e-9)
        order = order[1:][iou <= thr]
    return keep


def postprocess(out: np.ndarray, ratio: float, frame_size: tuple[int, int], score_thr: float = 0.3,
                nms_thr: float = 0.45) -> list[dict]:
    p = out[0]
    cls = p[:, 5:].argmax(1)
    score = p[:, 4] * p[np.arange(len(p)), 5 + cls]
    m = score >= score_thr
    if not m.any():
        return []
    p, cls, score = p[m], cls[m], score[m]
    b = np.stack([p[:, 0] - p[:, 2] / 2, p[:, 1] - p[:, 3] / 2, p[:, 0] + p[:, 2] / 2, p[:, 1] + p[:, 3] / 2], 1) / ratio
    W, H = frame_size
    b[:, [0, 2]] = b[:, [0, 2]].clip(0, W); b[:, [1, 3]] = b[:, [1, 3]].clip(0, H)
    dets = []
    for c in np.unique(cls):
        idx = np.where(cls == c)[0]
        for k in nms(b[idx], score[idx], nms_thr):
            i = idx[k]
            x1, y1, x2, y2 = (float(v) for v in b[i])
            dets.append({"class_id": int(c), "label": CLASS_NAMES.get(int(c), f"class {int(c)}"),
                         "score": round(float(score[i]), 4), "objectness": round(float(p[i, 4]), 4),
                         "class_scores": [round(float(v), 4) for v in p[i, 5:]],
                         "box_px": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                         "box": [round(x1 / W, 4), round(y1 / H, 4), round((x2 - x1) / W, 4), round((y2 - y1) / H, 4)]})
    return sorted(dets, key=lambda d: -d["score"])


class Detector:
    def __init__(self, path: str, threads: int = 4, variant: str = "bgr255"):
        import onnxruntime as ort
        t = time.perf_counter()
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        self.session = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
        shape = self.session.get_outputs()[0].shape
        self.num_classes = int(shape[-1]) - 5 if isinstance(shape[-1], int) else None
        self.init_s = time.perf_counter() - t
        self.variant = variant
        self.versions = {"onnxruntime": ort.__version__, "numpy": np.__version__, "pillow": Image.__version__}

    def __call__(self, img: Image.Image, score_thr: float = 0.3, nms_thr: float = 0.45) -> dict:
        t0 = time.perf_counter()
        x, r = letterbox(img, variant=self.variant)
        t1 = time.perf_counter()
        out = self.session.run(None, {"images": x})[0]
        t2 = time.perf_counter()
        dets = postprocess(out, r, img.size, score_thr, nms_thr)
        t3 = time.perf_counter()
        counts: dict[str, int] = {}
        for d in dets:
            counts[d["label"]] = counts.get(d["label"], 0) + 1
        return {"detections": dets, "counts": counts, "variant": self.variant, "score_threshold": score_thr,
                "nms_iou": nms_thr, "timing_ms": {"preprocess": round((t1 - t0) * 1000, 1),
                                                 "inference": round((t2 - t1) * 1000, 1),
                                                 "postprocess": round((t3 - t2) * 1000, 1),
                                                 "total": round((t3 - t0) * 1000, 1)},
                "max_raw_score": round(float((out[0][:, 4:5] * out[0][:, 5:]).max()), 4),
                "note": "class names unknown (not in the export); local model scores are not measured accuracy"}
