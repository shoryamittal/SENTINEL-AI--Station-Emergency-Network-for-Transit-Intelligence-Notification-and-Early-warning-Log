# Architecture

## System goals

SENTINEL separates real-time local safety work from remote delivery work. The design goal is simple: a WAN problem must not block local inference, durable incident recording, or local operator warning.

## Component and dependency map

```text
FrameSource → PersonDetector → SentinelRuntime → IncidentCandidate
                                               ↓
                                        IncidentJournal (SQLite/WAL)
                                         ↙             ↓              ↘
                         LocalAlertCenter       SyncWorker       ContinuityMetrics
                                                      ↓
                                      ConnectivityManager → SyncAdapter
                                                               ↓
                                            localhost qualification backend

Flask dashboard reads runtime health, journal records, alerts, and metrics.
```

Dependencies point from frame/risk processing toward the journal; sync depends on the journal, never the reverse. `SyncWorker` does not invoke live-alert generation.

## Safety Plane

`FrameSource` owns CAMERA or VIDEO input. `PersonDetector` performs YOLO detection. `SentinelRuntime` creates the 4×6 occupancy view, adaptive baseline, L/A/R signals, scenario, severity, `RiskSnapshot`, and an immutable `IncidentCandidate`. This plane does not make WAN calls.

## Continuity Plane

`IncidentJournal` commits incident payloads in SQLite/WAL before delivery work. `LocalAlertCenter` is a live presentation helper; SQLite provides durable history. `ConnectivityManager` independently classifies reachability. `SyncWorker` drains the durable outbox through a `SyncAdapter`, using retry/backoff and the existing event UUID. `ContinuityMetrics` combines runtime counters with durable status counts.

## Incident lifecycle

```text
new candidate → SQLite INSERT/COMMIT → local delivery → durable sync outbox
                                      │
                                      └─ same UUID for every retry/recovery

SYNC_PENDING → SYNCING → SYNCED
     └→ RETRYABLE_FAILURE → due retry ─┘
```

On terminated `SYNCING`, startup returns the record to `SYNC_PENDING`. `AUTH_BLOCKED` is separate: a 401/403 stops automatic retry until explicit requeue after credential refresh. Local status is independent from all sync statuses.

## Local operator lifecycle

```text
PERSISTED → LOCAL_DELIVERED → LOCAL_ACKNOWLEDGED
```

On startup, `PERSISTED` rows are recovered from the stored payload without a new UUID. If the incident still matches a fresh active runtime risk, it is presented locally; otherwise it becomes handled history without a new live/audible warning. `POST /alerts/<event_id>/ack` is idempotent for delivered alerts and changes no sync state.

## Failure isolation and identity

Network calls are background work. Optional SMS is best effort and skipped during historical recovery. An event UUID is SQLite’s primary key and the remote idempotency key. The qualification backend accepts a duplicate only when UUID and payload match (`ALREADY_ACCEPTED`); a changed payload produces `IDEMPOTENCY_CONFLICT` while retaining the canonical row.

## REALITY and SIMULATION

REALITY uses the configured camera; SIMULATION uses a bundled or uploaded video. Both instantiate the same `SentinelRuntime` and shared, serialized YOLO detector; only `FrameSource` changes. Switching protects single active capture ownership and avoids repeated model-weight loading.

## Production scaling path

```text
Camera group A → Edge SENTINEL A ┐
Camera group B → Edge SENTINEL B ├→ compact incident metadata → operator-controlled service
Camera group C → Edge SENTINEL C ┘
```

The logical scaling direction is horizontal edge nodes, not a claim that this prototype has been benchmarked at hundreds of cameras. Production would require camera-group orchestration, remote-service design, observability, retention, deployment automation, identity/security, and station-specific calibration.
