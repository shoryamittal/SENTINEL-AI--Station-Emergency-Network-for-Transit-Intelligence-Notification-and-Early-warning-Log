"""Tiled dense-scene person detection -- SIMULATION-ONLY, never wired into
Reality's detection path.

A single full-frame YOLO pass at production settings genuinely misses most
of a very dense, distant crowd (verified: 15/~50+ visible people on the
bundled competition clip's frame 43) -- not because of a bug anywhere in
the pipeline, but because the people are small in absolute pixel terms
relative to a full-frame inference size. This module recovers them by
running the *same* model, at the *same* confidence threshold, over
overlapping crops of the frame instead of the whole frame at once, so each
person occupies more of the model's input resolution.

This intentionally does not touch, subclass, wrap, or share any object
with ``src.detector.PersonDetector`` -- it loads its own model instance so
Reality's detector is provably unaffected by anything in this file.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import numpy as np

from .detector import Detection


@dataclass(frozen=True, slots=True)
class TileSpec:
    rows: int
    cols: int
    overlap: float  # fraction of tile width/height overlapped with neighbors


DEFAULT_TILE_SPEC = TileSpec(rows=2, cols=2, overlap=0.2)


def _tile_bounds(width: int, height: int, spec: TileSpec) -> list[tuple[int, int, int, int]]:
    """Return (x1, y1, x2, y2) pixel bounds for each overlapping tile."""
    tile_w = width / spec.cols
    tile_h = height / spec.rows
    overlap_x = tile_w * spec.overlap
    overlap_y = tile_h * spec.overlap
    bounds = []
    for row in range(spec.rows):
        for col in range(spec.cols):
            x1 = max(0, int(col * tile_w - overlap_x))
            y1 = max(0, int(row * tile_h - overlap_y))
            x2 = min(width, int((col + 1) * tile_w + overlap_x))
            y2 = min(height, int((row + 1) * tile_h + overlap_y))
            bounds.append((x1, y1, x2, y2))
    return bounds


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _merge_duplicates(boxes: list[Detection], iou_threshold: float = 0.4) -> list[Detection]:
    """Greedy confidence-ordered NMS across tile-overlap duplicates.

    Two tiles overlapping the same real person produce two boxes with high
    IoU in full-frame coordinates; this keeps only the higher-confidence one
    per cluster, exactly like standard single-pass NMS would within one
    frame -- it does not invent or discard people, only removes the extra
    detections a person's overlap region legitimately produces.
    """
    ordered = sorted(boxes, key=lambda d: d.confidence, reverse=True)
    kept: list[Detection] = []
    for candidate in ordered:
        if all(_iou(candidate.bbox, existing.bbox) <= iou_threshold for existing in kept):
            kept.append(candidate)
    return kept


class TiledPersonDetector:
    """Dense-scene detector: same model/class/confidence, tiled input.

    Deliberately duck-type-compatible with ``PersonDetector`` (a ``detect()``
    method returning ``(list[Detection], latency_ms)`` and a
    ``model_version`` attribute) so it can be dropped into
    ``SentinelRuntime`` without any change to runtime.py.
    """

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        confidence_threshold: float = 0.5,
        # Measured on the bundled competition clip's densest frame: 2x2
        # tiles at imgsz=1280 recovered more real people (51 vs. 15
        # standard full-frame) at *lower* latency than smaller-tile/
        # smaller-imgsz configurations (2x3@640, 3x3@640) -- fewer, larger
        # tiles beat more, smaller ones once per-tile model-call overhead
        # is accounted for.
        inference_size: int = 1280,
        tile_spec: TileSpec = DEFAULT_TILE_SPEC,
        merge_iou_threshold: float = 0.4,
    ):
        self.model_path = model_path
        self.confidence_threshold = confidence_threshold
        self.inference_size = inference_size
        self.tile_spec = tile_spec
        self.merge_iou_threshold = merge_iou_threshold
        self._model = None
        self.model_version = Path(model_path).name + f" (tiled {tile_spec.rows}x{tile_spec.cols})"

    def _load(self) -> None:
        if self._model is None:
            from ultralytics import YOLO

            self._model = YOLO(self.model_path)

    def detect(self, frame: np.ndarray) -> tuple[list[Detection], float]:
        started = time.perf_counter()
        self._load()
        height, width = frame.shape[:2]
        all_detections: list[Detection] = []
        for x1, y1, x2, y2 in _tile_bounds(width, height, self.tile_spec):
            tile = frame[y1:y2, x1:x2]
            if tile.size == 0:
                continue
            results = self._model.predict(
                tile, classes=[0], conf=self.confidence_threshold, imgsz=self.inference_size, verbose=False
            )
            for result in results:
                for box in result.boxes:
                    bx1, by1, bx2, by2 = (float(v) for v in box.xyxy[0].tolist())
                    # Map tile-local coordinates back to full-frame coordinates.
                    gx1, gy1, gx2, gy2 = bx1 + x1, by1 + y1, bx2 + x1, by2 + y1
                    all_detections.append(
                        Detection((gx1, gy1, gx2, gy2), ((gx1 + gx2) / 2, (gy1 + gy2) / 2), float(box.conf[0]))
                    )
        merged = _merge_duplicates(all_detections, self.merge_iou_threshold)
        return merged, (time.perf_counter() - started) * 1000
