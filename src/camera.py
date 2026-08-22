"""Local camera, video, and deterministic simulation inputs.

Live CAMERA capture runs on its own background thread, decoupled from
however slow inference/risk processing is. Capture and inference share
exactly one ``cv2.VideoCapture`` handle (owned here); inference and the
browser preview both read the *latest* captured frame, never a queue of
stale ones, and never call ``VideoCapture.read()`` themselves. This is what
keeps the camera preview and reported frame/camera-health freshness
accurate even while a single inference pass takes seconds: the background
thread keeps grabbing new frames the whole time, so whichever frame
inference (or the preview) picks up next was captured moments ago, not
whenever the last inference cycle happened to start.

VIDEO (pre-recorded file) playback intentionally keeps the original
synchronous, in-order, one-frame-per-``read()`` behavior: skipping ahead
through a saved clip to "keep up" would silently discard footage that was
never analyzed, which is the wrong tradeoff for offline file analysis (it
is only ever the right tradeoff for a live camera, where only "now"
matters).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time
from pathlib import Path
from threading import Event, Lock, Thread
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
        self._latest_packet: FramePacket | None = None
        self._consumed_frame_id = 0

        # Background capture thread (CAMERA mode only -- see module docstring).
        self._capture_thread: Thread | None = None
        self._capture_stop = Event()

    def start(self) -> bool:
        if self.source_mode is SourceMode.SIMULATION:
            self._recovering = False
            return True
        # Import only for real capture so deterministic/offline tests do not
        # require a host OpenCV GUI library.
        import cv2
        self._capture = cv2.VideoCapture(self.source if self.source is not None else 0)
        opened = bool(self._capture.isOpened())
        self._recovering = not opened

        if opened and self.source_mode is SourceMode.CAMERA:
            self._capture_stop.clear()
            self._capture_thread = Thread(target=self._capture_loop, daemon=True)
            self._capture_thread.start()

        # "Started" means "the device handle was acquired" -- not affected
        # by the background thread's first read (a device that opens but
        # immediately fails to deliver frames is INPUT_RECOVERING/
        # CAMERA_LOST via health(), not a failed start()).
        return opened

    def _capture_loop(self) -> None:
        """Continuously grab frames as fast as the camera provides them.

        Runs independently of whatever consumes frames via ``read()`` for
        inference, so a slow inference pass never throttles capture, and a
        stalled/disconnected camera is detected on its own timeline instead
        of only being noticed the next time inference happens to ask.
        """
        while not self._capture_stop.is_set():
            ok, frame = self._capture.read()
            if not ok or frame is None:
                self._recovering = True
                time.sleep(0.05)
                continue
            self._recovering = False
            self._frame_id += 1
            packet = FramePacket(self._frame_id, datetime.now(timezone.utc), self.source_mode, frame)
            with self._latest_frame_lock:
                self._latest_frame = frame.copy()
                self._latest_packet = packet
            self._last_success_mono = time.monotonic()

    def read(self) -> FramePacket | None:
        """Return the newest not-yet-consumed frame for inference, or None.

        CAMERA mode: never blocks and never re-reads the same frame twice --
        it hands back whatever the background capture thread most recently
        grabbed, skipping any frames captured in between (skipping is the
        correct tradeoff for a live feed; see module docstring).
        """
        if self.source_mode is SourceMode.SIMULATION:
            frame = self.simulation_factory() if self.simulation_factory else np.zeros((480, 640, 3), dtype=np.uint8)
            with self._latest_frame_lock:
                self._latest_frame = frame.copy()
            self._frame_id += 1
            self._last_success_mono = time.monotonic()
            return FramePacket(self._frame_id, datetime.now(timezone.utc), self.source_mode, frame)

        if self.source_mode is SourceMode.CAMERA:
            with self._latest_frame_lock:
                packet = self._latest_packet
            if packet is None or packet.frame_id == self._consumed_frame_id:
                return None
            self._consumed_frame_id = packet.frame_id
            return packet

        # VIDEO: original synchronous, in-order, one-frame-per-call behavior.
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

    def get_latest_frame_id(self) -> int:
        """Monotonically increasing id of the most recently captured frame.

        Cheap to poll frequently (e.g. by a preview stream) to detect a new
        frame without re-encoding an unchanged one.
        """
        with self._latest_frame_lock:
            return self._latest_packet.frame_id if self._latest_packet is not None else self._frame_id

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
        self._capture_stop.set()
        if self._capture_thread is not None:
            self._capture_thread.join(timeout=2)
            self._capture_thread = None
        if self._capture is not None:
            self._capture.release()
            self._capture = None
