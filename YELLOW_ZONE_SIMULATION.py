#!/usr/bin/env python3
q
import cv2
import numpy as np
import time
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent / "src"))

from src import (
    CrowdDensityAnalyzer,
    OccupancyMapper,
    DensityPredictor,
    SituationClassifier,
    ActionExecutor
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    logger.info("=" * 80)
    logger.info("🟡 SENTINEL AI - Yellow Zone Simulation")
    logger.info("=" * 80)
    
    # Initialize components
    analyzer = CrowdDensityAnalyzer(model_name="yolov8n.pt", confidence_threshold=0.5)
    mapper = OccupancyMapper(grid_size=(4, 6), zone_area_m2=10.0)
    predictor = DensityPredictor(history_length=60, prediction_horizon=5.0, use_lstm=False)
    classifier = SituationClassifier(
        green_threshold=4.0,
        yellow_threshold=5.0,
        red_threshold=6.0
    )
    executor = ActionExecutor(station_name="Central Station")
    
    width, height = 640, 480
    fps = 10
    frame_count = 0
    
    try:
        while True:
            # Generate yellow zone density (4.0-5.5 ppl/m²)
            density = 4.5 + np.random.uniform(4.0, 5.5)
            
            frame = np.zeros((height, width, 3), dtype=np.uint8)
            
            # Simulate detections
            detections = []
            people_count = int(density * 240 / 4)
            for i in range(people_count):
                x1 = np.random.randint(50, width - 50)
                y1 = np.random.randint(50, height - 50)
                x2 = x1 + np.random.randint(30, 50)
                y2 = y1 + np.random.randint(40, 60)
                detections.append({
                    'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2,
                    'bbox': (x1, y1, x2, y2),
                    'center': ((x1+x2)//2, (y1+y2)//2),
                    'confidence': 0.85
                })
            
            heatmap = analyzer.generate_heatmap(frame.shape, detections)
            grid = mapper.create_grid(frame.shape)
            grid = mapper.map_detections_to_grid(grid, detections)
            density_grid, statistics = mapper.calculate_density(grid)
            
            predictor.update_history(statistics)
            prediction = predictor.predict_future_density(time_minutes=5.0)
            state, confidence = classifier.classify(
                statistics["max_density"], "rising", prediction)
            
            actions = classifier.get_recommended_actions(state)
            executor.execute_actions(actions, state, max_density=statistics["max_density"], people_count=statistics["total_people"])
            
            vis_frame = analyzer.overlay_heatmap(frame, heatmap, alpha=0.4)
            vis_frame = mapper.visualize_grid(vis_frame, density_grid)
            vis_frame = analyzer.visualize_detections(vis_frame, detections)
            
            cv2.putText(vis_frame, "YELLOW ZONE - MODERATE CROWD", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            cv2.putText(vis_frame, f"PEOPLE: {statistics['total_people']}", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.putText(vis_frame, f"DENSITY: {statistics['max_density']:.2f} ppl/m²", (10, 90),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.imshow("Yellow Zone Simulation", vis_frame)
            
            if cv2.waitKey(int(1000/fps)) & 0xFF == ord('q'):
                break
            frame_count +=1
    except KeyboardInterrupt:
        logger.info("Simulation stopped by user")
    finally:
        cv2.destroyAllWindows()
        logger.info("=" * 80)
        logger.info("✅ Yellow Zone Simulation Complete")
        logger.info("=" * 80)
        
if __name__ == "__main__":
    main()
