"""The shelf recordings of experiments/recognition/MISSING_DATA.md as a guided plan for tools/record_sequence.py.

Each step is one action the person does and marks with Enter at the moment they do it; the mark is stored on
the recording clock (the same clock as the frame times in meta.jsonl). `after` gives the slot contents from
that moment on. Slot values: a product code, "empty", "unknown" (an item not in the catalogue options) or
"obstructed" (a hand covers the slot). Reference captures teach a model; evaluation captures are only scored.
"""

from __future__ import annotations

ASSIGNED = {"1": "477032", "2": "464316", "3": "463335"}  # rule set v4: bread, yoghurt, milk
FULL = dict(ASSIGNED)
EMPTY = {"1": "empty", "2": "empty", "3": "empty"}
NAMES = {"477032": "bread", "464316": "yoghurt", "463335": "milk"}
NO_REQUESTS = {"1": 0, "2": 0, "3": 0}


def _poses(slot: str) -> list[dict]:
    what = NAMES[ASSIGNED[slot]]
    return [{"at": 5, "do": f"turn the {what} in slot {slot} to its side", "after": {}},
            {"at": 10, "do": f"turn the {what} in slot {slot} to an angle", "after": {}},
            {"at": 15, "do": f"push the {what} in slot {slot} to the back", "after": {}}]


PLAN: dict[str, dict] = {
    # Reference captures (role reference): a model may learn from these; never scored.
    "ref-empty-day": {"role": "reference", "seconds": 10, "lighting": "day", "start": EMPTY, "steps": [],
                      "setup": "all three slots empty"},
    "ref-full-day": {"role": "reference", "seconds": 10, "lighting": "day", "start": FULL, "steps": [],
                     "setup": "all three slots full, labels facing the camera"},
    "ref-s1-bread-poses-day": {"role": "reference", "seconds": 20, "lighting": "day", "start": FULL,
                               "steps": _poses("1"), "setup": "all full; bread in slot 1 facing the camera"},
    "ref-s2-yoghurt-poses-day": {"role": "reference", "seconds": 20, "lighting": "day", "start": FULL,
                                 "steps": _poses("2"), "setup": "all full; yoghurt in slot 2 facing the camera"},
    "ref-s3-milk-poses-day": {"role": "reference", "seconds": 20, "lighting": "day", "start": FULL,
                              "steps": _poses("3"), "setup": "all full; milk in slot 3 facing the camera"},
    "ref-s3-lookalikes-day": {"role": "reference", "seconds": None, "lighting": "day", "start": None,
                              "steps": None, "setup": "slots 1 and 2 full; the first look-alike alone in slot 3"},
    "ref-full-dim": {"role": "reference", "seconds": 10, "lighting": "dim", "start": FULL, "steps": [],
                     "setup": "all three slots full, main light off or lamp moved"},
    "ref-empty-dim": {"role": "reference", "seconds": 10, "lighting": "dim", "start": EMPTY, "steps": [],
                      "setup": "all three slots empty, main light off or lamp moved"},
    # Evaluation recordings (role evaluation): scored only. Record each twice: NAME-a and NAME-b.
    "eval-identity-day": {"role": "evaluation", "seconds": 15, "lighting": "day", "start": FULL, "steps": [],
                          "setup": "all full, untouched", "expected_requests": NO_REQUESTS, "scenario": "identity"},
    "eval-identity-dim": {"role": "evaluation", "seconds": 15, "lighting": "dim", "start": FULL, "steps": [],
                          "setup": "all full, untouched, dim", "expected_requests": NO_REQUESTS,
                          "scenario": "identity"},
    "eval-removal-s3-day": {"role": "evaluation", "seconds": 20, "lighting": "day", "start": FULL,
                            "steps": [{"at": 5, "do": "take the milk out of slot 3 and keep it away",
                                       "after": {"3": "empty"}}],
                            "setup": "all full", "expected_requests": {"1": 0, "2": 0, "3": 1},
                            "scenario": "removal"},
    "eval-swap-s2-s3-day": {"role": "evaluation", "seconds": 20, "lighting": "day", "start": FULL,
                            "steps": [{"at": 5, "do": "hands in: start swapping yoghurt (2) and milk (3)",
                                       "after": {"2": "obstructed", "3": "obstructed"}},
                                      {"at": 8, "do": "hands away: swap done",
                                       "after": {"2": "463335", "3": "464316"}}],
                            "setup": "all full", "expected_requests": NO_REQUESTS, "scenario": "moved"},
    "eval-move-s3-to-s2-day": {"role": "evaluation", "seconds": 20, "lighting": "day", "start": FULL,
                               "steps": [{"at": 5, "do": "take the yoghurt out of slot 2 and keep it away",
                                          "after": {"2": "empty"}},
                                         {"at": 8, "do": "move the milk from slot 3 into slot 2",
                                          "after": {"2": "463335", "3": "empty"}}],
                               "setup": "all full", "expected_requests": {"1": 0, "2": 1, "3": 0},
                               "scenario": "moved"},
    "eval-unfamiliar-s3-day": {"role": "evaluation", "seconds": 20, "lighting": "day", "start": FULL,
                               "steps": [{"at": 5, "do": "replace the milk in slot 3 with the unfamiliar item",
                                          "after": {"3": "unknown"}}],
                               "setup": "all full; unfamiliar item ready", "expected_requests": NO_REQUESTS,
                               "scenario": "unfamiliar"},
    "eval-lookalike-s3-day": {"role": "evaluation", "seconds": 20, "lighting": "day", "start": FULL,
                              "steps": [{"at": 5, "do": "replace the milk in slot 3 with the look-alike carton",
                                         "after": {"3": "LOOKALIKE"}}],
                              "setup": "all full; look-alike carton ready", "expected_requests": NO_REQUESTS,
                              "scenario": "unfamiliar"},
    "eval-hand-day": {"role": "evaluation", "seconds": 20, "lighting": "day", "start": FULL,
                      "steps": [{"at": 3, "do": "hand over slot 1", "after": {"1": "obstructed"}},
                                {"at": 6, "do": "hand away from slot 1", "after": {"1": FULL["1"]}},
                                {"at": 9, "do": "hand over slot 2", "after": {"2": "obstructed"}},
                                {"at": 12, "do": "hand away from slot 2", "after": {"2": FULL["2"]}},
                                {"at": 15, "do": "hand over slot 3", "after": {"3": "obstructed"}},
                                {"at": 18, "do": "hand away from slot 3", "after": {"3": FULL["3"]}}],
                      "setup": "all full", "expected_requests": NO_REQUESTS, "scenario": "obstruction"},
    "eval-light-change": {"role": "evaluation", "seconds": 20, "lighting": "day-dim-day", "start": FULL,
                          "steps": [{"at": 5, "do": "light off", "after": {}},
                                    {"at": 12, "do": "light back on", "after": {}}],
                          "setup": "all full, day light", "expected_requests": NO_REQUESTS, "scenario": "light"},
}

TRANSITION_BEFORE, TRANSITION_AFTER = 0.5, 1.5  # seconds around each mark left unlabelled (hands moving)


def plan_for(name: str, lookalikes: list[str]) -> dict | None:
    """The plan of a recording name; evaluation names carry an occasion suffix (-a, -b, ...)."""
    base, occasion = name, None
    if name.startswith("eval-") and name.rsplit("-", 1)[-1] in "abcdef" and len(name.rsplit("-", 1)[-1]) == 1:
        base, occasion = name.rsplit("-", 1)
    p = PLAN.get(base)
    if p is None:
        return None
    p = {**p, "base": base, "occasion": occasion}
    if base == "ref-s3-lookalikes-day":
        if not lookalikes:
            raise SystemExit("ref-s3-lookalikes-day needs --lookalike CODE for each carton, in the order you show them")
        p["start"] = {**FULL, "3": lookalikes[0]}
        p["steps"] = [{"at": 5 * i, "do": f"swap in look-alike {c} alone in slot 3", "after": {"3": c}}
                      for i, c in enumerate(lookalikes[1:], start=1)]
        p["seconds"] = 5 * len(lookalikes)
    if base == "eval-lookalike-s3-day":
        if len(lookalikes) != 1:
            raise SystemExit("eval-lookalike-s3-day needs exactly one --lookalike CODE (the carton you use)")
        p["steps"] = [{**s, "after": {"3": lookalikes[0]}} for s in p["steps"]]
    if p["role"] == "evaluation" and not occasion:
        raise SystemExit(f"evaluation recordings are made twice: name it {name}-a or {name}-b")
    return p


def label_windows(start: dict, steps: list[dict], marks: list[float], end: float) -> list[dict]:
    """Per-slot windows [from_t, to_t) between marks; the transition around each mark stays unlabelled."""
    windows = []
    content = dict(start)
    since = {s: 0.0 for s in content}
    for step, t in zip(steps, marks):
        for slot, value in step["after"].items():
            if since[slot] < t - TRANSITION_BEFORE:
                windows.append({"from_t": round(since[slot], 3), "to_t": round(t - TRANSITION_BEFORE, 3),
                                "slots": {slot: content[slot]}, "validated": True})
            content[slot] = value
            since[slot] = t + TRANSITION_AFTER
    for slot in content:
        if since[slot] < end:
            windows.append({"from_t": round(since[slot], 3), "to_t": round(end, 3),
                            "slots": {slot: content[slot]}, "validated": True})
    return sorted(windows, key=lambda w: (w["from_t"], list(w["slots"])))
