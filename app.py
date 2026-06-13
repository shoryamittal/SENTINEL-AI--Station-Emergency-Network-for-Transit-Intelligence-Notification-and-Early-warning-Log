#!/usr/bin/env python3
"""
SENTINEL AI - Professional Streamlit Dashboard
Real-time crowd monitoring, prediction, and railway integration!
"""
import streamlit as st
import cv2
import numpy as np
import time
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src import (
    CameraFeed,
    CrowdDensityAnalyzer,
    OccupancyMapper,
    FlowSimulator,
    DensityPredictor,
    SituationClassifier,
    ActionExecutor,
    RailwayIntegration
)

# Page config
st.set_page_config(
    page_title="SENTINEL AI",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for better styling
st.markdown("""
    <style>
    .main {
        background-color: #f5f7fa;
    }
    .stMetric {
        background-color: white;
        padding: 1rem;
        border-radius: 0.5rem;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .state-green {
        background-color: #d4edda;
        border-left: 5px solid #28a745;
    }
    .state-yellow {
        background-color: #fff3cd;
        border-left: 5px solid #ffc107;
    }
    .state-red {
        background-color: #f8d7da;
        border-left: 5px solid #dc3545;
    }
    .state-black {
        background-color: #e2e3e5;
        border-left: 5px solid #343a40;
    }
    </style>
""", unsafe_allow_html=True)

# Initialize session state
if 'system_initialized' not in st.session_state:
    st.session_state.system_initialized = False
    st.session_state.frame_count = 0
    st.session_state.state_history = []
    st.session_state.total_people_history = []
    st.session_state.max_density_history = []
    st.session_state.start_time = None

def init_system(config):
    """Initialize all system components."""
    try:
        camera = CameraFeed(
            source=config['camera_source'],
            fps=config['fps']
        )
        analyzer = CrowdDensityAnalyzer(
            model_name=config['yolo_model'],
            confidence_threshold=config['confidence_threshold']
        )
        mapper = OccupancyMapper(
            grid_size=(config['grid_rows'], config['grid_cols']),
            zone_area_m2=config['zone_area_m2']
        )
        simulator = FlowSimulator(
            grid_size=(config['grid_rows'], config['grid_cols'])
        )
        predictor = DensityPredictor(
            history_length=config['history_length'],
            prediction_horizon=config['prediction_horizon']
        )
        classifier = SituationClassifier(
            green_threshold=config['green_threshold'],
            yellow_threshold=config['yellow_threshold'],
            red_threshold=config['red_threshold']
        )
        executor = ActionExecutor(
            station_name=config['station_name'],
            fast2sms_api_key=config.get('fast2sms_api_key')
        )
        railway = RailwayIntegration()
        if config['load_sample_railway_data']:
            railway.load_sample_data()
        
        st.session_state.camera = camera
        st.session_state.analyzer = analyzer
        st.session_state.mapper = mapper
        st.session_state.simulator = simulator
        st.session_state.predictor = predictor
        st.session_state.classifier = classifier
        st.session_state.executor = executor
        st.session_state.railway = railway
        st.session_state.system_initialized = True
        st.session_state.frame_count = 0
        st.session_state.state_history = []
        st.session_state.total_people_history = []
        st.session_state.max_density_history = []
        st.session_state.start_time = None
        return True
    except Exception as e:
        st.error(f"Failed to initialize system: {e}")
        return False

def main():
    # Sidebar
    st.sidebar.title("SENTINEL AI")
    st.sidebar.subheader("Configuration")
    
    camera_source = st.sidebar.selectbox(
        "Camera Source",
        [0, 1, "Video File"],
        index=0
    )
    video_path = None
    if camera_source == "Video File":
        uploaded_file = st.sidebar.file_uploader(
            "Upload Video",
            type=["mp4", "avi", "mov"]
        )
        if uploaded_file:
            video_path = Path("temp_video.mp4")
            with open(video_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            camera_source = str(video_path)
    
    yolo_model = st.sidebar.selectbox(
        "YOLO Model",
        ["yolov8n.pt", "yolov8s.pt", "yolov8m.pt"],
        index=0
    )
    confidence_threshold = st.sidebar.slider(
        "Confidence Threshold",
        0.1, 1.0, 0.5
    )
    green_threshold = st.sidebar.slider(
        "Green (Max Density)",
        1.0, 10.0, 4.0
    )
    yellow_threshold = st.sidebar.slider(
        "Yellow (Max Density)",
        1.0, 10.0, 5.0
    )
    red_threshold = st.sidebar.slider(
        "Red (Max Density)",
        1.0, 10.0, 6.0
    )
    config = {
        'camera_source': camera_source,
        'fps': 30,
        'yolo_model': yolo_model,
        'confidence_threshold': confidence_threshold,
        'grid_rows': 4,
        'grid_cols': 6,
        'zone_area_m2': 10.0,
        'green_threshold': green_threshold,
        'yellow_threshold': yellow_threshold,
        'red_threshold': red_threshold,
        'history_length': 60,
        'prediction_horizon': 5.0,
        'station_name': "Central Station",
        'load_sample_railway_data': True
    }
    
    col1, col2 = st.sidebar.columns(2)
    with col1:
        if st.button("Initialize System"):
            with st.spinner("Initializing system..."):
                if init_system(config):
                    st.sidebar.success("System initialized!")
    with col2:
        if st.button("Reset System"):
            st.session_state.system_initialized = False
            st.session_state.frame_count = 0
            st.session_state.state_history = []
            st.session_state.total_people_history = []
            st.session_state.max_density_history = []
            st.session_state.start_time = None
            st.sidebar.info("System reset!")
    
    st.sidebar.markdown("---")
    st.sidebar.markdown("### About")
    st.sidebar.info("""
    **SENTINEL AI** is a real-time crowd monitoring system for railway stations, featuring:
    - YOLOv8 for people detection
    - LSTM for density prediction
    - Digital twin flow simulation
    - Predictive reconfiguration
    - Railway operations integration
    """)
    
    # Main content
    st.title("SENTINEL AI")
    st.markdown("## Railway Station Crowd Monitoring & Stampede Prevention System")
    st.markdown("---")
    
    if not st.session_state.system_initialized:
        st.warning("Please initialize the system from the sidebar first!")
        return
    
    # Create placeholders
    video_placeholder = st.empty()
    status_placeholder = st.empty()
    stats_placeholder = st.empty()
    charts_placeholder = st.empty()
    actions_placeholder = st.empty()
    
    # Start button
    start_monitoring = st.button("Start Monitoring")
    stop_monitoring = st.button("Stop Monitoring")
    
    if start_monitoring:
        if st.session_state.camera.start():
            st.success("Camera started!")
            st.session_state.start_time = time.time()
            
            while not stop_monitoring:
                ret, frame = st.session_state.camera.read_frame()
                if not ret:
                    st.error("Failed to read frame")
                    break
                
                # Process frame
                detections = st.session_state.analyzer.detect_people(frame)
                heatmap = st.session_state.analyzer.generate_heatmap(
                    frame.shape,
                    detections,
                    radius=30,
                    blur_kernel=51
                )
                grid = st.session_state.mapper.create_grid(frame.shape)
                grid = st.session_state.mapper.map_detections_to_grid(grid, detections)
                density_grid, statistics = st.session_state.mapper.calculate_density(grid)
                
                # Update predictor
                st.session_state.predictor.update_history(statistics)
                prediction = st.session_state.predictor.predict_future_density(
                    time_minutes=config['prediction_horizon']
                )
                
                # Classify
                state, confidence = st.session_state.classifier.classify(
                    statistics['max_density'],
                    prediction['trend'],
                    prediction
                )
                
                # Update history
                st.session_state.frame_count += 1
                st.session_state.total_people_history.append(statistics['total_people'])
                st.session_state.max_density_history.append(statistics['max_density'])
                st.session_state.state_history.append({
                    'state': state,
                    'time': time.time()
                })
                
                # Visualize
                vis_frame = st.session_state.analyzer.overlay_heatmap(frame, heatmap, alpha=0.4)
                vis_frame = st.session_state.mapper.visualize_grid(vis_frame, density_grid)
                vis_frame = st.session_state.analyzer.visualize_detections(vis_frame, detections)
                
                # Convert BGR to RGB for Streamlit
                vis_frame_rgb = cv2.cvtColor(vis_frame, cv2.COLOR_BGR2RGB)
                video_placeholder.image(vis_frame_rgb, channels="RGB", use_container_width=True)
                
                # Display status
                with status_placeholder.container():
                    col1, col2, col3, col4 = st.columns(4)
                    
                    # State indicator with color
                    state_class = f"state-{state.lower()}"
                    with col1:
                        st.markdown(f"""
                            <div style="background-color: {
                                '#d4edda' if state == 'GREEN' else 
                                '#fff3cd' if state == 'YELLOW' else 
                                '#f8d7da' if state == 'RED' else '#e2e3e5'
                            }; border-left: 5px solid {
                                '#28a745' if state == 'GREEN' else 
                                '#ffc107' if state == 'YELLOW' else 
                                '#dc3545' if state == 'RED' else '#343a40'
                            }; padding: 1rem; border-radius: 0.5rem;">
                                <h4 style="margin: 0; color: {'#155724' if state == 'GREEN' else '#856404' if state == 'YELLOW' else '#721c24' if state == 'RED' else '#383d41'};">System State</h4>
                                <h2 style="margin: 0.5rem 0 0 0; color: {'#155724' if state == 'GREEN' else '#856404' if state == 'YELLOW' else '#721c24' if state == 'RED' else '#383d41'};">{state}</h2>
                                <p style="margin: 0.5rem 0 0 0; color: {'#155724' if state == 'GREEN' else '#856404' if state == 'YELLOW' else '#721c24' if state == 'RED' else '#383d41'};">Confidence: {confidence*100:.1f}%</p>
                            </div>
                        """, unsafe_allow_html=True)
                    
                    col2.metric(
                        "People Count",
                        f"{statistics['total_people']}"
                    )
                    
                    col3.metric(
                        "Max Density",
                        f"{statistics['max_density']:.2f} ppl/m²"
                    )
                    
                    elapsed = time.time() - st.session_state.start_time if st.session_state.start_time else 0
                    fps = st.session_state.frame_count / elapsed if elapsed > 0 else 0
                    col4.metric(
                        "FPS",
                        f"{fps:.1f}"
                    )
                
                # Display charts
                with charts_placeholder.container():
                    chart_col1, chart_col2 = st.columns(2)
                    if len(st.session_state.total_people_history) > 1:
                        chart_col1.line_chart(
                            st.session_state.total_people_history[-100:],
                            x_label="Frame",
                            y_label="People Count"
                        )
                        chart_col2.line_chart(
                            st.session_state.max_density_history[-100:],
                            x_label="Frame",
                            y_label="Max Density (ppl/m²)"
                        )
                
                # Display actions
                with actions_placeholder.container():
                    st.markdown("### Recommended Actions")
                    actions = st.session_state.classifier.get_recommended_actions(state)
                    for action in actions:
                        priority = "High" if action['priority'] == "high" else "Medium" if action['priority'] == "medium" else "Low"
                        st.markdown(f"- **{priority} Priority**: {action['action']} - {action['description']}")
                
                time.sleep(0.01)  # Small delay to prevent UI freeze
            
            # Cleanup
            st.session_state.camera.release()
            st.info("Camera released!")

if __name__ == '__main__':
    main()

