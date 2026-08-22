"""REALITY / SIMULATION mode switching and camera-freshness regression tests.

No webcam is opened by any test here: REALITY-mode switching uses the
deploy.py wiring against a SIMULATION-source stand-in (monkeypatched so no
real cv2.VideoCapture(0) call ever happens), and the "simulation" tests
drive an actual small synthetic video file through the real pipeline --
never a fake/hard-coded result.
"""
from __future__ import annotations

import time

import cv2
import numpy as np
import pytest

from src.camera import FrameSource
from src.config import RuntimeConfig
from src.contracts import SourceMode
from src.detector import Detection
from src.runtime import SentinelRuntime


class _OnePersonDetector:
    model_version = "operating-mode-fixture"

    def detect(self, frame):
        return [Detection((0, 0, 2, 2), (10, 10), 0.9)], 0.01


@pytest.fixture
def tiny_video(tmp_path):
    """A short, deterministic synthetic video: no external asset needed."""
    path = tmp_path / "scenario.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (64, 64))
    for i in range(15):
        frame = np.full((64, 64, 3), i % 255, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    return str(path)


# ----------------------------------------------------------------------
# Camera freshness regression: frame_age must be measured at read time,
# not after inference (the exact bug fixed this session).
# ----------------------------------------------------------------------
class _SlowDetector:
    """Detector whose detect() call takes a deliberate, measurable delay.

    Mirrors the real PersonDetector.detect() contract: it measures and
    returns its own elapsed latency, rather than a fixed placeholder.
    """

    model_version = "slow-fixture"
    delay_s = 0.3

    def detect(self, frame):
        started = time.perf_counter()
        time.sleep(self.delay_s)
        return [], (time.perf_counter() - started) * 1000


def test_frame_age_reflects_capture_time_not_post_inference_time():
    runtime = SentinelRuntime(
        FrameSource(SourceMode.SIMULATION, simulation_factory=lambda: np.zeros((10, 10, 3), dtype=np.uint8)),
        _SlowDetector(),
        RuntimeConfig(calibration_samples=1),
    )
    snapshot = runtime.process_once()
    assert snapshot is not None
    # If frame_age were (incorrectly) measured after detect() finishes, it
    # would be >= the detector's artificial 300ms delay. It must instead be
    # a tiny fraction of that -- the moment the frame was read, not the
    # moment inference happened to finish.
    assert snapshot.frame_age_ms < 100
    assert snapshot.processing_latency_ms >= _SlowDetector.delay_s * 1000 * 0.5


# ----------------------------------------------------------------------
# Mode switching: only one active FrameSource/SentinelRuntime at a time.
# ----------------------------------------------------------------------
def test_switch_to_simulation_and_back_leaves_exactly_one_active_source(monkeypatch, tiny_video):
    import deploy

    # Force the "reality" baseline to a deterministic SIMULATION source so
    # this test never touches a real webcam.
    monkeypatch.setattr(deploy, "_source_mode", SourceMode.SIMULATION)
    monkeypatch.setattr(deploy, "_source_value", None)
    monkeypatch.setattr(deploy, "runtime_config", RuntimeConfig(calibration_samples=1))

    original_runtime = deploy.runtime
    try:
        ok, error = deploy.switch_to_simulation(tiny_video)
        assert ok is True and error is None
        assert deploy._operating_mode == "SIMULATION"
        simulation_runtime = deploy.runtime
        assert simulation_runtime is not original_runtime
        assert simulation_runtime.source.source_mode == SourceMode.VIDEO
        # The old runtime's source must be released -- not two owners alive.
        assert original_runtime.source._capture is None

        ok, error = deploy.switch_to_reality()
        assert ok is True and error is None
        assert deploy._operating_mode == "REALITY"
        reality_runtime = deploy.runtime
        assert reality_runtime is not simulation_runtime
        assert reality_runtime.source.source_mode == SourceMode.SIMULATION
        # The simulation runtime's video capture must be released too.
        assert simulation_runtime.source._capture is None
    finally:
        deploy.runtime.stop()
        monkeypatch.setattr(deploy, "runtime", original_runtime)
        monkeypatch.setattr(deploy, "_operating_mode", "REALITY")


def test_status_endpoint_reports_operating_mode_and_source_label(tmp_path, monkeypatch, tiny_video):
    import deploy

    from src.connectivity import ConnectivityManager
    from src.metrics import ContinuityMetrics
    from src.persistence import IncidentJournal

    # /status also reads deploy.journal/metrics -- isolate them so this test
    # never depends on data/sentinel.db existing on disk.
    journal = IncidentJournal(tmp_path / "sentinel.db")
    journal.initialize()
    connectivity = ConnectivityManager(check_fn=lambda: (True, 10.0))
    connectivity.check_once()
    monkeypatch.setattr(deploy, "journal", journal)
    monkeypatch.setattr(deploy, "connectivity", connectivity)
    monkeypatch.setattr(deploy, "metrics", ContinuityMetrics(journal, connectivity))

    monkeypatch.setattr(deploy, "_source_mode", SourceMode.SIMULATION)
    monkeypatch.setattr(deploy, "_source_value", None)
    original_runtime = deploy.runtime
    try:
        client = deploy.app.test_client()
        before = client.get("/status").get_json()
        assert before["operating_mode"] == "REALITY"

        deploy.switch_to_simulation(tiny_video)
        after = client.get("/status").get_json()
        assert after["operating_mode"] == "SIMULATION"
        assert after["simulation_source_name"] == "scenario.mp4"
    finally:
        deploy.runtime.stop()
        monkeypatch.setattr(deploy, "runtime", original_runtime)
        monkeypatch.setattr(deploy, "_operating_mode", "REALITY")


# ----------------------------------------------------------------------
# Simulation runs the real pipeline: no hard-coded results.
# ----------------------------------------------------------------------
def test_uploaded_video_is_processed_through_the_real_pipeline_not_faked(tiny_video):
    runtime = SentinelRuntime(
        FrameSource(SourceMode.VIDEO, tiny_video),
        _OnePersonDetector(),
        RuntimeConfig(calibration_samples=1),
    )
    assert runtime.source.start() is True
    try:
        first = runtime.process_once()
        second = runtime.process_once()
        assert first is not None and second is not None
        assert first.source_mode == SourceMode.VIDEO
        # Frame ids must genuinely advance frame-by-frame from the file --
        # never a static/repeated value a fake pipeline might return.
        assert second.frame_id == first.frame_id + 1
        # people_count comes from the real (fake-but-honest) detector
        # actually running against each decoded frame, not a constant
        # baked into "simulation mode".
        assert first.people_count == 1
        assert first.occupancy_grid is not None
    finally:
        runtime.source.stop()


def test_simulation_video_source_mode_is_video_not_camera(tiny_video):
    """source_mode must clearly distinguish an uploaded scenario clip from
    a live webcam -- REALITY and SIMULATION are never mixed silently."""
    runtime = SentinelRuntime(
        FrameSource(SourceMode.VIDEO, tiny_video),
        _OnePersonDetector(),
        RuntimeConfig(calibration_samples=1),
    )
    runtime.source.start()
    try:
        snapshot = runtime.process_once()
        assert snapshot.source_mode == SourceMode.VIDEO
        assert snapshot.source_mode != SourceMode.CAMERA
    finally:
        runtime.source.stop()
