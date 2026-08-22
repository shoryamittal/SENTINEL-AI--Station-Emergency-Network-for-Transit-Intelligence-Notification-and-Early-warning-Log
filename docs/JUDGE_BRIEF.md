# SENTINEL AI — judge brief

## Problem

Station crowd risk is dynamic, and connectivity can be weakest when a warning matters. A raw headcount cannot distinguish stable crowding from accumulation, a local bottleneck, or rapid redistribution.

## Our approach

SENTINEL runs real YOLO detection on camera or video input, maps people into a 4×6 grid, calculates explainable L/A/R dynamics, classifies scenario/severity, and produces an operator recommendation. It commits incidents locally before any remote work.

## Technical differentiators

1. Explainable L/A/R signals rather than headcount only.
2. A local Safety Plane without WAN dependency.
3. SQLite/WAL commit before delivery and sync.
4. One immutable UUID across retries, restart, and recovery.
5. Server-side idempotent replay and conflict protection.
6. `AUTH_BLOCKED` is distinct from connectivity.
7. Restart-safe local warning plus durable acknowledgement.
8. REALITY/SIMULATION share one intelligence pipeline.
9. Stale input is not displayed as fresh risk.

## Reliability proof

The project’s deterministic tests cover offline continuity, restart recovery, timeout-after-server-success, authorization blocking, idempotent replay, stale output, and mode switching. A `PERSISTED` event survives a crash, retains its UUID, becomes locally deliverable after restart, and never duplicates the incident.

## Current qualification

At the current documented baseline, the repository reports 96 passing tests and a passing canonical verifier. The localhost qualification backend proves a protocol contract; it is not production cloud infrastructure.

## Scalability path and limitations

The intended direction is horizontal edge nodes per camera group sending compact incident metadata to an operator-controlled central service. The prototype does not claim calibrated people/m², certified thresholds, guaranteed prevention, production IAM/backend/TLS, or a multi-hundred-camera benchmark.
