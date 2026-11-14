"""Backend settings that change behaviour, with their committed defaults.

A fresh checkout runs with exactly these values, and the tests run with them too. `.env.example`
lists every key. The live `.env` holds only secrets and site values (PINs, keys, addresses,
file paths); a policy value there overrides the default below, and the app logs a notice at start
when it does, so the override is visible in the audit log.

Agent() with no settings uses DEFAULTS, never the environment, so a local `.env` cannot change
what a test checks. backend.app passes Settings.from_env().

The restock policy (docs/handoff/w1-reliability.md, decision C in docs/PARALLEL_WORK.md):
after a camera or knob trigger, the slot stays pending (no second order) until
  (a) a delivery check for the slot's order arrives, or
  (b) the slot reads stocked without a break for RESTOCK_CONFIRM_S, and at least RESTOCK_MIN_S
      have passed since the order was placed.
One "full" reading never ends it (unless RESTOCK_CONFIRM_S is 0). Rule (b) is a mitigation
against a hand, a reflection or a recalibration, not proof of a delivery: a product put back on
the shelf or stock from a back room also passes it.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True)
class Settings:
    # A stocked reading earlier than this after the slot's order cannot end the pending order.
    # 120 s is what the live backend ran with on 14 Nov after the duplicate order.
    restock_min_s: float = 120.0
    # How long the slot must read stocked without a break before the pending order ends without a
    # delivery check. The board's status (with health) arrives every 10 s, so 60 s is six reports.
    # 0 turns the hold off: one stocked reading after RESTOCK_MIN_S ends it (Alejo's original spec).
    restock_confirm_s: float = 60.0
    # The shelf box counts as offline after this long without any event.
    offline_after_s: float = 30.0
    # A health report older than this counts as unknown, and a gap this long between two readings
    # breaks a stocked hold (the hold starts again from the next reading).
    health_stale_s: float = 30.0

    @classmethod
    def from_env(cls, env=None) -> "Settings":
        """Defaults, overridden by RESTOCK_MIN_S, RESTOCK_CONFIRM_S, OFFLINE_AFTER_S, HEALTH_STALE_S."""
        env = os.environ if env is None else env
        values = {}
        for f in fields(cls):
            raw = env.get(f.name.upper(), "")
            if str(raw).strip() == "":
                continue
            value = float(raw)
            if value < 0:
                raise ValueError(f"{f.name.upper()} must be 0 or more, not {raw!r}")
            values[f.name] = value
        return cls(**values)

    def overrides(self) -> dict[str, float]:
        """The keys whose value differs from the committed default, as KEY: value."""
        base = asdict(DEFAULTS)
        return {k.upper(): v for k, v in asdict(self).items() if v != base[k]}


DEFAULTS = Settings()
