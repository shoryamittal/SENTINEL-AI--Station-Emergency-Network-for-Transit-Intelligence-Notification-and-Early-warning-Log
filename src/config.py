"""Prototype calibration parameters for the local safety-plane runtime."""

from dataclasses import dataclass


@dataclass(slots=True)
class RuntimeConfig:
    grid_rows: int = 4
    grid_cols: int = 6
    confidence_threshold: float = 0.5
    model_path: str = "yolov8n.pt"
    stale_frame_age_s: float = 2.0
    camera_failure_timeout_s: float = 5.0
    calibration_samples: int = 12
    baseline_floor: float = 2.0
    accumulation_window: int = 6
    redistribution_window: int = 4
    extreme_occupancy_guardrail: int = 20  # Prototype guardrail per grid zone.
    escalation_confirmations: int = 2
    deescalation_confirmations: int = 4
