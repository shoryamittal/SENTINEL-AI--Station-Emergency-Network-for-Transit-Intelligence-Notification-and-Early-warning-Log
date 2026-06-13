# PREEMPT AI - Platform Change Event Deployment Guide

## Architecture Overview
This system implements the PREEMPT AI MVP flow for platform change events as shown in the flowchart.

## Key Free AI Tools Used
1. **YOLOv8 (Ultralytics)** - Open-source object detection model for people counting
2. **OpenCV** - Real-time computer vision library for camera handling and image processing
3. **NumPy/SciPy** - Numerical computing for density calculations
4. **Matplotlib** - Visualization (if needed for detailed analysis)

## Step-by-Step Technical Deployment

### Step 1: Project Setup
- Clone/Create the project directory
- Install dependencies: `pip install -r requirements.txt`

### Step 2: Edge Device Configuration
- Use an edge device like NVIDIA Jetson or Intel NUC for local processing
- Ensure the device has camera access (USB or IP camera)
- Deploy the code to the edge device

### Step 3: Camera Feed Setup
- Configure camera source in `main.py` (0 for webcam, or RTSP URL for IP camera)
- Set appropriate FPS for your use case

### Step 4: AI Pipeline Configuration
- **Crowd Detection**: Uses YOLOv8n (nano model for speed, free from Ultralytics)
- **Heatmap Generation**: Gaussian-blurred density map from detection centroids
- **Occupancy Mapping**: Grid-based density calculation per m²
- **Situation Classification**: Based on density thresholds (<4, 4-5, >5 ppl/m²)
- **Prediction**: Trend-based future density estimation

### Step 5: Deployment & Execution
- Run the system: `python main.py`
- Press 'q' to quit

### Step 6: High Accuracy Improvements
1. **Model Selection**: For higher accuracy, use YOLOv8l (large) or YOLOv8x (extra large) instead of nano
2. **Camera Calibration**: Calibrate cameras for better perspective and area estimation
3. **Multiple Cameras**: Use multiple camera feeds for full coverage
4. **Fine-tuning**: Fine-tune YOLO on your specific dataset for better accuracy
5. **Temporal Smoothing**: Apply moving averages to density values for stability

### Step 7: Edge-Native Deployment
- Use PyTorch/TensorRT for model optimization on edge devices
- Ensure offline operation capability
- Set up periodic model updates when internet is available

## File Structure
```
rail/
├── src/
│   ├── __init__.py
│   ├── camera_feed.py       # Camera/video input handling
│   ├── crowd_density.py     # YOLO detection and heatmap generation
│   ├── occupancy_mapping.py # Density and occupancy grid mapping
│   ├── classification.py    # Situation classification and actions
│   └── prediction.py        # Future density prediction
├── data/                    # Test data
├── models/                  # AI model storage
├── docs/                    # Documentation
├── requirements.txt         # Dependencies
└── main.py                  # Entry point
```
