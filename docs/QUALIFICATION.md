# Qualification evidence

Current documented baseline: `b85c7bf` on `main`, **96 tests passed**, and `python verify_system.py` reports PASS. The suite is deterministic and local; it does not claim unmeasured performance benchmarks.

| Qualification group | What it establishes |
| --- | --- |
| Compilation/import integrity | canonical modules import and offline smoke path runs |
| Camera and frame freshness | capture ownership, freshness, stale-input behavior |
| Adaptive and spatial intelligence | 4×6 mapping, L/A/R, scenarios, recommendations |
| Runtime supervision | transient/persistent inference-failure behavior |
| Persistence and local delivery | SQLite/WAL commit boundary and id stability |
| Restart-safe local recovery | `PERSISTED` recovery, no duplicate row, durable ACK |
| Connectivity and sync retry | offline handling, backoff, recovery, interrupted `SYNCING` |
| Server idempotency | duplicate, conflict, and response-loss outcomes |
| Authorization | `AUTH_BLOCKED` separate from WAN state |
| REALITY/SIMULATION | same runtime path and capture-switch safety |
| Round 2 end-to-end | durable local-first continuity across failure/recovery |

The qualification backend is localhost/reference infrastructure used to test the protocol contract. It is not evidence of a production remote service or railway certification.
