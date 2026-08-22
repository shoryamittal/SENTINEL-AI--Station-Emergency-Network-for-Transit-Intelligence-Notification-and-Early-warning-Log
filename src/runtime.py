"""Thread-safe, offline-only orchestration boundary for the continuity plane."""
from __future__ import annotations
from datetime import datetime, timezone
from queue import Empty, Queue
from threading import Event, Lock, Thread
import time
from .adaptive_risk import AdaptiveRisk
from .baseline import AdaptiveBaseline
from .camera import FrameSource
from .config import RuntimeConfig
from .contracts import IncidentCandidate, RiskSnapshot, Severity
from .detector import PersonDetector
from .health import HealthMonitor
from .occupancy import OccupancyGrid
from .scenario import ScenarioEngine

class SentinelRuntime:
    def __init__(self, source: FrameSource, detector=None, config: RuntimeConfig | None = None):
        self.config = config or RuntimeConfig(); self.source = source
        self.detector = detector or PersonDetector(self.config.model_path, self.config.confidence_threshold)
        self.occupancy = OccupancyGrid(self.config.grid_rows, self.config.grid_cols)
        self.baseline = AdaptiveBaseline(self.config.calibration_samples)
        self.risk = AdaptiveRisk(self.config.baseline_floor, self.config.accumulation_window, self.config.redistribution_window)
        self.scenario = ScenarioEngine(self.config.escalation_confirmations, self.config.deescalation_confirmations, self.config.extreme_occupancy_guardrail)
        self.health = HealthMonitor(self.config.stale_frame_age_s); self._latest = None; self._lock = Lock(); self._incidents = Queue(); self._stop = Event(); self._thread = None; self._last_key = None
    def start(self):
        if self._thread and self._thread.is_alive(): return
        self.source.start(); self._stop.clear(); self._thread = Thread(target=self._run, daemon=True); self._thread.start()
    def stop(self):
        self._stop.set()
        if self._thread: self._thread.join(timeout=2)
        self.source.stop()
    def process_once(self):
        packet = self.source.read()
        if packet is None: return None
        self.health.record_frame(packet.capture_timestamp_utc)
        detections, latency = self.detector.detect(packet.frame); self.health.record_detection()
        occupancy = self.occupancy.map(detections, packet.frame.shape)
        base = self.baseline.values(); load, accumulation, redistribution = self.risk.update(occupancy.grid, base)
        primary, conditions, severity, confidence, code, action = self.scenario.evaluate(occupancy.grid, load, accumulation, redistribution, occupancy.hotspot_zone)
        self.baseline.update(occupancy.grid, abnormal=severity is not Severity.GREEN)
        snapshot = RiskSnapshot(datetime.now(timezone.utc), packet.frame_id, packet.source_mode, occupancy.people_count, occupancy.occupancy_index, occupancy.grid, self.baseline.state, load, accumulation, redistribution, primary, conditions, severity, confidence, occupancy.hotspot_zone, action, code, self.source.health(), self.health.frame_age_ms(), latency, self.detector.model_version)
        self.health.record_risk()
        with self._lock: self._latest = snapshot
        key = (severity, primary, occupancy.hotspot_zone)
        if severity is not Severity.GREEN and key != self._last_key:
            self._incidents.put(IncidentCandidate(severity, primary, conditions, occupancy.hotspot_zone, load, accumulation, redistribution, action, code, self.detector.model_version, packet.source_mode, packet.frame_id)); self._last_key = key
        return snapshot
    def get_latest_snapshot(self):
        with self._lock: return self._latest
    def get_next_incident(self, timeout=None):
        try: return self._incidents.get(timeout=timeout)
        except Empty: return None
    def _run(self):
        while not self._stop.is_set():
            if self.process_once() is None: time.sleep(.03)


class ContinuousMonitor:
    """Small compatibility adapter for the existing ``main.py`` entry point.

    It deliberately exposes no legacy density classifier or network action
    code; all assessments flow through :class:`SentinelRuntime`.
    """
    def __init__(self, config: dict):
        from .contracts import SourceMode
        source_value = config.get("camera_source", 0)
        mode = SourceMode.VIDEO if isinstance(source_value, str) else SourceMode.CAMERA
        runtime_config = RuntimeConfig(
            grid_rows=config.get("grid_rows", 4), grid_cols=config.get("grid_cols", 6),
            confidence_threshold=config.get("confidence_threshold", .5),
            model_path=config.get("yolo_model", "yolov8n.pt"),
        )
        self.runtime = SentinelRuntime(FrameSource(mode, source_value), config=runtime_config)
        self._frames, self._started = 0, time.monotonic()
    def run(self, display: bool = False):
        self.runtime.start()
        try:
            while True:
                snapshot = self.runtime.get_latest_snapshot()
                if snapshot: self._frames = snapshot.frame_id
                time.sleep(.1)
        finally: self.runtime.stop()
    def get_system_status(self):
        elapsed = max(time.monotonic() - self._started, .001)
        return {"frame_count": self._frames, "uptime_seconds": elapsed, "fps": self._frames / elapsed}
