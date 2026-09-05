# SENTINEL AI

Station Emergency Network for Transit Intelligence, Notification and Early-Warning

**Explainable edge crowd intelligence with a failure-tolerant local warning and recovery architecture for railway environments.**

SENTINEL AI is a crowd-risk prototype for station environments. It turns camera or scenario-video frames into an explainable operator view of dynamic crowd conditions, then preserves the local warning chain when connectivity is weak or absent.

> **The Internet can fail. The warning chain cannot.**

Connectivity is a dependency for synchronization, not a dependency for safety.

## Why this problem is dynamic

Headcount alone does not tell an operator whether people are accumulating, a local bottleneck is emerging, or a previously stable area is changing rapidly. Communications can also be degraded during exactly the period when a warning matters. SENTINEL addresses both crowd-state intelligence and warning continuity. It is decision-support software, not a guarantee of stampede prevention or a railway-authorized operating procedure.

## How it works

```text
Camera / Scenario Video
          ↓
YOLO person detection → 4×6 spatial occupancy grid → adaptive baseline
          ↓
  L: load anomaly   A: accumulation   R: redistribution
          ↓
scenario classification → severity + recommended response
          ↓
RiskSnapshot / immutable IncidentCandidate UUID
```

- **L — Load Anomaly:** how unusual current zone loading is relative to the adaptive baseline.
- **A — Accumulation:** whether occupancy is persistently building over time.
- **R — Redistribution:** whether people are moving between zones even when total headcount changes little.

High occupancy does not automatically mean RED. A crowded but stable scene can remain non-critical when its dynamic signals are low. The displayed Occupancy Index is relative; this prototype is not geometrically calibrated for exact people/m².

## Safety and continuity planes

```text
                         SENTINEL AI
 ┌──────────────────────── SAFETY PLANE ────────────────────────┐
 Camera → YOLO → 4×6 Grid → L/A/R → Scenario/Severity → UUID     │
 └──────────────────────────────┬───────────────────────────────┘
                                │ SQLite commit
                  ┌─────────────┴─────────────┐
                  ▼                           ▼
            Local warning              CONTINUITY PLANE
                                             │ durable outbox
                                             ▼
                            Connectivity: OFFLINE / RECOVERY / ONLINE
                                             ▼
                                     idempotent sync
```

No synchronous WAN call is permitted to block camera processing, inference, risk computation, incident persistence, or local warning. SQLite/WAL is the durable source of truth; remote sync is an independent background responsibility.

## Local warning and restart recovery

```text
PERSISTED → LOCAL_DELIVERED → LOCAL_ACKNOWLEDGED
```

`PERSISTED` means the incident is committed locally. `LOCAL_DELIVERED` means it reached the local operator warning path. `LOCAL_ACKNOWLEDGED` records explicit operator acknowledgement. Acknowledgement never changes remote sync state: for example, `LOCAL_ACKNOWLEDGED + SYNC_PENDING`, `AUTH_BLOCKED`, or `SYNCED` are all valid.

If a process stops after the SQLite commit but before local delivery, restart recovery rebuilds the original candidate from its stored payload and handles the **same UUID**. A still-current incident becomes a live local warning; an historical incident is marked locally handled without becoming a fresh audible emergency. Acknowledged state and dashboard history survive restart.

## Remote lifecycle and identity

```text
SYNC_PENDING → SYNCING → SYNCED
     └→ RETRYABLE_FAILURE → retry ─┘
```

`401`/`403` is `AUTH_BLOCKED`, not an Internet outage: the local event remains durable and automatic blind retry stops until credentials are refreshed. Every incident episode has one immutable UUID, reused across persistence retries, outages, restarts, interrupted sync, response loss, and recovery.

The localhost qualification backend demonstrates server idempotency: if it stores event X but its response is lost, SENTINEL retries X with the same UUID. The same canonical payload yields `ALREADY_ACCEPTED` and one remote row; altered content with that UUID yields `IDEMPOTENCY_CONFLICT` and preserves the canonical payload.

## Run the demo

```bash
pip install -r requirements.txt
python verify_system.py
pytest -q tests
python deploy.py
```

Open [http://localhost:5000](http://localhost:5000). Use **REALITY** for the configured camera, or **SIMULATION** for the bundled scenario video (with upload as an alternative). Simulation does not use fake risk output: both modes use the same `SentinelRuntime`, YOLO detector, grid, baseline, L/A/R, scenario, and severity logic; only the frame source changes.

For the optional localhost qualification backend:

```bash
python qualification_server.py
SYNC_ADAPTER_TYPE=HTTP SYNC_ENDPOINT_URL=http://127.0.0.1:5051/api/events python deploy.py
```

This backend is a reference qualification service, not production cloud infrastructure. See the [demo guide](docs/DEMO.md) for a complete judge flow.

## Operator view

The dashboard presents people count, relative occupancy, current risk, a 4×6 zone map, hotspot and loaded zones, L/A/R, explanation and recommended response, input/AI health, connectivity, local warning and acknowledgement state, remote sync state, event UUID, and events lost. Stale input is explicitly presented as stale/unknown rather than as current healthy risk.

## Engineering evidence

| Capability | Evidence |
| --- | --- |
| Automated suite | 96 passing tests at the documented baseline |
| Canonical verifier | `python verify_system.py` PASS |
| Offline continuity | deterministic Round 2 end-to-end coverage |
| Restart recovery | same immutable UUID and one SQLite row |
| Local warning | recovery from `PERSISTED`; acknowledgement independent of sync |
| Server idempotency | duplicate replay does not create a second remote row |
| Lost response | same UUID retry reaches `SYNCED` |
| Authorization | `AUTH_BLOCKED` separate from connectivity |
| Input modes | REALITY/SIMULATION use one intelligence pipeline |

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Judge demo guide](docs/DEMO.md)
- [Failure matrix](docs/FAILURE_MATRIX.md)
- [Qualification evidence](docs/QUALIFICATION.md)
- [Limitations](docs/LIMITATIONS.md)
- [Judge brief](docs/JUDGE_BRIEF.md)

## Scope and production boundary

The current prototype demonstrates YOLO person detection, relative spatial occupancy, adaptive L/A/R signals, local-first warning, durable recovery, and idempotent store-and-forward synchronization. It does not claim exact people/m², certified thresholds, guaranteed prediction/prevention, full directional counterflow tracking, national-scale deployment, or production IAM/TLS/backend infrastructure. Production use would require operator-controlled infrastructure, station-specific calibration, security controls, retention policy, and formal operational validation.

## Dashboard Features (World-Class Command Center)

| Feature | Description |
|---|---|
| **5-Step Guided Judge Pitch Tour** | Scripted demonstration runner (Safe Baseline → Rush Surge → Bottleneck & Crisis → Zero-WAN Outage → Failsafe Sync) for a seamless 2-minute judge pitch |
| **Quad-Cam CCTV Matrix** | Multi-channel video surveillance (CAM-01 Concourse, CAM-02 Platform 1 YOLO stream, CAM-03 North FOB, CAM-04 Turnstiles) with single/quad layout switch and channel focusing |
| **Predictive Surge & Time-to-Crush ($T_{crit}$)** | Real-time crowd inflow velocity regression ($\Delta N/\Delta t$) predicting critical chokepoint thresholds minutes before stampede risks develop |
| **Digital Twin Dynamic Flow Canvas** | HTML5 Canvas particle flow engine rendering 55 micro-vectors over 24 platform zones with dynamically generated green egress routing arrows during high-severity events |
| **Multilingual Voice & Speech Recognition** | Authentic 3-tone railway chime (F4, A4, C5), English/Hindi text-to-speech public address announcements, and hands-free microphone voice assistant |
| **Station Marshal (RPF) SLA Checklist** | Real-time emergency response timer enforcing $<90\text{s}$ compliance with timestamped milestone badges |
| **Forensic Audit Dossier (`/api/incident/report`)** | Official printable compliance certificate sealed with a SHA-256 cryptographic digest (`SEC-WAL-...`) of the SQLite WAL journal |
| **Railway Platform Layout** | Live SVG top-down station map — 8 tactical zones colored GREEN/YELLOW/RED/BLACK in real-time with interactive zone clearance dossiers |
| **Alerts & Response** | Per-incident Response SOPs with step-by-step protocols per severity level and one-click ACKNOWLEDGE action |
| **Density & People Trends** | Real-time Chart.js telemetry graphs of people count + occupancy index over time |
| **Keyboard Shortcuts** | D=Dashboard, M=Monitoring, P=Platform Layout, S=Simulation, A=Alerts, H=Health, Ctrl+F=Fullscreen |
| **CSV Export** | Download full incident history as CSV for compliance and reporting |
| **Security Headers** | X-Content-Type-Options, X-Frame-Options, Referrer-Policy on every response |
| **/health endpoint** | Uptime monitor / load balancer friendly lightweight health check |
