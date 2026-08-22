import numpy as np
from src.camera import FrameSource
from src.config import RuntimeConfig
from src.contracts import SourceMode
from src.detector import Detection
from src.runtime import SentinelRuntime

class FakeDetector:
    model_version = "fake"
    def detect(self, frame): return [Detection((0,0,2,2), (10,10), .9)], .1

def test_runtime_operates_without_internet():
    runtime = SentinelRuntime(FrameSource(SourceMode.SIMULATION, simulation_factory=lambda: np.zeros((100,100,3), dtype=np.uint8)), FakeDetector(), RuntimeConfig(calibration_samples=2))
    snapshot = runtime.process_once()
    assert snapshot is not None and snapshot.source_mode is SourceMode.SIMULATION
