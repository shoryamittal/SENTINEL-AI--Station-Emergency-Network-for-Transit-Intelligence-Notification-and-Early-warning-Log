"""Camera / video source capture wrapper with FPS throttling."""
# DEPRECATED: This module is superseded by the Round-2 pipeline in src/. Retained for reference only.
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Union

import cv2
import numpy as np


class CameraFeed:
    """Thin wrapper around cv2.VideoCapture with consistent start/read/release API.

    Supports:
      - integer camera index (0, 1, ...) for live webcam feeds
      - filepath string for pre-recorded video files
      - FPS throttling so reads stay near the target rate
    """

    def __init__(
        self,
        source: Union[int, str, Path] = 0,
        fps: int = 30,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> None:
        if isinstance(source, Path):
            source = str(source)
        self.source = source
        self.target_fps = max(1, int(fps))
        self._frame_interval = 1.0 / self.target_fps
        self.width = width
        self.height = height

        self._cap: Optional[cv2.VideoCapture] = None
        self._last_read_ts: float = 0.0
        self._is_running: bool = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self) -> bool:
        """Open the video source. Returns True on success."""
        if self._is_running:
            return True

        try:
            if isinstance(self.source, str) and Path(self.source).exists():
                self._cap = cv2.VideoCapture(self.source)
            else:
                self._cap = cv2.VideoCapture(self.source, cv2.CAP_ANY)
        except Exception:
            self._cap = None
            return False

        if not self._cap or not self._cap.isOpened():
            self._cap = None
            return False

        if self.width is not None:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        if self.height is not None:
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

        self._is_running = True
        self._last_read_ts = 0.0
        return True

    def read_frame(self) -> tuple[bool, Optional[np.ndarray]]:
        """Read the next frame, throttled to the target FPS.

        Returns (success_flag, frame_bgr_array_or_None).
        """
        if not self._is_running or self._cap is None:
            return False, None

        now = time.monotonic()
        elapsed = now - self._last_read_ts
        if elapsed < self._frame_interval:
            time.sleep(self._frame_interval - elapsed)
            now = time.monotonic()

        ok, frame = self._cap.read()
        self._last_read_ts = now
        if not ok or frame is None:
            return False, None
        return True, frame

    def release(self) -> None:
        """Close the video source cleanly."""
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._is_running = False

    def __enter__(self) -> "CameraFeed":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
