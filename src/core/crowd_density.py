"""YOLO-based person detection with lightweight bbox tracking + speed/flow estimation."""
from __future__ import annotations

from collections import deque
from pathlib import Path
import time

import cv2
import numpy as np

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PERSON_CLASS_ID = 0  # COCO "person" class

# Speed / accuracy sweet-spot for YOLOv8n on CPU / iGPU:
#   imgsz = 512 (faster than 640, still preserves small-person recall)
#   iou = 0.45 standard NMS threshold
#   half=False (CPU fp32 safe), max_det sensible cap
_DEFAULT_IMGSZ = 512
_DEFAULT_IOU = 0.45
_DEFAULT_MAX_DET = 200


def _iou_xyxy(a: tuple, b: tuple) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    x1, y1 = max(ax1, bx1), max(ay1, by1)
    x2, y2 = min(ax2, bx2), min(ay2, by2)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    a_area = max(1.0, (ax2 - ax1) * (ay2 - ay1))
    b_area = max(1.0, (bx2 - bx1) * (by2 - by1))
    return inter / (a_area + b_area - inter)


class PersonTracker:
    """Minimal IoU-based person tracker. Keeps last-seen centers per id and
    returns (flow_rate_px_per_s, avg_px_per_s, active_count, direction_cos).

    Zero extra models, zero heavy optical flow: just bbox matching.
    Good enough to stand out in competition as "crowd flow analytics".
    """

    def __init__(self, max_age_frames: int = 30, iou_threshold: float = 0.25,
                 history: int = 8):
        self.max_age = max_age_frames
        self.iou_threshold = iou_threshold
        self.history = history
        self._next_id = 0
        # id -> {"bbox","last_seen","hits","trail":deque((x,y,t))}
        self._tracks: dict[int, dict] = {}
        self._last_time = None

    def update(self, detections: list, frame_ts: float | None = None) -> dict:
        if frame_ts is None:
            frame_ts = time.time()
        dt = 0.0
        if self._last_time is not None:
            dt = max(0.016, frame_ts - self._last_time)
        self._last_time = frame_ts

        unmatched_dets = set(range(len(detections)))
        active_ids: list[int] = []

        # 1) Match existing tracks to detections by highest IoU
        for tid in list(self._tracks.keys()):
            t = self._tracks[tid]
            t["last_seen"] += 1
            best_i, best_v = -1, 0.0
            for di in unmatched_dets:
                v = _iou_xyxy(t["bbox"], detections[di]["bbox"])
                if v > best_v:
                    best_v, best_i = v, di
            if best_v >= self.iou_threshold and best_i >= 0:
                unmatched_dets.discard(best_i)
                d = detections[best_i]
                t["bbox"] = d["bbox"]
                t["confidence"] = d["confidence"]
                t["center"] = d["center"]
                t["last_seen"] = 0
                t["hits"] += 1
                t["trail"].append((*d["center"], frame_ts))
                active_ids.append(tid)
            elif t["last_seen"] > self.max_age:
                del self._tracks[tid]

        # 2) Spawn new tracks for unmatched detections
        for di in unmatched_dets:
            d = detections[di]
            tid = self._next_id
            self._next_id += 1
            self._tracks[tid] = {
                "bbox": d["bbox"],
                "confidence": d["confidence"],
                "center": d["center"],
                "last_seen": 0,
                "hits": 1,
                "trail": deque(maxlen=self.history),
            }
            self._tracks[tid]["trail"].append((*d["center"], frame_ts))
            active_ids.append(tid)

        # 3) Derive flow metrics from trails of active tracks that have >=2 pts
        speeds = []
        net_dx, net_dy = 0.0, 0.0
        movement_mag = 0.0
        for tid in active_ids:
            trail = self._tracks[tid]["trail"]
            if len(trail) < 2:
                continue
            x0, y0, t0 = trail[0]
            x1, y1, t1 = trail[-1]
            dt_trail = max(1e-3, t1 - t0)
            dx, dy = (x1 - x0), (y1 - y0)
            dist = float(np.hypot(dx, dy))
            sp = dist / dt_trail  # px/s
            speeds.append(sp)
            if dist > 1.5:  # filter tiny jitter
                net_dx += dx / dist
                net_dy += dy / dist
                movement_mag += 1.0

        n = len(speeds)
        avg_speed_pxs = float(np.mean(speeds)) if n else 0.0
        # Flow rate: fraction of tracked people moving > 5 px/s
        moving = sum(1 for s in speeds if s > 5.0)
        flow_rate = (moving / len(active_ids)) if active_ids else 0.0
        # Direction cosine: -1..1 per axis, 0 means chaotic/equal mix
        if movement_mag > 0:
            dir_x = net_dx / movement_mag
            dir_y = net_dy / movement_mag
        else:
            dir_x, dir_y = 0.0, 0.0

        return {
            "flow_rate": float(flow_rate),      # 0..1, how much crowd is moving
            "avg_speed_pxs": avg_speed_pxs,     # avg pixel/sec per moving person
            "active_count": len(active_ids),
            "tracked_count": len(self._tracks),
            "direction_x": float(dir_x),        # -1..1 (left/right)
            "direction_y": float(dir_y),        # -1..1 (up/down)
        }


class CrowdDensityAnalyzer:
    """Detects people in a frame using a YOLO model + tracks flow metrics."""

    def __init__(self, model_name: str = "yolov8n.pt", confidence_threshold: float = 0.30,
                 imgsz: int = _DEFAULT_IMGSZ, iou: float = _DEFAULT_IOU,
                 tracker_max_age: int = 20):
        self.confidence_threshold = float(confidence_threshold)
        self.imgsz = int(imgsz)
        self.iou = float(iou)
        self.model = None
        self._load_error = None
        self._tracker = PersonTracker(max_age_frames=tracker_max_age)
        # Short frame buffer to report processed FPS to dashboard (cheap)
        self._fps_window: deque = deque(maxlen=30)
        self._last_t = None
        try:
            from ultralytics import YOLO  # lazy import, fast cold start if absent

            weights_path = Path(model_name)
            if not weights_path.is_file():
                candidate = _REPO_ROOT / model_name
                weights_path = candidate if candidate.is_file() else Path(model_name)
            self.model = YOLO(str(weights_path))
        except Exception as exc:  # pragma: no cover - depends on optional model weights
            self._load_error = exc
            self.model = None

    # ------------------------------------------------------------------
    def detect_people(self, frame: np.ndarray) -> list:
        """Return a list of detections: {bbox, confidence, center}.

        Uses YOLOv8 best-defaults tuned for SPEED + ACCURACY balance:
          - imgsz 512 (not 640): ~20-25% faster, minimal person recall loss.
          - classes=[person] only (skips 79 COCO classes → postprocess lighter).
          - max_det 200 (covers busy platforms without spurious repeats).
          - verbose=False (silences per-frame stdout, real-time friendly).
        """
        if frame is None or self.model is None:
            return []

        results = self.model.predict(
            source=frame,
            classes=[_PERSON_CLASS_ID],
            conf=self.confidence_threshold,
            iou=self.iou,
            imgsz=self.imgsz,
            max_det=_DEFAULT_MAX_DET,
            half=False,
            verbose=False,
        )

        detections = []
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            xyxy = boxes.xyxy
            confs = boxes.conf
            if xyxy is None:
                continue
            n = xyxy.shape[0]
            for i in range(n):
                x1, y1, x2, y2 = [float(v) for v in xyxy[i].tolist()]
                confidence = float(confs[i]) if confs is not None else 0.0
                detections.append({
                    "bbox": (x1, y1, x2, y2),
                    "confidence": confidence,
                    "center": ((x1 + x2) * 0.5, (y1 + y2) * 0.5),
                })

        # ------------------------------------------------------------------
        # Cheap post-process: de-duplicate near-identical boxes (IoU > 0.85,
        # keep higher confidence). YOLO already does NMS but sometimes at
        # edges or reflections a double box slips through. O(n^2) tiny cost.
        # ------------------------------------------------------------------
        if len(detections) > 1:
            detections.sort(key=lambda d: d["confidence"], reverse=True)
            keep: list = []
            for d in detections:
                dup = False
                for k in keep:
                    if _iou_xyxy(d["bbox"], k["bbox"]) > 0.85:
                        dup = True
                        break
                if not dup:
                    keep.append(d)
            detections = keep

        return detections

    def track_flow(self, detections: list) -> dict:
        """Run lightweight tracker and return flow metrics dict."""
        return self._tracker.update(detections)

    # ------------------------------------------------------------------
    def generate_heatmap(self, frame_shape: tuple, detections: list,
                         radius: int = 30, blur_kernel: int = 51) -> np.ndarray:
        """Build a BGR heatmap directly from detection centers (no grid needed)."""
        h, w = frame_shape[0], frame_shape[1]
        canvas = np.zeros((h, w), dtype=np.float32)
        for det in detections:
            cx, cy = det["center"]
            cv2.circle(canvas, (int(cx), int(cy)), int(radius), 1.0, -1)

        k = int(blur_kernel)
        if k % 2 == 0:
            k += 1
        if k > 1 and canvas.max() > 0:
            canvas = cv2.GaussianBlur(canvas, (k, k), 0)

        peak = float(canvas.max())
        if peak > 0:
            canvas = canvas / peak
        gray = (np.clip(canvas, 0.0, 1.0) * 255.0).astype(np.uint8)
        return cv2.applyColorMap(gray, cv2.COLORMAP_JET)

    def overlay_heatmap(self, frame: np.ndarray, heatmap: np.ndarray,
                        alpha: float = 0.4) -> np.ndarray:
        """Blend a heatmap onto the source frame."""
        if frame is None or heatmap is None:
            return frame
        h, w = frame.shape[:2]
        if heatmap.shape[0] != h or heatmap.shape[1] != w:
            heatmap = cv2.resize(heatmap, (w, h), interpolation=cv2.INTER_LINEAR)
        return cv2.addWeighted(frame, 1.0, heatmap, alpha, 0.0)

    def report_fps(self) -> float:
        """Call after detect_people to keep a rolling FPS indicator. Pure math."""
        now = time.perf_counter()
        if self._last_t is not None:
            dt = now - self._last_t
            if dt > 0:
                self._fps_window.append(1.0 / dt)
        self._last_t = now
        return float(np.mean(self._fps_window)) if self._fps_window else 0.0

    # ------------------------------------------------------------------
    def visualize_detections(self, frame: np.ndarray, detections: list,
                             flow: dict | None = None) -> np.ndarray:
        """Draw bboxes + optional flow arrow with negligible overhead."""
        if frame is None:
            return frame

        vis_frame = frame.copy()
        for det in detections:
            x1, y1, x2, y2 = [int(round(v)) for v in det["bbox"]]
            conf = det["confidence"]
            # Color-code confidence → green=high → yellow=low (still detected)
            if conf >= 0.7:
                color = (16, 185, 129)   # emerald
                thick = 2
            elif conf >= 0.45:
                color = (245, 158, 11)   # amber
                thick = 2
            else:
                color = (59, 130, 246)   # blue (low conf but valid)
                thick = 1
            cv2.rectangle(vis_frame, (x1, y1), (x2, y2), color, thick)
            label = f"P {conf:.2f}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
            cv2.rectangle(vis_frame, (x1, max(y1 - th - 6, 0)),
                          (x1 + tw + 4, max(y1, 0) - 1), color, -1)
            cv2.putText(vis_frame, label, (x1 + 2, max(y1 - 4, th)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (15, 23, 42), 1)

        if flow and flow.get("active_count", 0) >= 2 and (
            abs(flow.get("direction_x", 0.0)) > 0.15 or abs(flow.get("direction_y", 0.0)) > 0.15
        ):
            h, w = vis_frame.shape[:2]
            cx, cy = w // 2, 50
            dx = int(flow.get("direction_x", 0.0) * 35)
            dy = int(flow.get("direction_y", 0.0) * 35)
            if dx or dy:
                cv2.arrowedLine(vis_frame, (cx, cy), (cx + dx, cy + dy),
                                (125, 211, 252), 3, tipLength=0.4)
                cv2.putText(vis_frame, "CROWD FLOW", (cx - 60, cy - 14),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 240, 220), 1)

        return vis_frame
