"""The Aimo Agent Gateway: the calls an outside buying agent uses.

    GET  /gateway/products?q=...     lookup: products with their source, time and hash
    POST /gateway/quotes             price, stock, delivery date and the rule decision, nothing ordered
    POST /gateway/orders             order a quote: placed, sent to a person, or refused
    GET  /gateway/orders/{run_id}    what became of an order request
    GET  /gateway/audit?run_id=...   the log entries of one order request

Every call needs "Authorization: Bearer <licence>". An agent gets a licence when it passes the
Agent Driving Test (POST /sandbox/test). The licence holds the agent's spend limit. The order
goes through the same rule check, approval and audit log as an order from the kitchen itself.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

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


def make_router(agent, catalog, driving_test, log) -> APIRouter:
    router = APIRouter(prefix="/gateway", tags=["gateway"])
    quotes: dict[str, dict] = {}
    runs: dict[str, dict] = {}       # run id -> {"token", "result"}
    by_key: dict[tuple, str] = {}    # (licence, idempotency key) -> run id

    def licence(authorization: str | None) -> dict:
        token = (authorization or "").removeprefix("Bearer ").strip()
        lic = driving_test.valid(token) if token else None
        if lic is None:
            raise HTTPException(401, "A valid licence is needed. An agent gets one when it passes POST /sandbox/test.")
        return lic

    def deadline(day: date | None) -> datetime | None:
        return datetime.combine(day, time(23, 59), tzinfo=TZ) if day else None

    def answer(run_id: str) -> JSONResponse:
        result = runs[run_id]["result"]
        return JSONResponse({"run_id": run_id, **result}, status_code=HTTP_STATUS.get(result["status"], 200))

    @router.get("/products")
    def products(q: str, authorization: str | None = Header(default=None)):
        licence(authorization)
        claim = catalog.search(q)
        return {"query": q, "products": [public(p) for p in claim.value],
                "source": {"endpoint": claim.endpoint, "checked_at": claim.checked_at, "sha256": claim.sha256}}

    @router.post("/quotes")
    def quote(body: QuoteRequest, authorization: str | None = Header(default=None)):
        lic = licence(authorization)
        result = agent.buy_exact(body.product_id, body.quantity, None, "quote", deadline=deadline(body.needed_by),
                                 mandate_eur=lic["scope"]["spend_limit_eur"], dry_run=True)
        if result["status"] != "quote":
            raise HTTPException(HTTP_STATUS[result["status"]], f"Product {body.product_id}: {result['status'].replace('_', ' ')}")
        quote_id = "Q-" + uuid.uuid4().hex[:8].upper()
        valid_until = datetime.now(TZ) + QUOTE_VALID
        quotes[quote_id] = {"token": lic["token"], "product_id": body.product_id, "quantity": body.quantity,
                            "needed_by": body.needed_by, "valid_until": valid_until}
        return {"quote_id": quote_id, "valid_until": valid_until.isoformat(timespec="seconds"),
                "quantity": body.quantity, "product": public(result["product"]),
                "unit_price_eur": result["unit_price_eur"], "available_units": result["available_units"],
                "delivery": result["delivery"], "total_eur": result["total_eur"],
                "decision": result["decision"], "claims": result["claims"]}

    @router.post("/orders")
    def order(body: OrderRequest, authorization: str | None = Header(default=None)):
        lic = licence(authorization)
        key = (lic["token"], body.idempotency_key)
        if key in by_key:  # the same key gives the same answer and never a second order
            return answer(by_key[key])
        q = quotes.get(body.quote_id)
        if q is None or q["token"] != lic["token"]:
            raise HTTPException(404, "No such quote for this licence.")
        if datetime.now(TZ) > q["valid_until"]:
            raise HTTPException(410, "The quote is older than 15 minutes. Ask for a new one.")
        run = uuid.uuid4().hex[:8]
        log.write("trigger", {"summary": f"{lic['agent']} asked through the gateway for {q['quantity']} × product "
                              f"{q['product_id']} (quote {body.quote_id}).", "quote_id": body.quote_id,
                              "product_id": q["product_id"], "quantity": q["quantity"]},
                  lic["agent"], agent.rules["version"], run_id=run)
        result = agent._safely(run, None, lambda: agent.buy_exact(
            q["product_id"], q["quantity"], None, run, deadline=deadline(q["needed_by"]),
            mandate_eur=lic["scope"]["spend_limit_eur"], actor=lic["agent"]))
        runs[run] = {"token": lic["token"], "result": result}
        by_key[key] = run
        return answer(run)

    def own_run(run_id: str, lic: dict) -> dict:
        run = runs.get(run_id)
        if run is None or run["token"] != lic["token"]:
            raise HTTPException(404, "No such order request for this licence.")
        return run

    @router.get("/orders/{run_id}")
    def order_state(run_id: str, authorization: str | None = Header(default=None)):
        run = own_run(run_id, licence(authorization))
        if run_id in agent.placed_runs:
            return {"run_id": run_id, **agent.placed_runs[run_id]}
        waiting = [a for a in agent.approvals.values() if a["run_id"] == run_id]
        if waiting:
            agent.open_approvals()  # lets an old request expire
            state = waiting[-1]["status"]
            return {"run_id": run_id, "status": "waiting_for_approval" if state == "open" else state}
        return {"run_id": run_id, **run["result"]}

    @router.get("/audit")
    def audit(run_id: str, authorization: str | None = Header(default=None)):
        own_run(run_id, licence(authorization))
        return [e for e in log.entries(100000) if e["run_id"] == run_id]

    return router
