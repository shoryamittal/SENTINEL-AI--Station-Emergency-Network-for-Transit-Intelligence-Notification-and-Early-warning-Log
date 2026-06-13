# PREEMPT AI - Platform Change Event Detection System

A real-time crowd monitoring and management system for platform change events at railway stations. Built with free, state-of-the-art AI tools for high-accuracy crowd density detection and situation assessment.

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
