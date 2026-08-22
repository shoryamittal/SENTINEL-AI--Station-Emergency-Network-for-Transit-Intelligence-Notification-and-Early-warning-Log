# Failure matrix

| Failure | SENTINEL behavior | Remains available | Evidence | Production implication |
| --- | --- | --- | --- | --- |
| WAN unavailable | retains durable outbox | local AI, SQLite, warning | Round 2 continuity tests | use controlled health endpoint |
| Degraded connectivity | state transitions with hysteresis | safety plane | connectivity tests | tune for site conditions |
| Server timeout | retryable failure/backoff | local event/history | qualification sync tests | monitor retry queues |
| Server stored, response lost | retries same UUID | one canonical remote row | timeout-after-success test | backend idempotency required |
| Process stops while `SYNCING` | startup requeues same row | SQLite history | recovery tests | preserve idempotency key |
| Stops after `PERSISTED` | startup finishes local handling | same UUID, one row | local-alert restart tests | no notifier replay for history |
| 401/403 | marks `AUTH_BLOCKED`, stops blind retry | local safety plane | auth-blocking tests | credential rotation/control needed |
| Transient SQLite failure | runtime retains candidate for retry | no new UUID | incident consumer tests | monitor durable-write failures |
| Inference exception | supervised runtime recovers | prior durable records | runtime tests | alert/observe degradation |
| Persistent inference degradation | reports degraded/stale, not healthy | continuity history | runtime/stale tests | operational escalation policy |
| Camera stale | risk is shown stale/unknown | dashboard/status | camera freshness tests | camera health monitoring |
| Browser refresh | event-ID browser dedupe avoids repeat cue | durable history | local-alert tests | browser storage is presentation-only |
| Mode switch | one source owner, same pipeline | journal/lifecycle | operating-mode tests | manage camera groups at scale |
| Repeated event replay | same ID remains one event | canonical payload | persistence/idempotency tests | immutable ID contract |
| Same UUID, altered payload | qualification conflict | canonical remote payload | qualification tests | reject conflicting writers |
