"""The Aimo Agent Gateway: the calls an outside buying agent uses.

    GET  /gateway/products?q=...     lookup: products with their source, time and hash
    POST /gateway/quotes             price, stock, delivery date and the rule decision, nothing ordered
    POST /gateway/orders             order a quote: placed, sent to a person, or refused
    GET  /gateway/orders/{run_id}    what became of an order request
    GET  /gateway/audit?run_id=...   the log entries of one order request
    GET  /gateway/key                the public key that checks the signature on quotes and order answers

Every call needs "Authorization: Bearer <licence>". An agent gets a licence when it passes the
Agent Driving Test (POST /sandbox/test). The licence holds the agent's spend limit and its
product groups. A product outside those groups goes to a person. The order goes through the
same rule check, approval and audit log as an order from the kitchen itself.

A restart of the backend keeps licences, placed orders and open approvals: they come back from
the audit log. It forgets quotes, which live 15 minutes, and the answers of refused requests.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .agent import NEEDED_HOUR
from .rules import TZ

QUOTE_VALID = timedelta(minutes=15)
PUBLIC = ("id", "name", "product_group", "properties", "allergens", "traces", "supplier", "origin", "pack",
          "max_temp_c", "delivery_reliability")
HTTP_STATUS = {"ordered": 201, "needs_approval": 202, "forbidden": 403, "no_match": 404, "not_orderable": 409,
               "blocked": 409, "error": 502}


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

    def licence(authorization: str | None) -> dict:
        token = (authorization or "").removeprefix("Bearer ").strip()
        lic = driving_test.valid(token) if token else None
        if lic is None:
            raise HTTPException(401, "A valid licence is needed. An agent gets one when it passes POST /sandbox/test.")
        return lic

    def deadline(day: date | None) -> datetime | None:
        """A date with no time means the same hour as "by Monday" in a text request."""
        return datetime.combine(day, time(NEEDED_HOUR), tzinfo=TZ) if day else None

    def who(lic: dict) -> dict:
        return {"mandate_eur": lic["scope"]["spend_limit_eur"], "scope_groups": lic["scope"]["product_groups"]}

    def run_of(lic: dict, key: str) -> str:
        """One licence and one idempotency key always give the same run id, also after a restart."""
        return hashlib.sha256(f"{lic['id']}:{key}".encode()).hexdigest()[:8]

    def answer(run_id: str, result: dict) -> JSONResponse:
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
        licence(authorization)
        claim = catalog.search(q)
        return {"query": q, "products": [public(p) for p in claim.value],
                "source": {"endpoint": claim.endpoint, "checked_at": claim.checked_at, "sha256": claim.sha256}}

    @router.post("/quotes")
    def quote(body: QuoteRequest, authorization: str | None = Header(default=None)):
        lic = licence(authorization)
        q = agent.quote_exact(body.product_id, body.quantity, deadline(body.needed_by), **who(lic))
        if q["status"] != "quote":
            raise HTTPException(HTTP_STATUS[q["status"]], f"Product {body.product_id}: {q['status'].replace('_', ' ')}")
        quote_id = "Q-" + uuid.uuid4().hex[:8].upper()
        valid_until = datetime.now(TZ) + QUOTE_VALID
        quotes[quote_id] = {"licence_id": lic["id"], "product_id": body.product_id, "quantity": body.quantity,
                            "needed_by": body.needed_by, "valid_until": valid_until}
        return signer.sign({
            "quote_id": quote_id, "valid_until": valid_until.isoformat(timespec="seconds"),
            "quantity": body.quantity, "product": public(q["product"]), "unit_price_eur": q["unit_price_eur"],
            "available_units": q["available_units"], "stock_known": q["stock_known"], "delivery": q["delivery"],
            "total_eur": q["total_eur"], "decision": q["decision"].to_dict(), "claims": q["claims"]})

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
                                   actor=lic["agent"], **who(lic))
        if result["status"] not in ("ordered", "needs_approval"):
            refused[run] = result
        return answer(run, result)

    @router.get("/orders/{run_id}")
    def order_state(run_id: str, authorization: str | None = Header(default=None)):
        own_run(run_id, licence(authorization))
        return {"run_id": run_id, **(state(run_id) or {"status": "ended", "detail": "See the audit entries of this run."})}

    @router.get("/key")
    def key():
        """Public, with no licence: anybody may check a signature."""
        return {"alg": "Ed25519", "key_id": signer.key_id, "public_key": signer.public_key,
                "signed": "the JSON of the answer without its signature entry, keys sorted, no spaces"}

    @router.get("/audit")
    def audit(run_id: str, authorization: str | None = Header(default=None)):
        return own_run(run_id, licence(authorization))

    return router
