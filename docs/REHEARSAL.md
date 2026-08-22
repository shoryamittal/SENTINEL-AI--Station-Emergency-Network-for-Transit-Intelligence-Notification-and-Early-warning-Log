# Competition-day rehearsal runbook

## Night-before check

- `git pull --rebase origin main`; record the commit SHA
- Run `pytest -q tests` and `python verify_system.py`
- Confirm the working tree is clean
- Verify camera input, REALITY mode, and bundled SIMULATION video
- Verify the localhost qualification backend and dashboard in a browser
- Verify the Wi-Fi toggle and record a backup video demonstration
- Pack charger; keep the demo clip local
- Close background camera applications and disable notifications, sleep, and updates for the presentation window

## 30-minutes-before check

- Start the qualification backend if it will be demonstrated
- Start `deploy.py`, open the dashboard, and allow the model to warm up
- Confirm camera and simulation modes, dashboard controls, browser zoom, and local SQLite write access
- Rehearse one WAN-off/recovery sequence and one acknowledgement sequence
- Keep a second terminal and backup prerecorded demo ready

## Five-minutes-before check

- [ ] Dashboard loads immediately at the correct URL
- [ ] Frame ID is advancing
- [ ] Camera health is LIVE and AI is HEALTHY
- [ ] REALITY/SIMULATION switch and bundled crowd video work
- [ ] Connectivity is ONLINE
- [ ] SQLite is writable
- [ ] Qualification server is running, if used
- [ ] Events Lost is 0
- [ ] Browser zoom is correct; no console/error overlays
- [ ] Charger is connected and Wi-Fi toggle is accessible

## Live demo sequence

1. Open with the railway-risk problem, not YOLO.
2. Show people count, grid, hotspot, L/A/R, scenario, and response.
3. Disable WAN physically where practical; show local warning and `SYNC_PENDING` while AI remains healthy.
4. Acknowledge locally; point out remote sync remains independent.
5. Restore WAN; show the same UUID become `SYNCED`.
6. End with the final five lines from [PITCH.md](PITCH.md), then stop.

## Failure recovery plan

| Problem | Immediate response | Say |
| --- | --- | --- |
| Camera fails | Switch to SIMULATION | “The frame source has failed, so we’re switching input. The intelligence pipeline is unchanged.” |
| Simulation fails | Use a backup prerecorded demo | “This backup records the same qualifying workflow; simulation is not an accuracy benchmark.” |
| WAN toggle fails | Show deterministic continuity tests | Do not claim the Internet is disconnected. |
| Qualification backend fails | Continue local continuity demonstration | “The backend is a reference qualification service; local safety behavior remains independent.” |
| Dashboard fails | Restart `deploy.py` only if rehearsed | Do not debug source code in front of judges. |
| YOLO cold start | Wait calmly | “The local model is warming up; the demo sequence begins once inference is live.” |

## Rehearsal repetitions

| Rehearsal | Goal |
| --- | --- |
| 1 — technical slow run | Correctness |
| 2 — timed run | Timing |
| 3 — interrupted run | Flexibility under questions |
| 4 — forced failure | Recovery discipline |
| 5 — full competition simulation | No notes beyond final keywords |

Record timing each time. Reach the final sentence at 2:45–2:55; do not exceed a three-minute event limit. If there are multiple presenters, use one primary presenter for a short pitch; optional roles are A: problem/AI, B: failure/architecture, C: technical Q&A.
