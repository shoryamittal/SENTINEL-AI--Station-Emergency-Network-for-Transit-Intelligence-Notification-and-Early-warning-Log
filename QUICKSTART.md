# PREEMPT AI - Quick Start Guide

## Getting Started in 5 Minutes

### Step 1: Install Dependencies

```bash
pip install -r requirements.txt
```

This will install all required packages including:
- YOLOv8 (Ultralytics)
- OpenCV
- NumPy
- PyTorch
- Matplotlib

### Step 2: Test with Webcam

```bash
python main.py
```

The system will:
1. Initialize all components
2. Start camera feed
3. Begin real-time crowd monitoring
4. Display visualization window

**Press 'q' to quit**

### Step 3: Understanding the Output

The system displays:
- **State**: Current situation (GREEN/YELLOW/RED/BLACK)
- **Confidence**: Classification confidence percentage
- **People**: Total people detected
- **Max Density**: Maximum crowd density in ppl/m²
- **FPS**: Current processing speed

### Step 4: Command Line Options

```bash
# Use different camera
python main.py --camera 1

# Process video file
python main.py --video-input crowd_scene.mp4

# Higher accuracy (slower)
python main.py --model yolov8l.pt

# Debug mode
python main.py --log-level DEBUG

# Headless mode (no display)
python main.py --no-display
```

### Step 5: Configuration

Edit `src/config/config.ini` to customize:
- Camera settings
- Detection thresholds
- Grid configuration
- Prediction parameters

## System States Explained

| State | Color | Density | What It Means | Actions Taken |
|-------|-------|---------|---------------|---------------|
| GREEN | Green | < 4/m² | Normal crowd | Monitor, update displays |
| YELLOW | Yellow | 4-5/m² | Moderate crowd | Compute paths, alert staff |
| RED | Red | 5-6/m² | High crowd | RPF notification, route changes |
| BLACK | Purple | > 6/m² | Dangerous crowd | Emergency protocol, gate control |

## Common Use Cases

### 1. Railway Platform Monitoring
```bash
python main.py --camera 0 --model yolov8s.pt --fps 30
```

### 2. Event Crowd Analysis
```bash
python main.py --video-input event_footage.mp4 --no-display --log-level INFO
```

### 3. Edge Device Deployment (Jetson Nano)
```bash
python main.py --model yolov8n.pt --fps 15 --log-file preempt.log
```

### 4. High Accuracy Mode
```bash
python main.py --model yolov8x.pt --confidence 0.8 --log-level DEBUG
```

## Troubleshooting

### Camera Not Detected
```bash
# List available cameras
python main.py --camera 0  # Try different index
python main.py --camera 1
```

### Low FPS
```bash
# Use lighter model
python main.py --model yolov8n.pt

# Reduce resolution (edit config.ini)
```

### Detection Issues
```bash
# Increase confidence threshold
python main.py --confidence 0.7

# Use better model
python main.py --model yolov8l.pt
```

## Performance Tips

1. **For Real-time (30+ FPS)**: Use YOLOv8n or YOLOv8s
2. **For Accuracy**: Use YOLOv8m or YOLOv8l
3. **For Edge Devices**: Use YOLOv8n with 15 FPS
4. **For Analysis**: Use video input with YOLOv8x

## Next Steps

- Read the full [README.md](README.md) for detailed information
- Check [ARCHITECTURE.txt](docs/ARCHITECTURE.txt) for system design
- Review [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) for deployment options
- Explore [src/core/](src/core/) for module details

## Support

For issues and questions:
1. Check the documentation
2. Review test files
3. Enable debug logging
4. Open an issue on GitHub

---

**Remember**: Press 'q' to quit the application!
