import numpy as np
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
