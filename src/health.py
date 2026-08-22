"""Freshness and local processing telemetry."""
from __future__ import annotations
from datetime import datetime, timezone
from collections import deque
import time
from .contracts import CameraHealth

class HealthMonitor:
    def __init__(self, stale_frame_age_s: float = 2.0):
        self.stale_frame_age_s, self.latest_frame_at, self.latest_detection_at, self.latest_risk_at = stale_frame_age_s, None, None, None
        self._risk_times = deque(maxlen=20)
    def frame_age_ms(self) -> float:
        if self.latest_frame_at is None: return float('inf')
        return max(0.0, (datetime.now(timezone.utc) - self.latest_frame_at).total_seconds() * 1000)
    def camera_health(self) -> CameraHealth:
        if self.latest_frame_at is None: return CameraHealth.CAMERA_LOST
        return CameraHealth.STALE if self.frame_age_ms() > self.stale_frame_age_s * 1000 else CameraHealth.LIVE
    def record_frame(self, timestamp: datetime) -> None: self.latest_frame_at = timestamp
    def record_detection(self) -> None: self.latest_detection_at = datetime.now(timezone.utc)
    def record_risk(self) -> None:
        self.latest_risk_at = datetime.now(timezone.utc); self._risk_times.append(time.monotonic())
    def processing_fps(self) -> float:
        return 0.0 if len(self._risk_times) < 2 else (len(self._risk_times)-1)/(self._risk_times[-1]-self._risk_times[0])
