"""Local camera, video, and deterministic simulation inputs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from pathlib import Path
from threading import Lock
from typing import Callable

import numpy as np

from .contracts import CameraHealth, SourceMode


@dataclass(slots=True)
class FramePacket:
    frame_id: int
    capture_timestamp_utc: datetime
    source_mode: SourceMode
    frame: np.ndarray


class FrameSource:
    def __init__(self, source_mode: SourceMode, source: int | str | None = None,
                 simulation_factory: Callable[[], np.ndarray] | None = None,
                 stale_frame_age_s: float = 2.0, camera_failure_timeout_s: float = 5.0):
        self.source_mode, self.source = source_mode, source
        self.simulation_factory = simulation_factory
        self.stale_frame_age_s, self.camera_failure_timeout_s = stale_frame_age_s, camera_failure_timeout_s
        self._capture = None
        self._frame_id = 0
        self._last_success_mono: float | None = None
        self._recovering = False
        self._latest_frame_lock = Lock()
        self._latest_frame: np.ndarray | None = None

    def start(self) -> bool:
        if self.source_mode is SourceMode.SIMULATION:
            self._recovering = False
            return True
        # Import only for real capture so deterministic/offline tests do not
        # require a host OpenCV GUI library.
        import cv2
        self._capture = cv2.VideoCapture(self.source if self.source is not None else 0)
        self._recovering = bool(not self._capture.isOpened())
        return not self._recovering

    def read(self) -> FramePacket | None:
        if self.source_mode is SourceMode.SIMULATION:
            frame = self.simulation_factory() if self.simulation_factory else np.zeros((480, 640, 3), dtype=np.uint8)
            with self._latest_frame_lock:
                self._latest_frame = frame.copy()
            self._frame_id += 1
            self._last_success_mono = time.monotonic()
            return FramePacket(self._frame_id, datetime.now(timezone.utc), self.source_mode, frame)
        if self._capture is None:
            return None
        ok, frame = self._capture.read()
        if not ok or frame is None:
            self._recovering = True
            return None
        self._recovering = False
        with self._latest_frame_lock:
            self._latest_frame = frame.copy()
        self._frame_id += 1
        self._last_success_mono = time.monotonic()
        return FramePacket(self._frame_id, datetime.now(timezone.utc), self.source_mode, frame)

    def get_latest_frame(self) -> np.ndarray | None:
        """Return a defensive copy of the latest successful frame, if any.

        This accessor is for local visualization only; capture ownership and
        all reads remain inside the runtime's single ``FrameSource``.
        """
        with self._latest_frame_lock:
            return None if self._latest_frame is None else self._latest_frame.copy()

    def health(self) -> CameraHealth:
        if self._recovering:
            return CameraHealth.INPUT_RECOVERING
        if self._last_success_mono is None:
            return CameraHealth.CAMERA_LOST
        age = time.monotonic() - self._last_success_mono
        if age > self.camera_failure_timeout_s:
            return CameraHealth.CAMERA_LOST
        if age > self.stale_frame_age_s:
            return CameraHealth.STALE
        return CameraHealth.LIVE

    def stop(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
