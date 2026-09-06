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
    def __init__(self, model_path: str = "yolov8n.pt", confidence_threshold: float = 0.5, inference_size: int = 960):
        self.model_path, self.confidence_threshold = model_path, confidence_threshold
        # Input resolution given to the model, not a confidence/safety
        # threshold: Ultralytics' default (~640) downscales frames enough
        # that small/distant people in a dense crowd go undetected even
        # though the same model at the same confidence would find them at a
        # larger input size. 960 was chosen by measurement as the point that
        # meaningfully recovers recall (yolov8n.pt: ~5->~14 detections on a
        # dense platform frame) while staying real-time (~70-90ms/frame on
        # this machine) for the live camera.
        self.inference_size = inference_size
        self._model = None
        self.model_version = Path(model_path).name

    def _load(self) -> None:
        if self._model is None:
            from ultralytics import YOLO
            self._model = YOLO(self.model_path)

    @staticmethod
    def _suppress_nested_and_duplicate_boxes(detections: list[Detection]) -> list[Detection]:
        """Suppress nested torso/patch false-positives and duplicate detections.

        When a person is close to the camera, YOLO frequently detects the full body
        as well as smaller sub-parts (torso, clothing patch, chest). Ultralytics NMS
        fails to suppress these because the smaller box has low IoU relative to the
        larger union. Filtering by Intersection-over-Smaller (IoS) containment
        eliminates phantom double-counts while preserving legitimate adjacent people.
        """
        if len(detections) <= 1:
            return detections
        # Sort by confidence descending
        sorted_dets = sorted(detections, key=lambda d: d.confidence, reverse=True)
        kept: list[Detection] = []
        for d in sorted_dets:
            x1, y1, x2, y2 = d.bbox
            area = (x2 - x1) * (y2 - y1)
            suppressed = False
            for k in kept:
                kx1, ky1, kx2, ky2 = k.bbox
                karea = (kx2 - kx1) * (ky2 - ky1)
                ix1, iy1 = max(x1, kx1), max(y1, ky1)
                ix2, iy2 = min(x2, kx2), min(y2, ky2)
                iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
                iarea = iw * ih
                if iarea > 0:
                    union = area + karea - iarea
                    iou = iarea / union if union > 0 else 0.0
                    min_area = min(area, karea)
                    ios = iarea / min_area if min_area > 0 else 0.0
                    # Suppress if standard high overlap (IoU > 0.55) or if smaller box is
                    # heavily contained inside a larger box (IoS > 0.65 and area < karea * 0.70)
                    if iou > 0.55 or (ios > 0.65 and area < karea * 0.70):
                        suppressed = True
                        break
            if not suppressed:
                kept.append(d)
        return kept

    def detect(self, frame: np.ndarray) -> tuple[list[Detection], float]:
        started = time.perf_counter()
        self._load()
        results = self._model.predict(
            frame, classes=[0], conf=self.confidence_threshold, imgsz=self.inference_size, verbose=False
        )
        raw_detections: list[Detection] = []
        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
                raw_detections.append(Detection((x1, y1, x2, y2), ((x1+x2)/2, (y1+y2)/2), float(box.conf[0])))
        detections = self._suppress_nested_and_duplicate_boxes(raw_detections)
        return detections, (time.perf_counter() - started) * 1000

