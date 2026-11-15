"""Model registry and Modal prices for the recognition experiment. No third-party imports here:
the Modal CLI imports this file locally in its own environment.

Revisions and licences were read from the Hugging Face model API on 2026-11-14 05:50 EET
(`sha` and `cardData.license` of https://huggingface.co/api/models/<repo>).
Prices are `modal billing rates` for workspace muodikas, recorded 2026-11-14 05:47 EET.
"""

from __future__ import annotations

MODELS = {
    "dinov2-small": {
        "kind": "embedding",
        "repo": "facebook/dinov2-small",
        "revision": "ed25f3a31f01632728cabb09d1542f84ab7b0056",
        "licence": "apache-2.0",
        "params": "22M",
        "hardware_gpu": "T4",
    },
    "clip-vit-b32": {
        "kind": "embedding",  # also zero-shot by text prompt
        "repo": "laion/CLIP-ViT-B-32-laion2B-s34B-b79K",
        "revision": "1a25a446712ba5ee05982a381eed697ef9b435cf",
        "licence": "mit (model card: weights trained on LAION-2B, card asks for research use)",
        "params": "151M",
        "hardware_gpu": "T4",
    },
    "qwen2.5-vl-7b": {
        "kind": "vlm",
        "repo": "Qwen/Qwen2.5-VL-7B-Instruct",
        "revision": "cc594898137f460bfe9f0759e9844b3ce807cfb5",
        "licence": "apache-2.0",
        "params": "8.3B (bf16, about 16.6 GB of weights)",
        "hardware_gpu": "L4",
    },
}

# USD per hour, from `modal billing rates` (2026-11-14 05:47 EET).
RATES = {
    "cpu_core_h": 0.0473,
    "mem_gib_h": 0.008,
    "T4": 0.59,
    "L4": 0.80,
    "A10G": 1.10,
    "volume_gib_month": 0.09,
}

# What each Modal function requests, per hardware profile (RECOG_HARDWARE=cpu|gpu at launch). GPU functions
# need a payment method on the workspace: on 2026-11-14 05:50 Modal refused the T4 with
# "Please add a payment method to use T4 GPU functions.", so the default profile is cpu.
PROFILES = {
    "gpu": {"embed": {"gpu": "T4", "cpu": 2.0, "memory_mib": 8192},
            "vlm": {"gpu": "L4", "cpu": 4.0, "memory_mib": 32768}},
    "cpu": {"embed": {"gpu": None, "cpu": 4.0, "memory_mib": 8192},
            "vlm": {"gpu": None, "cpu": 8.0, "memory_mib": 40960}},
}
DRIVER = {"gpu": None, "cpu": 1.0, "memory_mib": 2048}


def resources(hw: str) -> dict:
    return {**PROFILES[hw], "driver": DRIVER}


def hourly_cost(r: dict) -> float:
    """USD per hour for one container with the requested resources r (Modal bills at least the request)."""
    gpu = RATES[r["gpu"]] if r["gpu"] else 0.0
    return round(gpu + r["cpu"] * RATES["cpu_core_h"] + (r["memory_mib"] / 1024) * RATES["mem_gib_h"], 4)


def cost_per_1000(r: dict, seconds_per_image: float) -> float:
    """Warm-container cost of 1,000 images at the measured per-image latency (no cold start)."""
    return round(hourly_cost(r) * seconds_per_image * 1000 / 3600, 4)


# Identity candidates that are not learned models (not run yet; listed so the comparison covers them).
CANDIDATES = {
    "barcode-gtin": {
        "kind": "barcode",
        "how": "decode EAN/GTIN in the slot crop, map through the product file's GTINs (recog.core.barcode_label)",
        "on_board": "Arduino App Lab brick arduino:camera_code_detection (barcodes and QR codes)",
        "assumption": "the 480 x 360 preview frames that tools/record_sequence.py saves are likely too coarse to "
                      "decode an EAN-13 on a carton at shelf distance; untested",
    },
}
