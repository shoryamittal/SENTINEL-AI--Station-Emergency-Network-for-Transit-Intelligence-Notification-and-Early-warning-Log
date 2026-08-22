# SENTINEL AI — Model and Risk Policy

**Status:** Engineering policy  
**Purpose:** Prevent inconsistent model claims and unstable emergency classification

## 1. Why This Policy Exists

The current project materials describe multiple AI approaches:

- YOLOv8 is used in the repository documentation and code entry points for person detection.
- The pitch material refers to CSRNet for highly congested scenes.
- The Streamlit dashboard text refers to LSTM-based prediction.
- Current simulation code initializes `DensityPredictor(..., use_lstm=False)`, indicating a non-LSTM path is currently intended for those simulations.

These are not necessarily incompatible, but the project must distinguish **implemented**, **experimental**, and **planned** models so the system and presentation remain technically honest.

## 2. Canonical Model Status

### Tier A — Baseline / expected MVP

**Person detection:** YOLOv8  
**Image processing:** OpenCV  
**Density mapping:** zone/grid occupancy calculated from detections  
**Prediction:** short rolling-history trend forecast  
**Risk:** deterministic policy using current density, predicted density/trend, confidence, and persistence

### Tier B — Optional optimization

- smaller/larger YOLO variants based on hardware
- GPU/TensorRT optimized inference
- frame sampling based on load
- model-specific calibration for station camera views

### Tier C — Advanced / not to be claimed as production-ready unless implemented and evaluated

- CSRNet or another density-regression model for severe occlusion
- LSTM/GRU/Transformer temporal forecasting
- multi-camera tracking/fusion
- learned bottleneck prediction
- physics-calibrated digital twin

## 3. Model Selection Policy

### YOLO profile selection

| Profile | Default model | Use case |
|---|---|---|
| `edge_low_power` | YOLOv8n | CPU/low-power edge demo |
| `edge_balanced` | YOLOv8s | stronger edge hardware |
| `gpu_station` | YOLOv8m or benchmark-selected model | dedicated GPU |
| `accuracy_test` | YOLOv8l/x if hardware permits | offline evaluation only |

Model choice must be based on measured accuracy and latency, not model size alone.

## 4. Density Definition

The project currently expresses thresholds in people per square metre (`ppl/m²`). A valid physical density estimate requires camera/zone calibration.

### Required rule

Do not call a value `ppl/m²` unless the zone area is grounded in a calibrated physical area.

If calibration is unavailable, expose the metric as one of the following instead:

- `people_per_zone`
- `relative_density_score`
- `occupancy_index`

This prevents false precision.

## 5. Current Risk Threshold Baseline

The repository repeatedly uses these boundaries:

| State | Baseline density rule |
|---|---:|
| GREEN | `< 4.0 ppl/m²` |
| YELLOW | `4.0–<5.0 ppl/m²` |
| RED | `5.0–<6.0 ppl/m²` |
| BLACK | `>= 6.0 ppl/m²` |

These are **project baseline thresholds**, not universal railway safety limits. They must be calibrated with station geometry, operating procedures, camera perspective, and validated data before real deployment.

## 6. Risk Engine Inputs

Risk must not depend on one density number alone.

Recommended inputs:

```text
current_max_density
current_avg_density
predicted_max_density
rate_of_change
prediction_confidence
detection_confidence
bottleneck_score
flow_direction_change
camera_health
persistence_time
```

For the MVP, unavailable inputs can be omitted, but their absence should reduce confidence rather than being silently assumed safe.

## 7. Persistence and Hysteresis

A single noisy frame must not trigger repeated state switching.

### Proposed default policy

These are engineering defaults to validate, not measured facts:

```text
Escalation confirmation:
YELLOW: condition persists >= 1 second
RED:    condition persists >= 2 seconds
BLACK:  condition persists >= 3 seconds OR a validated emergency trigger exists

De-escalation:
require the lower-state condition to persist >= 5 seconds
```

### Hysteresis example

If RED begins at 5.0 ppl/m², do not immediately fall back to YELLOW at 4.99. Use a lower recovery boundary, for example 4.7, after validation.

This reduces alert flapping.

## 8. Prediction-Assisted Escalation

Prediction should improve early warning without allowing a low-confidence model to create unjustified emergency alerts.

Recommended logic:

```text
if current_density >= BLACK_THRESHOLD:
    candidate = BLACK
elif predicted_density >= BLACK_THRESHOLD and prediction_confidence >= C_black:
    candidate = RED or BLACK-pending according to policy
elif current_density >= RED_THRESHOLD:
    candidate = RED
elif predicted_density >= RED_THRESHOLD and trend_is_rising:
    candidate = YELLOW/RED-pending
...
```

Use a `PENDING` internal state if useful; the public operator states may remain the four canonical colors.

## 9. Confidence Policy

Every risk result should expose confidence separately from severity.

Example:

```text
risk_state = RED
risk_confidence = 0.83
reason_codes = [
  "DENSITY_ABOVE_RED",
  "RISING_TREND",
  "PREDICTION_ABOVE_RED"
]
```

Never combine confidence and severity into one opaque score.

## 10. Recommended Action Policy

### GREEN

- continue monitoring
- update dashboard
- store aggregate metrics

### YELLOW

- highlight affected zone
- precompute alternate routes
- prepare staff positioning recommendation
- increase monitoring frequency if adaptive sampling is used

### RED

- send urgent operator/RPF advisory
- display bottleneck and recommended diversion
- request acknowledgement
- prepare announcement and access-control recommendations

### BLACK

- send critical alert through all available approved local channels
- show emergency response checklist
- recommend inflow restriction / route isolation where appropriate
- preserve human authorization for safety-critical commands unless railway policy explicitly pre-authorizes automation

## 11. False Positive / False Negative Priority

For a safety system, both errors matter:

- False negatives can miss dangerous crowd development.
- False positives can create alert fatigue and unnecessary passenger movement.

Testing must therefore report both recall and false-alert rate. Optimizing only for raw detection accuracy is insufficient.

## 12. Model Upgrade Gate

No new model should replace the baseline because it “looks better” in a demo.

A model upgrade requires:

1. fixed evaluation dataset
2. reproducible environment
3. people-count error comparison
4. density/risk classification comparison
5. P95/P99 latency comparison
6. hardware utilization comparison
7. failure-case evaluation
8. rollback path

## 13. Model Versioning

Every runtime alert or saved evaluation should be attributable to:

```text
model_family
yolo_weights_name
weights_hash
confidence_threshold
input_resolution
risk_policy_version
prediction_model_version
configuration_profile
```

## 14. Presentation Language

Use the following language in demos and reports unless implementation changes:

> SENTINEL AI uses YOLOv8-based person detection and zone-based density estimation as its current computer-vision baseline. Short-horizon prediction and risk classification are applied to support proactive alerts. Advanced dense-crowd models and learned temporal forecasting are planned/experimental capabilities unless validated in the active build.

This keeps the synopsis, pitch, repository, and implementation aligned.

