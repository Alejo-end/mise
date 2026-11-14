# Worker briefs, unattended round of 14 Nov 2026 (from 05:45)

| Brief | Worktree | Branch | Priority |
|---|---|---|---|
| `w3-valio-data.md` | `junction2026-wt-valio/` | `wt/valio-data` | 1, the 12:00 milestone |
| `w1-reliability.md` | `junction2026-wt-detection/` | `wt/detection-reliability` | 2 |
| `w2-shelf-config.md` | `junction2026-wt-shelf/` | `wt/shelf-config` | 3 |
| `w4-recognition.md` | `junction2026-wt-recognition/` | `exp/recognition` | experimental, must not touch the demo |

Rules for every worker (also read `../PARALLEL_WORK.md`, `../../../TEAM_BRIEF.md`,
`../../HANDBOOK.md`, `../../DEMO_PLAN.md`):

1. Work only in your worktree, on your branch, in the files the contract gives you. Read anything.
2. Never touch the board (no `adb`, no `ssh` to 172.20.10.2, no app restarts), the live backend on
   port 8000, the camera page on 8765, the main checkout `junction2026/`, or `main`. Do not push.
3. Never print, log or commit a key, token or password. `.env` files and `data/` stay out of git.
   Run `git diff --cached` and a grep for `VALIO_API_KEY=`, `ak-`, `as-`, `BEGIN` before each commit.
4. First action: write `valio-challenge/data/WORKER_STATUS.md` (git ignores it) with your start time
   and plan. Update it at each milestone and when blocked.
5. Bounds: at most 3 hours of work (worktree 3: 4 hours), at most 3 retries of any failing step,
   no step longer than 20 minutes. Commit work in progress at least every 45 minutes with green
   tests. When blocked, commit what works, write the block into your handoff, and continue with
   independent work.
6. Last action: commit `valio-challenge/docs/handoff/<your brief name>.md`: what is done (with
   commits), what each test run showed (exact counts), what is not tested, open decisions, what
   needs a person or the board. Distinguish measured results from assumptions.
7. The language-model purchasing agent stays deferred. The team brief forbids features the work
   order does not list: if you think something is needed, write it into the handoff, do not build it.
