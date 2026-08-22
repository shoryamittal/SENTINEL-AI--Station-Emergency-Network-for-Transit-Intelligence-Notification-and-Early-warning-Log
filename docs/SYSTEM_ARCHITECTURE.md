# SENTINEL AI — System Architecture

**Status:** Engineering source of truth  
**Scope:** Railway-station crowd monitoring, prediction, risk classification, simulation, and operator alerting  
**Canonical name:** **SENTINEL AI — Station Emergency Network for Transit Intelligence, Notification & Early-warning**

## 1. Purpose

This document defines the target production architecture for SENTINEL AI and separates it from the current repository state. It should be treated as the primary reference before adding new modules, changing interfaces, or optimizing runtime performance.

The project goal is to move station crowd management from reactive monitoring to proactive intervention by continuously processing CCTV input, estimating crowd state, predicting near-future congestion, classifying risk, simulating possible flow outcomes, and presenting actionable recommendations to authorized operators.

## 2. Current Repository Health Gate

Before feature development, the repository must pass this minimum health gate.

### Current observed blockers

1. `main.py`, `app.py`, `COMPREHENSIVE_TEST.py`, `verify_system.py`, and the zone simulation scripts import modules from `src`, but the current GitHub `main` branch does not contain a `src/` directory.
2. `README.md` and deployment documentation describe a modular `src/core/...` layout that is not present in the current repository snapshot.
3. `app.py` imports `streamlit`, but `streamlit` is not currently listed in `requirements.txt`.
4. `MOBILE_INTEGRATION.md` instructs the user to run `deploy_mobile.py`, while the repository contains `deploy_mobile.py.backup` rather than an active `deploy_mobile.py`.
5. Naming is inconsistent across files: `SENTINEL AI`, `PREEMPT AI`, and `Suraksha Kavach AI` are all present. The canonical project name should be SENTINEL AI.
6. The package metadata in `setup.py` still uses `preempt-ai` and a different repository URL.

### Health gate requirement

No release should be tagged until all of the following are true:

- `python verify_system.py` passes.
- `python -m pytest` passes.
- `python main.py --help` works in a clean environment.
- `streamlit run app.py` starts if the Streamlit dashboard remains part of the product.
- All documented paths exist.
- No secret, phone number, CCTV credential, or API token is committed.

## 3. Target High-Level Architecture

```text
┌──────────────────────────── INPUT LAYER ────────────────────────────┐
│ CCTV / RTSP / video file / optional station events / ticketing    │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────── EDGE PIPELINE ──────────────────────────┐
│ 1. Camera ingest                                                   │
│ 2. Frame sampling / preprocessing                                  │
│ 3. Person detection                                                │
│ 4. Optional tracking                                               │
│ 5. Perspective/zone mapping                                        │
│ 6. Density + heatmap estimation                                    │
│ 7. Short rolling history                                           │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────── INTELLIGENCE LAYER ─────────────────────────┐
│ 8. Future-density prediction                                       │
│ 9. Bottleneck / flow simulation                                    │
│ 10. Risk classification: GREEN / YELLOW / RED / BLACK             │
│ 11. Confidence + persistence logic                                  │
│ 12. Recommended intervention generation                             │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────── DECISION & ACTION ──────────────────────────┐
│ Operator dashboard                                                  │
│ Human approval for safety-critical actions                          │
│ SMS / email / local notification                                    │
│ RPF / station staff routing recommendation                          │
│ Audit log                                                           │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────── CONTINUOUS FEEDBACK ─────────────────────────┐
│ Updated CCTV state → validate effect → recompute → de-escalate      │
└──────────────────────────────────────────────────────────────────────┘
```

## 4. Recommended Repository Structure

The following structure should replace the current mismatch between documentation and implementation:

```text
sentinel-ai/
├── app.py
├── main.py
├── requirements.txt
├── requirements-dev.txt
├── .env.example
├── src/
│   └── sentinel/
│       ├── __init__.py
│       ├── config.py
│       ├── pipeline.py
│       ├── camera/
│       │   ├── source.py
│       │   └── health.py
│       ├── vision/
│       │   ├── detector.py
│       │   ├── heatmap.py
│       │   └── calibration.py
│       ├── density/
│       │   └── occupancy.py
│       ├── prediction/
│       │   ├── baseline.py
│       │   └── models.py
│       ├── simulation/
│       │   └── flow.py
│       ├── risk/
│       │   ├── classifier.py
│       │   └── policy.py
│       ├── actions/
│       │   ├── recommendations.py
│       │   └── notifications.py
│       ├── integrations/
│       │   └── railway.py
│       └── observability/
│           ├── metrics.py
│           └── logging.py
├── configs/
│   ├── base.yaml
│   ├── edge_low_power.yaml
│   ├── edge_balanced.yaml
│   └── gpu_station.yaml
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── scenarios/
│   └── performance/
├── data/
│   ├── samples/
│   └── calibration/
├── models/
├── docs/
└── scripts/
```

The exact package layout can change, but there should be only one canonical implementation path.

## 5. Runtime Design for Efficiency

### 5.1 Decouple the video pipeline from the UI

The dashboard must never control the processing loop directly. Video inference should run independently and publish the latest state to the UI. This prevents a slow browser or dashboard refresh from slowing down crowd analysis.

Recommended split:

```text
Camera worker → inference worker → state store → dashboard
                                ↘ alert worker
```

### 5.2 Use bounded queues

For real-time safety monitoring, processing an old frame is often worse than dropping it. Use a small bounded queue between capture and inference.

Recommended behavior:

- Queue depth: 1–3 frames.
- If inference falls behind, discard the oldest unprocessed frame.
- Keep timestamps so latency is measured from capture time, not processing time.

### 5.3 Separate fast-path and slow-path computation

**Fast path — every processed frame:**

- person detection
- zone assignment
- density update
- immediate threshold check

**Slow path — lower frequency or event-triggered:**

- route simulation
- long-horizon prediction
- historical analytics
- report generation

This protects frame rate while preserving advanced functionality.

### 5.4 Event-trigger expensive simulation

The project pitch centers on the intervention window after a trigger such as a platform change. Flow simulation should not necessarily run at full camera FPS. It should run:

- when a platform-change or station event arrives,
- when predicted density crosses a warning threshold,
- when the active route becomes unsafe,
- or on a low-rate background schedule.

### 5.5 Keep alerting asynchronous

SMS/email/network failure must not block the computer-vision loop. Notification dispatch should use an independent worker and a retry policy.

## 6. Canonical Processing Contract

Each processed observation should produce a single structured state object.

```text
StationState
- timestamp
- camera_id
- frame_id
- people_count
- zone_densities[]
- max_density
- avg_density
- heatmap_reference
- trend
- predicted_density
- prediction_horizon_seconds
- prediction_confidence
- risk_state
- risk_confidence
- bottleneck_zones[]
- recommended_actions[]
- system_health
```

Every component should consume or extend this contract rather than passing loosely structured dictionaries with different keys.

## 7. System Boundaries

### In scope for the MVP

- CCTV/video ingestion
- person detection
- density/occupancy mapping
- heatmap generation
- short-horizon trend prediction
- GREEN/YELLOW/RED/BLACK classification
- recommendations and notifications
- operator dashboard
- simulated platform-change scenarios

### Planned / advanced capability

These should be clearly labeled as planned unless implemented and validated:

- multi-camera identity-consistent tracking
- CSRNet or other density-estimation fallback for extreme occlusion
- learned LSTM/GRU prediction
- full physics-based digital twin
- ticketing-feed integration
- weather integration
- multi-station network coordination
- automatic control of station hardware

## 8. Human-in-the-Loop Rule

SENTINEL AI should be designed as a decision-support system, not an unrestricted autonomous emergency controller.

Recommended policy:

- GREEN: automatic monitoring and logging.
- YELLOW: automatic advisory notification allowed.
- RED: urgent recommendation; operator acknowledgement required for consequential routing/public-announcement changes.
- BLACK: immediate critical alert; emergency protocol displayed prominently; actions affecting passenger routing remain subject to authorized station control unless an approved railway operating procedure explicitly allows automation.

## 9. Observability

At minimum, record:

- capture FPS
- processed FPS
- capture-to-risk latency
- inference latency
- dropped-frame count
- camera reconnect count
- model load status
- people count
- max density
- predicted density
- active risk state
- alert delivery result
- operator acknowledgement time

Logs must be structured and must never contain secrets.

## 10. Definition of Done for Architecture Work

The architecture can be considered stabilized when:

1. The `src` implementation exists and matches documented imports.
2. CLI, dashboard, simulations, and tests use the same core pipeline.
3. Configuration has one source of truth.
4. Risk thresholds are not duplicated in multiple scripts.
5. UI and notification latency cannot block inference.
6. Performance metrics are measurable.
7. The risk policy includes persistence/hysteresis.
8. Every major module has unit and integration tests.
9. The repository name, package metadata, environment template, and UI consistently use SENTINEL AI.

