"""In-memory robust baseline that never adapts while abnormality is active."""
from __future__ import annotations
from collections import deque
import numpy as np
from .contracts import BaselineState

class AdaptiveBaseline:
    def __init__(self, calibration_samples: int = 12, recovery_samples: int = 6):
        self.calibration_samples, self.recovery_samples = calibration_samples, recovery_samples
        self._history = deque(maxlen=120); self._state = BaselineState.UNINITIALIZED; self._recoveries = 0
    @property
    def state(self): return self._state
    def values(self):
        return None if len(self._history) < self.calibration_samples else np.median(np.stack(self._history), axis=0)
    def update(self, grid, abnormal: bool = False, scene_changed: bool = False):
        values = np.asarray(grid, dtype=float)
        if scene_changed:
            self._history.clear(); self._state = BaselineState.CALIBRATING; self._recoveries = 0
        if self._state is BaselineState.UNINITIALIZED: self._state = BaselineState.CALIBRATING
        if abnormal:
            self._state = BaselineState.FROZEN; self._recoveries = 0; return self.values()
        if self._state is BaselineState.FROZEN:
            self._state = BaselineState.RECOVERING
        if self._state is BaselineState.RECOVERING:
            self._recoveries += 1
            if self._recoveries < self.recovery_samples: return self.values()
            self._state = BaselineState.ACTIVE
        self._history.append(values.copy())
        if len(self._history) >= self.calibration_samples: self._state = BaselineState.ACTIVE
        return self.values()
