"""Serve the mise web app and the shelf recognition service on Modal, with a public link for the judges.

    uvx modal deploy deploy/modal_app.py        (from the repository root)

Two web functions in one app:

- `web` (label `mise`) is the page and the backend. The link runs on the stand-in catalog (CATALOG=local), never on
  the Valio challenge files, which must not leave the challenge. The head chef's demo PIN shows on the page
  (SHOW_PINS=1), so a judge can approve an order. It is a demo PIN for this link only. The audit log and the signing
  key live on a Modal volume, so approvals and the scorecard survive a restart. Exactly one container runs, because
  the agent keeps its open approvals in memory.
- `recognition` (label `mise-recognition`) is the shelf recognition service (experiments/recognition/shelf_service):
  Kimi on Verda with the labelled reference photos. Its reference photos and model cache live on their own volume.
  The Verda key comes from the Modal secret `mise-verda`.

The shelf box talks only to the `mise` link: it sends its recognition calls to `<backend>/recognition/...`, and the
backend passes them on to the recognition service. The box's own local model still runs on the board.
"""
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parent.parent
DEMO_PIN = "2468"  # a demo PIN for the public link, shown on the page

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_pyproject(str(ROOT / "pyproject.toml"))
    .pip_install("numpy", "pillow")
    .env({"CATALOG": "local", "RULES_FILE": "/root/app/rules/school_kitchen.yaml", "SHOW_PINS": "1",
          "APPROVER_PINS": f"{DEMO_PIN}:Head chef", "OPEN_FOOD_FACTS": "1",
          "AUDIT_DB": "/state/audit.sqlite", "GATEWAY_KEY": "/state/gateway_key.pem"})
    .add_local_dir(ROOT, remote_path="/root/app",
                   ignore=[".venv", ".git", "**/__pycache__", ".pytest_cache", ".env", "data", "docs/decks",
                           "enclosure", "screen", "tests", "fixtures/cache", "experiments"])
    .add_local_dir(ROOT / "enclosure" / "fonts", remote_path="/root/app/enclosure/fonts")   # the cursive of the name
)

recognition_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("libgl1", "libglib2.0-0")
    .pip_install("torch>=2.6,<3", index_url="https://download.pytorch.org/whl/cpu")
    .pip_install("fastapi>=0.115,<1", "httpx>=0.27,<1", "numpy>=2,<3", "pillow>=11,<14",
                 "opencv-python-headless>=4.10,<5", "transformers==4.57.1")
    .env({"SHELF_STORE": "/store/shelf", "SHELF_PRODUCTS": "/root/app/products/active.json"})
    .add_local_dir(ROOT / "experiments" / "recognition", remote_path="/root/app/experiments/recognition",
                   ignore=[".venv", "**/__pycache__", ".pytest_cache", "**/store", "**/results", "**/tests",
                           "**/regression"])
    .add_local_file(ROOT / "products" / "active.json", "/root/app/products/active.json")
)

state = modal.Volume.from_name("mise-state", create_if_missing=True)
recognition_store = modal.Volume.from_name("mise-recognition-store", create_if_missing=True)
app = modal.App("mise", image=image)


@app.function(image=recognition_image, secrets=[modal.Secret.from_name("mise-verda")], cpu=2, memory=4096,
              min_containers=1, max_containers=1, timeout=600, volumes={"/store": recognition_store})
@modal.concurrent(max_inputs=8)
@modal.asgi_app(label="mise-recognition")
def recognition():
    import os
    import sys

    os.chdir("/root/app/experiments/recognition")
    sys.path.insert(0, "/root/app/experiments/recognition")
    from shelf_service.app import create_app
    return create_app()


@app.function(min_containers=1, max_containers=1, timeout=3600, volumes={"/state": state})
@modal.concurrent(max_inputs=50)
@modal.asgi_app(label="mise")
def web():
    import os
    import sys

    if not os.environ.get("RECOGNITION_URL"):
        try:
            os.environ["RECOGNITION_URL"] = recognition.get_web_url()
        except Exception as e:  # the page still works; the box's recognition calls then answer 404
            print(f"No recognition service URL: {e}")
    os.chdir("/root/app")
    sys.path.insert(0, "/root/app")
    from backend.app import app as fastapi_app
    return fastapi_app
