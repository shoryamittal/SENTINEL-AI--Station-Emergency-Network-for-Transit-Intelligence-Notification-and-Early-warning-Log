#!/usr/bin/env python3
"""
SENTINEL AI - Complete Zone Simulation
Demonstrates all 4 states: GREEN → YELLOW → RED → BLACK
"""
"""
This file simulates crowd density changes over time to show all states!
"""

import cv2
import numpy as np
import time
import logging
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src import (
    CrowdDensityAnalyzer,
    OccupancyMapper,
    FlowSimulator,
    DensityPredictor,
    SituationClassifier,
    ActionExecutor,
    RailwayIntegration
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def generate_frame(width: int = 640, height: int = 480) -> np.ndarray:
    """Generate a blank frame for simulation"""
    return np.zeros((height, width, 3), dtype=np.uint8)


def simulate_detections(frame: np.ndarray, density_level: float, grid_size=None):
    """Simulate people detections based on density level"""
    height, width = frame.shape[:2]
    detections = []
    
    # Calculate number of people based on density
    # Density in ppl/m², grid cell area is 10 m² per cell
    # Total area = 4x6 grid *10=240 m²
    people_count = int(density_level * 240 / 4)  # Scale down for visibility
    
    for i in range(people_count):
        x1 = np.random.randint(50, width - 50)
        y1 = np.random.randint(50, height - 50)
        w = np.random.randint(30, 50)
        h = np.random.randint(40, 60)
        x2 = x1 + w
        y2 = y1 + h
        center_x = (x1 + x2) // 2
        center_y = (y1 + y2) // 2
        detections.append({
            'x1': x1,
            'y1': y1,
            'x2': x2,
            'y2': y2,
            'bbox': (x1, y1, x2, y2),
            'center': (center_x, center_y),
            'confidence': 0.85
        })
    
    return detections


def main():
    logger.info("=" * 80)
    logger.info("🚨 SENTINEL AI - Complete Zone Simulation")
    logger.info("=" * 80)
    
    # Initialize components
    analyzer = CrowdDensityAnalyzer(
        model_name="yolov8n.pt",
        confidence_threshold=0.5
    )
    mapper = OccupancyMapper(
        grid_size=(4, 6),
        zone_area_m2=10.0
    )
    simulator = FlowSimulator(
        grid_size=(4, 6)
    )
    predictor = DensityPredictor(
        history_length=60,
        prediction_horizon=5.0,
        use_lstm=False)
    classifier = SituationClassifier(
        green_threshold=4.0,
        yellow_threshold=5.0,
        red_threshold=6.0
    )
    executor = ActionExecutor(
        station_name="Central Station"
    )
    railway = RailwayIntegration()
    railway.load_sample_data()
    
    # Simulation parameters
    width, height = 640, 480
    fps = 10
    frame_count = 0
    
    logger.info("\n📊 Simulation phases:")
    logger.info("  1. GREEN Zone (0-200 frames) - Normal crowd")
    logger.info("  2. YELLOW Zone (200-400 frames) - Moderate crowd")
    logger.info("  3. RED Zone (400-600 frames) - High crowd")
    logger.info("  4. BLACK Zone (600-800 frames) - Critical crowd")
    logger.info("  Press 'q' at any time to exit\n")
    
    time.sleep(2)
    
    try:
        while True:
            # Determine current phase and density level
            if frame_count < 200:
                phase = "GREEN SIMULATION"
                density = 2.0 + np.random.uniform(1.0, 3.5)
                trend = "stable"
            elif frame_count < 400:
                phase = "YELLOW SIMULATION"
                density = 4.5 + np.random.uniform(4.0, 5.5)
                trend = "rising"
            elif frame_count < 600:
                phase = "RED SIMULATION"
                density = 6.5 + np.random.uniform(6.0, 8.0)
                trend = "rapidly rising"
            else:
                phase = "BLACK SIMULATION"
                density = 9.0 + np.random.uniform(8.0, 10.0)
                trend = "rapidly rising"
            
            # Generate frame and simulate detections
            frame = generate_frame(width, height)
            detections = simulate_detections(frame, density, grid_size=None)
            
            # Generate heatmap
            heatmap = analyzer.generate_heatmap(
                frame.shape,
                detections,
                radius=30,
                blur_kernel=51
            )
            
            # Create and populate grid
            grid = mapper.create_grid(frame.shape)
            grid = mapper.map_detections_to_grid(grid, detections)
            density_grid, statistics = mapper.calculate_density(grid)
            
            # Update predictor and predict
            predictor.update_history(statistics)
            prediction = predictor.predict_future_density(time_minutes=5.0)
            
            # Override prediction trend for simulation realism
            prediction["trend"] = trend
            
            # Classify situation
            state, confidence = classifier.classify(
                statistics["max_density"], trend, prediction)
            
            # Get recommended actions
            actions = classifier.get_recommended_actions(state)
            
            # Execute actions
            execution_result = executor.execute_actions(
                actions, state, max_density=statistics["max_density"],
                people_count=statistics["total_people"])
            
            # Visualize
            vis_frame = analyzer.overlay_heatmap(frame, heatmap, alpha=0.4)
            vis_frame = mapper.visualize_grid(vis_frame, density_grid)
            vis_frame = analyzer.visualize_detections(vis_frame, detections)
            
            # Draw phase and state info
            color_map = {
                "GREEN": (0, 255, 0),
                "YELLOW": (0, 255, 255),
                "RED": (0, 0, 255),
                "BLACK": (128, 128, 128)
            }
            color = color_map[state]
            
            cv2.putText(vis_frame, phase, (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.putText(vis_frame, f"STATE: {state}", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            cv2.putText(vis_frame, f"CONFIDENCE: {confidence*100:.1f}%", (10, 90),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.putText(vis_frame, f"PEOPLE COUNT: {statistics['total_people']}", (10, 120),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.putText(vis_frame, f"MAX DENSITY: {statistics['max_density']:.2f} ppl/m²", (10, 150),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.putText(vis_frame, f"TREND: {trend}", (10, 180),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 255), 2)
            cv2.putText(vis_frame, f"FRAME: {frame_count}", (10, height - 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            
            # Show frame
            cv2.imshow("SENTINEL AI - Complete Zone Simulation", vis_frame)
            
            # Check for exit
            if cv2.waitKey(int(1000/fps)) & 0xFF == ord('q'):
                logger.info("\n👋 Simulation stopped by user")
                break
            
            frame_count +=1
            
            # Reset after full cycle
            if frame_count >= 800:
                frame_count =0
                logger.info("\n🔄 Simulation restarting...")
                time.sleep(1)
                
    except KeyboardInterrupt:
        logger.info("\n👋 Simulation stopped by user")
    finally:
        cv2.destroyAllWindows()
        logger.info("\n" + "=" *80)
        logger.info("✅ Simulation Complete")
        logger.info("=" *80)


if __name__ == "__main__":
    main()
