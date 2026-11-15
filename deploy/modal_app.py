"""Serve the mise web app on Modal, with a public link for the judges.

    uvx modal deploy deploy/modal_app.py        (from the repository root)

The link runs on the stand-in catalog (CATALOG=local), never on the Valio challenge files, which must
not leave the challenge. The head chef's demo PIN shows on the page (SHOW_PINS=1), so a judge can approve
an order. It is a demo PIN for this link only. The audit log and the signing key live on a Modal volume,
so approvals and the scorecard survive a restart. Exactly one container runs, because the agent keeps
its open approvals in memory.

The shelf box and the touch screen stay on the local backend: the screen speaks plain HTTP.
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
                           "enclosure", "screen", "device", "tests", "fixtures/cache"])
    .add_local_dir(ROOT / "enclosure" / "fonts", remote_path="/root/app/enclosure/fonts")   # the cursive of the name
)

state = modal.Volume.from_name("mise-state", create_if_missing=True)
app = modal.App("mise", image=image)


@app.function(min_containers=1, max_containers=1, timeout=3600, volumes={"/state": state})
@modal.concurrent(max_inputs=50)
@modal.asgi_app(label="mise")
def web():
    import os
    import sys

    os.chdir("/root/app")
    sys.path.insert(0, "/root/app")
    from backend.app import app as fastapi_app
    return fastapi_app
