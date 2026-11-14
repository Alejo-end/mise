"""Write the OpenAPI description of the gateway calls to docs/valio-api/gateway-openapi.json.

    CATALOG=local uv run python -m backend.export_openapi

The file is what an outside agent reads to learn the interface. A test fails when it is out of date.
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "valio-api" / "gateway-openapi.json"
PUBLIC_PREFIXES = ("/gateway", "/sandbox")


def gateway_openapi(app) -> dict:
    """The calls an outside agent uses, with only the schemas they name."""
    spec = app.openapi()
    paths = {p: v for p, v in spec["paths"].items() if p.startswith(PUBLIC_PREFIXES)}
    text = json.dumps(paths)
    schemas = spec.get("components", {}).get("schemas", {})
    used, grew = set(), True
    while grew:  # a schema can name another schema
        grew = False
        for name, body in schemas.items():
            ref = f"#/components/schemas/{name}"
            if name not in used and (ref in text or any(ref in json.dumps(schemas[u]) for u in used)):
                used.add(name)
                grew = True
    return {"openapi": spec["openapi"],
            "info": {"title": "Aimo Agent Gateway", "version": spec["info"]["version"],
                     "description": "Lookup, quote, order, order state and audit for an outside buying agent. "
                                    "Every /gateway call but /gateway/key needs the licence of the Agent Driving Test "
                                    "as a Bearer token. Quotes and order answers are signed with Ed25519."},
            "paths": paths, "components": {"schemas": {n: schemas[n] for n in sorted(used)}}}


def render(app) -> str:
    return json.dumps(gateway_openapi(app), indent=1, sort_keys=True) + "\n"


if __name__ == "__main__":
    from .app import app

    OUT.write_text(render(app))
    print("wrote", OUT)
