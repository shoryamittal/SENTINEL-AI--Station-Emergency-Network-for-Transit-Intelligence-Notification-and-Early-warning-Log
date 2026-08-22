"""Continuous end-to-end monitoring loop that wires all SENTINEL modules together."""
from __future__ import annotations

import logging
import time
from typing import Optional

import cv2
import numpy as np

from .camera_feed import CameraFeed
from .crowd_density import CrowdDensityAnalyzer
from .occupancy_mapping import OccupancyMapper, overlay_heatmap
from .flow_simulation import FlowSimulator
from .prediction import DensityPredictor
from .classification import SituationClassifier
from .action_executor import ActionExecutor
from .railway_integration import RailwayIntegration

logger = logging.getLogger("sentinel.monitor")


class ContinuousMonitor:
    """Orchestrates the full crowd-density + response pipeline in a blocking loop.

    The constructor accepts a config dict (see ``main.load_config`` for defaults)
    and builds all required sub-components. Call :meth:`run` to start monitoring.
    """

    def __init__(self, config: dict) -> None:
        self.config = dict(config)

        # --- Sub-components ---
        self.camera = CameraFeed(
            source=config.get("camera_source", 0),
            fps=int(config.get("fps", 30)),
        )
        self.analyzer = CrowdDensityAnalyzer(
            model_name=config.get("yolo_model", "yolov8n.pt"),
            confidence_threshold=float(config.get("confidence_threshold", 0.5)),
        )
        self.mapper = OccupancyMapper(
            grid_size=(
                int(config.get("grid_rows", 4)),
                int(config.get("grid_cols", 6)),
            ),
            zone_area_m2=float(config.get("zone_area_m2", 10.0)),
        )
        self.simulator = FlowSimulator(
            grid_size=(
                int(config.get("grid_rows", 4)),
                int(config.get("grid_cols", 6)),
            ),
        )
        self.predictor = DensityPredictor(
            history_length=int(config.get("history_length", 30)),
            prediction_horizon=float(config.get("prediction_horizon", 1.0)),
        )
        self.classifier = SituationClassifier(
            green_threshold=float(config.get("green_threshold", 4.0)),
            yellow_threshold=float(config.get("yellow_threshold", 5.0)),
            red_threshold=float(config.get("red_threshold", 6.0)),
        )
        self.executor = ActionExecutor(
            station_name=config.get("station_name", "Central Station"),
            fast2sms_api_key=config.get("fast2sms_api_key"),
        )
        self.railway = RailwayIntegration()
        try:
            self.railway.load_sample_data()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Railway sample data unavailable: %s", exc)

        # --- Runtime stats ---
        self.frame_count: int = 0
        self.start_ts: Optional[float] = None
        self.end_ts: Optional[float] = None
        self.last_state: str = "GREEN"
        self._stopping = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run(self, display: bool = True) -> None:
        """Block and run the monitoring loop until KeyboardInterrupt or :meth:`stop`.

        Args:
            display: When True (default), render frames via ``cv2.imshow``.
                     Pass False for headless server deployments.
        """
        if not self.camera.start():
            logger.error("Failed to start camera source: %s", self.camera.source)
            raise RuntimeError(f"CameraFeed.start() failed for {self.camera.source!r}")

        logger.info("ContinuousMonitor started (display=%s)", display)
        self.start_ts = time.time()
        self.frame_count = 0
        self._stopping = False

        try:
            while not self._stopping:
                ok, frame = self.camera.read_frame()
                if not ok or frame is None:
                    logger.warning("Empty frame read, retrying...")
                    time.sleep(0.05)
                    continue

                stats = self._process_frame(frame)
                self.frame_count += 1
                self.last_state = stats["state"]

                if display:
                    vis = self._visualize(frame, stats)
                    cv2.imshow("SENTINEL AI - Live Monitor", vis)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q"):
                        logger.info("Stop requested via 'q' key")
                        break
        except KeyboardInterrupt:
            logger.info("Monitoring interrupted by user")
        finally:
            self.end_ts = time.time()
            self.camera.release()
            if display:
                cv2.destroyAllWindows()
            logger.info("ContinuousMonitor stopped. Total frames: %d", self.frame_count)

    def stop(self) -> None:
        """Request :meth:`run` to exit cleanly from another thread."""
        self._stopping = True

    def get_system_status(self) -> dict:
        """Return a small status dict suitable for shutdown summaries."""
        now = time.time()
        if self.start_ts is None:
            uptime = 0.0
        elif self.end_ts is not None:
            uptime = self.end_ts - self.start_ts
        else:
            uptime = now - self.start_ts
        fps = self.frame_count / uptime if uptime > 0 else 0.0
        return {
            "frame_count": int(self.frame_count),
            "uptime_seconds": float(uptime),
            "fps": float(fps),
            "last_state": self.last_state,
            "station": self.config.get("station_name", "Central Station"),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _process_frame(self, frame: np.ndarray) -> dict:
        detections = self.analyzer.detect_people(frame)
        grid = self.mapper.create_grid(frame.shape)
        grid = self.mapper.map_detections_to_grid(grid, detections)
        density_grid, statistics = self.mapper.calculate_density(grid)
        heatmap = self.mapper.render_heatmap(frame.shape, statistics)

        self.predictor.update_history(statistics)
        horizon = float(self.config.get("prediction_horizon", 1.0))
        prediction = self.predictor.predict_future_density(time_minutes=horizon)
        trend = prediction.get("trend", "stable")

        state, confidence = self.classifier.classify(
            statistics["max_density"], trend, prediction
        )

        actions = self.classifier.get_recommended_actions(state)
        self.executor.execute_actions(
            actions, state,
            max_density=statistics["max_density"],
            people_count=statistics["total_people"],
        )

        return {
            "detections": detections,
            "heatmap": heatmap,
            "density_grid": density_grid,
            "statistics": statistics,
            "prediction": prediction,
            "state": state,
            "confidence": confidence,
            "actions": actions,
        }

    def _visualize(self, frame: np.ndarray, stats: dict) -> np.ndarray:
        vis = overlay_heatmap(frame, stats["heatmap"], alpha=0.4)
        vis = self.analyzer.visualize_detections(vis, stats["detections"])

        state_colors = {
            "GREEN": (0, 200, 0),
            "YELLOW": (0, 220, 220),
            "RED": (0, 0, 220),
            "BLACK": (100, 100, 100),
        }
        color = state_colors.get(stats["state"], (255, 255, 255))
        s = stats["statistics"]
        cv2.putText(vis, f"STATE: {stats['state']}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        cv2.putText(vis, f"PEOPLE: {s['total_people']}", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(vis, f"MAX DENSITY: {s['max_density']:.2f}", (10, 85),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        cv2.putText(vis, f"FRAME: {self.frame_count}", (10, 110),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
        return vis
