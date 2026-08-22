"""Relative 4x6 spatial occupancy mapping; not calibrated density estimation."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .detector import Detection

@dataclass(frozen=True, slots=True)
class OccupancyResult:
    grid: tuple[tuple[int, ...], ...]
    people_count: int
    occupancy_index: float
    hotspot_zone: str | None
    zone_shares: tuple[tuple[float, ...], ...]

class OccupancyGrid:
    def __init__(self, rows: int = 4, cols: int = 6):
        self.rows, self.cols = rows, cols

    def map(self, detections: list[Detection], frame_shape: tuple[int, ...]) -> OccupancyResult:
        height, width = frame_shape[:2]
        counts = np.zeros((self.rows, self.cols), dtype=int)
        for detection in detections:
            x, y = detection.centroid
            row = min(self.rows - 1, max(0, int(y * self.rows / max(height, 1))))
            col = min(self.cols - 1, max(0, int(x * self.cols / max(width, 1))))
            counts[row, col] += 1
        total = int(counts.sum())
        hottest = int(np.argmax(counts))
        hotspot = None if total == 0 else f"r{hottest // self.cols}c{hottest % self.cols}"
        shares = counts / total if total else np.zeros_like(counts, dtype=float)
        return OccupancyResult(tuple(tuple(int(v) for v in row) for row in counts), total,
            float(np.count_nonzero(counts) / counts.size), hotspot,
            tuple(tuple(float(v) for v in row) for row in shares))
