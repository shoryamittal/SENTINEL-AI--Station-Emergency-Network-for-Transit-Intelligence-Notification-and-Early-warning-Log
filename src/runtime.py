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
    def __init__(self, source: FrameSource, detector=None, config: RuntimeConfig | None = None, incident_sink=None):
        self.config = config or RuntimeConfig(); self.source = source
        self.detector = detector or PersonDetector(self.config.model_path, self.config.confidence_threshold)
        self.occupancy = OccupancyGrid(self.config.grid_rows, self.config.grid_cols)
        self.baseline = AdaptiveBaseline(self.config.calibration_samples)
        self.risk = AdaptiveRisk(self.config.baseline_floor, self.config.accumulation_window, self.config.redistribution_window)
        self.scenario = ScenarioEngine(self.config.escalation_confirmations, self.config.deescalation_confirmations, self.config.extreme_occupancy_guardrail)
        self.health = HealthMonitor(self.config.stale_frame_age_s); self._latest = None; self._lock = Lock(); self._incidents = Queue(); self._stop = Event(); self._thread = None; self._last_key = None
        self._ever_started = False
        self._stopped = False
        self._last_success_at = None
        self._last_error_at = None
        self._last_error_type = None
        self._consecutive_failures = 0
        self._incident_sink = incident_sink
        self._pending_incident = None
        self._pending_incident_key = None
        self._last_sink_attempt_mono = 0.0
        self._incident_sink_failures = 0
    def start(self):
        if self._thread and self._thread.is_alive(): return
        self.source.start(); self._stop.clear()
        with self._lock:
            self._ever_started = True
            self._stopped = False
        self._thread = Thread(target=self._run, daemon=True); self._thread.start()
    def stop(self):
        self._stop.set()
        if self._thread: self._thread.join(timeout=2)
        self.source.stop()
        with self._lock:
            self._stopped = True
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
        with self._lock:
            self._latest = snapshot
            self._last_success_at = snapshot.timestamp_utc
            self._consecutive_failures = 0
        key = (severity, primary, occupancy.hotspot_zone)
        if severity is Severity.GREEN:
            # GREEN closes the current emitted episode. A pending candidate
            # is deliberately retained as historical audit evidence until
            # the local sink accepts it.
            with self._lock:
                self._last_key = None
        else:
            self._begin_incident_if_needed(
                key,
                lambda: IncidentCandidate(severity, primary, conditions, occupancy.hotspot_zone, load, accumulation, redistribution, action, code, self.detector.model_version, packet.source_mode, packet.frame_id),
            )
        self._attempt_pending_incident_sink()
        return snapshot

    def _begin_incident_if_needed(self, key, candidate_factory):
        """Create at most one candidate for an abnormal episode."""
        with self._lock:
            if self._pending_incident is not None or key == self._last_key:
                return
            candidate = candidate_factory()
            if self._incident_sink is None:
                self._incidents.put(candidate)
                self._last_key = key
                return
            self._pending_incident = candidate
            self._pending_incident_key = key
            # Permit the first local durability attempt immediately.
            self._last_sink_attempt_mono = 0.0

    def _attempt_pending_incident_sink(self):
        """Try one local durability handoff without blocking inference on I/O."""
        with self._lock:
            candidate = self._pending_incident
            if candidate is None:
                return
            now = time.monotonic()
            if now - self._last_sink_attempt_mono < 0.25:
                return
            self._last_sink_attempt_mono = now
        try:
            accepted = bool(self._incident_sink(candidate))
        except Exception:
            accepted = False
        with self._lock:
            # The single runtime thread owns this state; the identity check
            # still avoids changing a newer pending candidate unexpectedly.
            if self._pending_incident is not candidate:
                return
            if not accepted:
                self._incident_sink_failures += 1
                return
            self._incidents.put(candidate)
            self._last_key = self._pending_incident_key
            self._pending_incident = None
            self._pending_incident_key = None
    def get_latest_snapshot(self):
        with self._lock: return self._latest
    def get_runtime_health(self):
        """Return serializable worker health without exposing exception detail."""
        with self._lock:
            snapshot = self._latest
            ever_started = self._ever_started
            stopped = self._stopped
            consecutive_failures = self._consecutive_failures
            last_success_at = self._last_success_at
            last_error_at = self._last_error_at
            last_error_type = self._last_error_type
            pending_incident = self._pending_incident
            incident_sink_failures = self._incident_sink_failures
        worker_alive = bool(self._thread and self._thread.is_alive())
        snapshot_age_ms = None
        if snapshot is not None:
            snapshot_age_ms = max(0.0, (datetime.now(timezone.utc) - snapshot.timestamp_utc).total_seconds() * 1000)
        snapshot_fresh = snapshot_age_ms is not None and snapshot_age_ms <= self.config.stale_frame_age_s * 1000
        if not ever_started:
            state = "NOT_STARTED"
        elif stopped or not worker_alive:
            state = "STOPPED"
        elif consecutive_failures:
            state = "DEGRADED"
        elif snapshot is None:
            state = "STARTING"
        elif not snapshot_fresh:
            state = "STALE"
        else:
            state = "HEALTHY"
        return {
            "state": state,
            "worker_alive": worker_alive,
            "consecutive_failures": consecutive_failures,
            "last_success_at": last_success_at.isoformat() if last_success_at else None,
            "last_error_at": last_error_at.isoformat() if last_error_at else None,
            "last_error_type": last_error_type,
            "snapshot_age_ms": snapshot_age_ms,
            "snapshot_fresh": snapshot_fresh,
            "camera_health": self.source.health().value,
            "pending_incident": pending_incident is not None,
            "pending_incident_event_id": pending_incident.event_id if pending_incident else None,
            "incident_sink_failures": incident_sink_failures,
        }
    def get_next_incident(self, timeout=None):
        try: return self._incidents.get(timeout=timeout)
        except Empty: return None
    def _run(self):
        while not self._stop.is_set():
            try:
                snapshot = self.process_once()
            except Exception as exc:
                with self._lock:
                    self._consecutive_failures += 1
                    self._last_error_at = datetime.now(timezone.utc)
                    self._last_error_type = type(exc).__name__
                    failures = self._consecutive_failures
                self._stop.wait(min(0.1 * (2 ** max(0, failures - 1)), 2.0))
                continue
            if snapshot is None:
                self._stop.wait(.03)


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
