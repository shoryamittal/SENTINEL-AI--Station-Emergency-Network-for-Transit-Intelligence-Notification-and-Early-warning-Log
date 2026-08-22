"""Camera capture/preview decoupling tests.

No real webcam is used: cv2.VideoCapture is monkeypatched to a deterministic
in-memory fake, so these tests never touch hardware and never depend on an
exact millisecond timing -- they poll with a generous deadline instead.
"""
from __future__ import annotations

import threading
import time

import cv2
import numpy as np

from src.camera import FrameSource
from src.contracts import CameraHealth, SourceMode


class _FakeCapture:
    """Deterministic stand-in for cv2.VideoCapture. No real camera hardware."""

    def __init__(self, *_args, **_kwargs):
        self._opened = True
        self._frame_count = 0
        self._lock = threading.Lock()

    def isOpened(self):
        return self._opened

    def read(self):
        with self._lock:
            self._frame_count += 1
            value = self._frame_count % 255
        return True, np.full((4, 4, 3), value, dtype=np.uint8)

    def release(self):
        self._opened = False


class _StallingCapture(_FakeCapture):
    """Simulates a camera that stops delivering frames entirely."""

    def read(self):
        return False, None


def _wait_until(predicate, timeout_s: float = 2.0, interval_s: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


def test_background_capture_advances_frame_id_without_any_consumer(monkeypatch):
    """Frame capture must not need read() to be called at all to make progress."""
    monkeypatch.setattr(cv2, "VideoCapture", _FakeCapture)
    source = FrameSource(SourceMode.CAMERA, source=0)
    assert source.start() is True
    try:
        assert _wait_until(lambda: source.get_latest_frame_id() >= 5)
    finally:
        source.stop()


def test_latest_frame_is_available_without_waiting_for_a_consumer(monkeypatch):
    monkeypatch.setattr(cv2, "VideoCapture", _FakeCapture)
    source = FrameSource(SourceMode.CAMERA, source=0)
    assert source.start() is True
    try:
        assert _wait_until(lambda: source.get_latest_frame() is not None)
        frame = source.get_latest_frame()
        assert frame is not None
        assert frame.shape == (4, 4, 3)
    finally:
        source.stop()


def test_slow_consumer_does_not_block_capture_progress(monkeypatch):
    """A consumer that never calls read() must not stall the capture thread
    -- this is the property that keeps camera preview freshness independent
    of however long inference takes."""
    monkeypatch.setattr(cv2, "VideoCapture", _FakeCapture)
    source = FrameSource(SourceMode.CAMERA, source=0)
    assert source.start() is True
    try:
        assert _wait_until(lambda: source.get_latest_frame_id() >= 1)
        first_id = source.get_latest_frame_id()
        assert _wait_until(lambda: source.get_latest_frame_id() > first_id)
    finally:
        source.stop()


def test_read_never_hands_back_the_same_frame_twice(monkeypatch):
    """Inference must see each captured frame at most once -- no reprocessing,
    no backlog of stale frames."""
    monkeypatch.setattr(cv2, "VideoCapture", _FakeCapture)
    source = FrameSource(SourceMode.CAMERA, source=0)
    assert source.start() is True
    try:
        assert _wait_until(lambda: source.get_latest_frame_id() >= 1)
        first_packet = source.read()
        assert first_packet is not None
        # No new frame has necessarily arrived in the instant between these
        # two calls being scheduled back-to-back is not guaranteed either
        # way -- but read() must never return the exact same frame_id twice.
        second_packet = source.read()
        if second_packet is not None:
            assert second_packet.frame_id != first_packet.frame_id
    finally:
        source.stop()


def test_read_skips_ahead_to_the_newest_frame_after_a_slow_consumer(monkeypatch):
    monkeypatch.setattr(cv2, "VideoCapture", _FakeCapture)
    source = FrameSource(SourceMode.CAMERA, source=0)
    assert source.start() is True
    try:
        # Let many frames accumulate in the background before ever calling
        # read() -- simulating a consumer busy with slow inference.
        assert _wait_until(lambda: source.get_latest_frame_id() >= 10)
        packet = source.read()
        assert packet is not None
        assert packet.frame_id >= 10  # got the newest frame, not frame #1
    finally:
        source.stop()


def test_only_one_capture_owner_stop_releases_cleanly(monkeypatch):
    monkeypatch.setattr(cv2, "VideoCapture", _FakeCapture)
    source = FrameSource(SourceMode.CAMERA, source=0)
    assert source.start() is True
    assert _wait_until(lambda: source.get_latest_frame_id() >= 1)
    source.stop()
    assert source._capture is None
    assert source._capture_thread is None


def test_stale_and_camera_lost_detection_still_works(monkeypatch):
    """Existing safety behavior must survive: a stalled camera is reported
    as recovering/lost, never silently treated as LIVE."""
    monkeypatch.setattr(cv2, "VideoCapture", _StallingCapture)
    source = FrameSource(
        SourceMode.CAMERA, source=0, stale_frame_age_s=0.02, camera_failure_timeout_s=0.05
    )
    assert source.start() is True
    try:
        assert _wait_until(lambda: source.health() != CameraHealth.LIVE, timeout_s=1.0)
        assert source.health() in (CameraHealth.INPUT_RECOVERING, CameraHealth.CAMERA_LOST)
        assert source.get_latest_frame() is None
    finally:
        source.stop()


def test_simulation_mode_is_unchanged_synchronous_behavior():
    """SIMULATION source must keep its original, deterministic, no-thread
    behavior -- every read() call produces exactly one new frame."""
    source = FrameSource(
        SourceMode.SIMULATION,
        simulation_factory=lambda: np.zeros((10, 10, 3), dtype=np.uint8),
    )
    assert source.start() is True
    first = source.read()
    second = source.read()
    assert first.frame_id == 1
    assert second.frame_id == 2
    assert source._capture_thread is None
