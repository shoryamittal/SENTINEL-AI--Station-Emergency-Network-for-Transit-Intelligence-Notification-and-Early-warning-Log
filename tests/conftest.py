"""Ensure tests import the repository package when invoked from any cwd."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pytest

from src.camera import FrameSource
from src.config import RuntimeConfig
from src.contracts import IncidentCandidate, SourceMode
from src.detector import Detection
from src.runtime import SentinelRuntime


class _CrowdedFakeDetector:
    """Deterministic detector that always reports a dense single-cell crowd.

    Used only by Person 2 (continuity-plane) tests to generate real, valid
    ``IncidentCandidate`` objects through Person 1's actual runtime/scenario
    pipeline, rather than hand-constructing contract objects by hand.
    """

    model_version = "continuity-test-fixture"

    def detect(self, frame):
        return [Detection((0, 0, 2, 2), (10, 10), 0.9) for _ in range(5)], 0.01


def make_incident_candidate(frame_id: int | None = None) -> IncidentCandidate:
    """Build one real IncidentCandidate via a real (simulated) SentinelRuntime.

    Configured so the very first processed frame confirms a BLACK
    LOCAL_BOTTLENECK incident deterministically (see
    tests/test_runtime.py for the same detector/config pattern).
    """
    runtime = SentinelRuntime(
        FrameSource(SourceMode.SIMULATION, simulation_factory=lambda: np.zeros((100, 100, 3), dtype=np.uint8)),
        _CrowdedFakeDetector(),
        RuntimeConfig(calibration_samples=1, extreme_occupancy_guardrail=1, escalation_confirmations=1),
    )
    runtime.process_once()
    candidate = runtime.get_next_incident(timeout=0.1)
    assert candidate is not None, "fixture failed to produce an incident candidate"
    return candidate


@pytest.fixture
def incident_factory():
    """Callable fixture: incident_factory() -> a fresh, real IncidentCandidate."""
    return make_incident_candidate
