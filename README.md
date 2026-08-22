# SENTINEL AI - Platform Change Event Detection System

A real-time crowd monitoring and management system for platform change events at railway stations. Built with free, state-of-the-art AI tools for high-accuracy crowd density detection and situation assessment.

## Round 2: Continuity, Persistence & Recovery

**Core principle: connectivity is a dependency for synchronization, not for safety.**

Round 2 adds a continuity plane on top of Person 1's offline `SentinelRuntime`
(`src/contracts.py`, `src/camera.py`, `src/detector.py`, `src/occupancy.py`,
`src/baseline.py`, `src/adaptive_risk.py`, `src/scenario.py`, `src/health.py`,
`src/runtime.py`). It does not change how risk is computed; it guarantees that
frame processing, risk scoring, local persistence, and local alerting all keep
running when Internet/WAN connectivity is weak or gone.

```
SentinelRuntime (Person 1, offline-only)
        |
        v
incident consumer thread
        |
        v
IncidentJournal (SQLite, WAL) -- PERSISTED before anything remote
        |
        +--> LocalAlertCenter (zero-network, always fires)
        |
        v
SYNC_PENDING --> SyncWorker (own thread) --> SyncAdapter (mock by default)

ConnectivityManager runs on its own background thread; a slow or hung
remote check can never block SentinelRuntime, the sync worker, or the
Flask dashboard.
```

### Run it

```bash
python deploy.py
```

Opens the dashboard at `http://localhost:5000` (or `$PORT`). It uses the
real webcam by default (`CAMERA_SOURCE=0`); see `.env.example` for every
knob (database path, connectivity check interval, mock sync-adapter fault
mode, optional Fast2SMS).

### What's new

| Module | Responsibility |
|---|---|
| `src/persistence.py` | `IncidentJournal` -- SQLite/WAL incident journal, keyed by Person 1's own `event_id`. Never regenerates it. Commits locally before any remote contact. |
| `src/connectivity.py` | `ConnectivityManager` -- background ONLINE/DEGRADED/OFFLINE/RECOVERY state machine with hysteresis, injectable check function, demo-only manual override. |
| `src/sync.py` | `SyncAdapter`/`MockSyncAdapter` + `SyncWorker` -- idempotent store-and-forward with bounded exponential backoff. `ALREADY_ACCEPTED` counts as success. |
| `src/alerts.py` | `LocalAlertCenter` -- guaranteed, zero-network local alert feed; optional Fast2SMS is best-effort and cannot block or fail the local alert. |
| `src/metrics.py` | `ContinuityMetrics` -- runtime-derived counters for the dashboard (generated/persisted/pending/synced/failed/lost, outage duration, etc). Nothing hard-coded. |
| `deploy.py` | Wires all of the above around `SentinelRuntime` and serves the operator dashboard. |

### Event lifecycle

```
CREATED -> PERSISTED -> LOCAL_DELIVERED -> SYNC_PENDING -> SYNCING -> SYNCED
                                                  \-> RETRYABLE_FAILURE (backoff, retried)
                                                  \-> PERMANENT_FAILURE (stays visible, never deleted)
```

An event that is `SYNC_PENDING` is **not** "lost" -- it is safely on disk.
`events_lost = events_generated - events_successfully_persisted`.

### Stale alert protection

A historical incident (e.g. a RED alert generated while offline, synced
minutes later once connectivity returns) is replicated to remote history by
`SyncWorker`. That worker **never** triggers a live emergency notification --
live alerting happens exactly once, at generation time, in `deploy.py`'s
incident consumer. See `tests/test_recovery.py::test_stale_historical_incident_syncs_without_a_new_live_alert`.

### Offline / recovery qualification loop

```bash
python deploy.py
# dashboard shows CAMERA live, frame_id advancing, connectivity ONLINE

# disable Wi-Fi, or POST /debug/connectivity?state=OFFLINE (demo-only override)
# -> connectivity badge turns OFFLINE (blue/neutral, never red/black)
# -> frame_id keeps advancing, risk severity keeps updating
# -> a RED/BLACK incident is persisted (SQLite) and alerted locally immediately

# kill the process, restart `python deploy.py`
# -> the event is still in data/sentinel.db

# reconnect / POST /debug/connectivity (no state, to clear the override)
# -> connectivity -> RECOVERY -> ONLINE
# -> the pending event syncs with its original event_id -> SYNCED
```

### Tests

```bash
pytest -q tests
```

`tests/test_persistence.py`, `tests/test_connectivity.py`,
`tests/test_offline_continuity.py`, and `tests/test_recovery.py` require no
Internet, no webcam, and no real remote backend -- connectivity checks and
the remote endpoint are both injected/mocked.

## Architecture Overview

The system implements the complete PREEMPT AI flowchart for platform change events:

```
┌─────────────────────────────────────────────────────────────────┐
│                  PREEMPT AI FLOWCHART                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐                                             │
│  │ Edge Device   │ ← Camera Feed Input                        │
│  │ (Camera)      │                                             │
│  └──────┬───────┘                                             │
│         │                                                      │
│         ▼                                                      │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ 1. Crowd Density Heatmap Generation                      │  │
│  │    • YOLOv8 Object Detection (Free AI Tool)              │  │
│  │    • Real-time People Counting                          │  │
│  │    • Heatmap Visualization                               │  │
│  └──────┬───────────────────────────────────────────────────┘  │
│         │                                                      │
│         ▼                                                      │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ 2. Density & Occupancy Mapping                           │  │
│  │    • Grid-based Zone Analysis                            │  │
│  │    • People per Square Meter Calculation                 │  │
│  │    • Hot Zone Detection                                   │  │
│  └──────┬───────────────────────────────────────────────────┘  │
│         │                                                      │
│         ▼                                                      │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ 3. Flow Simulation (Digital Twin)                         │  │
│  │    • Crowd Movement Modeling                              │  │
│  │    • Bottleneck Identification                           │  │
│  │    • Shortest Path Calculation                           │  │
│  └──────┬───────────────────────────────────────────────────┘  │
│         │                                                      │
│         ▼                                                      │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ 4. Future Density Prediction                              │  │
│  │    • Temporal Trend Analysis                              │  │
│  │    • Anomaly Detection                                    │  │
│  │    • Density Forecasting                                  │  │
│  └──────┬───────────────────────────────────────────────────┘  │
│         │                                                      │
│         ▼                                                      │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ 5. Situation Assessment & Classification                  │  │
│  │    • State Machine: GREEN → YELLOW → RED → BLACK        │  │
│  │    • Confidence Scoring                                   │  │
│  │    • Risk Level Assessment                                │  │
│  └──────┬───────────────────────────────────────────────────┘  │
│         │                                                      │
│         ▼                                                      │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ 6. Action Execution                                       │  │
│  │    • Automated Actions                                    │  │
│  │    • Manual Approval Queue                                │  │
│  │    • Notification System                                  │  │
│  └──────┬───────────────────────────────────────────────────┘  │
│         │                                                      │
│         ▼                                                      │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ 7. Continuous Monitoring Loop                              │  │
│  │    • 90-Second Intervention Window                        │  │
│  │    • Real-time Visualization                              │  │
│  │    • Performance Metrics                                  │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Features

- **Real-time Crowd Detection**: Uses YOLOv8 (free, open-source) for accurate people counting
- **Heatmap Generation**: Visual density representation with color-coded zones
- **Grid-based Analysis**: 4x6 grid mapping for precise zone identification
- **Flow Simulation**: Digital twin for crowd movement prediction
- **Future Density Prediction**: Trend analysis and anomaly detection
- **Situation Classification**: GREEN → YELLOW → RED → BLACK state machine
- **Automated Actions**: Pre-configured response actions based on situation
- **Risk Assessment**: Real-time risk level calculation
- **Dashboard Visualization**: Professional monitoring interface
- **Command-line Interface**: Flexible configuration options

## Free AI Tools Used

1. **YOLOv8 (Ultralytics)**: State-of-the-art object detection model
   - Free and open-source
   - High accuracy for people detection
   - Multiple model sizes (nano to xlarge)
   
2. **OpenCV**: Computer vision library
   - Real-time video processing
   - Image manipulation
   - Drawing and visualization
   
3. **NumPy/SciPy**: Scientific computing
   - Numerical calculations
   - Statistical analysis
   - Array operations

4. **Matplotlib**: Visualization (optional)
   - Plot generation
   - Chart creation

## Installation

### Prerequisites
- Python 3.8+
- pip package manager
- Webcam or IP camera (optional)

### Steps

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd rail
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the system**:
   ```bash
   python main.py
   ```

## Usage

### Basic Usage

```bash
# Run with default webcam
python main.py

# Use specific camera
python main.py --camera 1

# Use video file as input
python main.py --video-input path/to/video.mp4

# Use larger YOLO model for better accuracy
python main.py --model yolov8l.pt

# Enable debug logging
python main.py --log-level DEBUG
```

### Command Line Options

- `--camera`: Camera source index (default: 0)
- `--video-input`: Path to video file
- `--fps`: Target frames per second (default: 30)
- `--config`: Configuration file path
- `--model`: YOLO model size (n/s/m/l/x)
- `--confidence`: Detection confidence threshold (0.0-1.0)
- `--log-level`: Logging level (DEBUG/INFO/WARNING/ERROR)
- `--log-file`: Log file path
- `--no-display`: Run without display window

### Configuration File

Edit `src/config/config.ini` to customize:
- Camera settings
- YOLO model parameters
- Density thresholds
- Grid configuration
- Prediction settings
- Logging options

## Project Structure

```
rail/
├── src/
│   ├── __init__.py                 # Package initialization
│   ├── main.py                     # Main entry point
│   ├── core/                       # Core modules
│   │   ├── camera_feed.py          # Camera input handling
│   │   ├── crowd_density.py        # YOLO detection & heatmap
│   │   ├── occupancy_mapping.py    # Density & occupancy
│   │   ├── flow_simulation.py      # Digital twin
│   │   ├── prediction.py           # Future density
│   │   ├── classification.py        # Situation assessment
│   │   ├── action_executor.py       # Action execution
│   │   └── monitor.py               # Continuous loop
│   ├── models/                      # Data models
│   │   └── data_models.py           # Data structures
│   ├── utils/                       # Utilities
│   │   └── visualizer.py            # Visualization tools
│   ├── config/                      # Configuration
│   │   └── config.ini               # Config file
│   └── database/                    # Database (future)
├── tests/                          # Test files
│   └── test_modules.py              # Unit tests
├── data/                          # Data directory
├── models/                        # Model storage
├── docs/                          # Documentation
├── requirements.txt               # Dependencies
├── README.md                      # This file
└── DEPLOYMENT_GUIDE.md            # Deployment guide
```

## State Classification

The system classifies crowd situations into four states:

| State | Density (ppl/m²) | Risk Level | Actions |
|-------|------------------|------------|---------|
| GREEN | < 4.0 | LOW | Monitoring, display updates |
| YELLOW | 4.0 - 5.0 | MODERATE | Path computation, staff positioning |
| RED | 5.0 - 6.0 | HIGH | Route recommendations, RPF notification |
| BLACK | > 6.0 | CRITICAL | Inflow restriction, emergency protocol |

## Deployment Options

### Edge Device Deployment
1. Deploy on NVIDIA Jetson or Intel NUC
2. Use TensorRT for model optimization
3. Configure for offline operation
4. Set up periodic model updates

### Cloud Deployment
1. Deploy as containerized service
2. Use Kubernetes for orchestration
3. Implement API endpoints
4. Set up monitoring and alerts

### Hybrid Deployment
1. Edge processing for real-time analysis
2. Cloud for historical analysis
3. Load balancing between devices
4. Central management system

## High Accuracy Tips

1. **Model Selection**: Use larger YOLO models (l or x) for better accuracy
2. **Camera Calibration**: Calibrate cameras for accurate area measurement
3. **Multiple Cameras**: Deploy multiple cameras for full coverage
4. **Fine-tuning**: Fine-tune YOLO on your specific dataset
5. **Temporal Smoothing**: Apply moving averages for stable readings
6. **Occlusion Handling**: Use multiple angles to reduce occlusion effects

## Performance Optimization

- Use YOLOv8n for real-time processing on edge devices
- Implement frame skipping for high FPS cameras
- Use GPU acceleration when available
- Optimize image resolution based on scene
- Implement efficient data structures

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## License

MIT License - See LICENSE file for details

## Authors

PREEMPT AI Team

## Acknowledgments

- Ultralytics for YOLOv8
- OpenCV community
- All contributors

## Contact

For questions and support, please open an issue on GitHub.

---

**Note**: This system is designed for crowd safety and management. Always follow local regulations and guidelines when deploying crowd control systems.
