# Recognition scale study, round 3

Experimental only. No Valio API calls and no demo/board changes. Sponsor catalogue inputs remain on the
private Volume. The earlier `modal_app.py` smoke jobs are retained; use `scale_app.py` for round 3.

## Run and resume

From this directory, first inspect `modal app list --json` and `modal volume ls valio-recognition /`.
Never launch while another `valio-recognition-exp` is running. Use `~/.local/bin/modal` on this laptop.

```
modal run --detach scale_app.py --run-id r3-tiny --tiny
modal run --detach scale_app.py --run-id r3-full
```

Use the same run ID to resume after the prior app stops. Finished per-item JSON files are skipped,
including infrastructure failures (delete only specific failed item files deliberately to retry them).
The detached coordinator waits for its children. The local entrypoint records its app, dashboard and
call ID in STATUS immediately. Every child launch is recorded on the Volume in `infrastructure/calls.json`
and `infrastructure/calls/`; per-item files include the executing call ID. GPU bound: two L4/A10G/A100-40GB Qwen
containers plus one T4 embedding container. CPU bound: six containers, below the shared 20/app limit.

Results: `/runs/r3/<run-id>/synthetic/bias/`, `synthetic/baselines/`, `real/`, `infrastructure/`.
Download to a private local directory, then:

```
/Users/bigboss/.local/share/uv/tools/modal/bin/python fetch_results.py r3-full /tmp/r3-full
uv run python summarize.py /tmp/r3-full
```

Do not commit downloaded item files: their prompts include sponsor catalogue names. Summaries contain
only aggregate counts. Model weights reuse `/hf-cache`. Qwen, DINOv2 and CLIP revisions are pinned in
`recog/models.py`; transformers 4.51.3 and torch 2.6.0 retain the proven Qwen loading path.

## Design and interpretation

False-product bias: 18 inputs, 27 Qwen / 30 Kimi prompt variants, 10 repeats per unit = 4,860 Qwen and
5,400 Kimi outputs. Qwen repeats alternate greedy and temperature 0.7/top-p 0.9 sampling (five each).
Kimi default uses reasoning low, with explicit reasoning-none and temperature-zero configurations.
Two-step wording makes two requests, both accounted and rate-limited. One serial Verda lane spaces
all requests by at least 5.05 seconds, checks remaining request/token budgets, honours Retry-After,
and retries at most three times after the first attempt. Three consecutive failed items stop a lane.
DeepSeek is probed at batch start; its access status remains infrastructure evidence.

The 18 inputs are five frames x three crops plus grey/black/noise controls. They contain no catalogue
product. The no-list outcome is *any claimed packaged grocery*, distinct from the listed-code endpoint.
They are synthetic stress tests. Repeats and adjacent frames are dependent; Wilson intervals describe
the observed sample only and must not be presented as an independent real-shelf accuracy estimate.

Baselines: 5 frames x 3 slots x 13 deterministic augmentations (blur, brightness, occlusion, rotation).
DINOv2, CLIP and RGB template nearest neighbours use first-frame slot views as references; four fixed
thresholds and a 0.03 embedding margin. These are view-consistency tests, not product identities.
The actual two-photo detector also runs on augmented full frames against saved calibration arrays.
The thumbnail RGB template is an additional comparison, not a replacement for the two-photo detector.

## Human-validated real recordings

Integration uploads `tests/device/sequences/<name>/` to `/inputs/recordings/<name>/`, including frames,
`meta.jsonl`, `config.json`, calibration arrays and a `labels.json` file. Example proposal (never scored):

```json
{
  "validated": false,
  "validated_by": "",
  "validation_source": "human",
  "session_id": "capture-session-20261114-day-a",
  "role": "evaluation",
  "scenario": "identity",
  "assigned": {"1": "477032", "2": "464316", "3": "463335"},
  "expected_requests": {"1": 0, "2": 0, "3": 0},
  "labels": [{"from_t": 0, "to_t": 15, "slots": {"1": "477032", "2": "464316", "3": "463335"}}]
}
```

A person reviews the labels, fills `validated_by`, and sets `validated` to true. Automated proposals
must keep it false. A reference capture has `role: reference`; all folders from a single capture
session must share `session_id`. Same-session reference/evaluation splits are rejected, even when
folder names differ. Unknown and obstructed reference examples are excluded from training. Labels,
config and image contents are fingerprinted so corrected data produces new outputs.

A CPU watcher checks once per minute for 12 hours after synthetic baselines finish. It stays idle,
without allocating a GPU, until both validated reference and held-out evaluation sessions exist.
Resume the same run after the watch expires. `real/status.json` states why it is idle.

The specialised candidate is frozen DINOv2/CLIP 1-NN **per slot**, trained only on validated references.
Threshold 0.85/margin 0.03 is provisional and fixed before evaluation. Identity, unknown, obstruction,
wrong-product and moved-product counts use the existing scorer. Downstream results compare board
classify/observe with and without the veto gate, counting confirmed shortage decisions. This is a
proxy: it excludes gateway purchasing and backend restock policy, and is labelled accordingly.
Calibration files must exist for downstream scoring. No external order can be sent.

## Usage and tests

Before/after snapshots use the Modal billing SDK equivalent of CLI summary/report; permission or reporting
lag is explicit. Verda usage sums response prompt/completion/total tokens including both turns. Local
Qwen token counts are kept separate. No assumed token price is converted to a measured dollar cost.

`cd ../.. && uv run pytest experiments/recognition/tests` runs the own tests. The main suite on this old
worktree needs `RESTOCK_MIN_S=0` to avoid two known pre-existing environment-dependent restock failures.
