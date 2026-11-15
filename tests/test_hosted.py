"""The hosted demo end to end, without the box: the page, the shared observation, and one recorded shelf frame sent
through the backend to the recognition service, as the box sends it. Runs only when MISE_URL names a deployment:

    MISE_URL=https://alejo-end--mise.us-east.modal.run uv run pytest tests/test_hosted.py -v
"""
import base64
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

URL = os.environ.get("MISE_URL", "").rstrip("/")
pytestmark = pytest.mark.skipif(not URL, reason="MISE_URL is not set")
FRAME = Path(__file__).parent / "device/sequences/steady-01/frames/000003.jpg"


def test_the_page_and_the_shared_observation_answer():
    assert httpx.get(f"{URL}/approve", timeout=30).status_code == 200
    o = httpx.get(f"{URL}/observations/visible", timeout=30).json()
    assert "snapshot" in o and "box_online" in o


def test_the_recognition_service_is_reachable_through_the_backend():
    h = httpx.get(f"{URL}/recognition/health", timeout=60).json()
    assert h["ok"] and h["product_set"]["schema"] == "shelf-product-set/2"


def test_a_recorded_frame_is_recognised_through_the_backend():
    body = {"image_b64": base64.b64encode(FRAME.read_bytes()).decode(), "captured_at": datetime.now(timezone.utc).isoformat(),
            "request_id": "hosted-test", "frame_id": 3, "local": None, "cloud": "whole-frame"}
    r = httpx.post(f"{URL}/recognition/recognize/hybrid", json=body, timeout=150)
    assert r.status_code == 200, r.text[:300]
    assert isinstance(r.json(), dict) and not r.json().get("error")
