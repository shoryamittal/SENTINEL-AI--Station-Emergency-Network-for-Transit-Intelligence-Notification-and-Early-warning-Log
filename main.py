#!/usr/bin/env python3
"""SENTINEL AI local adaptive crowd-risk runtime.

This compatibility CLI runs only the local Safety Plane and requires no WAN
connectivity. ``deploy.py`` is the canonical Round 2 application because it
also wires the Continuity Plane, dashboard, and local event journal.
"""
from __future__ import annotations

import argparse
import logging
import sys

from src import ContinuousMonitor


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="SENTINEL AI - Local Adaptive Crowd-Risk Runtime",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--camera", type=int, default=0, help="Camera index")
    source.add_argument("--video-input", help="Local video file path")
    parser.add_argument("--model", default="yolov8n.pt", help="Local YOLO model path")
    parser.add_argument("--confidence", type=float, default=0.5, help="YOLO person confidence threshold")
    parser.add_argument("--grid-rows", type=int, default=4, help="Occupancy grid rows")
    parser.add_argument("--grid-cols", type=int, default=6, help="Occupancy grid columns")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(asctime)s - %(levelname)s - %(message)s")
    logger = logging.getLogger(__name__)
    source = args.video_input if args.video_input else args.camera
    config = {
        "camera_source": source,
        "yolo_model": args.model,
        "confidence_threshold": args.confidence,
        "grid_rows": args.grid_rows,
        "grid_cols": args.grid_cols,
    }
    logger.info("SENTINEL AI - Local Adaptive Crowd-Risk Runtime")
    logger.info("Local Safety Plane only; deploy.py is the canonical Round 2 application.")
    monitor = ContinuousMonitor(config)
    try:
        monitor.run()
    except KeyboardInterrupt:
        logger.info("Local runtime interrupted")
    except Exception:
        logger.exception("Local runtime failed")
        raise
    finally:
        status = monitor.get_system_status()
        logger.info("Frames: %s | uptime: %.1fs | average FPS: %.1f", status["frame_count"], status["uptime_seconds"], status["fps"])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        sys.exit(1)
