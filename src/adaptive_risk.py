"""Interpretable L/A/R signals over the occupancy grid."""
from __future__ import annotations
from collections import deque
import numpy as np

class AdaptiveRisk:
    def __init__(self, baseline_floor: float = 2.0, window: int = 6, redistribution_window: int = 4):
        self.baseline_floor, self.window, self.redistribution_window = baseline_floor, window, redistribution_window
        self._counts = deque(maxlen=window); self._shares = deque(maxlen=redistribution_window)
    def update(self, grid, baseline=None):
        current = np.asarray(grid, dtype=float); total = current.sum()
        self._counts.append(total); self._shares.append(current.ravel()/total if total else np.zeros(current.size))
        if baseline is None: load = 0.0
        else:
            b = np.asarray(baseline, dtype=float)
            meaningful = current >= self.baseline_floor
            ratios = np.clip((current - b) / np.maximum(b, self.baseline_floor), 0, 3)
            load = float(np.percentile(ratios[meaningful], 80) / 3) if np.any(meaningful) else 0.0
        accumulation = 0.0
        if len(self._counts) >= 4:
            slope = np.polyfit(np.arange(len(self._counts)), list(self._counts), 1)[0]
            accumulation = float(np.clip(slope / max(np.median(self._counts), 5), 0, 1))
        redistribution = 0.0
        if len(self._shares) >= self.redistribution_window and total >= 4:
            redistribution = float(0.5 * np.abs(self._shares[-1] - self._shares[0]).sum())
        return load, accumulation, redistribution
