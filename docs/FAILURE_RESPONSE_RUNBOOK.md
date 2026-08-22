# SENTINEL AI — Failure Response Runbook

**Status:** Operational engineering runbook  
**Purpose:** Define deterministic behavior when sensors, models, networks, or integrations fail

## 1. Core Safety Principle

**Unknown is not safe.**

If SENTINEL AI loses trustworthy input, it must not silently convert missing data into a GREEN state.

Examples:

- missing camera frames are not zero crowd
- stale frames are not live observations
- prediction failure is not a prediction of low risk
- SMS failure is not successful alert delivery

## 2. System Health States

Keep system health separate from crowd risk.

Recommended health states:

```text
HEALTHY
DEGRADED
UNAVAILABLE
```

The dashboard should show both:

```text
Crowd risk: RED
System health: DEGRADED — Camera 3 unavailable
```

## 3. Camera Offline

### Detection

Trigger when:

- frame reads fail repeatedly
- RTSP connection closes
- no frame timestamp advances within timeout

### Response

1. mark camera `UNAVAILABLE`
2. stop treating its last frame as current
3. use overlapping camera if available
4. preserve last valid metrics with a clear `STALE` label
5. notify operator
6. retry connection with backoff
7. record outage duration

### Never do

- set people count to zero
- set crowd state to GREEN solely because input disappeared

## 4. Frozen Camera / Stale Feed

A camera can remain connected while showing the same image.

Detection methods may include:

- unchanged timestamps
- repeated frame hashes
- near-zero frame difference for an implausible duration

Response:

```text
camera_health = DEGRADED
reason = STALE_VIDEO
operator warning = visible
```

## 5. Inference Failure

Examples:

- model exception
- CUDA error
- corrupted weights
- out-of-memory

Response:

1. catch the exception outside the main process supervisor
2. mark inference degraded
3. attempt controlled model reload once
4. if GPU path fails and a validated CPU fallback exists, switch profile
5. otherwise stop producing new AI risk estimates
6. continue camera health monitoring
7. alert operator

Do not repeatedly crash/restart in a tight loop.

## 6. Low FPS / Excessive Latency

Trigger if:

```text
processed FPS < configured minimum
OR
P95 frame age > configured threshold
```

Response order:

1. stop unnecessary visualization work
2. process newest frame only
3. increase frame skip
4. reduce inference resolution if allowed by active profile
5. reduce simulation frequency
6. warn operator if latency remains unsafe

Record every automatic degradation step.

## 7. Prediction Unavailable

If prediction fails but current detection/density is healthy:

- continue current-state monitoring
- mark prediction unavailable
- reduce risk confidence where prediction is normally required
- do not suppress current density threshold alerts

Dashboard example:

```text
Risk: YELLOW
Prediction: unavailable
Current density monitoring: healthy
```

## 8. Flow Simulation Unavailable

If digital-twin/route simulation fails:

- continue monitoring and classification
- display previously approved static emergency route guidance if available
- do not invent a route
- flag `ROUTE_RECOMMENDATION_UNAVAILABLE`

## 9. Internet Failure

The project intends edge-native resilience. Therefore:

### Must continue locally

- camera ingestion
- detection
- density calculation
- risk classification
- local dashboard
- local audible/visual alert where deployed

### May become unavailable

- cloud analytics
- external SMS
- email
- remote dashboard

Store unsent non-expired events for later audit, but do not flood recipients with stale alerts after connectivity returns.

## 10. SMS / Email Provider Failure

Notification dispatch must be asynchronous.

Response:

```text
attempt 1
short retry
attempt 2
provider marked degraded
fallback channel attempted if configured
operator dashboard shows delivery failure
```

For critical alerts, local station channels must not depend only on public internet delivery.

## 11. Database / Historical Store Failure

If database storage fails:

- continue real-time monitoring if pipeline does not require database access
- buffer a bounded amount of aggregate state locally
- drop old analytics before allowing memory exhaustion
- clearly report logging degradation

## 12. Conflicting Sensors

If two cameras/sensors disagree materially:

1. do not average blindly
2. flag `SENSOR_DISAGREEMENT`
3. prefer calibrated/healthy sources according to configured trust policy
4. lower confidence
5. surface both values to the operator if safety-relevant

## 13. Low Confidence

If detection/prediction confidence falls below policy minimum:

- preserve severity evidence that is directly observed
- reduce confidence indicator
- request human attention
- avoid highly specific route recommendations based only on weak data

## 14. RED / BLACK Operational Response

### RED

- publish zone and reason codes
- send urgent advisory
- display recommended diversion/staff placement
- request operator acknowledgement
- increase monitoring frequency if supported

### BLACK

- publish critical alert through all approved available channels
- show emergency-response checklist
- recommend inflow restriction / localized route control
- keep continuous state updates visible
- require explicit operator action for consequential control commands unless a formally approved SOP authorizes automation

## 15. De-Escalation

Do not downgrade immediately after one improved frame.

Recommended behavior:

```text
BLACK → RED only after validated recovery interval
RED → YELLOW only after validated recovery interval
YELLOW → GREEN only after validated recovery interval
```

The exact time is configured by risk policy.

## 16. Process Crash

Use a supervisor in deployment.

On restart:

1. initialize logging
2. validate configuration
3. validate model availability
4. reconnect camera
5. warm model
6. show `STARTING` health
7. only mark `HEALTHY` after a valid processed frame

## 17. Disk Full

Logs/video must never exhaust the station device.

Use:

- rotating logs
- bounded event storage
- configurable retention
- low-disk alert

Raw video storage should be opt-in and governed by privacy policy.

## 18. Incident Record

For every RED/BLACK incident or major system failure, preserve:

```text
incident_id
timestamps
camera/system health
risk state history
reason codes
aggregate density values
prediction output
recommended actions
notification outcomes
operator acknowledgements/overrides
software version
model version
configuration fingerprint
```

Do not include unnecessary personal identity data.

## 19. Recovery Checklist

After a failure:

- confirm live camera timestamps
- confirm inference latency
- confirm model version
- confirm risk engine output
- confirm dashboard updates
- confirm alert worker health
- confirm no notification backlog will send stale emergency messages
- document root cause

