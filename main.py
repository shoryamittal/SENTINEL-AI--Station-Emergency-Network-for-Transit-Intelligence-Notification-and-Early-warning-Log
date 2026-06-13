#!/usr/bin/env python3
"""
PREEMPT AI - Platform Change Event Detection System
Main entry point for the real-time crowd monitoring system.
"""

import sys
import argparse
import logging
from pathlib import Path

from src import ContinuousMonitor


def setup_logging(log_level: str = "INFO", log_file: str = None):
    """
    Setup logging configuration.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        log_file: Optional log file path
    """
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    handlers = [logging.StreamHandler()]
    
    if log_file:
        handlers.append(logging.FileHandler(log_file))
        
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format=log_format,
        handlers=handlers
    )


def load_config(config_file: str = None) -> dict:
    """
    Load configuration from file.
    
    Args:
        config_file: Optional config file path
        
    Returns:
        Configuration dictionary
    """
    config = {
        "camera_source": 0,
        "fps": 30,
        "yolo_model": "yolov8n.pt",
        "confidence_threshold": 0.5,
        "grid_rows": 4,
        "grid_cols": 6,
        "zone_area_m2": 10.0,
        "heatmap_radius": 30,
        "heatmap_blur_kernel": 51,
        "green_threshold": 4.0,
        "yellow_threshold": 5.0,
        "red_threshold": 6.0,
        "history_length": 30,
        "prediction_horizon": 1.0,
        "fast2sms_api_key": None
    }
    
    if config_file and Path(config_file).exists():
        import configparser
        cfg = configparser.ConfigParser()
        cfg.read(config_file)
        
        try:
            if "CAMERA" in cfg:
                try:
                    config["camera_source"] = cfg.getint("CAMERA", "source", fallback=0)
                except (ValueError, TypeError):
                    config["camera_source"] = cfg.get("CAMERA", "source", fallback=0)
                try:
                    config["fps"] = cfg.getint("CAMERA", "fps", fallback=30)
                except (ValueError, TypeError):
                    pass
                
            if "YOLO" in cfg:
                config["yolo_model"] = cfg.get("YOLO", "model_name", fallback="yolov8n.pt")
                try:
                    config["confidence_threshold"] = cfg.getfloat("YOLO", "confidence_threshold", fallback=0.5)
                except (ValueError, TypeError):
                    pass
                
            if "DENSITY" in cfg:
                try:
                    config["grid_rows"] = cfg.getint("DENSITY", "grid_rows", fallback=4)
                    config["grid_cols"] = cfg.getint("DENSITY", "grid_cols", fallback=6)
                    config["zone_area_m2"] = cfg.getfloat("DENSITY", "zone_area_m2", fallback=10.0)
                except (ValueError, TypeError):
                    pass
                
            if "CLASSIFICATION" in cfg:
                try:
                    config["green_threshold"] = cfg.getfloat("CLASSIFICATION", "green_threshold", fallback=4.0)
                    config["yellow_threshold"] = cfg.getfloat("CLASSIFICATION", "yellow_threshold", fallback=5.0)
                    config["red_threshold"] = cfg.getfloat("CLASSIFICATION", "red_threshold", fallback=6.0)
                except (ValueError, TypeError):
                    pass
                
            if "PREDICTION" in cfg:
                try:
                    config["history_length"] = cfg.getint("MONITORING", "history_length", fallback=30)
                    config["prediction_horizon"] = cfg.getfloat("PREDICTION", "prediction_horizon_minutes", fallback=1.0)
                except (ValueError, TypeError):
                    pass
                
            if "NOTIFICATIONS" in cfg:
                config["fast2sms_api_key"] = cfg.get("NOTIFICATIONS", "fast2sms_api_key", fallback=None)
        except Exception:
            pass
            
    return config


def parse_arguments():
    """
    Parse command line arguments.
    
    Returns:
        Parsed arguments
    """
    parser = argparse.ArgumentParser(
        description="PREEMPT AI - Platform Change Event Detection System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                                    # Run with default settings
  python main.py --camera 0 --fps 30               # Use camera 0 at 30 FPS
  python main.py --config config.ini               # Load from config file
  python main.py --log-level DEBUG                  # Enable debug logging
  python main.py --video-input test.mp4             # Use video file as input
        """
    )
    
    parser.add_argument(
        "--camera",
        type=int,
        default=0,
        help="Camera source index (default: 0)"
    )
    
    parser.add_argument(
        "--video-input",
        type=str,
        help="Path to video file instead of camera"
    )
    
    parser.add_argument(
        "--fps",
        type=int,
        default=30,
        help="Target frames per second (default: 30)"
    )
    
    parser.add_argument(
        "--config",
        type=str,
        default="src/config/config.ini",
        help="Path to configuration file"
    )
    
    parser.add_argument(
        "--model",
        type=str,
        default="yolov8n.pt",
        choices=["yolov8n.pt", "yolov8s.pt", "yolov8m.pt", "yolov8l.pt", "yolov8x.pt"],
        help="YOLO model size (default: yolov8n.pt)"
    )
    
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.5,
        help="Detection confidence threshold (default: 0.5)"
    )
    
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)"
    )
    
    parser.add_argument(
        "--log-file",
        type=str,
        help="Log file path"
    )
    
    parser.add_argument(
        "--no-display",
        action="store_true",
        help="Run without display window"
    )
    
    parser.add_argument(
        "--fast2sms-api-key",
        type=str,
        default=None,
        help="Fast2SMS API key for real SMS notifications (India)"
    )
    
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_arguments()
    
    setup_logging(args.log_level, args.log_file)
    logger = logging.getLogger(__name__)
    
    logger.info("="*60)
    logger.info("PREEMPT AI - Platform Change Event Detection System")
    logger.info("="*60)
    
    config = load_config(args.config)
    
    if args.video_input:
        config["camera_source"] = args.video_input
    else:
        config["camera_source"] = args.camera
        
    config["fps"] = args.fps
    config["yolo_model"] = args.model
    config["confidence_threshold"] = args.confidence
    
    if args.fast2sms_api_key:
        config["fast2sms_api_key"] = args.fast2sms_api_key
    
    logger.info("Configuration:")
    for key, value in config.items():
        logger.info(f"  {key}: {value}")
    
    monitor = ContinuousMonitor(config)
    
    try:
        logger.info("Starting system...")
        monitor.run(display=not args.no_display)
    except KeyboardInterrupt:
        logger.info("System interrupted by user")
    except Exception as e:
        logger.error(f"System error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        status = monitor.get_system_status()
        logger.info("Final System Status:")
        logger.info(f"  Total Frames: {status['frame_count']}")
        logger.info(f"  Uptime: {status['uptime_seconds']:.1f} seconds")
        logger.info(f"  Average FPS: {status['fps']:.1f}")
        logger.info("="*60)
        logger.info("System shutdown complete")
        logger.info("="*60)


if __name__ == "__main__":
    main()
