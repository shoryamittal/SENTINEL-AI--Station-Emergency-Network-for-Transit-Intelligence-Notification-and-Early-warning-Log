"""Dense-scene tiled detector -- SIMULATION-only, never touches Reality.

Uses the real bundled competition clip and the real yolov8n model at
production confidence, because the property under test (does tiling
recover more real people without fabricating/duplicating any) can only be
observed against real dense video content -- a synthetic fixture would
prove nothing here.
"""
from __future__ import annotations

import cv2
import pytest

from src.dense_detector import TiledPersonDetector, TileSpec, _iou, _merge_duplicates
from src.detector import Detection


VIDEO_PATH = "data/demo/crowd_station.mp4"


def _decode_frame(index: int):
    cap = cv2.VideoCapture(VIDEO_PATH)
    try:
        assert cap.isOpened(), f"bundled clip not available at {VIDEO_PATH}"
        frame = None
        for _ in range(index + 1):
            ok, frame = cap.read()
            assert ok
        return frame
    finally:
        cap.release()


def test_iou_merge_removes_only_true_duplicates():
    """A pure unit check of the merge logic, independent of any model."""
    a = Detection((0, 0, 10, 10), (5, 5), 0.9)
    b = Detection((1, 1, 11, 11), (6, 6), 0.6)  # heavy overlap with a -> duplicate
    c = Detection((50, 50, 60, 60), (55, 55), 0.5)  # far away -> distinct person

    assert _iou(a.bbox, b.bbox) > 0.4
    assert _iou(a.bbox, c.bbox) == 0.0

    merged = _merge_duplicates([a, b, c], iou_threshold=0.4)
    assert len(merged) == 2
    kept_confidences = sorted(d.confidence for d in merged)
    assert kept_confidences == [0.5, 0.9]  # higher-confidence duplicate wins, distinct person kept


def test_dense_detector_recovers_more_real_people_than_standard_pass():
    """The whole point of this module: prove it finds MORE real people on
    a genuinely dense frame than the standard single-pass detector, using
    the exact same model and confidence threshold -- never a hard-coded
    or estimated count.
    """
    from src.detector import PersonDetector

    frame = _decode_frame(42)  # frame_id 43 in 1-indexed FrameSource terms

    standard = PersonDetector("yolov8n.pt", confidence_threshold=0.5, inference_size=960)
    standard_detections, _ = standard.detect(frame)

    dense = TiledPersonDetector("yolov8n.pt", confidence_threshold=0.5, tile_spec=TileSpec(2, 2, 0.2), inference_size=1280)
    dense_detections, latency_ms = dense.detect(frame)

    # Not a fabricated number: every dense detection is a real YOLO box
    # (bbox/confidence/centroid all come straight from the model), and the
    # count is just len() of that real list.
    assert len(dense_detections) > len(standard_detections)
    for d in dense_detections:
        assert 0.5 <= d.confidence <= 1.0
        x1, y1, x2, y2 = d.bbox
        assert x2 > x1 and y2 > y1

    # No duplicate boxes survive the cross-tile merge (any two kept boxes
    # must not be near-identical detections of the same person).
    for i, a in enumerate(dense_detections):
        for b in dense_detections[i + 1:]:
            assert _iou(a.bbox, b.bbox) <= 0.4

    # Operationally bounded: a dense-scene analysis pass must stay under a
    # few seconds even on modest hardware, not become unusable.
    assert latency_ms < 5000


def test_dense_detector_model_version_is_labeled_as_tiled():
    """Diagnostic/telemetry honesty: it must be obvious from model_version
    that a result came from the dense-scene path, not the standard one."""
    detector = TiledPersonDetector("yolov8n.pt")
    assert "tiled" in detector.model_version.lower()
    assert "yolov8n.pt" in detector.model_version
