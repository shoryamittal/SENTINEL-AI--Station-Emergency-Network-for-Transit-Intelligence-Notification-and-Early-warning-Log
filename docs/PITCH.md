# SENTINEL AI pitch

**Positioning:** Explainable edge crowd intelligence with a failure-tolerant local warning and recovery architecture for railway environments.

## 30-second elevator pitch (67 words)

SENTINEL AI is an explainable, local-first crowd-risk prototype for railway environments. YOLO provides perception, then a 4×6 occupancy grid and L/A/R signals identify accumulation, redistribution, and local bottlenecks—not just headcount. Every incident is committed locally before remote work, so warnings and operator acknowledgement survive a WAN outage or restart. The Internet can fail. The warning chain cannot.

## 60-second emergency pitch (132 words)

Crowd danger is not simply many people. A station can be crowded but stable; risk rises when people accumulate, redistribute rapidly, or create a local bottleneck. SENTINEL uses YOLO as its perception layer, then maps people into a 4×6 grid and reasons with L—Load Anomaly, A—Accumulation, and R—Redistribution. It produces a scenario, severity, and recommended response.

The key engineering decision is local-first continuity. An incident is committed to SQLite before remote synchronization. If WAN connectivity fails, camera processing, risk updates, local warning, and operator acknowledgement continue; only sync is deferred. On recovery, SENTINEL sends the same immutable UUID, not a new emergency. That supports idempotent replay without duplicate remote events. Connectivity is a dependency for synchronization, not a dependency for safety.

## 3-minute full judge presentation (about 370 words; rehearse at 145–150 wpm)

### 0:00–0:20 — Problem

Railway crowd danger is not simply “many people.” A high headcount can be stable, while risk can emerge through accumulation, rapid redistribution, or a local bottleneck hidden inside the overall crowd. Communication infrastructure can also become unreliable during the exact period when a warning matters. SENTINEL addresses both crowd-state intelligence and warning continuity.

### 0:20–0:50 — How SENTINEL understands the crowd

This is the pipeline: camera or scenario video, YOLO detection, a 4×6 occupancy grid, an adaptive baseline, then L/A/R—Load Anomaly, Accumulation, and Redistribution. Those signals feed scenario classification, severity, and a recommended response. YOLO is our perception layer, not the complete solution. A crowded station is not automatically a dangerous station; changing crowd behavior is what matters.

### 0:50–1:15 — Intelligence demonstration

Here, the people count may be almost unchanged, but the spatial grid shows movement toward this hotspot. L/A/R explains why the scenario is **MASS REDISTRIBUTION** or **LOCAL BOTTLENECK**, and the dashboard gives the operator both a reason and a recommended response. Global headcount can hide local danger.

### 1:15–1:55 — Failure demonstration

Now I disable WAN connectivity. Notice what did not stop: the camera remains live, AI inference remains healthy, and crowd risk continues to update. The event has already been committed to SQLite, the local warning is delivered, remote status is `SYNC_PENDING`, and Events Lost remains zero. Only remote synchronization has been deferred. The Internet failed. The warning chain did not.

### 1:55–2:20 — Operator acknowledgement

The operator can acknowledge this local warning while remote sync is still pending. These are intentionally independent state machines: acknowledgement records local human action and does not need Internet connectivity.

### 2:20–2:40 — Recovery

When connectivity returns, status moves from OFFLINE through RECOVERY to ONLINE. SENTINEL synchronizes the exact immutable UUID that was created during the outage; it does not regenerate the emergency. Server-side idempotency makes a replay safe if a prior response was lost.

### 2:40–3:00 — Technical depth and close

Underneath this is a SQLite/WAL journal, immutable event identity, restart-safe local warning recovery, and `AUTH_BLOCKED` state separate from network state. We also qualify the ambiguous case where a server saves an event but its acknowledgement is lost: replaying the same UUID cannot create a duplicate.

SENTINEL is not just a people detector. It is an explainable local-first warning architecture designed to keep working when the environment becomes unreliable. Connectivity is a dependency for synchronization, not a dependency for safety. The Internet can fail. The warning chain cannot.

## Five lines to memorize

1. “High occupancy does not automatically mean high risk; changing crowd behavior is what matters.”
2. “YOLO is our perception layer; L/A/R provides the crowd-state reasoning.”
3. “An incident is committed locally before remote synchronization is attempted.”
4. “The same immutable event identity survives outage, restart and recovery.”
5. “Connectivity is a dependency for synchronization, not a dependency for safety. The Internet can fail. The warning chain cannot.”
