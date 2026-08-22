# SENTINEL AI — Testing and Validation Standard

**Status:** Required engineering test plan  
**Purpose:** Replace “script runs” confidence with measurable system validation

## 1. Current Test Baseline

The repository contains `COMPREHENSIVE_TEST.py` and `verify_system.py`, which are useful smoke/structure checks. However, they should not be treated as proof that the crowd-safety system has zero bugs or validated predictive accuracy.

A passing initialization test means a component can be created; it does not establish that it is accurate, robust, or safe.

## 2. Known Repository Issues to Fix Before Validation

### Missing implementation package

Historical note: this observation predates the current modular implementation. The current `main` branch contains `src/`; see [QUALIFICATION.md](QUALIFICATION.md) for the current test evidence.

### Simulation density-range defect

The complete simulation currently calculates phase densities using expressions such as:

```python
# labeled GREEN
density = 2.0 + np.random.uniform(1.0, 3.5)

# labeled YELLOW
density = 4.5 + np.random.uniform(4.0, 5.5)

# labeled RED
density = 6.5 + np.random.uniform(6.0, 8.0)

# labeled BLACK
density = 9.0 + np.random.uniform(8.0, 10.0)
```

Those result in ranges that do not match the labels. For example, the YELLOW expression produces approximately `8.5–10.0`, which is above the baseline BLACK threshold of 6.0.

The individual GREEN/YELLOW/BLACK scripts contain similar additive range construction.

Simulation ranges should instead be generated directly inside the target threshold band, for example:

```text
GREEN:  uniform(1.0, 3.8)
YELLOW: uniform(4.1, 4.8)
RED:    uniform(5.1, 5.8)
BLACK:  uniform(6.2, 8.0)
```

Exact ranges should include boundary tests and should not be random-only.

### Missing declared dashboard dependency

`app.py` imports Streamlit, while the current `requirements.txt` does not list Streamlit.

## 3. Test Pyramid

### L1 — Unit tests

Test pure logic:

- threshold boundaries
- state hysteresis
- density calculation
- prediction math
- route scoring
- config validation
- reason codes
- retry logic

### L2 — Module integration

Test component contracts:

- frame → detections
- detections → zones
- zones → density
- density history → prediction
- state → recommendations
- recommendations → notification queue

### L3 — Recorded video replay

Use fixed videos to ensure deterministic regressions.

### L4 — Scenario tests

Simulate station events and expected state progression.

### L5 — Performance tests

Measure FPS, P95 latency, memory, and dropped frames.

### L6 — Failure injection

Disconnect cameras, disable network, fail notification providers, and simulate model exceptions.

### L7 — End-to-end operational simulation

Replay a platform-change event from trigger through operator alert and recovery.

## 4. Required Scenario Catalogue

| ID | Scenario | Expected focus |
|---|---|---|
| SCN-001 | Empty platform | zero/near-zero false detections |
| SCN-002 | Normal passenger flow | stable GREEN |
| SCN-003 | Boundary at 4.0 | GREEN→YELLOW behavior |
| SCN-004 | Boundary at 5.0 | YELLOW→RED behavior |
| SCN-005 | Boundary at 6.0 | RED→BLACK behavior |
| SCN-006 | Noisy threshold oscillation | hysteresis prevents flapping |
| SCN-007 | Sudden platform change | early prediction path |
| SCN-008 | Stairwell bottleneck | hotspot/route warning |
| SCN-009 | Severe occlusion | degraded-confidence behavior |
| SCN-010 | Camera disconnect | fail-safe health state |
| SCN-011 | Frozen camera frame | stale-feed detection |
| SCN-012 | SMS provider timeout | inference remains responsive |
| SCN-013 | Internet outage | local monitoring remains available |
| SCN-014 | RED recovery | controlled de-escalation |
| SCN-015 | BLACK recovery | no instant downgrade |
| SCN-016 | Long-duration run | memory/resource stability |

## 5. Boundary Tests Are Mandatory

For thresholds `4.0`, `5.0`, and `6.0`, test values around each boundary:

```text
3.99 / 4.00 / 4.01
4.99 / 5.00 / 5.01
5.99 / 6.00 / 6.01
```

Also test persistence duration and recovery hysteresis around those boundaries.

## 6. Accuracy Metrics

### Person detection

- precision
- recall
- F1
- false detections per frame

### Counting/density

- people-count MAE
- people-count percentage error
- zone-density MAE
- hotspot localization error

### Prediction

- MAE at selected horizons
- RMSE
- direction/trend accuracy
- confidence calibration if available

### Risk classification

- per-state precision/recall
- critical-event recall
- false critical alerts per hour
- time-to-detect RED/BLACK

## 7. Performance Metrics

Every end-to-end scenario should also record:

```text
capture_fps
processed_fps
p50_capture_to_risk_ms
p95_capture_to_risk_ms
p99_capture_to_risk_ms
dropped_frames
cpu_percent
gpu_percent
ram_mb
```

## 8. Synthetic Data Rules

Synthetic simulations are useful for state-machine tests but must not be used as evidence of computer-vision accuracy.

Separate reports into:

- **logic simulation result**
- **recorded-real-video result**
- **live-camera result**

## 9. Determinism

Random simulations must accept a fixed seed:

```text
--seed 42
```

CI should use deterministic seeds. Interactive demos may use random seeds.

## 10. Notification Testing

Never send real emergency messages during routine automated tests.

Use:

- mock notification provider
- sandbox/test recipient
- explicit opt-in integration test

The current test suite includes a real-looking phone number in source text; replace direct personal contact details with configuration or a test placeholder.

## 11. CI Gate

A pull request should fail if any of these fail:

```text
lint/static checks
unit tests
config validation
test imports
risk boundary tests
simulation deterministic tests
secret scan
basic performance smoke test
```

Performance regression can initially be advisory, then become blocking once a stable benchmark host exists.

## 12. Release Acceptance

Before a demo or pilot release:

1. clean install succeeds
2. all declared entry points run
3. no missing package paths
4. at least one fixed recorded video passes
5. every risk state can be reproduced intentionally
6. boundary tests pass
7. camera disconnect is detected
8. network failure does not stop local inference
9. no secrets or personal contact details are committed
10. benchmark report is attached

## 13. Replace Overclaiming

Avoid automated messages such as:

```text
ALL TESTS PASSED! ZERO BUGS FOUND!
System working perfectly!
```

Use:

```text
All configured tests passed.
See test coverage, scenario coverage, and benchmark report for validated scope.
```

This is more accurate and more credible for a safety-oriented project.
