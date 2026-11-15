"""Verda inference client (OpenAI-compatible chat completions) for the recognition experiment.

Rules (worker briefs): the key comes only from the environment (on Modal: the Secret `verda-inference`), it is
never printed, logged or written to a result. Request timeout 60 s, at most 3 retries, Retry-After honoured on
429, exponential back-off with jitter, then the request is recorded as an infrastructure failure.
Rate limits are only in the `x-ratelimit-*` response headers; a shared gate keeps the remaining request count
above a reserve and waits for the reset when it runs low.

httpx is imported lazily so the Modal CLI can import this package without it.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import time
from email.utils import parsedate_to_datetime

TIMEOUT_S = 60.0
MAX_RETRIES = 3


def parse_duration(v: str | None) -> float | None:
    """'47s', '1m2s', '250ms', '0.5' -> seconds."""
    if not v:
        return None
    v = v.strip()
    try:
        return float(v)
    except ValueError:
        pass
    total, found = 0.0, False
    for num, unit in re.findall(r"([0-9.]+)\s*(ms|h|m|s)", v):
        found = True
        total += float(num) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total if found else None


def retry_after(value):
    seconds = parse_duration(value)
    if seconds is not None:
        return max(0, seconds)
    try:
        return max(0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        return None


def ratelimit_headers(h) -> dict:
    return {k.lower(): v for k, v in h.items()
            if "ratelimit" in k.lower() or k.lower() in ("retry-after", "x-request-id")}


class RateGate:
    """Shared by all concurrent requests: keeps `reserve` requests of the window unused, sleeps to the reset."""

    def __init__(self, reserve: int = 2, requests_per_minute: int = 12):
        self.reserve = reserve
        self.interval = 60.0 / requests_per_minute + .05
        self.next_request = 0.0
        self.lock = asyncio.Lock()
        self.remaining: int | None = None
        self.reset_at = 0.0
        self.pause_until = 0.0
        self.waits = 0

    async def acquire(self) -> None:
        async with self.lock:
            await self._acquire()

    async def _acquire(self) -> None:
        while True:
            now = time.monotonic()
            if now < self.next_request:
                await asyncio.sleep(self.next_request - now)
                continue
            if now < self.pause_until:
                self.waits += 1
                await asyncio.sleep(self.pause_until - now + random.uniform(0.0, 0.5))
                continue
            if self.remaining is not None and self.remaining <= self.reserve and now < self.reset_at:
                self.waits += 1
                await asyncio.sleep(self.reset_at - now + random.uniform(0.2, 1.0))
                continue
            if self.remaining is not None:
                if now >= self.reset_at and self.remaining <= self.reserve:
                    self.remaining = None  # window has reset; the next answer tells us the new state
                else:
                    self.remaining -= 1
            self.next_request = time.monotonic() + self.interval
            return

    def update(self, headers: dict) -> None:
        token_rem = headers.get("x-ratelimit-remaining-tokens", "")
        if token_rem.isdigit() and int(token_rem) < 20000:
            self.pause(parse_duration(headers.get("x-ratelimit-reset-tokens")) or 60)
        ra = retry_after(headers.get("retry-after"))
        if ra is not None:
            self.pause(ra)
        rem = headers.get("x-ratelimit-remaining-requests")
        reset = parse_duration(headers.get("x-ratelimit-reset-requests"))
        if rem is not None and rem.isdigit():
            self.remaining = int(rem)
            if reset is not None:
                self.reset_at = time.monotonic() + reset

    def pause(self, seconds: float) -> None:
        self.pause_until = max(self.pause_until, time.monotonic() + seconds)


def _key() -> str:
    k = os.environ.get("VERDA_INFERENCE_API_KEY")
    if not k:
        raise RuntimeError("VERDA_INFERENCE_API_KEY is not set (Modal Secret verda-inference)")
    return k


def base_url() -> str:
    return os.environ.get("VERDA_INFERENCE_BASE_URL", "https://inference.verda.com/v1").rstrip("/")


async def chat(client, gate: RateGate, body: dict) -> dict:
    """One chat completion with retries. Returns a record without the key and without the request body."""
    import httpx

    url = f"{base_url()}/chat/completions"
    attempt, backoff = 0, 1.0
    rec: dict = {"attempts": 0}
    while True:
        await gate.acquire()
        rec["attempts"] = attempt + 1
        t = time.perf_counter()
        r, err = None, None
        try:
            r = await client.post(url, json=body, headers={"Authorization": f"Bearer {_key()}"},
                                  timeout=httpx.Timeout(TIMEOUT_S, connect=10.0))
        except httpx.HTTPError as e:
            err = type(e).__name__  # never persist URLs or provider exception text
        rec["latency_s"] = round(time.perf_counter() - t, 3)
        rec["status"] = r.status_code if r is not None else None
        rec["error"] = err
        if r is not None:
            h = ratelimit_headers(r.headers)
            rec["ratelimit"] = h
            gate.update(h)
            if r.status_code == 200:
                try:
                    d = r.json()
                    ch = d["choices"][0]
                    msg = ch.get("message") or {}
                    rec.update({"content": msg.get("content") or "", "finish_reason": ch.get("finish_reason"),
                                "reasoning_chars": len(msg.get("reasoning_content") or msg.get("reasoning") or ""),
                                "usage": d.get("usage") or {}, "model_returned": d.get("model")})
                    return rec
                except (ValueError, KeyError, IndexError, TypeError) as e:
                    rec["error"] = f"bad 200 body: {type(e).__name__}"
            else:
                rec["error"] = f"HTTP {r.status_code}"  # response bodies may echo private request data
        retryable = (r is None or r.status_code == 429 or r.status_code >= 500
                     or (rec.get("error") or "").startswith("bad"))
        if not retryable or attempt >= MAX_RETRIES:
            rec["infra_failure"] = True
            return rec
        attempt += 1
        ra = retry_after(r.headers.get("retry-after")) if r is not None else None
        wait = ra if ra is not None else backoff + random.uniform(0.0, backoff)
        if r is not None and r.status_code == 429:
            gate.pause(wait)
        await asyncio.sleep(wait)
        backoff *= 2
