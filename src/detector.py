"""YOLO person detection with no Ultralytics objects crossing this boundary."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import numpy as np


@dataclass(frozen=True, slots=True)
class Detection:
    bbox: tuple[float, float, float, float]
    centroid: tuple[float, float]
    confidence: float


class PersonDetector:
    def __init__(self, model_path: str = "yolov8n.pt", confidence_threshold: float = 0.5):
        self.model_path, self.confidence_threshold = model_path, confidence_threshold
        self._model = None
        self.model_version = Path(model_path).name

    def _load(self) -> None:
        if self._model is None:
            from ultralytics import YOLO
            self._model = YOLO(self.model_path)

    def detect(self, frame: np.ndarray) -> tuple[list[Detection], float]:
        started = time.perf_counter()
        self._load()
        results = self._model.predict(frame, classes=[0], conf=self.confidence_threshold, verbose=False)
        detections: list[Detection] = []
        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
                detections.append(Detection((x1, y1, x2, y2), ((x1+x2)/2, (y1+y2)/2), float(box.conf[0])))
        return detections, (time.perf_counter() - started) * 1000
