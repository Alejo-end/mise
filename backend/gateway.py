"""The Aimo Agent Gateway: the calls an outside buying agent uses.

    GET  /gateway/products?q=...     lookup: products with their source, time and hash
    POST /gateway/quotes             price, stock, delivery date and the rule decision, nothing ordered
    POST /gateway/orders             order a quote: placed, sent to a person, or refused. Never above the quoted price
    GET  /gateway/orders/{run_id}    what became of an order request
    GET  /gateway/audit?run_id=...   the log entries of one order request
    GET  /gateway/key                the public key that checks the signature on quotes and order answers

Every call needs "Authorization: Bearer <licence>". An agent gets a licence when it passes the
Agent Driving Test (POST /sandbox/test). The licence holds the agent's spend limit and its
product groups. A product outside those groups goes to a person. The order goes through the
same rule check, approval and audit log as an order from the kitchen itself.

A restart of the backend keeps licences, placed orders and open approvals: they come back from
the audit log. It forgets quotes, which live 15 minutes, and the answers of refused requests.

What the wholesaler keeps to itself: a quote shows no more stock than the quantity asked for, and
no raw record of the product. One licence gets a fixed number of lookups and quotes per minute,
because each quote costs two calls to the wholesaler's systems and a fast agent can read the
stock of the whole catalog.
"""

from __future__ import annotations

import hashlib
import time as clock
import uuid
from collections import defaultdict, deque
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .agent import DRY, NEEDED_HOUR
from .rules import TZ

QUOTE_VALID = timedelta(minutes=15)
LOOKUPS_PER_MINUTE = 60  # lookups and quotes of one licence
PUBLIC = ("id", "gtin", "name", "product_group", "properties", "allergens", "traces", "supplier", "origin", "pack",
          "max_temp_c", "delivery_reliability", "carbon_kg_co2e_per_kg", "sales_unit", "lot_size", "units",
          "deposits", "mass_kg")
HTTP_STATUS = {"ordered": 201, "needs_approval": 202, "forbidden": 403, "no_match": 404, "not_orderable": 409,
               "blocked": 409, "price_changed": 409, "invalid_quantity": 422, "error": 502}
RELIABILITY_MEANING = ("historical share of the ordered quantity that was delivered (Valio challenge file); "
                       "not an on-time delivery probability")


def comparison(product: dict, quantity: int) -> dict:
    """Valio's delivery reliability and carbon figures as information. They inform; the rule check decides.

    A missing value stays unknown. The carbon estimate needs the ordered mass from the product file;
    without it the estimate is unknown.
    """
    rel = product.get("delivery_reliability")
    factor = product.get("carbon_kg_co2e_per_kg")
    mass = product.get("mass_kg")
    total_mass = round(mass * quantity, 3) if mass is not None else None
    return {
        "delivery_reliability": {"value": rel, "status": "known" if rel is not None else "unknown",
                                 "meaning": RELIABILITY_MEANING},
        "carbon": {"factor_kg_co2e_per_kg": factor, "basis": "product-group average from the Valio challenge file",
                   "order_mass_kg": total_mass,
                   "estimate_kg_co2e": round(total_mass * factor, 2) if factor is not None and total_mass is not None else None,
                   "status": "estimated" if factor is not None and total_mass is not None else
                             "unknown: " + ("no carbon value for this product" if factor is None
                                            else "the file does not establish the ordered mass")},
    }


def _when(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


class QuoteRequest(BaseModel):
    product_id: str
    quantity: int = Field(ge=1, le=100000)
    needed_by: date | None = None  # the delivery date the buyer needs; default: the kitchen's own rule


class OrderRequest(BaseModel):
    quote_id: str
    idempotency_key: str = Field(min_length=1, max_length=80)


def public(product: dict) -> dict:
    """What an outside agent may see of a product. Stock internals stay inside."""
    return {k: product[k] for k in PUBLIC if k in product}


def make_router(agent, catalog, driving_test, log, signer) -> APIRouter:
    router = APIRouter(prefix="/gateway", tags=["gateway"])
    quotes: dict[str, dict] = {}
    refused: dict[str, dict] = {}  # run id -> the answer of a request that ended with no order and no approval
    asked: dict[str, deque] = defaultdict(deque)  # licence id -> the times of its lookups and quotes

    def licence(authorization: str | None) -> dict:
        token = (authorization or "").removeprefix("Bearer ").strip()
        lic = driving_test.valid(token) if token else None
        if lic is None:
            raise HTTPException(401, "A valid licence is needed. An agent gets one when it passes POST /sandbox/test.")
        return lic

    def throttle(lic: dict) -> None:
        """Refuse a licence that asks more often than LOOKUPS_PER_MINUTE."""
        times, now = asked[lic["id"]], clock.monotonic()
        while times and now - times[0] > 60:
            times.popleft()
        if len(times) >= LOOKUPS_PER_MINUTE:
            raise HTTPException(429, f"More than {LOOKUPS_PER_MINUTE} lookups and quotes in a minute. Wait and ask again.",
                                headers={"Retry-After": "60"})
        times.append(now)

    def deadline(day: date | None) -> datetime | None:
        """A date with no time means the same hour as "by Monday" in a text request."""
        return datetime.combine(day, time(NEEDED_HOUR), tzinfo=TZ) if day else None

    def who(lic: dict) -> dict:
        return {"mandate_eur": lic["scope"]["spend_limit_eur"], "scope_groups": lic["scope"]["product_groups"]}

    def run_of(lic: dict, key: str) -> str:
        """One licence and one idempotency key always give the same run id, also after a restart."""
        return hashlib.sha256(f"{lic['id']}:{key}".encode()).hexdigest()[:8]

    def answer(run_id: str, result: dict) -> JSONResponse:
        if result.get("status") == "ordered":  # also for an order restored from the log after a restart
            result = result | DRY
        body = signer.sign({"run_id": run_id, **result})
        return JSONResponse(body, status_code=HTTP_STATUS.get(result["status"], 200))

    def state(run_id: str) -> dict | None:
        return agent.run_state(run_id) or refused.get(run_id)

    def own_run(run_id: str, lic: dict) -> list[dict]:
        """The log entries of a run, if this licence started it."""
        entries = [e for e in log.entries(100000) if e["run_id"] == run_id]
        if not any(e["step"] == "trigger" and e["detail"].get("licence_id") == lic["id"] for e in entries):
            raise HTTPException(404, "No such order request for this licence.")
        return entries

    @router.get("/products")
    def products(q: str, authorization: str | None = Header(default=None)):
        throttle(licence(authorization))
        claim = catalog.search(q)
        return {"query": q, "products": [public(p) for p in claim.value],
                "source": {"endpoint": claim.endpoint, "checked_at": claim.checked_at, "sha256": claim.sha256}}

    @router.post("/quotes")
    def quote(body: QuoteRequest, authorization: str | None = Header(default=None)):
        lic = licence(authorization)
        throttle(lic)
        q = agent.quote_exact(body.product_id, body.quantity, deadline(body.needed_by), **who(lic))
        if q["status"] != "quote":
            why = f": {q['reason']}" if q.get("reason") else ""
            raise HTTPException(HTTP_STATUS[q["status"]], f"Product {body.product_id}: {q['status'].replace('_', ' ')}{why}")
        quote_id = "Q-" + uuid.uuid4().hex[:8].upper()
        line = q["line"]
        # The quote lives 15 minutes, and never past the order cut-off or the end of the price's validity.
        ends = {"15 minutes": datetime.now(TZ) + QUOTE_VALID, "the order cut-off (closingTime)": _when(line.get("closing_time")),
                "the end of the price's validity (validTo)": _when(line.get("price_valid_to"))}
        limit, valid_until = min(((k, v) for k, v in ends.items() if v is not None), key=lambda kv: kv[1])
        quotes[quote_id] = {"licence_id": lic["id"], "product_id": body.product_id, "quantity": body.quantity,
                            "needed_by": body.needed_by, "valid_until": valid_until,
                            "unit_price_eur": q["unit_price_eur"]}
        deposits = q["charges"]["deposits"]
        return signer.sign({
            "quote_id": quote_id, "valid_until": valid_until.isoformat(timespec="seconds"), "valid_until_set_by": limit,
            "quantity": body.quantity, "sales_unit": line.get("sales_unit"), "product": public(q["product"]),
            "unit_price_eur": q["unit_price_eur"],
            "price_basis": "per sales unit, without tax, at the catalog price (catalogPrice.value)",
            "price_valid_to": line.get("price_valid_to"),
            "available_units": q["available_units"], "stock_known": q["stock_known"],
            "delivery": q["delivery"], "delivery_time": "not supplied: the source gives a delivery date only"
            if q["delivery"] and len(q["delivery"]) == 10 else "as given in delivery",
            "order_cutoff": line.get("closing_time"),
            "total_eur": q["total_eur"], "total_covers": "goods only, without tax",
            "delivery_charge": q["charges"]["delivery"],
            "deposit_charge": deposits.get(body.product_id, "none listed in the product data"),
            "comparison": comparison(q["product"], body.quantity),
            "source": q["source"], "decision": q["decision"].to_dict(),
            # Where and when the gateway got the product and the price. The raw records stay inside.
            "claims": [{k: c[k] for k in ("endpoint", "checked_at", "sha256")} for c in q["claims"]]})

    @router.post("/orders")
    def order(body: OrderRequest, authorization: str | None = Header(default=None)):
        lic = licence(authorization)
        run = run_of(lic, body.idempotency_key)
        known = state(run)
        if known:  # the same key gives the same order or approval, and never a second one
            waiting = known["status"] == "waiting_for_approval"
            return answer(run, known | {"status": "needs_approval"} if waiting else known)
        q = quotes.get(body.quote_id)
        if q is None or q["licence_id"] != lic["id"]:
            raise HTTPException(404, "No such quote for this licence. Ask for a new one.")
        if datetime.now(TZ) > q["valid_until"]:
            raise HTTPException(410, "The quote is older than 15 minutes. Ask for a new one.")
        log.write("trigger", {"summary": f"{lic['agent']} asked through the gateway for {q['quantity']} × product "
                              f"{q['product_id']} (quote {body.quote_id}).", "quote_id": body.quote_id,
                              "licence_id": lic["id"], "product_id": q["product_id"], "quantity": q["quantity"]},
                  lic["agent"], agent.rules["version"], run_id=run)
        result = agent.order_exact(q["product_id"], q["quantity"], run, deadline=deadline(q["needed_by"]),
                                   actor=lic["agent"], max_unit_price_eur=q["unit_price_eur"], **who(lic))
        if result["status"] not in ("ordered", "needs_approval"):
            refused[run] = result
        return answer(run, result)

    @router.get("/orders/{run_id}")
    def order_state(run_id: str, authorization: str | None = Header(default=None)):
        own_run(run_id, licence(authorization))
        known = state(run_id) or {"status": "ended", "detail": "See the audit entries of this run."}
        return {"run_id": run_id, **(known | DRY if known.get("status") == "ordered" else known)}

    @router.get("/key")
    def key():
        """Public, with no licence: anybody may check a signature."""
        return {"alg": "Ed25519", "key_id": signer.key_id, "public_key": signer.public_key,
                "signed": "the JSON of the answer without its signature entry, keys sorted, no spaces"}

    @router.get("/audit")
    def audit(run_id: str, authorization: str | None = Header(default=None)):
        return own_run(run_id, licence(authorization))

    return router
