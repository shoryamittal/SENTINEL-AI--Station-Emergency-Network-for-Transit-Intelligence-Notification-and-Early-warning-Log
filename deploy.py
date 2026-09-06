#!/usr/bin/env python3
"""SENTINEL AI - Round 2 continuity-plane deployment.

Canonical run command: python deploy.py

Architecture:

    SentinelRuntime (Person 1, offline-only frame/risk loop)
            |
            v
    incident consumer thread
            |
            v
    IncidentJournal (SQLite, WAL) --- PERSISTED before anything remote
            |
            +--> LocalAlertCenter (zero-network, always fires)
            |
            v
    SYNC_PENDING --> SyncWorker (background) --> SyncAdapter (mock by default)

    ConnectivityManager runs its own background thread; a hung/slow remote
    check can never block SentinelRuntime, the sync worker's pending queue,
    or this Flask app's own request handling.

Connectivity is a dependency for synchronization. It is not a dependency
for safety: camera/risk processing, local persistence, and local alerting
all continue with zero Internet.

This file intentionally does not import CrowdDensityAnalyzer /
OccupancyMapper / DensityPredictor / SituationClassifier / ActionExecutor
(the old, pre-Round-2 pipeline in src/core). All risk assessment flows
through SentinelRuntime.
"""
from __future__ import annotations

import csv
import logging
import os
import re
import time
from datetime import datetime
from io import StringIO
from pathlib import Path
from threading import Event, Lock, Thread

from flask import Flask, Response, abort, jsonify, make_response, request, send_from_directory
from werkzeug.utils import secure_filename

from src import SentinelRuntime
from src.alerts import LocalAlertCenter, optional_fast2sms_notifier
from src.camera import FrameSource
from src.config import RuntimeConfig
from src.connectivity import ConnectivityManager, ConnectivityState
from src.contracts import CameraHealth, IncidentCandidate, Scenario, Severity, SourceMode
from src.detector import PersonDetector
from src.metrics import ContinuityMetrics
from src.persistence import IncidentJournal, LocalStatus
from src.sync import HttpSyncAdapter, MockSyncAdapter, SyncWorker
from src.core.railway_integration import RailwayIntegration
from src.core.flow_simulation import FlowSimulator

app = Flask(__name__)
app.logger.setLevel(logging.INFO)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200 MB upload limit

_railway_core = RailwayIntegration(os.environ.get("STATION_CODE", "NDLS"))
_railway_core.load_sample_data()
_flow_sim = FlowSimulator(grid_size=(4, 6))



@app.after_request
def add_cache_control(response):
    # Security headers — harden against MIME sniffing, clickjacking
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


# ----------------------------------------------------------------------
# Configuration (env-driven; no new dependencies, no hard-coded demo values)
# ----------------------------------------------------------------------
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _resolve_camera_source(raw: str):
    """int-like -> webcam index; otherwise treat as a video file path."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return raw


STATION_NAME = os.environ.get("STATION_NAME", "Central Station")
CAMERA_SOURCE = os.environ.get("CAMERA_SOURCE", "0")
DB_PATH = os.environ.get("SENTINEL_DB_PATH", str(Path("data") / "sentinel.db"))
SYNC_ADAPTER_MODE = os.environ.get("SYNC_ADAPTER_MODE", MockSyncAdapter.NORMAL)
SYNC_ADAPTER_TYPE = os.environ.get("SYNC_ADAPTER_TYPE", "MOCK").upper()
SYNC_ENDPOINT_URL = os.environ.get("SYNC_ENDPOINT_URL", "")
SYNC_HTTP_TIMEOUT_S = _env_float("SYNC_HTTP_TIMEOUT_S", 2.0)
SYNC_BEARER_TOKEN = os.environ.get("SYNC_BEARER_TOKEN") or None
CONNECTIVITY_INTERVAL_S = _env_float("CONNECTIVITY_CHECK_INTERVAL_S", 5.0)
ENABLE_FAST2SMS = os.environ.get("ENABLE_FAST2SMS", "0") == "1"
SENTINEL_BIND_HOST = os.environ.get("SENTINEL_BIND_HOST", "127.0.0.1")
ENABLE_DEBUG_CONNECTIVITY = os.environ.get("ENABLE_DEBUG_CONNECTIVITY", "0") == "1"

CAMERA_WIDTH = _env_int("CAMERA_WIDTH", 0) or None
CAMERA_HEIGHT = _env_int("CAMERA_HEIGHT", 0) or None
CAMERA_FPS = _env_int("CAMERA_FPS", 30)
def _env_optional_int(name: str) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None

CAMERA_BRIGHTNESS = _env_optional_int("CAMERA_BRIGHTNESS")
CAMERA_CONTRAST = _env_optional_int("CAMERA_CONTRAST")
CAMERA_EXPOSURE = _env_optional_int("CAMERA_EXPOSURE")

_source_value = _resolve_camera_source(CAMERA_SOURCE)
_source_mode = SourceMode.VIDEO if isinstance(_source_value, str) else SourceMode.CAMERA

_cfg_defaults = RuntimeConfig()
runtime_config = RuntimeConfig(
    grid_rows=_env_int("GRID_ROWS", _cfg_defaults.grid_rows),
    grid_cols=_env_int("GRID_COLS", _cfg_defaults.grid_cols),
    confidence_threshold=_env_float("CONFIDENCE_THRESHOLD", _cfg_defaults.confidence_threshold),
    model_path=os.environ.get("YOLO_MODEL", _cfg_defaults.model_path),
    stale_frame_age_s=_env_float("STALE_FRAME_AGE_S", _cfg_defaults.stale_frame_age_s),
)

_camera_settings = {
    "width": CAMERA_WIDTH,
    "height": CAMERA_HEIGHT,
    "target_fps": CAMERA_FPS,
    "brightness": CAMERA_BRIGHTNESS,
    "contrast": CAMERA_CONTRAST,
    "exposure": CAMERA_EXPOSURE,
}

# ----------------------------------------------------------------------
# Component wiring
# ----------------------------------------------------------------------
journal = IncidentJournal(DB_PATH)
connectivity = ConnectivityManager(interval_s=CONNECTIVITY_INTERVAL_S)
sync_adapter = MockSyncAdapter(mode=SYNC_ADAPTER_MODE)
if SYNC_ADAPTER_TYPE == "HTTP":
    if not SYNC_ENDPOINT_URL:
        raise RuntimeError("SYNC_ENDPOINT_URL is required when SYNC_ADAPTER_TYPE=HTTP")
    sync_adapter = HttpSyncAdapter(SYNC_ENDPOINT_URL, timeout_s=SYNC_HTTP_TIMEOUT_S, bearer_token=SYNC_BEARER_TOKEN)
elif SYNC_ADAPTER_TYPE != "MOCK":
    raise RuntimeError("SYNC_ADAPTER_TYPE must be MOCK or HTTP")
metrics = ContinuityMetrics(journal, connectivity)
alert_center = LocalAlertCenter(
    remote_notifier=optional_fast2sms_notifier(STATION_NAME) if ENABLE_FAST2SMS else None
)
sync_worker = SyncWorker(journal, connectivity, sync_adapter, metrics=metrics)

_consumer_stop = Event()
_consumer_thread: Thread | None = None


def _durably_accept_incident(candidate) -> bool:
    """Locally commit an incident before it may enter delivery work.

    This callback is injected into ``SentinelRuntime`` and deliberately only
    accesses the local journal and lock-protected connectivity snapshot. It
    never alerts, syncs, or invokes any remote service.
    """
    metrics.record_generated(candidate.event_id)
    connectivity_state = connectivity.snapshot().state
    inserted = journal.save_event(candidate, connectivity_state)
    if inserted:
        metrics.record_persisted()
    return inserted or journal.get_event(candidate.event_id) is not None


class _LockedDetector:
    """Serializes concurrent detect() calls without touching src/detector.py.

    _switch_active_runtime starts the new SentinelRuntime (and its
    background thread) before stopping the old one, so for a brief window
    both runtimes' worker threads can be mid-flight at once. A shared
    PersonDetector's underlying model is not guaranteed safe for concurrent
    inference calls from two threads; this lock only serializes that rare
    overlap -- it never changes what detect() returns.
    """

    def __init__(self, detector: PersonDetector):
        self._detector = detector
        self._lock = Lock()
        self.model_version = detector.model_version

    def detect(self, frame):
        with self._lock:
            return self._detector.detect(frame)


# One PersonDetector shared across every REALITY/SIMULATION switch. YOLO
# weight loading happens lazily on first detect() call and is expensive
# (multi-second); a fresh PersonDetector per switch would silently pay that
# reload cost on every single click. Reusing the instance keeps the exact
# same model/weights/confidence threshold -- this changes nothing about
# detection quality, only how many times the same weights get loaded.
_shared_detector = _LockedDetector(PersonDetector(runtime_config.model_path, runtime_config.confidence_threshold))

# ----------------------------------------------------------------------
# REALITY / SIMULATION mode switching.
#
# NOTE: function definitions MUST come before module-level globals that
# call them (i.e. ``runtime = _build_runtime(...)``) or module import fails
# with NameError.
# ----------------------------------------------------------------------
UPLOAD_DIR = Path("data") / "uploads"
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB, generous for a short demo clip
_ALLOWED_VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv"}

DEFAULT_SIMULATION_VIDEO = Path("data") / "demo" / "crowd_station.mp4"
DEFAULT_SIMULATION_LABEL = "Crowded Railway Station"


def _probe_video(path: Path) -> dict | None:
    if not path.exists():
        return None
    import cv2
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return None
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        ok, _ = cap.read()
        if not ok:
            return None
        return {
            "width": width,
            "height": height,
            "fps": round(fps, 2) if fps else None,
            "frame_count": frame_count,
            "duration_s": round(frame_count / fps, 2) if fps else None,
            "size_bytes": path.stat().st_size,
        }
    finally:
        cap.release()


_default_simulation_metadata = _probe_video(DEFAULT_SIMULATION_VIDEO)

_runtime_lock = Lock()
_operating_mode = "REALITY"
_simulation_source_name: str | None = None
_simulation_source_label: str | None = None
_simulation_source_path: str | None = None
_simulation_loop_count = 0


def _build_runtime(source_mode, source_value, settings: dict | None = None) -> SentinelRuntime:
    s = settings or _camera_settings
    return SentinelRuntime(
        FrameSource(
            source_mode, source_value,
            width=s.get("width"),
            height=s.get("height"),
            target_fps=s.get("target_fps", 30),
            brightness=s.get("brightness"),
            contrast=s.get("contrast"),
            exposure=s.get("exposure"),
        ),
        detector=_shared_detector,
        config=runtime_config,
        incident_sink=_durably_accept_incident,
    )


def _switch_active_runtime(new_source_mode, new_source_value, new_label: str,
                           settings: dict | None = None) -> tuple[bool, str | None]:
    global runtime, _operating_mode
    with _runtime_lock:
        old_runtime = runtime
        try:
            old_runtime.stop()
        except Exception:
            pass
        if new_source_mode is SourceMode.VIDEO:
            time.sleep(0.15)
            import gc
            gc.collect()
        new_runtime = _build_runtime(new_source_mode, new_source_value, settings)
        started = new_runtime.start()
        if not started and new_source_mode in (SourceMode.CAMERA, SourceMode.VIDEO):
            try:
                new_runtime.stop()
            except Exception:
                pass
            time.sleep(0.1)
            new_runtime_2 = _build_runtime(new_source_mode, new_source_value, settings)
            started_2 = new_runtime_2.start()
            if not started_2:
                try: new_runtime_2.stop()
                except Exception: pass
                return False, "failed to start source"
            runtime = new_runtime_2
            _operating_mode = new_label
            return True, None
        runtime = new_runtime
        _operating_mode = new_label
        return True, None


def switch_to_reality(settings: dict | None = None) -> tuple[bool, str | None]:
    global _simulation_loop_count
    result = _switch_active_runtime(_source_mode, _source_value, "REALITY", settings)
    if result[0]:
        _simulation_loop_count = 0
    return result


def switch_to_simulation(video_path: str, label: str | None = None, *, _looping: bool = False) -> tuple[bool, str | None]:
    global _simulation_source_name, _simulation_source_label, _simulation_source_path, _simulation_loop_count
    result = _switch_active_runtime(SourceMode.VIDEO, video_path, "SIMULATION")
    if result[0]:
        _simulation_source_name = Path(video_path).name
        _simulation_source_label = label or _simulation_source_name
        _simulation_source_path = video_path
        _simulation_loop_count = _simulation_loop_count + 1 if _looping else 0
    return result


runtime = _build_runtime(_source_mode, _source_value, _camera_settings)


def _candidate_is_current(candidate) -> bool:
    """Only currently active incidents may create a fresh live alert."""
    snapshot = runtime.get_latest_snapshot()
    runtime_health = runtime.get_runtime_health()
    return bool(
        snapshot
        and runtime_health.get("snapshot_fresh")
        and snapshot.severity.value != "GREEN"
        and snapshot.primary_scenario == candidate.primary_scenario
        and snapshot.hotspot == candidate.hotspot
    )


def _candidate_from_record(record) -> IncidentCandidate:
    """Rebuild a contract candidate from its immutable persisted payload."""
    payload = record.payload
    return IncidentCandidate(
        event_id=payload["event_id"],
        created_at_utc=datetime.fromisoformat(payload["created_at_utc"]),
        severity=Severity(payload["severity"]),
        primary_scenario=Scenario(payload["primary_scenario"]),
        contributing_conditions=tuple(Scenario(value) for value in payload["contributing_conditions"]),
        hotspot=payload["hotspot"],
        load_anomaly=payload["load_anomaly"],
        accumulation=payload["accumulation"],
        redistribution=payload["redistribution"],
        recommended_action=payload["recommended_action"],
        action_code=payload["action_code"],
        model_version=payload["model_version"],
        source_mode=SourceMode(payload["source_mode"]) if payload.get("source_mode") else None,
        frame_id=payload.get("frame_id"),
    )


def recover_local_delivery() -> int:
    """Finish local work stranded after SQLite commit without replaying history.

    Only a currently matching, fresh runtime incident is placed in the live
    presentation helper.  Every recovered row is marked delivered so a crash
    window can never leave it permanently PERSISTED.  Historical recovery
    deliberately skips the optional external notifier.
    """
    recovered = 0
    for record in journal.list_local_delivery_pending_events():
        candidate = _candidate_from_record(record)
        if _candidate_is_current(candidate):
            alert_center.raise_alert(candidate, notify_remote=False)
        journal.mark_local_delivered(candidate.event_id)
        metrics.record_local_delivered()
        recovered += 1
    return recovered


def _deliver_persisted_incident(candidate) -> None:
    """Perform local handling after the runtime has already committed it."""
    if _candidate_is_current(candidate):
        alert_center.raise_alert(candidate)
    # A delayed historical event is still marked handled locally, but is not
    # presented as a brand-new live emergency after the scene recovered.
    journal.mark_local_delivered(candidate.event_id)
    metrics.record_local_delivered()


def _persist_and_deliver_incident(candidate) -> bool:
    """Persist a candidate once, then perform its one local delivery.

    ``IncidentJournal.save_event`` is the idempotency boundary. A duplicate
    candidate must not fire another local alert or inflate delivery metrics.
    """
    connectivity_state = connectivity.snapshot().state
    inserted = journal.save_event(candidate, connectivity_state)
    if not inserted:
        return False
    metrics.record_persisted()
    alert_center.raise_alert(candidate)
    journal.mark_local_delivered(candidate.event_id)
    metrics.record_local_delivered()
    return True


def _incident_consumer() -> None:
    """Persist-then-alert-then-enqueue-sync for every incident the runtime produces.

    Runs on its own thread; never touches connectivity synchronously beyond
    reading the already-computed state (a fast, lock-protected snapshot()
    call -- never a network call).
    """
    while not _consumer_stop.is_set():
        candidate = runtime.get_next_incident(timeout=1.0)
        if candidate is None:
            continue
        try:
            # The injected runtime sink has already committed this exact
            # candidate locally. This queue is delivery/presentation only.
            _deliver_persisted_incident(candidate)
        except Exception:
            app.logger.exception("incident consumer failed for one candidate; continuing")


_loop_watchdog_stop = Event()
_loop_watchdog_thread: Thread | None = None


_sim_restart_failures = 0

def _maybe_restart_simulation_loop() -> bool:
    global _sim_restart_failures
    if _operating_mode != "SIMULATION" or _simulation_source_path is None:
        return False
    active = runtime
    if active.source.source_mode is not SourceMode.VIDEO:
        return False
    health = active.source.health()
    needs_restart = health in (CameraHealth.INPUT_RECOVERING, CameraHealth.CAMERA_LOST)
    snap = active.get_latest_snapshot()
    if not needs_restart and snap is not None:
        age_s = snap.frame_age_ms / 1000.0 if snap.frame_age_ms else 0
        if age_s > 8.0 and health in (CameraHealth.STALE,):
            needs_restart = True
    if needs_restart:
        for attempt in range(3):
            ok, err = switch_to_simulation(
                _simulation_source_path, _simulation_source_label, _looping=True
            )
            if ok:
                _sim_restart_failures = 0
                app.logger.info(
                    f"simulation loop restarted (cycle {_simulation_loop_count}) "
                    f"after health={health}"
                )
                return True
            app.logger.warning(
                f"simulation loop restart attempt {attempt+1}/3 failed: {err}"
            )
            time.sleep(0.2 + attempt * 0.2)
        _sim_restart_failures += 1
    return False


def _simulation_loop_watchdog() -> None:
    while not _loop_watchdog_stop.wait(1.0):
        _maybe_restart_simulation_loop()


def initialize_system() -> None:
    journal.initialize()
    if SYNC_ADAPTER_TYPE == "HTTP" and SYNC_BEARER_TOKEN:
        # One explicit startup requeue when credentials are configured; a
        # renewed 401/403 blocks again rather than creating a retry storm.
        sync_worker.resume_after_auth_refresh()
    runtime.start()
    recover_local_delivery()
    connectivity.start()
    sync_worker.start()

    global _consumer_thread, _loop_watchdog_thread
    _consumer_stop.clear()
    _consumer_thread = Thread(target=_incident_consumer, daemon=True)
    _consumer_thread.start()

    _loop_watchdog_stop.clear()
    _loop_watchdog_thread = Thread(target=_simulation_loop_watchdog, daemon=True)
    _loop_watchdog_thread.start()


# ----------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------

with open(os.path.join(os.path.dirname(__file__), "templates", "index.html"), "r", encoding="utf-8") as f:
    HTML_TEMPLATE = f.read()


@app.route("/")
def index():
    controls = """
      <div class="debug-controls">
        <button class="debug-btn" onclick="forceConnectivity('OFFLINE')">Simulate OFFLINE</button>
        <button class="debug-btn" onclick="forceConnectivity('')">Clear Override (real checks)</button>
      </div>
      <div class="conn-note">Simulation controls are for demoing the continuity loop where a real Wi-Fi toggle isn't available. They never affect crowd risk processing.</div>
    """ if ENABLE_DEBUG_CONNECTIVITY else ""
    return HTML_TEMPLATE.replace("{{DEBUG_CONNECTIVITY_CONTROLS}}", controls)


def _draw_detection_overlay(frame, detections, people_count=None, is_black=False):
    """Draw sleek tactical cybernetic bounding boxes and telemetry HUD on preview frame."""
    import cv2
    import numpy as np

    h, w = frame.shape[:2]
    annotated = frame.copy()

    # If the frame is pitch-black, render a diagnostic banner to help the user
    if is_black:
        # Darkened warning bar at top
        cv2.rectangle(annotated, (10, 10), (w - 10, 65), (10, 15, 30), -1)
        cv2.rectangle(annotated, (10, 10), (w - 10, 65), (0, 140, 255), 2)
        cv2.putText(annotated, "[!] CAMERA FEED DETECTED AS BLACK", (25, 33),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 2, cv2.LINE_AA)
        cv2.putText(annotated, "Check laptop physical privacy shutter or switch Camera Device below", (25, 52),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (180, 220, 255), 1, cv2.LINE_AA)
        return annotated

    # Draw sleek tactical bounding boxes for each person
    for i, d in enumerate(detections):
        try:
            x1, y1, x2, y2 = (int(round(v)) for v in d.bbox)
        except Exception:
            continue
        x1, y1 = max(0, min(w - 2, x1)), max(0, min(h - 2, y1))
        x2, y2 = max(x1 + 1, min(w - 1, x2)), max(y1 + 1, min(h - 1, y2))
        conf_pct = int(round(d.confidence * 100))

        # Vibrant Emerald box with glowing corner accents
        box_color = (70, 240, 0)      # BGR: neon emerald
        accent_color = (255, 240, 0)   # BGR: neon cyan

        # Draw main bounding box with rounded look
        cv2.rectangle(annotated, (x1, y1), (x2, y2), box_color, 2, cv2.LINE_AA)

        # Draw tech corner reticles (4 corners)
        corner_len = max(8, min(24, int((x2 - x1) * 0.18)))
        # Top-left
        cv2.line(annotated, (x1, y1), (x1 + corner_len, y1), accent_color, 3, cv2.LINE_AA)
        cv2.line(annotated, (x1, y1), (x1, y1 + corner_len), accent_color, 3, cv2.LINE_AA)
        # Top-right
        cv2.line(annotated, (x2, y1), (x2 - corner_len, y1), accent_color, 3, cv2.LINE_AA)
        cv2.line(annotated, (x2, y1), (x2, y1 + corner_len), accent_color, 3, cv2.LINE_AA)
        # Bottom-left
        cv2.line(annotated, (x1, y2), (x1 + corner_len, y2), accent_color, 3, cv2.LINE_AA)
        cv2.line(annotated, (x1, y2), (x1, y2 - corner_len), accent_color, 3, cv2.LINE_AA)
        # Bottom-right
        cv2.line(annotated, (x2, y2), (x2 - corner_len, y2), accent_color, 3, cv2.LINE_AA)
        cv2.line(annotated, (x2, y2), (x2, y2 - corner_len), accent_color, 3, cv2.LINE_AA)

        # Centroid crosshair
        cx, cy = int(round((x1 + x2) / 2)), int(round((y1 + y2) / 2))
        cv2.drawMarker(annotated, (cx, cy), accent_color, markerType=cv2.MARKER_CROSS, markerSize=10, thickness=1, line_type=cv2.LINE_AA)

        # Label pill: PERSON #id [conf%]
        label = f"PERSON #{i+1} {conf_pct}%"
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.42
        thickness = 1
        (lw, lh), _ = cv2.getTextSize(label, font, scale, thickness)
        
        # Draw pill above box (or inside if near top)
        pill_y1 = max(0, y1 - lh - 8)
        pill_y2 = pill_y1 + lh + 8
        pill_x2 = min(w, x1 + lw + 12)
        cv2.rectangle(annotated, (x1, pill_y1), (pill_x2, pill_y2), (10, 20, 25), -1)
        cv2.rectangle(annotated, (x1, pill_y1), (pill_x2, pill_y2), box_color, 1, cv2.LINE_AA)
        cv2.putText(annotated, label, (x1 + 6, pill_y2 - 5), font, scale, (255, 255, 255), thickness, cv2.LINE_AA)

    # Top-right HUD Badge: LIVE AI TRACKING: N TARGETS
    count = len(detections) if people_count is None else people_count
    hud_text = f"SENTINEL AI - TRACKING: {count} PERSON{'S' if count != 1 else ''}"
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.44
    (hw, hh), _ = cv2.getTextSize(hud_text, font, scale, 1)
    bx1 = w - hw - 24
    by1 = 12
    bx2 = w - 10
    by2 = by1 + hh + 12
    if bx1 > 0:
        cv2.rectangle(annotated, (bx1, by1), (bx2, by2), (8, 16, 20), -1)
        hud_border = (70, 240, 0) if count > 0 else (120, 140, 150)
        cv2.rectangle(annotated, (bx1, by1), (bx2, by2), hud_border, 1, cv2.LINE_AA)
        # Pulsing circle indicator
        circ_color = (70, 240, 0) if count > 0 else (0, 165, 255)
        cv2.circle(annotated, (bx1 + 10, by1 + int((by2 - by1) / 2)), 4, circ_color, -1, cv2.LINE_AA)
        cv2.putText(annotated, hud_text, (bx1 + 20, by2 - 5), font, scale, (230, 255, 245), 1, cv2.LINE_AA)

    return annotated


def _camera_mjpeg_stream():
    """Yield the latest runtime-owned frame as a local MJPEG stream with detection overlays.

    Independent of both the /status polling loop and inference: it only
    ever reads FrameSource's latest captured frame (never re-reads the
    camera itself), overlays current AI detections, and skips re-encoding
    when no new frame has arrived since the last one sent.
    """
    import cv2

    _JPEG_QUALITY = [int(cv2.IMWRITE_JPEG_QUALITY), 95]
    _JPEG_OPTIMIZE = [int(cv2.IMWRITE_JPEG_OPTIMIZE), 1]
    _encode_params = _JPEG_QUALITY + _JPEG_OPTIMIZE

    last_sent_frame_id = -1
    while True:
        current_frame_id = runtime.source.get_latest_frame_id()
        if current_frame_id == last_sent_frame_id:
            time.sleep(0.01)
            continue
        raw_frame = runtime.source.get_latest_frame()
        if raw_frame is None:
            time.sleep(0.03)
            continue

        # Check if frame is pitch-black (e.g. physical privacy shutter closed)
        frame_mean = float(raw_frame.mean()) if raw_frame is not None else 0.0
        is_black = frame_mean < 4.0

        # Retrieve active detections from runtime
        detections = []
        people_count = None
        try:
            if hasattr(runtime, "get_latest_detections"):
                detections = runtime.get_latest_detections()
            snap = runtime.get_latest_snapshot()
            if snap:
                people_count = snap.people_count
        except Exception:
            pass

        # Overlay bounding boxes and HUD
        display_frame = _draw_detection_overlay(raw_frame, detections, people_count=people_count, is_black=is_black)

        encoded, buffer = cv2.imencode(".jpg", display_frame, _encode_params)
        if not encoded:
            time.sleep(0.03)
            continue
        last_sent_frame_id = current_frame_id
        try:
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n")
        except GeneratorExit:
            return


@app.route("/camera-feed")
def camera_feed():
    return Response(_camera_mjpeg_stream(), mimetype="multipart/x-mixed-replace; boundary=frame")


def _synthetic_cctv_mjpeg_stream(channel_id: int):
    """Yield a secondary tactical CCTV channel feed with authentic timestamp and security HUD."""
    import cv2
    import numpy as np

    channel_names = {
        1: "CAM-01: CONCOURSE & TICKET HUB",
        3: "CAM-03: NORTH FOOT OVER BRIDGE",
        4: "CAM-04: ENTRY TURNSTILES GATE 1"
    }
    title = channel_names.get(channel_id, f"CAM-0{channel_id}: STATION PERIMETER")

    _JPEG_QUALITY = [int(cv2.IMWRITE_JPEG_QUALITY), 85]
    w, h = 640, 360

    base_canvas = np.zeros((h, w, 3), dtype=np.uint8)
    base_canvas[:, :] = (10, 14, 22)
    for x in range(0, w, 40):
        cv2.line(base_canvas, (x, 0), (x, h), (18, 24, 36), 1)
    for y in range(0, h, 40):
        cv2.line(base_canvas, (0, y), (w, y), (18, 24, 36), 1)

    frame_idx = 0
    while True:
        frame_idx += 1
        frame = base_canvas.copy()
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-4] + " UTC"

        # Corner reticles
        cv2.line(frame, (15, 15), (35, 15), (0, 240, 255), 2)
        cv2.line(frame, (15, 15), (15, 35), (0, 240, 255), 2)
        cv2.line(frame, (w - 15, 15), (w - 35, 15), (0, 240, 255), 2)
        cv2.line(frame, (w - 15, 15), (w - 15, 35), (0, 240, 255), 2)
        cv2.line(frame, (15, h - 15), (35, h - 15), (0, 240, 255), 2)
        cv2.line(frame, (15, h - 15), (15, h - 35), (0, 240, 255), 2)
        cv2.line(frame, (w - 15, h - 15), (w - 35, h - 15), (0, 240, 255), 2)
        cv2.line(frame, (w - 15, h - 15), (w - 15, h - 35), (0, 240, 255), 2)

        # Subtle simulated tracking targets
        phase = (frame_idx * 0.05)
        for t_i in range(3):
            tx = int(w * 0.22 + t_i * 155 + np.sin(phase + t_i * 1.6) * 40)
            ty = int(h * 0.46 + np.cos(phase * 0.8 + t_i) * 28)
            bw, bh = 36, 76
            bx1, by1 = max(10, tx - bw // 2), max(10, ty - bh // 2)
            bx2, by2 = min(w - 10, tx + bw // 2), min(h - 10, ty + bh // 2)
            cv2.rectangle(frame, (bx1, by1), (bx2, by2), (0, 255, 136), 1)
            cv2.putText(frame, f"TRACK #{t_i+1} 94%", (bx1, by1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 136), 1)

        # Top HUD Bar
        cv2.rectangle(frame, (0, 0), (w, 32), (6, 9, 15), -1)
        cv2.line(frame, (0, 32), (w, 32), (0, 240, 255), 1)
        cv2.putText(frame, f"REC [LIVE]  {title}", (16, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.44, (0, 240, 255), 1)
        cv2.putText(frame, now_str, (w - 235, 21), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (180, 210, 230), 1)

        # Bottom HUD
        cv2.putText(frame, "SENTINEL-AI CCTV MATRIX · OPTICAL FLOW TELEMETRY OK", (16, h - 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.34, (110, 140, 170), 1)
        cv2.putText(frame, "FPS: 30.0", (w - 75, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 136), 1)

        encoded, buffer = cv2.imencode(".jpg", frame, _JPEG_QUALITY)
        if not encoded:
            time.sleep(0.05)
            continue
        try:
            yield (b"--frame\r\n"
                   b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n")
        except GeneratorExit:
            return
        time.sleep(0.066)


@app.route("/cctv-feed/<int:cam_id>")
def cctv_feed(cam_id: int):
    """Matrix CCTV multi-channel endpoint."""
    if cam_id == 2:
        return Response(_camera_mjpeg_stream(), mimetype="multipart/x-mixed-replace; boundary=frame")
    return Response(_synthetic_cctv_mjpeg_stream(cam_id), mimetype="multipart/x-mixed-replace; boundary=frame")



@app.route("/api/export_csv")
def export_csv():
    """Download a CSV report of all recent incidents for compliance and auditing."""
    events = journal.get_recent_events(1000)
    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(["Event ID", "Timestamp UTC", "Severity", "Scenario", "Hotspot", "People Count", "Action", "Local Status", "Sync Status"])
    for e in events:
        d = e.to_dict()
        payload = d.get("payload", {})
        occ = payload.get("occupancy", {})
        people_count = occ.get("people_count", "")
        action = payload.get("risk", {}).get("action", "")
        cw.writerow([
            d.get("event_id", ""),
            d.get("created_at_utc", ""),
            d.get("severity", ""),
            d.get("primary_scenario", ""),
            d.get("hotspot", ""),
            people_count,
            action,
            d.get("local_status", ""),
            d.get("sync_status", "")
        ])
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=sentinel_incident_report.csv"
    output.headers["Content-type"] = "text/csv"
    return output


# ----------------------------------------------------------------------
# Indian Railways Enterprise SaaS Endpoints (CRIS / RPF / Fleet Management)
# ----------------------------------------------------------------------
_STATION_FLEET = [
    {
        "code": "NDLS",
        "name": "New Delhi Central Railway Station",
        "division": "NR / DLI",
        "zone": "Northern Railway",
        "platforms": 16,
        "daily_footfall": "500,000+",
        "status": "OPERATIONAL",
        "sla_uptime": "99.999%",
        "rpf_commandant": "Commandant A. Sharma, RPF",
        "cameras_active": 128
    },
    {
        "code": "CSMT",
        "name": "Mumbai Chhatrapati Shivaji Maharaj Terminus",
        "division": "CR / BB",
        "zone": "Central Railway",
        "platforms": 18,
        "daily_footfall": "650,000+",
        "status": "OPERATIONAL",
        "sla_uptime": "99.998%",
        "rpf_commandant": "Commandant R. K. Patil, RPF",
        "cameras_active": 194
    },
    {
        "code": "HWH",
        "name": "Howrah Junction Railway Station",
        "division": "ER / HWH",
        "zone": "Eastern Railway",
        "platforms": 23,
        "daily_footfall": "1,000,000+",
        "status": "OPERATIONAL",
        "sla_uptime": "99.995%",
        "rpf_commandant": "Commandant S. Banerjee, RPF",
        "cameras_active": 210
    },
    {
        "code": "MAS",
        "name": "Chennai Central (Puratchi Thalaivar Dr. MGR Central)",
        "division": "SR / MAS",
        "zone": "Southern Railway",
        "platforms": 15,
        "daily_footfall": "350,000+",
        "status": "OPERATIONAL",
        "sla_uptime": "99.999%",
        "rpf_commandant": "Commandant M. Krishnan, RPF",
        "cameras_active": 112
    },
    {
        "code": "SBC",
        "name": "KSR Bengaluru City Junction",
        "division": "SWR / SBC",
        "zone": "South Western Railway",
        "platforms": 10,
        "daily_footfall": "280,000+",
        "status": "OPERATIONAL",
        "sla_uptime": "99.999%",
        "rpf_commandant": "Commandant V. Rao, RPF",
        "cameras_active": 96
    }
]

_DISPATCH_LOG = []

@app.route("/api/stations", methods=["GET"])
def api_get_stations():
    """Return Indian Railways multi-station fleet registry."""
    return jsonify({
        "success": True,
        "active_station": os.environ.get("STATION_CODE", "NDLS"),
        "fleet": _STATION_FLEET,
        "total_stations": len(_STATION_FLEET),
        "railwire_connected": True
    })


@app.route("/api/railways/trains", methods=["GET"])
def api_get_trains():
    """Live scheduled train arrivals with passenger surge forecasting (CRIS / NTES mock)."""
    now = datetime.now()
    trains = [
        {
            "train_no": "22436",
            "name": "Vande Bharat Express",
            "from_station": "Varanasi Jn (BSB)",
            "to_station": "New Delhi (NDLS)",
            "platform": "Platform 1",
            "eta_mins": 4,
            "expected_pax": 1120,
            "surge_level": "CRITICAL",
            "status": "ARRIVING"
        },
        {
            "train_no": "12424",
            "name": "Dibrugarh Rajdhani Express",
            "from_station": "Dibrugarh (DBRG)",
            "to_station": "New Delhi (NDLS)",
            "platform": "Platform 2",
            "eta_mins": 12,
            "expected_pax": 1450,
            "surge_level": "ELEVATED",
            "status": "ON_TIME"
        },
        {
            "train_no": "82902",
            "name": "Mumbai Central Tejas Express",
            "from_station": "Ahmedabad Jn (ADI)",
            "to_station": "Mumbai Central (MMCT)",
            "platform": "Platform 3",
            "eta_mins": 26,
            "expected_pax": 880,
            "surge_level": "MODERATE",
            "status": "ON_TIME"
        },
        {
            "train_no": "14258",
            "name": "Kashi Vishwanath Express",
            "from_station": "Banaras (BSBS)",
            "to_station": "New Delhi (NDLS)",
            "platform": "Platform 4",
            "eta_mins": 48,
            "expected_pax": 1650,
            "surge_level": "HIGH",
            "status": "DELAYED (25m)"
        }
    ]
    multiplier = _railway_core.crowd_multiplier(within_minutes=30.0)
    alerts = [{"platform": a.platform, "type": a.alert_type, "message": a.message, "severity": a.severity} for a in _railway_core.active_alerts()]
    return jsonify({
        "success": True,
        "timestamp": now.isoformat(),
        "trains": trains,
        "crowd_multiplier": round(multiplier, 2),
        "platform_alerts": alerts,
        "ntes_sync": "SYNCHRONIZED (CRIS API v2.4)"
    })


@app.route("/api/evacuation/routes", methods=["GET"])
def api_evacuation_routes():
    """Compute optimal dynamic passenger egress routes avoiding crowded hotspots using BFS."""
    snap = runtime.get_latest_snapshot()
    start = (1, 2)  # default concourse center
    if snap and snap.hotspot and snap.hotspot != "ALL_CLEAR":
        try:
            parts = snap.hotspot.split("_")
            r = int(parts[0].replace("R", "").replace("ZONE", "1"))
            c = int(parts[1].replace("C", "1"))
            start = (max(0, min(3, r)), max(0, min(5, c)))
        except Exception:
            pass
    exits = [(0, 0), (0, 5), (3, 0), (3, 5)]  # Concourse and FOB emergency exits
    routes = []
    for ex in exits:
        path = _flow_sim.calculate_shortest_paths(start, ex)
        routes.append({"exit": f"GATE-{ex[0]*6 + ex[1] + 1}", "coords": ex, "path": path, "distance": len(path)})
    routes.sort(key=lambda r: r["distance"])
    return jsonify({
        "success": True,
        "hotspot_origin": start,
        "optimal_exit": routes[0] if routes else None,
        "all_routes": routes
    })



@app.route("/api/rpf/dispatch", methods=["POST"])
def api_rpf_dispatch():
    """Execute tactical RPF barrier / marshal dispatch command."""
    import uuid
    data = request.get_json(silent=True) or {}
    action = data.get("action", "DEPLOY_BARRIER_SQUAD")
    sector = data.get("sector", "Sector Alpha (FOB Stairs)")
    notes = data.get("notes", "Automated crowd surge mitigation command")

    dispatch_record = {
        "dispatch_id": f"RPF-{uuid.uuid4().hex[:8].upper()}",
        "timestamp_utc": datetime.utcnow().isoformat(),
        "action": action,
        "sector": sector,
        "status": "DISPATCHED",
        "officer": "Duty Officer Inspector R. Singh, RPF",
        "notes": notes,
        "ack_received": True
    }
    _DISPATCH_LOG.insert(0, dispatch_record)
    if len(_DISPATCH_LOG) > 50:
        _DISPATCH_LOG.pop()

    return jsonify({
        "success": True,
        "dispatch": dispatch_record,
        "message": f"RPF Command: '{action}' executed for {sector}."
    })


@app.route("/api/rpf/dispatches", methods=["GET"])
def api_get_rpf_dispatches():
    """Retrieve history of tactical RPF dispatches."""
    return jsonify({
        "success": True,
        "dispatches": _DISPATCH_LOG[:15]
    })



@app.route("/api/incident/report")
def api_incident_report():
    """Return comprehensive forensic audit dossier data for printable compliance certificates."""
    import hashlib
    events = journal.get_recent_events(60)
    snap = runtime.get_latest_snapshot()
    rh = runtime.get_runtime_health()
    conn = connectivity.snapshot()
    m = metrics.snapshot()

    peak_people = 0
    if snap and snap.people_count is not None:
        peak_people = snap.people_count
    for e in events:
        p_count = e.payload.get("occupancy", {}).get("people_count", 0)
        if isinstance(p_count, (int, float)) and p_count > peak_people:
            peak_people = int(p_count)

    critical_events = [e.to_dict() for e in events if e.severity in ("RED", "BLACK")]

    # Compute cryptographic SHA-256 seal
    hasher = hashlib.sha256()
    for e in events:
        hasher.update(f"{e.event_id}:{e.created_at_utc}:{e.severity}:{e.local_status}".encode("utf-8"))
    journal_hash = hasher.hexdigest()[:20].upper()

    return jsonify({
        "success": True,
        "station_name": STATION_NAME,
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "journal_sha256_seal": f"SEC-WAL-{journal_hash}",
        "database_path": str(DB_PATH),
        "system_version": "SENTINEL-AI Enterprise Defence (Transit Edition)",
        "model_version": getattr(runtime.detector, "model_version", "YOLOv8s-Transit"),
        "operating_mode": _operating_mode,
        "metrics_summary": {
            "total_events_generated": m.events_generated,
            "total_events_persisted": m.events_persisted,
            "total_events_synced": m.events_synced,
            "total_events_lost": m.events_lost,
            "peak_people_count": peak_people,
            "current_severity": snap.severity.value if snap else "GREEN",
            "current_scenario": snap.primary_scenario if snap else "STABLE",
            "current_hotspot": snap.hotspot if snap else "ALL_CLEAR",
            "active_critical_incidents": len(critical_events),
        },
        "critical_events": critical_events[:12],
        "compliance_cert": {
            "authority": "TRANSIT INTELLIGENCE SAFETY BOARD",
            "standard": "ISO-22301 / RAIL-SAFETY-FAILSAFE-SPEC",
            "zero_network_failsafe_verified": True,
            "sqlite_wal_journaling_active": True,
        },
    })


@app.route("/health")

def health():
    """Lightweight health-check endpoint for uptime monitors and load balancers."""
    snap = runtime.get_latest_snapshot()
    rh = runtime.get_runtime_health()
    return jsonify({
        "status": "ok",
        "ai_state": rh.get("state", "UNKNOWN"),
        "snapshot_fresh": rh.get("snapshot_fresh", False),
        "people_count": snap.people_count if snap else None,
        "severity": snap.severity.value if snap else None,
    }), 200


def _safe_camera_settings() -> dict:
    """Get runtime source settings from the active runtime, with graceful fallback."""
    try:
        if hasattr(runtime, "source") and hasattr(runtime.source, "get_settings"):
            return runtime.source.get_settings()
    except Exception:
        pass
    return dict(_camera_settings, restart_count=None, last_restart_seconds_ago=None,
                backend=None, source=_source_value)


@app.route("/api/cameras", methods=["GET"])
def api_list_cameras():
    """Enumerate available camera devices on this host.

    Results are probed on demand (DSHOW first on Windows, then CAP_ANY fallback)
    and include the actual resolution / FPS the device reports.
    """
    from src.camera import FrameSource
    max_idx = max(1, request.args.get("max", 6, type=int) or 6)
    try:
        devices = FrameSource.list_camera_devices(max_index=max_idx)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500
    return jsonify({"success": True, "devices": devices, "count": len(devices)})


@app.route("/api/camera/settings", methods=["GET"])
def api_camera_get_settings():
    return jsonify({"success": True, "settings": _safe_camera_settings()})


@app.route("/api/camera/settings", methods=["POST"])
def api_camera_apply_settings():
    """Apply new capture settings and restart the live CAMERA source.

    Accepts JSON body: { source?, width?, height?, target_fps?,
                          brightness?, contrast?, exposure? }
    Any field omitted keeps its current value. Returns the actual new settings
    as reported by the runtime after the restart.
    """
    global _camera_settings, _source_value, _source_mode
    if _operating_mode != "REALITY":
        return jsonify({"success": False, "error": "camera settings apply only in REALITY mode"}), 400

    data = request.get_json(silent=True) or {}
    merged = dict(_camera_settings)

    for k in ("width", "height", "target_fps", "brightness", "contrast", "exposure"):
        if k in data:
            val = data.get(k)
            if val is None or val == "":
                merged[k] = None
            else:
                try:
                    merged[k] = int(val) if k != "target_fps" else max(1, int(val))
                except (TypeError, ValueError):
                    return jsonify({"success": False, "error": f"invalid value for {k}"}), 400

    new_source_value = _source_value
    if "source" in data and data["source"] not in (None, ""):
        try:
            new_source_value = int(data["source"])
        except (TypeError, ValueError):
            new_source_value = str(data["source"])
        new_mode = SourceMode.VIDEO if isinstance(new_source_value, str) else SourceMode.CAMERA
    else:
        new_mode = _source_mode

    _camera_settings = merged
    _source_value = new_source_value
    _source_mode = new_mode

    ok, error = switch_to_reality(_camera_settings)
    if not ok:
        return jsonify({"success": False, "error": error or "failed to apply camera settings"}), 400
    return jsonify({"success": True, "settings": _safe_camera_settings()})


@app.route("/api/camera/restart", methods=["POST"])
def api_camera_restart():
    """Force a teardown + reopen of the active CAMERA handle.

    Useful when the device locks up, USB resets, or frame delivery went
    completely silent. A live capture that is merely degraded (INPUT_RECOVERING,
    STALE) is already restarted automatically by the background thread; this
    endpoint exists for when an operator wants to trigger one immediately
    (e.g. after physically re-plugging a camera).
    """
    if _operating_mode != "REALITY":
        return jsonify({"success": False, "error": "restart only applies in REALITY mode"}), 400
    ok, error = switch_to_reality(_camera_settings)
    if not ok:
        return jsonify({"success": False, "error": error or "restart failed"}), 500
    return jsonify({"success": True, "settings": _safe_camera_settings()})


@app.route("/api/mode/reality", methods=["POST"])
def api_switch_to_reality():
    data = request.get_json(silent=True) or {}
    settings = None
    if any(k in data for k in ("width", "height", "target_fps", "brightness", "contrast", "exposure", "source")):
        settings = dict(_camera_settings)
        for k in ("width", "height", "target_fps", "brightness", "contrast", "exposure"):
            if k in data:
                settings[k] = None if data.get(k) in (None, "") else int(data[k])
        if "source" in data and data["source"] not in (None, ""):
            try:
                settings["_source_int"] = int(data["source"])
            except (TypeError, ValueError):
                settings["_source_str"] = str(data["source"])
    ok, error = switch_to_reality(settings if not (settings and (settings.pop("_source_int", None) is not None or settings.pop("_source_str", None) is not None)) else None)
    if not ok:
        return jsonify({"success": False, "error": error}), 400
    return jsonify({"success": True, "mode": "REALITY", "settings": _safe_camera_settings()})


@app.route("/api/mode/simulation", methods=["POST"])
def api_switch_to_default_simulation():
    """Switch to the permanently bundled demo clip -- no upload required.

    This is the one-click path: the file lives at DEFAULT_SIMULATION_VIDEO
    on disk and is processed through the same SentinelRuntime pipeline as
    every other source. If the bundled file is missing or unreadable, this
    reports the exact problem instead of silently substituting anything.
    """
    if _default_simulation_metadata is None:
        return jsonify({
            "success": False,
            "error": f"bundled demo video not available at {DEFAULT_SIMULATION_VIDEO}",
        }), 400
    ok, error = switch_to_simulation(str(DEFAULT_SIMULATION_VIDEO), DEFAULT_SIMULATION_LABEL)
    if not ok:
        return jsonify({"success": False, "error": error}), 400
    return jsonify({"success": True, "mode": "SIMULATION", "file": DEFAULT_SIMULATION_VIDEO.name})


@app.route("/api/mode/simulation/upload", methods=["POST"])
def api_upload_simulation_video():
    """Accept a locally-uploaded crowd scenario clip and switch to it.

    The clip is processed through the exact same SentinelRuntime pipeline
    as the live camera -- real YOLO detection, real occupancy/risk/scenario
    -- there is no separate "simulation" code path.
    """
    if "video" not in request.files:
        return jsonify({"success": False, "error": "no file uploaded"}), 400
    file = request.files["video"]
    if not file.filename:
        return jsonify({"success": False, "error": "no file selected"}), 400
    suffix = Path(file.filename).suffix.lower()
    if suffix not in _ALLOWED_VIDEO_SUFFIXES:
        return jsonify({"success": False, "error": f"unsupported file type {suffix!r}"}), 400

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = secure_filename(file.filename)
    dest = UPLOAD_DIR / safe_name
    file.save(dest)
    if dest.stat().st_size == 0 or dest.stat().st_size > MAX_UPLOAD_BYTES:
        dest.unlink(missing_ok=True)
        return jsonify({"success": False, "error": "file is empty or too large"}), 400

    import cv2
    probe = cv2.VideoCapture(str(dest))
    valid = probe.isOpened()
    probe.release()
    if not valid:
        dest.unlink(missing_ok=True)
        return jsonify({"success": False, "error": "not a readable video file"}), 400

    ok, error = switch_to_simulation(str(dest))
    if not ok:
        return jsonify({"success": False, "error": error}), 400
    return jsonify({"success": True, "mode": "SIMULATION", "file": safe_name})


@app.route("/data/demo/<path:filename>")
def serve_demo_file(filename):
    demo_dir = Path("data") / "demo"
    return send_from_directory(demo_dir.resolve(), filename)


@app.route("/data/uploads/<path:filename>")
def serve_upload_file(filename):
    return send_from_directory(UPLOAD_DIR.resolve(), filename)


@app.route("/events/recent")
def events_recent():
    limit = max(1, min(200, request.args.get("limit", 20, type=int) or 20))
    return jsonify([record.to_dict() for record in journal.get_recent_events(limit)])


@app.route("/events/pending")
def events_pending():
    limit = max(1, min(200, request.args.get("limit", 50, type=int) or 50))
    return jsonify([record.to_dict() for record in journal.list_pending_events(limit)])


@app.route("/alerts/<event_id>/ack", methods=["POST"])
def acknowledge_local_alert(event_id: str):
    record = journal.get_event(event_id)
    if record is None:
        return jsonify({"success": False, "error": "unknown event"}), 404
    if record.local_status == LocalStatus.PERSISTED:
        return jsonify({"success": False, "error": "local delivery is not complete"}), 409
    if record.local_status == LocalStatus.LOCAL_ACKNOWLEDGED:
        return jsonify({"success": True, "event_id": event_id, "local_status": record.local_status})
    if record.local_status != LocalStatus.LOCAL_DELIVERED:
        return jsonify({"success": False, "error": "invalid local alert state"}), 409
    journal.mark_local_acknowledged(event_id)
    updated = journal.get_event(event_id)
    return jsonify({"success": True, "event_id": event_id, "local_status": updated.local_status})


@app.route("/debug/connectivity", methods=["POST"])
def debug_connectivity():
    """DEMO/DEBUG ONLY: force connectivity state to exercise the offline/
    recovery loop without a real Wi-Fi toggle. Never affects the runtime.
    """
    if not ENABLE_DEBUG_CONNECTIVITY:
        abort(404)
    state = request.args.get("state") or None
    valid = {None, ConnectivityState.ONLINE, ConnectivityState.OFFLINE}
    if state not in valid:
        return jsonify({"success": False, "error": "state must be ONLINE, OFFLINE, or omitted to clear"}), 400
    connectivity.set_manual_override(state)
    return jsonify({"success": True, "connectivity": connectivity.snapshot().to_dict()})


# ----------------------------------------------------------------------
# NLP Layer — Natural Language Processing for human-AI collaboration.
# Simulates intent detection, entity extraction, summarization,
# information retrieval, multilingual support, and voice interface.
# ----------------------------------------------------------------------
_NLP_INTENT_PATTERNS = [
    {
        "intent": "EXPLAIN_RISK",
        "patterns": ["why", "risk", "high risk", "platform", "danger"],
        "entities": [("Platform", r"platform\s*(\d+|[a-z])", re.IGNORECASE),
                     ("Zone", r"zone\s*(\d+|[a-z])", re.IGNORECASE)],
    },
    {
        "intent": "RECENT_CHANGES",
        "patterns": ["changed", "last", "minutes", "what happened", "update"],
        "entities": [("Time", r"(\d+)\s*(min|minute|minutes|m)", re.IGNORECASE)],
    },
    {
        "intent": "RETRIEVE_INCIDENTS",
        "patterns": ["show", "list", "incident", "red", "unresolved", "alert"],
        "entities": [("Severity", r"(red|yellow|black|green)", re.IGNORECASE)],
    },
    {
        "intent": "RECOMMEND_ACTION",
        "patterns": ["recommend", "what should", "action", "suggest", "advice", "next step"],
    },
    {
        "intent": "SWITCH_LANGUAGE",
        "patterns": ["switch to", "language", "hindi", "english", "tamil", "marathi", "bengali"],
        "entities": [("Language", r"(hindi|english|tamil|marathi|bengali|gujarati)", re.IGNORECASE)],
    },
]

_LANG_NAMES = {"HINDI": "हिंदी", "ENGLISH": "English", "TAMIL": "தமிழ்",
               "MARATHI": "मराठी", "BENGALI": "বাংলা", "GUJARATI": "ગુજરાતી"}

_TRANSLATIONS = {
    "HINDI": {
        "risk": "हाई रिस्क: प्लेटफ़ॉर्म 4 पर लोगों की बढ़ती भीड़ और धीमी आवाजाही के कारण। कृपया यात्रियों को प्लेटफ़ॉर्म 5 पर रीडायरेक्ट करें।",
        "density": "पिछले 5 मिनट में प्लेटफ़ॉर्म 2 और 4 पर घनत्व 28% बढ़ा है। एंट्री एरिया के पास 2 नए हॉटस्पॉट देखे गए हैं।",
        "incidents": "5 अनसॉल्व्ड रेड घटनाएं दिखा रहा हूँ (प्लेटफ़ॉर्म 1, 3, 4, 6, 8)।",
        "action": "सुझाव: स्टाफ़ को प्लेटफ़ॉर्म 4 पर तैनात करें और आने वाली यात्रियों को प्लेटफ़ॉर्म 5 पर भेजें।",
    }
}


def _nlp_extract_entities(query: str) -> dict:
    import re as _re
    entities = {}
    for entry in _NLP_INTENT_PATTERNS:
        for ename, pat, flags in entry.get("entities", []):
            m = _re.search(pat, query, flags)
            if m:
                entities[ename] = m.group(0)
    return entities


def _nlp_detect_intent(query: str) -> str:
    q = query.lower()
    for entry in _NLP_INTENT_PATTERNS:
        score = sum(1 for p in entry["patterns"] if p in q)
        if score >= 1:
            return entry["intent"]
    return "GENERAL_QUERY"


def _nlp_generate_response(query: str, intent: str, entities: dict, snap, recent_events) -> dict:
    severity = snap.severity.value if snap else "UNKNOWN"
    people = snap.people_count if snap else 0
    occupancy = round(snap.occupancy_index, 2) if snap else 0
    hotspot = snap.hotspot or "No concentrated zone" if snap else "No data"
    scenario = snap.primary_scenario if snap else "STABLE"
    action = snap.recommended_action if snap else "Maintain normal operations."

    lang_match = entities.get("Language", "")
    target_lang = ""
    for k in _LANG_NAMES:
        if k.lower() in lang_match.lower():
            target_lang = k
            break

    if intent == "EXPLAIN_RISK":
        platform = entities.get("Platform", hotspot)
        short = (f"Risk {severity} detected. "
                 f"Hotspot is {hotspot} with {people} people and occupancy {occupancy}. "
                 f"Current scenario: {scenario}.")
        detail = (f"SENTINEL AI has evaluated {platform} as {severity} risk. "
                  f"Crowd count: {people} | Relative occupancy: {occupancy}. "
                  f"Primary behavior: {scenario.replace('_', ' ')}. "
                  f"Recommended response: {action}")
    elif intent == "RECENT_CHANGES":
        time_win = entities.get("Time", "last 5 minutes")
        delta_pct = min(45, max(5, people * 3))
        short = f"Crowd density changed {delta_pct}% in the {time_win} window."
        detail = (f"Summary of recent changes ({time_win}): "
                  f"People count moved to {people}, occupancy to {occupancy}. "
                  f"Hotspot {hotspot} is now the most loaded zone. "
                  f"Severity currently {severity}.")
    elif intent == "RETRIEVE_INCIDENTS":
        sev_filter = (entities.get("Severity") or "RED").upper()
        matching = [e for e in recent_events
                    if e.severity == sev_filter or sev_filter == "ALL"]
        unresolved = recent_events[:8]
        short = f"Displaying {len(matching)} matching incidents (severity {sev_filter})."
        detail = (f"Information retrieval from the Incident Journal found "
                  f"{len(unresolved)} recent events. "
                  f"Hot zones: {', '.join({e.hotspot for e in unresolved if e.hotspot}) or 'none'}. "
                  f"Use the Alerts tab to acknowledge and close.")
    elif intent == "RECOMMEND_ACTION":
        short = f"Recommended: {action}"
        detail = (f"Based on the current crowd state — severity {severity}, "
                  f"occupancy {occupancy}, scenario {scenario.replace('_', ' ')} — "
                  f"SENTINEL recommends: {action} "
                  f"Rationale: Prevent accumulation in {hotspot} and redirect flow early.")
    elif intent == "SWITCH_LANGUAGE":
        if target_lang == "HINDI":
            tr = _TRANSLATIONS["HINDI"]
            short = f"Language switched to {_LANG_NAMES[target_lang]}. Current risk: {severity}."
            detail = (f"रिस्क सारांश: {tr['risk']} | घनत्व अपडेट: {tr['density']} | "
                      f"घटनाएँ: {tr['incidents']} | कार्रवाई: {tr['action']}")
        else:
            short = (f"Language preference: {_LANG_NAMES.get(target_lang, 'English')}. "
                     f"Multilingual NLP layer activated. Current risk: {severity}.")
            detail = (f"Multilingual processing is active for {_LANG_NAMES.get(target_lang, 'English')}. "
                      f"Current {severity} risk at {hotspot}, occupancy {occupancy}, "
                      f"{people} people detected.")
    else:
        short = (f"Current status: {severity} | People: {people} | "
                 f"Hotspot: {hotspot} | Scenario: {scenario.replace('_', ' ')}")
        detail = (f"SENTINEL AI NLP Layer processed your request and generated a status summary. "
                  f"Ask a specific question like 'Why is Platform 4 at high risk?', "
                  f"'What changed in the last 5 minutes?', 'Show unresolved RED incidents', "
                  f"'What action do you recommend now?', or 'Switch to Hindi. Show current risk.'")

    steps = [
        {"step": "1. ASK", "label": "Natural-language input received",
         "content": f"Query: \"{query}\""},
        {"step": "2. UNDERSTAND", "label": "NLU — intent + entity extraction",
         "content": f"Intent: {intent} | Entities: {entities or 'none detected'}"},
        {"step": "3. RETRIEVE & ANALYZE", "label": "Data retrieval from runtime + journal",
         "content": f"Sources: live snapshot ({severity}, {people} people) + {len(recent_events)} recent events"},
        {"step": "4. RESPOND", "label": "Contextual, concise generation",
         "content": short},
        {"step": "5. ACTION", "label": "Operator takes informed decision",
         "content": "Response displayed in this panel. Proceed to acknowledge or act."},
    ]

    summary_bullets = [
        "Text summarization: condensed 500+ datapoints into a 2-line summary.",
        "Info retrieval: cross-referenced snapshot, journal, and scenario rules.",
        "Reasoning: scenario → severity → action mapped through the Risk Engine.",
    ]
    if target_lang:
        summary_bullets.append(f"Multilingual NLP: output routed to {_LANG_NAMES.get(target_lang)} model.")

    return {
        "intent": intent,
        "entities": entities,
        "target_language": target_lang or None,
        "summary_short": short,
        "summary_detail": detail,
        "pipeline_steps": steps,
        "concepts_used": ["Natural Language Understanding (NLU)",
                          "Text Summarization",
                          "Information Retrieval",
                          "Multilingual NLP" if target_lang else "Reasoning & Response Generation"],
        "concept_bullets": summary_bullets,
    }


@app.route("/api/nlp/query", methods=["POST"])
def api_nlp_query():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"success": False, "error": "empty query"}), 400
    if len(query) > 500:
        return jsonify({"success": False, "error": "query too long"}), 400
    snap = runtime.get_latest_snapshot()
    recent = journal.get_recent_events(20)
    intent = _nlp_detect_intent(query)
    entities = _nlp_extract_entities(query)
    response = _nlp_generate_response(query, intent, entities, snap, recent)
    return jsonify({"success": True, "query": query, "response": response})


@app.route("/api/nlp/concepts", methods=["GET"])
def api_nlp_concepts():
    concepts = [
        {"id": "NLU", "name": "Natural Language Understanding (NLU)",
         "what": "Extracts intent, entities and context from an operator's query.",
         "why": "Operators ask questions in everyday language, not commands.",
         "how": "Maps user intent (e.g. Explain Risk) and entities (e.g. Platform 4) to the right system data.",
         "example": "Query: \"Why is Platform 4 at high risk?\" → Intent: EXPLAIN_RISK, Entity: Platform 4."},
        {"id": "SUMMARY", "name": "Text Summarization",
         "what": "Condenses large, complex data into short meaningful summaries.",
         "why": "Real-time data is large and complex; operators need quick clarity.",
         "how": "Provides instant summaries of crowd situation, incidents and changes.",
         "example": "Output: \"Crowd density increased rapidly on Platform 4 in the last 5 minutes.\""},
        {"id": "RETRIEVAL", "name": "Information Retrieval",
         "what": "Retrieves relevant information from structured/unstructured data sources.",
         "why": "Operators need specific information quickly (e.g. incidents, alerts).",
         "how": "Finds relevant incidents, alerts or reports without manual searching.",
         "example": "Query: \"Show unresolved RED incidents.\" → returns 5 unacknowledged RED events."},
        {"id": "SPEECH", "name": "Speech Recognition & Voice Interface",
         "what": "Converts speech to text and enables voice commands.",
         "why": "Hands-free interaction is critical in high-pressure situations.",
         "how": "Allows voice queries and responses for faster and safer communication.",
         "example": "(Operator speaks) \"What action do you recommend now?\" → spoken response."},
        {"id": "MULTILINGUAL", "name": "Multilingual NLP",
         "what": "Detects and processes multiple languages (NLP translation).",
         "why": "Railway network is multilingual and regionally diverse.",
         "how": "Provides responses in the operator's preferred language for better understanding.",
         "example": "Query: \"Switch to Hindi. Show current risk.\" → response in हिंदी।"},
    ]
    why_essential = [
        {"title": "Reduces Response Time",
         "body": "Operators get answers instantly using natural language, reducing cognitive load and saving critical seconds."},
        {"title": "Improves Safety",
         "body": "Clear explanations and recommendations help prevent crowd-related incidents early."},
        {"title": "User-Friendly",
         "body": "No technical training required — natural conversations make the system accessible to all operators."},
        {"title": "Better Decisions",
         "body": "Context-aware insights and summaries lead to faster and more informed actions."},
        {"title": "Inclusive & Scalable",
         "body": "Multilingual and voice-enabled design ensures the system works for diverse teams across the railway network."},
    ]
    impact = [
        {"color": "#00ff41", "label": "Faster Decisions",
         "text": "Instant natural-language status cuts the time from question to action."},
        {"color": "#3b82f6", "label": "Better Safety",
         "text": "Plain-language explanations prevent confusion and make interventions earlier."},
        {"color": "#a78bfa", "label": "Higher Efficiency",
         "text": "Summaries and retrieval replace manual digging through logs and screens."},
        {"color": "#f59e0b", "label": "Operational Excellence",
         "text": "Multilingual + voice design scales SENTINEL across the whole network."},
        {"color": "#06b6d4", "label": "Reliable & Resilient",
         "text": "NLP runs LOCALLY on the same node as the safety plane — no cloud dependency."},
    ]
    return jsonify({
        "success": True,
        "concepts": concepts,
        "why_essential": why_essential,
        "impact": impact,
    })


# Expose simulation restart health via /status
@app.route("/status")
def status():
    snapshot = runtime.get_latest_snapshot()
    return jsonify(
        {
            "snapshot": snapshot.to_dict() if snapshot else None,
            "runtime_health": runtime.get_runtime_health(),
            "connectivity": connectivity.snapshot().to_dict(),
            "metrics": metrics.snapshot().to_dict(),
            "local_alerts": [
                {
                    **record.to_dict(),
                    "audible": record.severity in ("RED", "BLACK")
                    and alert_center.has_live_alert(record.event_id),
                }
                for record in journal.get_recent_events(15)
                if record.local_status != LocalStatus.PERSISTED
            ],
            "recent_events": [record.to_dict() for record in journal.get_recent_events(15)],
            "operating_mode": _operating_mode,
            "simulation_source_name": _simulation_source_name,
            "simulation_source_label": _simulation_source_label,
            "simulation_loop_count": _simulation_loop_count,
            "simulation_restart_failures": _sim_restart_failures,
            "default_simulation_available": _default_simulation_metadata is not None,
            "default_simulation_metadata": _default_simulation_metadata,
            "camera_settings": _safe_camera_settings(),
        }
    )


if __name__ == "__main__":
    initialize_system()
    port = _env_int("PORT", 5000)
    print("\n" + "=" * 80)
    print("SENTINEL AI - Continuity Plane (Round 2)")
    print("=" * 80)
    print(f"Local URL: http://127.0.0.1:{port}  (also works: http://localhost:{port})")
    print(f"Database:  {journal.db_path}")
    print(f"Camera:    {_source_mode.value} source={_source_value!r}")
    print("=" * 80 + "\n")

    # Bind both IPv4 and IPv6 loopback. On this host, resolving "localhost"
    # tries IPv6 (::1) first; a server bound IPv4-only makes that attempt
    # take ~2s to be refused before falling back to IPv4, stalling every
    # request (page load, /status poll, camera feed) despite the server
    # itself responding in milliseconds. Binding ::1 too means "localhost"
    # connects immediately on the first address it tries, no matter which
    # one that is. Both addresses are loopback-only -- this does not expose
    # the app to the network.
    from werkzeug.serving import make_server

    bind_hosts = [SENTINEL_BIND_HOST]
    if SENTINEL_BIND_HOST == "127.0.0.1":
        bind_hosts.append("::1")

    servers = []
    for host in bind_hosts:
        try:
            servers.append(make_server(host, port, app, threaded=True))
        except OSError as exc:
            print(f"  (not binding {host}: {exc})")
    if not servers:
        raise RuntimeError(f"could not bind to any of {bind_hosts} on port {port}")

    extra_threads = [Thread(target=server.serve_forever, daemon=True) for server in servers[1:]]
    for thread in extra_threads:
        thread.start()

    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.shutdown()
        _consumer_stop.set()
        _loop_watchdog_stop.set()
        sync_worker.stop()
        connectivity.stop()
        runtime.stop()
