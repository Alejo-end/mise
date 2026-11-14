"""Critical review of completed research outputs by Kimi K3.1 Pro on Verda (one request per direction, then a synthesis).

  uv run --with httpx python tools/review_research.py OUT_DIR

Reads the committed version of each output (git show BRANCH:PATH), so files being edited are not reviewed half
written. Sequential, at most 4 requests per minute, 3 retries, 60 s timeout... per request 180 s for long inputs.
The key comes from VERDA_INFERENCE_API_KEY (environment) or a local .env; it is never printed or saved.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DIRECTIONS = {
    "replenishment-simulator": [("exp/replenishment-sim", "valio-challenge/experiments/replenishment-sim/FINDINGS.md")],
    "market-research": [("exp/market-research", "valio-challenge/research/market/report.md")],
    "economics": [("exp/sustainability", "valio-challenge/docs/sustainability.md")],
    "recognition": [
        ("exp/recognition", "valio-challenge/experiments/recognition/results/synthetic/r3-full-first-summary.md"),
        ("exp/recognition", "valio-challenge/experiments/recognition/results/infrastructure/kimi-payload-20261114/README.md"),
        ("exp/recognition", "valio-challenge/experiments/recognition/results/synthetic/conditioning-full-first-summary.md"),
        ("exp/recognition", "valio-challenge/docs/handoff/w6-recognition-scale.md"),
        ("exp/vlm-models", "valio-challenge/docs/handoff/vlm-models.md"),
        ("exp/vlm-models", "valio-challenge/experiments/vlm-models/evidence/first-results.md")],
    "agent-benchmark": [("exp/agent-bench", "valio-challenge/docs/handoff/w5-agent-bench.md")],
    "shelf-detector": [("valio/integration-unattended", "valio-challenge/docs/measurements/detector-gap-2026-11-14.txt"),
                       ("valio/integration-unattended", "valio-challenge/docs/measurements/board-baseline-2026-11-14.md")],
}
CONTEXT = (
    "Context: Junction 2026 hackathon, Valio Aimo challenge 'Trusted Trade Agents'. Our entry is the Aimo Agent Gateway "
    "(lookup, signed quotes, orders with idempotency, human approval, audit log, an agent Driving Test) with a kitchen "
    "shelf box (camera on an Arduino UNO Q) as one client. Judging: end-to-end request-lookup-order/approval demo 30%, "
    "matching the buyer's need 20%, autonomy and controls 20%, robustness to exceptions 15%, openness and path to "
    "production 15%. No validated recordings of the real shelf products exist yet. The replenishment proposal "
    "(continuous observation, scheduled ordering) is pending team agreement and was only simulated.")
TASK = (
    "You are a critical reviewer. Review the research output below. Return JSON only with keys: "
    "\"conclusions\": list of {\"claim\": short, \"evidence\": exact pointer inside the text (section, table, number), "
    "\"evidence_class\": one of measured-infrastructure | synthetic | public-dataset | vendor-claim | independent-source | "
    "real-shelf-validation | assumption | code-claim, \"supported\": yes | partly | no, \"decision_informed\": which team "
    "decision it informs, \"caveat\": the most important limitation}; \"unsupported_or_overstated\": list of statements that "
    "go beyond their evidence; \"repetitive_or_low_value\": list of items a team summary should drop; \"negative_results\": "
    "list of useful negative findings; \"open_question_worth_a_run\": at most two specific runs that would resolve a "
    "remaining uncertainty, or an empty list. Be strict: synthetic or public-data results never establish recognition of "
    "the team's own products or performance on the UNO Q; vendor documentation is a claim, not independent evidence; "
    "statements about implemented code must be marked code-claim so the coordinator can verify them.")


def key() -> str:
    if os.getenv("VERDA_INFERENCE_API_KEY"):
        return os.environ["VERDA_INFERENCE_API_KEY"]
    for env in (ROOT / ".env", ROOT.parents[1] / "junction2026-wt-recognition" / "valio-challenge" / ".env"):
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("VERDA_INFERENCE_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("VERDA_INFERENCE_API_KEY not configured")


def committed(branch: str, path: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), "show", f"origin/{branch}:{path}"], capture_output=True, text=True,
                          check=True).stdout


def ask(client: httpx.Client, k: str, prompt: str) -> dict:
    body = {"model": "kimi-k3-1-pro", "max_tokens": 6000, "reasoning_effort": "low", "temperature": 0,
            "response_format": {"type": "json_object"}, "messages": [{"role": "user", "content": prompt}]}
    for attempt in range(1, 4):
        r = client.post("https://inference.verda.com/v1/chat/completions", json=body, timeout=180,
                        headers={"Authorization": f"Bearer {k}"})
        if r.status_code == 200:
            j = r.json()
            return {"content": j["choices"][0]["message"]["content"], "usage": j.get("usage", {}), "attempts": attempt}
        wait = float(r.headers.get("retry-after", 20 * attempt))
        print(f"  HTTP {r.status_code}, retry in {wait:.0f} s", flush=True)
        time.sleep(wait)
    return {"error": f"HTTP {r.status_code}", "attempts": 3}


def main() -> None:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "-C", str(ROOT), "fetch", "-q", "origin"], check=True)
    k = key()
    reviews = {}
    with httpx.Client() as client:
        for name, sources in DIRECTIONS.items():
            if (out / f"{name}.json").exists():
                reviews[name] = json.loads((out / f"{name}.json").read_text())
                continue
            text = "\n\n".join(f"===== {b}:{p} =====\n{committed(b, p)}" for b, p in sources)
            print(f"{name}: {len(text)} chars", flush=True)
            rec = ask(client, k, f"{CONTEXT}\n\n{TASK}\n\nDirection: {name}\n\n{text}")
            rec["sources"] = [f"{b}:{p}" for b, p in sources]
            rec["at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            (out / f"{name}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False))
            reviews[name] = rec
            time.sleep(16)  # at most 4 requests per minute
        if not (out / "synthesis.json").exists():
            digest = {n: r.get("content", r.get("error")) for n, r in reviews.items()}
            prompt = (f"{CONTEXT}\n\nBelow are your per-direction reviews (JSON). Return JSON only: {{\"directions\": list of "
                      "{\"direction\", \"recommendation\": keep | change | defer | drop, \"reason\": one sentence citing the "
                      "evidence class, \"next_step\": one concrete step or none}, \"cross_cutting_risks\": list, "
                      "\"claims_to_remove_from_team_summary\": list}}. Prefer negative results over unsupported claims.\n\n"
                      + json.dumps(digest, ensure_ascii=False))
            rec = ask(client, k, prompt)
            rec["at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            (out / "synthesis.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False))
    total = {}
    for f in out.glob("*.json"):
        for kk, v in (json.loads(f.read_text()).get("usage") or {}).items():
            if isinstance(v, int):
                total[kk] = total.get(kk, 0) + v
    print("usage", total)


if __name__ == "__main__":
    main()
