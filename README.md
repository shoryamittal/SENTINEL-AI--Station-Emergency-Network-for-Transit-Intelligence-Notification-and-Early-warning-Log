# SENTINEL AI

Station Emergency Network for Transit Intelligence, Notification and Early-Warning

SENTINEL AI is a local-first adaptive crowd-risk prototype for railway platform environments. It detects people with YOLOv8, maps occupancy into a 4×6 spatial grid, evaluates relative load anomaly, accumulation, and redistribution, generates explainable risk states, and preserves the critical warning loop during weak or lost connectivity.

> **The Internet can fail. The warning chain cannot.**

Connectivity is a dependency for synchronization, not a dependency for safety.

`python deploy.py` is the canonical Round 2 application and judge/demo command.

## Architecture

```text
Camera / Video
      |
      v
YOLOv8 Person Detection
      |
      v
4x6 Occupancy Grid
      |
      v
Adaptive Baseline
      |
      v
L / A / R
      |
      v
Scenario + Severity
      |
      v
RiskSnapshot / IncidentCandidate
      |
      v
SQLite Local Journal
      +--> Local Alert
      |
      v
Sync Outbox -> Connectivity Manager -> ONLINE / DEGRADED / OFFLINE / RECOVERY
      |
      v
Idempotent Recovery Sync
```

The **Safety Plane** (camera through `IncidentCandidate`) is local and does not make network calls. The **Continuity Plane** persists candidates before attempting any synchronization, delivers local alerts independently of WAN state, and replays the outbox when connectivity recovers.

## Adaptive crowd intelligence

SENTINEL intentionally uses three explainable dynamic signals:

- **L — Load Anomaly:** compares current zone occupancy with the scene's adaptive baseline.
- **A — Accumulation:** detects sustained crowd build-up using smoothed temporal behavior.
- **R — Redistribution:** detects spatial movement between zones even when total people count is approximately unchanged.

The current scenarios are `STABLE_HIGH_OCCUPANCY`, `ACCUMULATION`, `MASS_REDISTRIBUTION`, `LOCAL_BOTTLENECK`, and `UNKNOWN`. High occupancy does not automatically mean RED: a crowded but stable scene can remain GREEN when dynamic anomaly signals remain low.

Severity is `GREEN`, `YELLOW`, `RED`, or `BLACK`. Its values and hysteresis are prototype calibration parameters, not Indian Railways safety standards. Recommendations are prototype decision support, not authorized operating procedures.

The webcam/video prototype reports people count, zone occupancy, and a relative **Occupancy Index**. It is not geometrically calibrated for exact people/m² density.

Flow Conflict (F) and tracker-based counterflow analysis are future work / under validation.

## Continuity under low connectivity

`IncidentJournal` uses SQLite with WAL mode and short-lived transactional connections. Each event retains its original UUID event ID and is written locally before any remote action.

- `LocalAlertCenter` provides a zero-network local alert path.
- `ConnectivityManager` runs independently with hysteresis across ONLINE, DEGRADED, OFFLINE, and RECOVERY states.
- `SyncWorker` uses a durable outbox, bounded exponential retry/backoff, and idempotent replay.
- Historical sync does not create a new live alert.
- Runtime-derived continuity metrics expose persisted, delivered, synchronized, and lost-event counts.

`MockSyncAdapter` remains the default for deterministic development and demo fault injection. An optional localhost-only HTTP qualification backend (`python qualification_server.py`) can prove server-side `event_id` uniqueness, including the timeout-after-server-success recovery case: a retry of the same UUID receives `ALREADY_ACCEPTED` and does not create a second remote row. It is a reference/qualification service, **not** production cloud infrastructure. Production deployment still requires authenticated, operator-controlled remote synchronization infrastructure.

An HTTP `401`/`403` is treated separately as `AUTH_BLOCKED`: the local event remains durable and is excluded from automatic sync retries until an explicit credential refresh requeues the same original event ID. This does not change network connectivity or interrupt camera, AI, local alert, or SQLite safety functions.

The default prototype connectivity probe uses a public reachability endpoint. A production deployment should instead use an operator-controlled service or health endpoint.

## Run

```bash
pip install -r requirements.txt
python verify_system.py
pytest -q tests
python deploy.py
```

Open the operator dashboard at [http://localhost:5000](http://localhost:5000).

The dashboard binds to `127.0.0.1` by default. The debug connectivity override is disabled by default (`ENABLE_DEBUG_CONNECTIVITY=0`); physical WAN loss is the preferred continuity demonstration. A controlled LAN bind requires an explicit `SENTINEL_BIND_HOST=0.0.0.0` setting.

`python main.py` is an optional local Safety Plane CLI. It has no persistence or connectivity wiring; use `deploy.py` for the Round 2 demo.

To run the optional qualification protocol demo in a second local terminal:

```bash
python qualification_server.py
SYNC_ADAPTER_TYPE=HTTP SYNC_ENDPOINT_URL=http://127.0.0.1:5051/api/events python deploy.py
```

The server binds to `127.0.0.1` by default. `QUALIFICATION_API_TOKEN` can add a small bearer-token protocol test, but it is intentionally not a production identity system. Production deployment requires proper operator authentication, authorization, TLS, secret management, and access control.

## Qualification demo loop

1. Start `python deploy.py`.
2. Confirm frame ID and risk timestamp advance.
3. Disable WAN, or use the clearly labelled debug connectivity override.
4. Trigger an incident.
5. Show the local alert and SQLite persistence.
6. Show `SYNC_PENDING` in the outbox.
7. Restart the app if demonstrating restart persistence.
8. Restore connectivity.
9. Show `RECOVERY`.
10. Show the same event ID reaching `SYNCED`.
11. Show `EVENTS LOST = 0`.

A physical WAN disconnect demonstrates real loss of reachability. The debug override is a demo control only and does not itself prove a physical network failure.

## Feature status

| Status | Capability |
| --- | --- |
| Implemented | YOLOv8 person detection; camera/video input; 4×6 occupancy mapping; Occupancy Index; adaptive baseline; L/A/R signals; scenario engine; severity hysteresis; local recommendations |
| Implemented | Flask operator dashboard; SQLite WAL persistence; zero-network local alerts; connectivity state machine; store-and-forward recovery; exponential backoff; idempotent event IDs; stale-alert protection; deterministic offline tests |
| Prototype / demo | Public reachability connectivity probe; `MockSyncAdapter`; localhost qualification HTTP backend; optional best-effort Fast2SMS notifier; manual connectivity override |
| Future work | Calibrated people/m²; Flow Conflict F; tracker-based counterflow; production remote backend; operator-controlled health endpoint; advanced prediction; digital twin; rail-system integration |

## Configuration

Copy `.env.example` to `.env` to configure the camera source, local YOLO model, occupancy grid, local SQLite path, and continuity demo controls. `SYNC_ADAPTER_TYPE=MOCK` is the default; `SYNC_ADAPTER_MODE` is DEVELOPMENT / DEMO fault injection only. `SYNC_ADAPTER_TYPE=HTTP` selects the separate localhost qualification backend, not a production cloud backend.

`deploy.py` is the supported Round 2 path. The repository retains legacy `app.py` and historical simulation scripts for compatibility, but they are not part of the Round 2 qualification path.
