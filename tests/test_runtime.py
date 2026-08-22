import numpy as np
import time
from src.camera import FrameSource
from src.config import RuntimeConfig
from src.contracts import Scenario, Severity, SourceMode
from src.detector import Detection
from src.runtime import SentinelRuntime

class FakeDetector:
    model_version = "fake"
    def detect(self, frame): return [Detection((0,0,2,2), (10,10), .9)], .1

def test_runtime_operates_without_internet():
    runtime = SentinelRuntime(FrameSource(SourceMode.SIMULATION, simulation_factory=lambda: np.zeros((100,100,3), dtype=np.uint8)), FakeDetector(), RuntimeConfig(calibration_samples=2))
    snapshot = runtime.process_once()
    assert snapshot is not None and snapshot.source_mode is SourceMode.SIMULATION


class SequenceScenario:
    def __init__(self):
        self._states = iter((Severity.RED, Severity.GREEN, Severity.RED))

    def evaluate(self, *args, **kwargs):
        severity = next(self._states)
        primary = Scenario.ACCUMULATION if severity is Severity.RED else Scenario.STABLE_HIGH_OCCUPANCY
        return primary, (), severity, 1.0, "TEST_ACTION", "Test recommendation"


def test_same_incident_key_can_recur_after_green_recovery():
    runtime = SentinelRuntime(
        FrameSource(SourceMode.SIMULATION, simulation_factory=lambda: np.zeros((100, 100, 3), dtype=np.uint8)),
        FakeDetector(),
        RuntimeConfig(calibration_samples=1, escalation_confirmations=1),
    )
    runtime.scenario = SequenceScenario()

    runtime.process_once()
    first = runtime.get_next_incident(timeout=0.1)
    assert first is not None

    runtime.process_once()
    assert runtime.get_next_incident(timeout=0.01) is None

    runtime.process_once()
    second = runtime.get_next_incident(timeout=0.1)
    assert second is not None
    assert second.event_id != first.event_id
    assert second.severity is Severity.RED
    assert second.primary_scenario is Scenario.ACCUMULATION


def _wait_until(predicate, timeout_s=2.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class FailingThenWorkingDetector:
    model_version = "failing-then-working"

    def __init__(self):
        self.calls = 0

    def detect(self, frame):
        self.calls += 1
        if self.calls <= 2:
            raise RuntimeError("transient inference failure")
        return [Detection((0, 0, 2, 2), (10, 10), .9)], .1


class AlwaysFailingDetector:
    model_version = "always-failing"

    def detect(self, frame):
        raise RuntimeError("persistent inference failure")


def _background_runtime(detector):
    return SentinelRuntime(
        FrameSource(SourceMode.SIMULATION, simulation_factory=lambda: np.zeros((100, 100, 3), dtype=np.uint8)),
        detector,
        RuntimeConfig(calibration_samples=1),
    )


def test_background_runtime_recovers_from_transient_inference_failures():
    detector = FailingThenWorkingDetector()
    runtime = _background_runtime(detector)
    runtime.start()
    try:
        assert _wait_until(lambda: detector.calls >= 3 and runtime.get_latest_snapshot() is not None)
        health = runtime.get_runtime_health()
        assert health["worker_alive"] is True
        assert health["state"] == "HEALTHY"
        assert health["consecutive_failures"] == 0
        assert health["last_error_type"] == "RuntimeError"
    finally:
        runtime.stop()
    assert runtime.get_runtime_health()["state"] == "STOPPED"
    assert runtime.get_runtime_health()["worker_alive"] is False


def test_background_runtime_remains_alive_during_persistent_inference_failure():
    runtime = _background_runtime(AlwaysFailingDetector())
    runtime.start()
    try:
        assert _wait_until(lambda: runtime.get_runtime_health()["consecutive_failures"] >= 1)
        health = runtime.get_runtime_health()
        assert health["worker_alive"] is True
        assert health["state"] == "DEGRADED"
        assert health["last_error_type"] == "RuntimeError"
        assert runtime.get_latest_snapshot() is None
    finally:
        runtime.stop()
    assert runtime.get_runtime_health()["state"] == "STOPPED"
