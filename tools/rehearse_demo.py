"""Rehearse the smallest purchasing demo in isolated databases: no live backend, no live Valio call, no real order.

  uv run python tools/rehearse_demo.py            (from valio-challenge/, product file in data/valio)

Each scenario starts the real backend app (FastAPI TestClient) on a fresh temporary audit database with the real
product file and rule set v4 (rules/school_kitchen_valio.yaml: slot 3 = 463335). Price and availability come from
the **recorded** answers of Valio's mock API (data/valio/api-recordings, all for delivery on Monday 16 Nov), replayed;
orders go to the gateway's own order book only. The trigger is an outside buyer agent on the Aimo Agent Gateway:
Driving Test licence -> product lookup -> signed quote -> order within its authority or a Head chef approval. The
shelf props (chips, Funky Monkey, the physical milk carton) are not used: they stay unmapped for purchasing.
Changes at approval time are **injected** by this script and labelled as such.
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REC = ROOT / "data" / "valio" / "api-recordings"
PIN = "4821"


def recorded(path: str, product: str, day: str):
    for f in sorted(REC.glob("*.json")):
        e = json.loads(f.read_text())
        req, res = e.get("request") or {}, e.get("response") or {}
        body = req.get("json") or {}
        if req.get("url", "").endswith(path) and res.get("status") == 200 and body.get("requestedDate") == day \
                and product in (body.get("products") or []):
            return e
    raise SystemExit(f"no recorded {path} answer for {product} on {day}")


class Replay:
    """Replays the first recorded answer; `inject` changes the next answers (the recheck at approval)."""

    def __init__(self):
        self.inject: dict = {}
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        ids, day = body["products"], str(body.get("requestedDate", ""))[:10]
        e = recorded(request.url.path, ids[0], day)
        out = json.loads(json.dumps(e["response"]["json"]))
        out["products"] = [p for p in out["products"] if p["productId"] in ids]
        self.calls.append(f"{request.url.path} {ids} {self.inject or ''}")
        for p in out["products"]:
            if request.url.path.endswith("/price") and "price" in self.inject:
                for k in ("catalogPrice", "listPrice"):
                    if isinstance(p.get(k), dict) and "value" in p[k]:
                        p[k]["value"] = self.inject["price"]
                    elif isinstance(p.get(k), (int, float)):
                        p[k] = self.inject["price"]
            if request.url.path.endswith("/availability"):
                if "available" in self.inject:
                    for s in p.get("scheduleLines", []):
                        s["availableAmount"] = self.inject["available"]
                if "closing" in self.inject:
                    p["closingTime"] = self.inject["closing"]
        return httpx.Response(200, json=out)


def start(rules_file: Path, auto_limit: float):
    tmp = Path(tempfile.mkdtemp(prefix="rehearsal-"))
    os.environ.update({"AUDIT_DB": str(tmp / "audit.sqlite"), "CATALOG": "valio", "RULES_FILE": str(rules_file),
                       "VALIO_API_KEY": "rehearsal-replay-no-live-calls", "VALIO_DATA_DIR": str(ROOT / "data" / "valio"),
                       "APPROVER_PINS": f"{PIN}:Head chef", "GATEWAY_KEY": str(tmp / "gateway_key.pem"),
                       "RESTOCK_MIN_S": "120", "SHELF_EVENTS": "observe"})
    os.environ.pop("RECORDING_SESSION", None)
    import backend.app as m
    m = importlib.reload(m)
    replay = Replay()
    m.catalog.http = httpx.Client(base_url="https://replay.invalid", transport=httpx.MockTransport(replay))
    m.catalog.min_gap_s = 0
    m.agent.auto_limit_eur = auto_limit
    return m, TestClient(m.app), replay, tmp


def story(c: TestClient, since: int = 0) -> list[str]:
    entries = c.get("/log?format=json&limit=200").json()
    out = []
    for e in entries:
        d = e.get("detail") or {}
        out.append(f"{e['step']}: {d.get('summary') or d.get('subject') or d.get('result') or json.dumps(d)[:150]}"[:240])
    return out[since:]


def show(title: str, c: TestClient, result: dict | None = None):
    orders = c.get("/orders").json()
    print(f"\n### {title}")
    if result is not None:
        print("  result:", json.dumps(result)[:300])
    for line in story(c):
        if line.split(":")[0] in ("trigger", "quote", "rule_check", "approval", "recheck", "blocked", "order", "decision", "notice"):
            print("  -", line)
    print("  orders:", [(o["order_id"], [(l["product_id"], l["quantity"], l.get("unit_price_eur")) for l in o["lines"]]) for o in orders])
    return orders


def main():
    base = ROOT / "rules" / "school_kitchen_valio.yaml"
    from backend import rules as R

    # Rehearsal-only rule copy for R1: decision G (date-only delivery on the needed day) set to allowed. Pending team decision.
    g_dir = Path(tempfile.mkdtemp(prefix="rules-g-"))
    g = g_dir / "school_kitchen_valio.yaml"
    g.write_text(base.read_text().replace('  latest_needed: "next weekday 07:00"',
                                          '  latest_needed: "next weekday 07:00"\n  date_only_same_day: allowed  # REHEARSAL ONLY: decision G pending'))
    R.sign(g)

    def licence(c):
        lic = c.post("/sandbox/test", json={"agent": "safe-kitchen", "pin": PIN}).json()
        return {"Authorization": f"Bearer {lic['licence']['token']}"}, lic

    def buy(c, auth, qty, key):
        q = c.post("/gateway/quotes", json={"product_id": "463335", "quantity": qty}, headers=auth).json()
        o = c.post("/gateway/orders", json={"quote_id": q.get("quote_id"), "idempotency_key": key}, headers=auth).json()
        return q, o

    # R0: the shelf props never buy (SHELF_EVENTS=observe): detector "empty" and the knob on slot 3 are ignored
    m, c, replay, _ = start(base, 40)
    r0 = [c.post("/events", json=ev).json() for ev in ({"type": "slot_state", "slot": 3, "state": "empty", "estimated_units": 0},
                                                       {"type": "manual_low", "slot": 3})]
    print("### R0 shelf props observe only:", [x.get("status") for x in r0], "| orders", c.get("/orders").json(),
          "| approvals", c.get("/approvals/open").json(), "| Valio calls", len(replay.calls))

    # Lookup once (any scenario): what an outside agent sees for "lactose free milk"
    m, c, replay, _ = start(base, 40)
    auth, lic = licence(c)
    print("Driving Test passed:", lic.get("passed"), "| licence:", {k: v for k, v in (lic.get("licence") or {}).items() if k != "token"})
    hits = c.get("/gateway/products", params={"q": "laktoositon maitojuoma 1 l"}, headers=auth).json()
    rows = hits if isinstance(hits, list) else hits.get("products", [])
    print("lookup:", [(h.get("product_id") or h.get("id"), (h.get("name") or "")[:45]) for h in rows[:5]])

    # R1: authorised automatic mock order. Assumes decision G = allowed (rehearsal-only signed copy): the API gives a
    # delivery date without a time, and v4 sends such a same-day delivery to a person.
    m, c, replay, _ = start(g, 40)
    auth, _ = licence(c)
    q, o = buy(c, auth, 6, "r1")
    print("\n### R1 gateway order within the licence and the auto-limit (6 x 463335; decision G assumed allowed)")
    print("  quote:", {k: q.get(k) for k in ("quote_id", "total_eur", "unit_price_eur", "delivery", "valid_until", "needs_person")})
    show("R1 result", c, o)

    # R2: purchase requiring approval, rule set v4 as signed (date-only same-day delivery -> a person)
    m, c, replay, _ = start(base, 40)
    auth, _ = licence(c)
    q, o = buy(c, auth, 24, "r2")
    print("\n### R2 gateway order that needs the Head chef (24 x 463335; v4 as signed)")
    print("  order answer:", json.dumps(o)[:220])
    ap = c.get("/approvals/open").json()
    d = c.post(f"/approvals/{ap[0]['id']}", json={"action": "approve", "option": 0, "pin": PIN}).json() if ap else {"error": "no approval"}
    show("R2 after the Head chef approves with the PIN (recheck with fresh price and date)", c, d)

    # R3: a change between the approval request and the approval
    for label, inject, patch_product in (
            ("R3a price up 1.40 -> 1.95 at approval", {"price": 1.95}, None),
            ("R3b stock gone at approval", {"available": 0}, None),
            ("R3c order cut-off passed at approval (cut-off before the API time in the answer)", {"closing": "2026-11-14T05:00:00+0200"}, None),
            ("R3d product loses 'lactose_free' at approval (product constraint)", {}, "lactose_free")):
        m, c, replay, _ = start(base, 40)
        auth, _ = licence(c)
        buy(c, auth, 24, "r3")
        ap = c.get("/approvals/open").json()
        replay.inject = inject
        if patch_product:
            original = m.catalog.product

            def product(pid, _orig=original):
                claim = _orig(pid)
                if pid == "463335" and claim.value:
                    claim.value = dict(claim.value, properties=[x for x in claim.value.get("properties", []) if x != patch_product])
                return claim
            m.catalog.product = product
        d = c.post(f"/approvals/{ap[0]['id']}", json={"action": "approve", "option": 0, "pin": PIN}).json() if ap else {"error": "no approval"}
        show(f"{label} (injected by the rehearsal)", c, d)
        print("  open approvals now:", [(a["id"], a.get("what", "")[:100]) for a in c.get("/approvals/open").json()])


if __name__ == "__main__":
    main()
