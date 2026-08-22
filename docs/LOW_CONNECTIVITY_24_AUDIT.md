# 24-anomaly low-connectivity / error-handling audit

**Audited source:** `dff5f31f1f7be2ba7d45bc4c53ae6c3305c6105c` on `main`  
**Essential task:** detect crowd-risk locally and deliver a meaningful local operator warning without WAN: camera/video → YOLO → 4×6 occupancy → adaptive L/A/R → scenario/severity → `IncidentCandidate` → SQLite commit → local warning → local acknowledgement.  
**Principle:** Connectivity is a dependency for synchronization, not a dependency for safety.

Remote synchronization is a secondary continuity responsibility. “Data safe” below means the local incident is durable; “duplicate safe” means the same immutable event identity cannot create a second canonical event through the documented path.

| # | Failure / actual current behavior | Essential task? | Data safe? | Duplicate safe? | Evidence | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 01 | Total WAN loss: runtime, SQLite and local delivery continue; sync is pending. | YES | YES | YES | `test_full_round2_offline_restart_recovery` | CLEAR |
| 02 | Weak/degraded connectivity: state machine distinguishes ONLINE, DEGRADED, OFFLINE and RECOVERY with thresholds. | YES | YES | YES | `test_connectivity.py` | CLEAR |
| 03 | Flapping: hysteresis and one-event sync attempts limit churn; no dedicated rapid-flap/queue proof. | YES | YES | YES | connectivity + sync tests | PARTIAL |
| 04 | Outage at incident creation: durable sink commits before presentation/sync; connectivity is read only. | YES | YES | YES | `test_durable_sink_accepts_before_candidate_is_queued`, Round 2 | CLEAR |
| 05 | Persisted before local presentation: restart reconstructs same payload/UUID then handles local delivery. | YES | YES | YES | `test_crash_after_persist_recovers_same_row_and_uuid` | CLEAR |
| 06 | Outage after warning: `LOCAL_DELIVERED + SYNC_PENDING` remains valid and durable. | YES | YES | YES | Round 2, journal lifecycle tests | CLEAR |
| 07 | Offline local ACK: ACK writes only `local_status`; it neither calls sync nor changes sync state. | YES | YES | YES | ACK independence tests | CLEAR |
| 08 | Restart while offline: SQLite record/UUID and pending sync state survive a new journal instance. | YES | YES | YES | persistence + Round 2 tests | CLEAR |
| 09 | Restart during `SYNCING`: initialization requeues the same row as `SYNC_PENDING`. | YES | YES | YES | `test_restart_requeues_event_stranded_in_syncing` | CLEAR |
| 10 | Remote server down with Internet available: adapter failure becomes durable retry; local path is unaffected. | YES | YES | YES | `test_http_connection_failure_is_retryable_and_keeps_local_event` | CLEAR |
| 11 | Timeout before remote commit: retry/backoff preserves same payload and UUID without local blocking. | YES | YES | YES | sync retry tests | CLEAR |
| 12 | Server commit, lost response: replaying same UUID yields `ALREADY_ACCEPTED`; remote count remains one. | YES | YES | YES | `test_timeout_after_server_success_retries_same_event_id_and_syncs` | CLEAR |
| 13 | Duplicate retry: local primary key and backend idempotency retain one canonical event. | YES | YES | YES | persistence/recovery/qualification tests | CLEAR |
| 14 | Same UUID, changed payload: backend returns `IDEMPOTENCY_CONFLICT`, preserving canonical payload. | YES | YES | YES | qualification conflict tests | CLEAR |
| 15 | HTTP 401/403: connectivity can remain ONLINE while sync becomes `AUTH_BLOCKED`; blind retry stops. | YES | YES | YES | `test_auth_expiry_blocks_worker_and_explicit_reauth_resumes_same_event` | CLEAR |
| 16 | Credential recovery: explicit requeue sends the original UUID; no regeneration. | YES | YES | YES | auth-expiry/resume test | CLEAR |
| 17 | Transient SQLite write failure: retained candidate retries with the same UUID; no delivery before durable acceptance. It is in memory until a retry commits. | DEGRADED | YES after retry | YES | `test_canonical_sink_retries_sqlite_failure_without_uuid_or_metric_inflation` | PARTIAL |
| 18 | Crash after SQLite commit: one row and same UUID recover from `PERSISTED` to local handling. | YES after restart | YES | YES | local-alert restart tests | CLEAR |
| 19 | Camera becomes stale during outage: dashboard marks risk stale/unknown; history remains durable. WAN+stale is not directly combined. | DEGRADED | YES | YES | camera freshness/runtime tests | PARTIAL |
| 20 | Inference failure during outage: runtime reports degraded/recovery and does not corrupt journal; combined WAN case is not direct. | DEGRADED | YES | YES | runtime supervision tests | PARTIAL |
| 21 | Browser refresh offline: local Flask endpoint and SQLite history remain available; event-ID browser dedupe is template-tested. | YES | YES | YES | local-alert/browser-dedupe tests | PARTIAL |
| 22 | REALITY↔SIMULATION with unsynced event: switching replaces only runtime/frame source; journal is untouched, but no combined test exists. | YES | YES | YES | operating-mode + journal tests | PARTIAL |
| 23 | App recovers before WAN: offline restart and later recovery are separately proven; local ACK across whole chain is not one test. | YES | YES | YES | Round 2 + local ACK tests | PARTIAL |
| 24 | Worst-case chain: all primitives are proven separately, but no single test combines ACK, offline restart, lost response, and final sync. | YES | YES | YES | Round 2, local recovery, qualification tests | PARTIAL |

## Detailed notes for PARTIAL cases

**03 — Connectivity flapping.** The `ConnectivityManager` has failure/success hysteresis and `SyncWorker` performs at most one pending event per pass with durable retry state. This is sufficient for the essential task because the safety plane does not wait for it. A dedicated deterministic rapid-flap test would improve confidence in queue/backoff behavior, but is not required to prove local warning continuity.

**17 — Transient SQLite write failure.** The runtime retains the immutable candidate and retries the sink; it does not enqueue/deliver it as durable before the write succeeds. That directly prevents unsafe advancement, but the candidate is memory-resident during the retry window. A process loss before a successful retry can lose that uncommitted candidate, so this is a meaningful score-improvement opportunity rather than a claim of zero loss under simultaneous storage and process failure.

**19 — Camera stale during outage.** Input/risk health is independently surfaced as stale or unknown, so the UI does not present stale risk as current. Durable incidents remain in SQLite. The gap is only absence of one test that forces WAN loss and camera staleness simultaneously.

**20 — Inference exception during outage.** Runtime tests establish transient recovery and persistent degraded reporting, while continuity records are independent SQLite rows. A simultaneous WAN/inference fault is not directly qualified; the current isolation is architectural and separately tested.

**21 — Browser refresh while offline.** Dashboard status reads the local process and durable journal; acknowledgement is durable, and sound dedupe keys browser storage by immutable event ID. The proof is unit/template-level rather than a real-browser offline integration test.

**22 — Mode switch with unsynced event.** The switch replaces `SentinelRuntime`/`FrameSource`; it does not reset `IncidentJournal`, UUIDs, local status, or sync status. Mode tests and journal tests prove the pieces, but not this exact combined sequence.

**23–24 — Composite chains.** Offline restart, local recovery, acknowledgement independence, timeout-after-server-success, and idempotent final sync are all separately deterministic. The audit does not claim a single integrated test where every action occurs in one process chain.

## Findings and decision gate

- **P0 — Competition blocker:** NONE.
- **P1 — Strong score improvement:** a single deterministic master test for anomaly 24 would turn several PARTIAL rows into direct evidence. A bounded durable handoff strategy for a candidate during transient SQLite failure would close the simultaneous write-failure/process-loss window in anomaly 17. Neither is needed to demonstrate the primary low-connectivity task and neither is added during this audit-only pass.
- **P2 — Production hardening:** dedicated rapid connectivity-flap and real-browser offline-refresh qualification; storage-full/corruption and process-host failure procedures.
- **P3 — Future research/product work:** calibrated people/m², directional tracker-based counterflow, station-specific datasets/calibration, production identity/TLS/backend operations.

## Primary problem-statement assessment

**Weak connectivity:** YES. Hysteresis distinguishes degraded state while the local safety plane remains independent.  
**Lost connectivity:** YES. Camera/video processing, local inference, SQLite persistence, local warning, and local acknowledgement continue; remote synchronization is deferred.  
**Synchronous network call in the Safety Plane:** No. The incident sink records a lock-protected connectivity snapshot, but makes no network call; `SyncWorker` is a separate background worker.

The audit recommendation is to keep production code frozen. The documented PARTIAL findings are proof-composition opportunities, not violations of the primary low-connectivity requirement.
