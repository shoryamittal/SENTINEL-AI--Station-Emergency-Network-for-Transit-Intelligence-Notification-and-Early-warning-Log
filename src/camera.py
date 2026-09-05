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
import platform
import sys
import time
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Callable, Optional

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
                 stale_frame_age_s: float = 2.0, camera_failure_timeout_s: float = 5.0,
                 width: Optional[int] = None, height: Optional[int] = None,
                 target_fps: int = 30, brightness: Optional[int] = None,
                 contrast: Optional[int] = None, exposure: Optional[int] = None):
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
        self.width = width
        self.height = height
        self.target_fps = max(1, int(target_fps))
        self.brightness = brightness
        self.contrast = contrast
        self.exposure = exposure
        self._restart_count = 0
        self._last_restart_mono: float | None = None

        self._capture_thread: Thread | None = None
        self._capture_stop = Event()

    @staticmethod
    def list_camera_devices(max_index: int = 5) -> list[dict]:
        """Probe available camera devices and return a list of {index, name, available, resolution, fps}."""
        import cv2
        devices = []
        backends_to_try = []
        if platform.system() == "Windows":
            backends_to_try.append(("DSHOW", cv2.CAP_DSHOW))
        backends_to_try.append(("ANY", cv2.CAP_ANY))
        for idx in range(max_index):
            entry = {"index": idx, "available": False, "backend": None, "width": None, "height": None, "fps": None}
            cap = None
            for backend_name, backend_id in backends_to_try:
                try:
                    cap = cv2.VideoCapture(idx, backend_id)
                except Exception:
                    continue
                if cap and cap.isOpened():
                    ok, _ = cap.read()
                    if ok:
                        entry["available"] = True
                        entry["backend"] = backend_name
                        entry["width"] = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                        entry["height"] = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                        entry["fps"] = round(cap.get(cv2.CAP_PROP_FPS), 1) if cap.get(cv2.CAP_PROP_FPS) else None
                        break
            if cap is not None:
                cap.release()
            if entry["available"]:
                devices.append(entry)
        return devices

    @staticmethod
    def _preprocess_frame(frame: np.ndarray, brightness: Optional[int] = None,
                          contrast: Optional[int] = None, exposure: Optional[int] = None) -> np.ndarray:
        """Preserve original camera quality. Only applies soft adjustments when explicitly requested.

        No CLAHE histogram equalization (it amplifies noise and degrades natural colors).
        No HSV round-trip conversion (introduces color banding artifacts).
        Brightness/contrast only applied if non-zero values are explicitly set.
        """
        import cv2
        out = frame
        if brightness is not None and brightness != 0:
            beta = float(brightness)
            try:
                out = cv2.convertScaleAbs(out, alpha=1.0, beta=max(-100.0, min(100.0, beta)))
            except Exception:
                out = frame
        if contrast is not None and contrast != 0:
            alpha = (contrast + 100.0) / 100.0
            try:
                out = cv2.convertScaleAbs(out, alpha=max(0.5, min(3.0, alpha)), beta=0.0)
            except Exception:
                pass
        return out

    def _apply_capture_properties(self) -> None:
        """Apply resolution, fps, brightness, contrast, exposure props to the active capture handle.

        Only touches hardware controls when the user explicitly set them -- otherwise
        leaves the camera's factory defaults (auto-exposure, auto-white-balance) alone,
        which produces far better image quality than any arbitrary forced values.
        """
        import cv2
        if self._capture is None or not self._capture.isOpened():
            return
        if self.width is not None:
            self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, int(self.width))
        if self.height is not None:
            self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.height))
        try:
            self._capture.set(cv2.CAP_PROP_FPS, float(self.target_fps))
        except Exception:
            pass
        try:
            self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        if self.brightness is not None:
            try: self._capture.set(cv2.CAP_PROP_BRIGHTNESS, float(max(-100, min(100, self.brightness))))
            except Exception: pass
        if self.contrast is not None:
            try: self._capture.set(cv2.CAP_PROP_CONTRAST, float(max(-100, min(100, self.contrast))))
            except Exception: pass
        if self.exposure is not None:
            try: self._capture.set(cv2.CAP_PROP_EXPOSURE, float(self.exposure))
            except Exception: pass
            try: self._capture.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
            except Exception: pass

    def _flush_buffers(self, frames_to_drain: int = 5) -> None:
        """Discard buffered frames so the next read() is the real "now".

        Cheap and effective: USB webcams and most DSHOW backends buffer 3-10
        frames; draining a few at start / after stalls guarantees that the
        inference preview is never looking at a stale buffered frame.
        """
        if self._capture is None or not self._capture.isOpened():
            return
        for _ in range(max(1, frames_to_drain)):
            ok, _ = self._capture.read()
            if not ok:
                break

    def start(self) -> bool:
        if self.source_mode is SourceMode.SIMULATION:
            self._recovering = False
            return True
        import cv2
        source = self.source if self.source is not None else 0
        backends = []
        if platform.system() == "Windows" and isinstance(source, int):
            backends.append(cv2.CAP_DSHOW)
        backends.append(cv2.CAP_ANY)
        opened = False
        last_exc = None
        for backend in backends:
            try:
                cap = cv2.VideoCapture(source, backend)
            except Exception as exc:
                last_exc = exc
                continue
            if cap and cap.isOpened():
                self._capture = cap
                opened = True
                break
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass
        if not opened:
            self._capture = None
            self._recovering = True
            return False

        self._apply_capture_properties()
        if self.source_mode is SourceMode.VIDEO:
            try:
                import cv2
                self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            except Exception:
                pass
        if self.source_mode is SourceMode.CAMERA:
            self._flush_buffers(8)
        self._restart_count += 1
        self._last_restart_mono = time.monotonic()
        self._recovering = False

        if opened and self.source_mode is SourceMode.CAMERA:
            self._capture_stop.clear()
            if self._capture_thread is None or not self._capture_thread.is_alive():
                self._capture_thread = Thread(target=self._capture_loop, daemon=True)
                self._capture_thread.start()
        return True

    def _capture_loop(self) -> None:
        """Continuously grab frames as fast as the camera provides them.

        Runs independently of whatever consumes frames via ``read()`` for
        inference, so a slow inference pass never throttles capture, and a
        stalled/disconnected camera is detected on its own timeline instead
        of only being noticed the next time inference happens to ask.

        Adds robust auto-recovery:
          * consecutive failed reads -> sleep with backoff
          * prolonged failure -> tear down and re-open the VideoCapture handle
          * after recovery -> flush buffers so the next frame is truly "now"
          * per-frame preprocessing (CLAHE/brightness/contrast) so downstream
            inference and the preview see a more readable frame on low-light
            or high-contrast cameras.
        """
        import cv2
        consecutive_failures = 0
        _min_frame_interval = 1.0 / max(1, self.target_fps)
        _last_frame_mono = 0.0
        while not self._capture_stop.is_set():
            if self._capture is None:
                self._recovering = True
                consecutive_failures += 1
                backoff = min(2.0, 0.1 * consecutive_failures)
                time.sleep(backoff)
                if consecutive_failures >= 5:
                    consecutive_failures = 0
                    self._try_restart_capture()
                continue

            ok, frame = self._capture.read()
            if not ok or frame is None:
                self._recovering = True
                consecutive_failures += 1
                if consecutive_failures >= 15:
                    consecutive_failures = 0
                    self._try_restart_capture()
                    time.sleep(0.3)
                else:
                    time.sleep(0.05 + min(0.2, consecutive_failures * 0.01))
                continue

            if consecutive_failures > 0:
                self._flush_buffers(6)
            consecutive_failures = 0
            self._recovering = False

            try:
                frame = FrameSource._preprocess_frame(frame, self.brightness, self.contrast, self.exposure)
            except Exception:
                pass

            now = time.monotonic()
            delta = now - _last_frame_mono
            if delta < _min_frame_interval:
                time.sleep(_min_frame_interval - delta)
                now = time.monotonic()
            _last_frame_mono = now

            self._frame_id += 1
            packet = FramePacket(self._frame_id, datetime.now(timezone.utc), self.source_mode, frame)
            with self._latest_frame_lock:
                self._latest_frame = frame.copy()
                self._latest_packet = packet
            self._last_success_mono = now

    def _try_restart_capture(self) -> bool:
        """Idempotent emergency restart for a live camera that stopped delivering frames.

        Called from the capture thread on its own schedule (never from the
        main runtime) so the main thread never blocks behind a slow or hung
        VideoCapture open/release.
        """
        if self.source_mode is not SourceMode.CAMERA:
            return False
        import cv2
        try:
            if self._capture is not None:
                try: self._capture.release()
                except Exception: pass
                self._capture = None
        except Exception:
            self._capture = None
        source = self.source if self.source is not None else 0
        backends = []
        if platform.system() == "Windows" and isinstance(source, int):
            backends.append(cv2.CAP_DSHOW)
        backends.append(cv2.CAP_ANY)
        opened = False
        for backend in backends:
            try:
                cap = cv2.VideoCapture(source, backend)
            except Exception:
                continue
            if cap and cap.isOpened():
                self._capture = cap
                opened = True
                break
            try:
                if cap is not None:
                    cap.release()
            except Exception:
                pass
        if not opened:
            return False
        self._apply_capture_properties()
        self._flush_buffers(8)
        self._restart_count += 1
        self._last_restart_mono = time.monotonic()
        return True

    def reconfigure(self, *, width: Optional[int] = None, height: Optional[int] = None,
                    target_fps: Optional[int] = None, brightness: Optional[int] = None,
                    contrast: Optional[int] = None, exposure: Optional[int] = None,
                    source: Optional[int | str] = None) -> bool:
        """Reconfigure capture settings and restart the live CAMERA source.

        Returns True if the restart succeeded. Safe to call from an API handler:
        the capture thread notices the new source/properties on the next loop.
        """
        if self.source_mode is not SourceMode.CAMERA:
            return False
        self.stop()
        if source is not None:
            self.source = source
        if width is not None: self.width = width
        if height is not None: self.height = height
        if target_fps is not None: self.target_fps = max(1, int(target_fps))
        if brightness is not None: self.brightness = brightness
        if contrast is not None: self.contrast = contrast
        if exposure is not None: self.exposure = exposure
        return self.start()

    def get_settings(self) -> dict:
        """Return the currently configured capture settings for the dashboard."""
        import cv2
        actual = {"width": self.width, "height": self.height, "target_fps": self.target_fps,
                  "brightness": self.brightness, "contrast": self.contrast,
                  "exposure": self.exposure, "restart_count": self._restart_count,
                  "last_restart_seconds_ago": None,
                  "backend": None, "source": self.source}
        if self._last_restart_mono is not None:
            actual["last_restart_seconds_ago"] = round(time.monotonic() - self._last_restart_mono, 2)
        if self._capture is not None and self._capture.isOpened():
            try:
                actual["width"] = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH)) or self.width
                actual["height"] = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT)) or self.height
                actual["backend"] = platform.system() if isinstance(self.source, int) else "FILE"
            except Exception:
                pass
        return actual

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
