# SENTINEL AI — Configuration Profiles

**Status:** Configuration policy  
**Purpose:** Eliminate duplicated constants and make deployment reproducible

## 1. Problem

The current repository defines configuration in multiple places:

- `main.py` has built-in defaults.
- `app.py` creates another configuration dictionary.
- `.env.example` contains overlapping values.
- the README refers to `src/config/config.ini`.
- simulation scripts hard-code model names, thresholds, FPS, history length, and prediction horizon.

This makes it easy for the CLI, dashboard, test suite, and simulations to behave differently.

## 2. Rule: One Configuration Source of Truth

Recommended precedence:

```text
1. hard-coded safe defaults in config schema
2. selected profile file
3. station-specific config file
4. environment variables
5. command-line overrides
```

The final resolved configuration must be printed/logged at startup with secrets redacted.

## 3. Canonical Keys

```text
project.name
station.name
runtime.mode

camera.source
camera.target_fps
camera.width
camera.height
camera.reconnect_seconds

vision.model
vision.confidence_threshold
vision.input_size
vision.device
vision.frame_skip

mapping.grid_rows
mapping.grid_cols
mapping.zone_area_m2
mapping.calibration_file

prediction.enabled
prediction.method
prediction.history_length
prediction.horizon_seconds
prediction.min_confidence

risk.green_threshold
risk.yellow_threshold
risk.red_threshold
risk.persistence.yellow_seconds
risk.persistence.red_seconds
risk.persistence.black_seconds
risk.recovery_seconds

simulation.enabled
simulation.update_hz

notifications.enabled
notifications.sms_enabled
notifications.email_enabled
notifications.provider

dashboard.enabled
logging.level
logging.json
logging.file
```

## 4. Recommended Profiles

### `edge_low_power`

Use for low-power demo hardware.

```yaml
runtime:
  mode: edge
camera:
  target_fps: 15
  width: 640
  height: 360
vision:
  model: yolov8n.pt
  confidence_threshold: 0.5
  frame_skip: 1
prediction:
  enabled: true
  method: trend
simulation:
  update_hz: 1
```

### `edge_balanced`

```yaml
runtime:
  mode: edge
camera:
  target_fps: 20
  width: 640
  height: 480
vision:
  model: yolov8s.pt
  confidence_threshold: 0.5
prediction:
  enabled: true
  method: trend
simulation:
  update_hz: 2
```

### `gpu_station`

```yaml
runtime:
  mode: station
camera:
  target_fps: 30
  width: 1280
  height: 720
vision:
  model: yolov8m.pt
  confidence_threshold: 0.5
prediction:
  enabled: true
simulation:
  update_hz: 2
```

### `simulation`

```yaml
runtime:
  mode: simulation
camera:
  source: synthetic
  target_fps: 10
notifications:
  enabled: false
```

The model/profile values above are recommended starting points and must be benchmarked.

## 5. Canonical Risk Baseline

Until calibration changes it, keep the repository baseline in one place:

```yaml
risk:
  green_threshold: 4.0
  yellow_threshold: 5.0
  red_threshold: 6.0
```

Interpretation:

```text
GREEN  < 4
YELLOW >= 4 and < 5
RED    >= 5 and < 6
BLACK  >= 6
```

Do not redefine these numbers in each simulation file.

## 6. Prediction Units

The current code uses both minute-based parameters and project messaging around a 90-second intervention concept. To prevent ambiguity, use seconds in the canonical config:

```yaml
prediction:
  horizon_seconds: 300
```

The UI can display minutes, but internal configuration should have one unit.

## 7. Secret Handling

The configuration system must never place secrets in normal profile files.

Use environment variables for:

```text
FAST2SMS_API_KEY
SMTP_PASSWORD
RTSP_USERNAME
RTSP_PASSWORD
DATABASE_URL
JWT_SECRET
```

The `.env.example` file should contain placeholders only.

## 8. Naming Cleanup

The current `.env.example` header says `Suraksha Kavach AI`, while other files use `PREEMPT AI` or `SENTINEL AI`.

Replace project branding in configuration and metadata with:

```text
SENTINEL AI
```

If `PreEmpt` remains as a product concept/tagline, document it separately rather than using it as an interchangeable package name.

## 9. Validation Rules

The application should refuse invalid configuration before starting monitoring.

Minimum checks:

```text
0 < confidence_threshold <= 1
fps > 0
grid_rows > 0
grid_cols > 0
zone_area_m2 > 0 when physical density is enabled
green_threshold < yellow_threshold < red_threshold
prediction_horizon_seconds > 0
camera source is reachable or a valid file
```

## 10. Configuration Fingerprint

Every run should compute a non-secret configuration fingerprint and include it in logs/evaluation outputs.

Example:

```text
config_profile=edge_balanced
config_hash=abc123...
model=yolov8s.pt
risk_policy=v1
```

This makes results reproducible.

## 11. Environment Separation

Use clearly separated modes:

### Development

- verbose logging
- local webcam/video
- notifications disabled by default
- synthetic scenarios allowed

### Demo

- stable known video/camera
- deterministic scenario inputs where possible
- test phone/email only

### Production-like pilot

- real camera calibration
- secrets injected at runtime
- audit logs enabled
- notification retries enabled
- operator authentication enabled

## 12. Dependency Configuration

`requirements.txt` currently uses broad minimum versions. For reproducible demos/deployments, keep:

- `requirements.txt` as the human-maintained dependency intent, and
- a generated lock/frozen file for a known-good environment.

Also ensure runtime dependencies match actual entry points; for example, if `app.py` remains Streamlit-based, Streamlit must be part of the declared runtime dependencies.

## 13. Migration Plan

1. Create a typed/config-validated loader.
2. Move all thresholds and model names out of simulations.
3. Make `main.py` and `app.py` use the same loader.
4. Make tests load a dedicated test profile.
5. Remove dead/duplicate config paths.
6. Add a startup configuration validation test.

