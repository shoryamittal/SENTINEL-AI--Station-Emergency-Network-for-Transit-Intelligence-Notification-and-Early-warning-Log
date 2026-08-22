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

import logging
import os
import time
from datetime import datetime
from pathlib import Path
from threading import Event, Lock, Thread

from flask import Flask, Response, abort, jsonify, request

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

app = Flask(__name__)
app.logger.setLevel(logging.INFO)


@app.after_request
def add_cache_control(response):
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

_source_value = _resolve_camera_source(CAMERA_SOURCE)
_source_mode = SourceMode.VIDEO if isinstance(_source_value, str) else SourceMode.CAMERA

runtime_config = RuntimeConfig(
    grid_rows=_env_int("GRID_ROWS", 4),
    grid_cols=_env_int("GRID_COLS", 6),
    confidence_threshold=_env_float("CONFIDENCE_THRESHOLD", 0.5),
    model_path=os.environ.get("YOLO_MODEL", "yolov8n.pt"),
)

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

runtime = SentinelRuntime(
    FrameSource(_source_mode, _source_value),
    detector=_shared_detector,
    config=runtime_config,
    incident_sink=_durably_accept_incident,
)

# ----------------------------------------------------------------------
# REALITY / SIMULATION mode switching.
#
# Both modes build a real SentinelRuntime around a real FrameSource
# (CAMERA for reality, VIDEO for an uploaded scenario clip) -- there is no
# separate "simulation pipeline". Detection, occupancy, adaptive risk,
# scenario, and severity are identical code paths in both modes; only the
# frame source differs. _runtime_lock guarantees at most one FrameSource
# (and therefore at most one camera/video capture owner) is ever active.
# ----------------------------------------------------------------------
UPLOAD_DIR = Path("data") / "uploads"
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB, generous for a short demo clip
_ALLOWED_VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv"}

# The one approved, permanently bundled competition demo clip. It is
# processed through the exact same SentinelRuntime pipeline as any other
# VIDEO source -- there is no separate "default demo" code path.
DEFAULT_SIMULATION_VIDEO = Path("data") / "demo" / "crowd_station.mp4"
DEFAULT_SIMULATION_LABEL = "Crowded Railway Station"


def _probe_video(path: Path) -> dict | None:
    """Open+read real metadata from a video file, or None if it can't be used.

    Never fabricated: every field here comes from the file itself via OpenCV.
    """
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
_operating_mode = "REALITY"  # UI-facing label; independent of contracts.SourceMode
_simulation_source_name: str | None = None
_simulation_source_label: str | None = None
_simulation_source_path: str | None = None
_simulation_loop_count = 0


def _build_runtime(source_mode, source_value) -> SentinelRuntime:
    return SentinelRuntime(
        FrameSource(source_mode, source_value),
        detector=_shared_detector,
        config=runtime_config,
        incident_sink=_durably_accept_incident,
    )


def _switch_active_runtime(new_source_mode, new_source_value, new_label: str) -> tuple[bool, str | None]:
    """Atomically replace the single active FrameSource/SentinelRuntime.

    The new source is started before the old one is stopped, so a failed
    switch never leaves the system with no active source; if the new
    source fails to deliver frames the caller can detect that via its
    camera_health and revert without ever having torn down the old one.
    """
    global runtime, _operating_mode
    with _runtime_lock:
        old_runtime = runtime
        new_runtime = _build_runtime(new_source_mode, new_source_value)
        new_runtime.start()
        runtime = new_runtime
        old_runtime.stop()
        _operating_mode = new_label
        return True, None


def switch_to_reality() -> tuple[bool, str | None]:
    global _simulation_loop_count
    result = _switch_active_runtime(_source_mode, _source_value, "REALITY")
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


def _maybe_restart_simulation_loop() -> bool:
    """Restart the active scenario clip if it has played through to the end.

    Only acts while SIMULATION is the active mode and the active source is
    the video most recently switched to; every restart re-enters the real
    pipeline from frame 1 (no fake state reset, no synthetic frames).
    Returns True if a restart was triggered (for tests).
    """
    if _operating_mode != "SIMULATION" or _simulation_source_path is None:
        return False
    active = runtime
    if active.source.source_mode is not SourceMode.VIDEO:
        return False
    # VIDEO EOF sets FrameSource._recovering permanently (nothing ever flips
    # it back), so health() reports INPUT_RECOVERING forever from that point
    # on rather than escalating to CAMERA_LOST -- that state is exactly
    # "this clip is exhausted and needs a fresh read()".
    if active.source.health() in (CameraHealth.INPUT_RECOVERING, CameraHealth.CAMERA_LOST):
        switch_to_simulation(_simulation_source_path, _simulation_source_label, _looping=True)
        return True
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
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SENTINEL AI - Station Operations Console</title>
<style>
  :root {
    --bg: #070c11;
    --panel: #0d151c;
    --panel-2: #101b24;
    --border: #1c2b36;
    --border-soft: #16232c;
    --cyan: #22d3ee;
    --cyan-dim: rgba(34,211,238,.12);
    --text: #e6edf3;
    --muted: #7d92a3;
    --muted-2: #56707f;
    --amber: #f5b143;
    --red: #f2545b;
    --green: #35d399;
    --blue: #4d9fec;
    --mono: 'Cascadia Code', 'Consolas', 'SFMono-Regular', Menlo, monospace;
    --sans: 'Segoe UI', system-ui, -apple-system, sans-serif;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:var(--sans); background:var(--bg); color:var(--text); min-height:100vh; font-size:14px; }
  ::-webkit-scrollbar { width:10px; height:10px; }
  ::-webkit-scrollbar-track { background:var(--bg); }
  ::-webkit-scrollbar-thumb { background:#1e2f3a; border-radius:5px; }

  /* ---------------------------------------------------------------
     Shell: persistent sidebar + topbar + main workspace. Every "page"
     is a plain hidden/shown section in one document -- no reloads, one
     continuously-running /status poll drives all of them, and the
     single live camera <img> is re-parented between view slots so
     there is never more than one MJPEG connection open.
  ------------------------------------------------------------------*/
  .shell { display:grid; grid-template-columns:222px 1fr; grid-template-rows:56px 1fr; grid-template-areas:"side top" "side main"; min-height:100vh; }
  @media (max-width:900px){ .shell{ grid-template-columns:64px 1fr; } .nav-label{ display:none; } .brand-sub{ display:none; } }

  .topbar { grid-area:top; background:var(--panel); border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:space-between; padding:0 1.25rem; gap:1rem; }
  .topbar-left { display:flex; align-items:center; gap:.6rem; font-size:.85rem; color:var(--muted); min-width:0; }
  .topbar-left b { color:var(--text); font-weight:700; }
  .topbar-left .sep { color:var(--muted-2); }
  .topbar-right { display:flex; align-items:center; gap:.75rem; flex-wrap:wrap; }

  .sidebar { grid-area:side; background:var(--panel); border-right:1px solid var(--border); display:flex; flex-direction:column; padding:1rem .7rem; gap:.15rem; }
  .brand { display:flex; align-items:center; gap:.55rem; padding:.4rem .5rem 1.1rem .5rem; }
  .brand svg { flex-shrink:0; }
  .brand-name { font-size:1.05rem; font-weight:800; letter-spacing:.03em; color:var(--cyan); line-height:1.1; }
  .brand-sub { font-size:.62rem; color:var(--muted-2); letter-spacing:.08em; text-transform:uppercase; }

  .nav-item { display:flex; align-items:center; gap:.7rem; padding:.6rem .65rem; border-radius:.4rem; color:var(--muted); cursor:pointer; border-left:2px solid transparent; font-size:.82rem; font-weight:600; letter-spacing:.01em; transition:background .12s, color .12s; }
  .nav-item svg { flex-shrink:0; opacity:.85; }
  .nav-item:hover { background:#101c26; color:var(--text); }
  .nav-item.active { background:var(--cyan-dim); color:var(--cyan); border-left-color:var(--cyan); }
  .nav-item.active svg { opacity:1; }
  .nav-spacer { flex:1; }
  .nav-foot { font-size:.62rem; color:var(--muted-2); padding:.6rem .65rem; letter-spacing:.03em; }

  .main { grid-area:main; padding:1.1rem 1.25rem 2rem 1.25rem; overflow-y:auto; }
  .view { display:none; }
  .view.active { display:block; }
  .view-title { font-size:1rem; font-weight:700; letter-spacing:.02em; margin-bottom:1rem; display:flex; align-items:center; gap:.6rem; }
  .view-title .sub { font-size:.72rem; font-weight:500; color:var(--muted); text-transform:none; letter-spacing:0; }

  /* ---------------- Operating mode segmented control ---------------- */
  .mode-switch { display:inline-flex; background:var(--bg); border:1px solid var(--border); border-radius:.4rem; padding:.15rem; gap:.15rem; }
  .mode-btn { background:transparent; border:none; color:var(--muted); font-weight:700; font-size:.68rem; letter-spacing:.05em; padding:.4rem .8rem; border-radius:.3rem; cursor:pointer; transition:all .15s; font-family:var(--sans); }
  .mode-btn:hover { color:var(--text); }
  .mode-btn.active { background:var(--cyan); color:#04141a; }
  .mode-btn.active#mode-btn-simulation { background:#a78bfa; color:#1a1030; }

  /* ---------------- Badges & status language ---------------- */
  .badge { display:inline-flex; align-items:center; gap:.35rem; padding:.3rem .6rem; border-radius:.3rem; font-weight:700; font-size:.68rem; letter-spacing:.04em; white-space:nowrap; }
  .badge::before { content:''; width:.4rem; height:.4rem; border-radius:50%; background:currentColor; flex-shrink:0; }
  .badge-GREEN { background:rgba(53,211,153,.12); color:var(--green); border:1px solid rgba(53,211,153,.3); }
  .badge-YELLOW { background:rgba(245,177,67,.12); color:var(--amber); border:1px solid rgba(245,177,67,.3); }
  .badge-RED { background:rgba(242,84,91,.14); color:var(--red); border:1px solid rgba(242,84,91,.35); }
  .badge-BLACK { background:rgba(230,237,243,.1); color:var(--text); border:1px solid rgba(230,237,243,.25); }
  .badge-UNKNOWN, .badge-STALE { background:rgba(125,146,163,.1); color:var(--muted); border:1px dashed var(--muted-2); }
  .badge-ONLINE { background:rgba(77,159,236,.12); color:var(--blue); border:1px solid rgba(77,159,236,.3); }
  .badge-DEGRADED { background:rgba(245,177,67,.12); color:var(--amber); border:1px solid rgba(245,177,67,.3); }
  .badge-OFFLINE { background:rgba(125,146,163,.12); color:var(--muted); border:1px solid var(--border); }
  .badge-RECOVERY { background:rgba(34,211,238,.12); color:var(--cyan); border:1px solid rgba(34,211,238,.3); }
  .badge-LIVE { background:rgba(53,211,153,.12); color:var(--green); border:1px solid rgba(53,211,153,.3); }
  .badge-ACTIVE { background:rgba(34,211,238,.12); color:var(--cyan); border:1px solid rgba(34,211,238,.3); }
  .badge-CAMERA_LOST, .badge-STOPPED, .badge-NOT_STARTED { background:rgba(242,84,91,.1); color:var(--red); border:1px solid rgba(242,84,91,.3); }
  .badge-DEFERRED { background:rgba(245,177,67,.1); color:var(--amber); border:1px solid rgba(245,177,67,.3); }
  .badge-INPUT_RECOVERING, .badge-DEGRADED2, .badge-STARTING { background:rgba(245,177,67,.12); color:var(--amber); border:1px solid rgba(245,177,67,.3); }

  /* ---------------- Cards / stat tiles ---------------- */
  .card { background:var(--panel); border:1px solid var(--border); border-radius:.5rem; padding:1rem 1.1rem; }
  .card h2 { font-size:.68rem; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin-bottom:.85rem; font-weight:700; }
  .grid-kpi { display:grid; grid-template-columns:repeat(6,1fr); gap:.7rem; margin-bottom:1rem; }
  @media (max-width:1300px){ .grid-kpi{ grid-template-columns:repeat(3,1fr); } }
  @media (max-width:640px){ .grid-kpi{ grid-template-columns:repeat(2,1fr); } }
  .kpi { background:var(--panel); border:1px solid var(--border); border-radius:.5rem; padding:.85rem 1rem; }
  .kpi .label { font-size:.62rem; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin-bottom:.35rem; font-weight:700; }
  .kpi .value { font-size:1.55rem; font-weight:800; color:var(--text); font-family:var(--mono); line-height:1; }
  .kpi .note { font-size:.65rem; color:var(--muted-2); margin-top:.3rem; }
  .kpi.risk { border-width:1px; }
  .kpi.risk.sev-GREEN { border-color:rgba(53,211,153,.4); background:rgba(53,211,153,.05); } .kpi.risk.sev-GREEN .value { color:var(--green); }
  .kpi.risk.sev-YELLOW { border-color:rgba(245,177,67,.4); background:rgba(245,177,67,.05); } .kpi.risk.sev-YELLOW .value { color:var(--amber); }
  .kpi.risk.sev-RED { border-color:rgba(242,84,91,.45); background:rgba(242,84,91,.07); } .kpi.risk.sev-RED .value { color:var(--red); }
  .kpi.risk.sev-BLACK { border-color:rgba(230,237,243,.3); } .kpi.risk.sev-BLACK .value { color:var(--text); }

  .stat { background:var(--bg); border:1px solid var(--border); border-radius:.4rem; padding:.65rem .8rem; }
  .stat .label { font-size:.62rem; text-transform:uppercase; color:var(--muted); letter-spacing:.05em; margin-bottom:.25rem; font-weight:700; }
  .stat .value { font-size:1.05rem; font-weight:700; color:var(--text); font-family:var(--mono); }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:.6rem; }
  .grid3 { display:grid; grid-template-columns:repeat(3,1fr); gap:.6rem; }
  .grid4 { display:grid; grid-template-columns:repeat(4,1fr); gap:.6rem; }
  @media (max-width:700px){ .grid3,.grid4{ grid-template-columns:repeat(2,1fr); } }

  .cols-2 { display:grid; grid-template-columns:1.3fr 1fr; gap:1rem; align-items:start; }
  @media (max-width:1150px){ .cols-2{ grid-template-columns:1fr; } }
  .stack { display:flex; flex-direction:column; gap:1rem; }

  /* ---------------- Stale / disconnected banners ---------------- */
  .banner { display:none; align-items:center; gap:.5rem; background:rgba(125,146,163,.1); border:1px dashed var(--muted-2); color:var(--muted); padding:.6rem .8rem; border-radius:.4rem; margin-bottom:1rem; font-size:.78rem; }
  .banner.show { display:flex; }

  /* ---------------- Camera panel ---------------- */
  .camera-frame { position:relative; background:#000; border:1px solid var(--border); border-radius:.5rem; overflow:hidden; }
  .camera-frame img { width:100%; height:100%; object-fit:contain; display:block; background:#000; }
  .camera-frame .cam-tag { position:absolute; top:.6rem; left:.6rem; display:flex; gap:.4rem; align-items:center; }
  .camera-frame .cam-tag span { background:rgba(7,12,17,.75); backdrop-filter:blur(2px); border:1px solid rgba(255,255,255,.12); padding:.25rem .5rem; border-radius:.3rem; font-size:.65rem; font-weight:700; letter-spacing:.04em; font-family:var(--mono); }
  /* Camera badge shows source HEALTH (is the picture actually fresh?),
     never just source TYPE -- REALITY does not automatically mean LIVE. */
  .cam-health-LIVE { color:var(--green); }
  .cam-health-STALE, .cam-health-INPUT_RECOVERING { color:var(--amber); }
  .cam-health-CAMERA_LOST { color:var(--red); }

  /* ---------------- Topbar compact status strip ---------------- */
  .status-strip { display:flex; align-items:center; gap:.6rem; padding:0 .5rem; border-right:1px solid var(--border); margin-right:.25rem; }
  .status-chip { display:flex; flex-direction:column; align-items:flex-start; line-height:1.2; }
  .status-chip .k { font-size:.58rem; text-transform:uppercase; letter-spacing:.05em; color:var(--muted-2); font-weight:700; }
  .status-chip .v { font-size:.72rem; font-weight:700; font-family:var(--mono); }
  .status-chip .v.ok { color:var(--green); }
  .status-chip .v.warn { color:var(--amber); }
  .status-chip .v.bad { color:var(--red); }
  .status-chip .v.neutral { color:var(--muted); }
  .safety-note { font-size:.7rem; color:var(--muted); background:rgba(34,211,238,.06); border:1px solid rgba(34,211,238,.2); border-radius:.4rem; padding:.5rem .7rem; margin-top:.6rem; }
  .safety-note b { color:var(--cyan); }
  .cam-slot { min-height:220px; }
  .cam-slot.tall { min-height:420px; }
  .cam-placeholder { display:flex; align-items:center; justify-content:center; height:100%; min-height:220px; color:var(--muted-2); font-size:.8rem; text-align:center; padding:2rem; }
  .cam-strip { display:flex; gap:1rem; flex-wrap:wrap; margin-top:.75rem; font-size:.72rem; color:var(--muted); font-family:var(--mono); }
  .cam-strip b { color:var(--text); }

  /* ---------------- Crowd intelligence summary (dashboard/simulation) --- */
  .summary-row { display:grid; grid-template-columns:1fr 1fr 1.2fr; gap:.7rem; margin-bottom:.9rem; }
  @media (max-width:700px){ .summary-row{ grid-template-columns:1fr; } }
  .behavior-value { font-size:.95rem; font-weight:700; color:var(--text); background:var(--bg); border:1px solid var(--border); border-radius:.4rem; padding:.55rem .7rem; }
  .why-text { font-size:.78rem; color:#c3d2dc; line-height:1.5; background:var(--bg); border:1px solid var(--border); border-radius:.4rem; padding:.55rem .7rem; }
  .response-block { background:var(--bg); border:1px solid var(--border); border-left:3px solid var(--cyan); border-radius:.4rem; padding:.6rem .75rem; }
  .response-action { font-size:.86rem; font-weight:700; color:var(--text); }
  .response-zone { font-size:.7rem; color:var(--muted); margin-top:.25rem; }
  .subhead { font-size:.65rem; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:.9rem 0 .5rem 0; font-weight:700; }
  .subhead:first-child { margin-top:0; }

  /* ---------------- Spatial map ---------------- */
  .spatial-layout { display:flex; gap:1.1rem; align-items:flex-start; flex-wrap:wrap; }
  .zone-map-lg { display:grid; grid-template-columns:repeat(6, 1fr); grid-auto-rows:1fr; gap:5px; width:100%; max-width:460px; aspect-ratio:6/4; flex-shrink:0; }
  .zone-map-lg.big { max-width:none; }
  .zone-cell { border-radius:.3rem; background:#0e1720; border:1px solid var(--border-soft); display:flex; flex-direction:column; align-items:center; justify-content:center; gap:.15rem; font-size:.72rem; color:#3f5566; font-weight:600; font-family:var(--mono); transition:background .2s; }
  .zone-cell .zc-id { font-size:.6rem; opacity:.75; letter-spacing:.03em; }
  .zone-cell .zc-val { font-size:1rem; font-weight:800; line-height:1; }
  .zone-cell.load-low { color:var(--muted-2); background:#0e1720; }
  .zone-cell.load-med { background:rgba(245,177,67,.12); border-color:rgba(245,177,67,.4); color:var(--amber); font-size:.8rem; font-weight:700; }
  .zone-cell.load-high { background:rgba(242,84,91,.14); border-color:rgba(242,84,91,.45); color:var(--red); font-size:.85rem; font-weight:800; }
  .zone-cell.load-hotspot { background:rgba(242,84,91,.22); border-color:var(--red); color:#ffd9db; font-size:.9rem; font-weight:800; box-shadow:0 0 0 1px var(--red) inset; }
  .spatial-side { flex:1; min-width:180px; display:flex; flex-direction:column; gap:.9rem; }
  .spatial-side .label { font-size:.62rem; text-transform:uppercase; color:var(--muted); letter-spacing:.05em; margin-bottom:.3rem; font-weight:700; }
  .hotspot-block .value { font-size:1.3rem; font-weight:800; color:var(--red); font-family:var(--mono); }
  .hotspot-block .sub { font-size:.72rem; color:var(--muted); margin-top:.2rem; }
  .top-zones-row { display:flex; align-items:center; gap:.5rem; font-size:.76rem; color:#c3d2dc; margin-bottom:.4rem; }
  .top-zones-row .tz-id { width:2.6rem; color:var(--muted); font-weight:700; font-family:var(--mono); }
  .top-zones-row .tz-bar-track { flex:1; height:6px; background:#0e1720; border-radius:3px; overflow:hidden; }
  .top-zones-row .tz-bar-fill { height:100%; background:linear-gradient(90deg,var(--amber),var(--red)); }
  .top-zones-row .tz-val { width:2.2rem; text-align:right; font-weight:700; color:var(--text); font-family:var(--mono); }

  /* ---------------- L/A/R bars ---------------- */
  .lar-row { display:flex; align-items:center; gap:.6rem; font-size:.75rem; color:var(--muted); margin-bottom:.5rem; }
  .lar-row .lar-name { width:9rem; text-transform:uppercase; letter-spacing:.03em; font-size:.65rem; font-weight:700; }
  .lar-row .lar-bar-track { flex:1; height:7px; background:#0e1720; border-radius:4px; overflow:hidden; }
  .lar-row .lar-bar-fill { height:100%; background:linear-gradient(90deg,var(--cyan),#7dd3fc); }
  .lar-row .lar-val { width:3rem; text-align:right; font-weight:700; color:var(--text); font-family:var(--mono); }
  .interpretation { font-size:.76rem; color:var(--muted); background:var(--bg); border:1px solid var(--border); border-radius:.4rem; padding:.55rem .7rem; margin-top:.2rem; }

  /* ---------------- Simulation page ---------------- */
  .sim-source-card { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.8rem; background:var(--bg); border:1px solid var(--border); border-radius:.4rem; padding:.8rem 1rem; margin-bottom:1rem; }
  .sim-source-card .name { font-size:.95rem; font-weight:700; }
  .sim-source-card .meta { font-size:.7rem; color:var(--muted); font-family:var(--mono); margin-top:.25rem; }
  .btn { background:var(--cyan); color:#04141a; border:0; font-weight:700; font-size:.72rem; letter-spacing:.03em; padding:.5rem .9rem; border-radius:.35rem; cursor:pointer; }
  .btn:hover { filter:brightness(1.08); }
  .btn.secondary { background:transparent; color:var(--text); border:1px solid var(--border); }
  .btn.secondary:hover { border-color:var(--muted); }
  .upload-btn { display:inline-flex; align-items:center; background:#241a42; color:#c9b8ff; font-size:.72rem; font-weight:700; padding:.5rem .8rem; border-radius:.35rem; cursor:pointer; border:1px solid #372a5c; }
  .upload-btn:hover { background:#2c2050; }
  .upload-btn input { display:none; }
  #simulation-upload-status { font-size:.72rem; color:var(--muted); margin-left:.6rem; }
  .flow-strip { display:flex; align-items:center; gap:.6rem; font-size:.72rem; color:var(--muted); font-weight:700; letter-spacing:.03em; margin-bottom:1rem; flex-wrap:wrap; }
  .flow-strip .node { background:var(--bg); border:1px solid var(--border); border-radius:.3rem; padding:.35rem .6rem; color:var(--text); }
  .flow-strip .arrow { color:var(--cyan); }
  .explainer { font-size:.76rem; color:#c4b5fd; background:rgba(167,139,250,.08); border:1px solid rgba(167,139,250,.25); border-radius:.4rem; padding:.6rem .8rem; margin-bottom:1rem; }
  .sim-unavailable { text-align:center; padding:3rem 1rem; color:var(--muted); }
  .sim-unavailable .btn { margin-top:1rem; }

  /* ---------------- Alerts ---------------- */
  .tab-row { display:flex; gap:.4rem; margin-bottom:1rem; border-bottom:1px solid var(--border); }
  .tab-btn { background:transparent; border:none; color:var(--muted); font-size:.75rem; font-weight:700; letter-spacing:.03em; padding:.55rem .9rem; cursor:pointer; border-bottom:2px solid transparent; font-family:var(--sans); }
  .tab-btn.active { color:var(--cyan); border-bottom-color:var(--cyan); }
  .alert-card { background:var(--bg); border:1px solid var(--border); border-left:3px solid var(--red); border-radius:.4rem; padding:.8rem .9rem; margin-bottom:.65rem; }
  .alert-card.sev-YELLOW { border-left-color:var(--amber); }
  .alert-card.sev-GREEN { border-left-color:var(--green); }
  .alert-card.acknowledged { border-left-color:var(--muted-2); opacity:.72; }
  .alert-head { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.5rem; margin-bottom:.4rem; }
  .alert-title { font-weight:800; font-size:.88rem; }
  .alert-meta { font-size:.72rem; color:var(--muted); margin-top:.2rem; }
  .ack-btn { margin-top:.6rem; background:var(--cyan); border:0; border-radius:.35rem; color:#04141a; cursor:pointer; font-weight:700; font-size:.68rem; padding:.4rem .7rem; }
  .empty-note { color:var(--muted-2); font-size:.8rem; padding:1rem 0; }

  .events-table { width:100%; border-collapse:collapse; font-size:.76rem; }
  .events-table th { text-align:left; color:var(--muted); text-transform:uppercase; font-size:.62rem; letter-spacing:.04em; padding:.5rem .5rem; border-bottom:1px solid var(--border); font-weight:700; }
  .events-table td { padding:.5rem; border-bottom:1px solid var(--border-soft); font-family:var(--mono); }
  .src-tag { font-size:.62rem; font-weight:700; padding:.15rem .4rem; border-radius:.25rem; letter-spacing:.03em; }
  .src-tag.REALITY { background:rgba(77,159,236,.12); color:var(--blue); }
  .src-tag.SIMULATION { background:rgba(167,139,250,.15); color:#c4b5fd; }

  /* ---------------- System health ---------------- */
  .health-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:.8rem; }
  @media (max-width:1100px){ .health-grid{ grid-template-columns:repeat(2,1fr); } }
  @media (max-width:560px){ .health-grid{ grid-template-columns:1fr; } }
  .health-card { background:var(--bg); border:1px solid var(--border); border-radius:.5rem; padding:.9rem 1rem; }
  .health-card .name { font-size:.68rem; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); font-weight:700; margin-bottom:.6rem; }
  .health-card .age { font-size:.7rem; color:var(--muted-2); margin-top:.5rem; font-family:var(--mono); }

  .debug-controls { display:flex; gap:.5rem; margin-top:.9rem; }
  .debug-btn { padding:.4rem .75rem; border-radius:.35rem; border:1px solid var(--border); background:var(--bg); color:var(--muted); font-size:.72rem; cursor:pointer; }
  .debug-btn:hover { border-color:var(--muted); color:var(--text); }
  .note { font-size:.68rem; color:var(--muted-2); margin-top:.5rem; line-height:1.5; }

  a.notif-bell { position:relative; color:var(--muted); cursor:default; }
  .notif-dot { position:absolute; top:-3px; right:-3px; width:.45rem; height:.45rem; border-radius:50%; background:var(--red); display:none; }
  .notif-dot.show { display:block; }
</style>
</head>
<body>
<div class="shell">

  <nav class="sidebar">
    <div class="brand">
      <svg width="30" height="30" viewBox="0 0 24 24" fill="none"><path d="M12 2 L21 6 V12 C21 17 17 20.5 12 22 C7 20.5 3 17 3 12 V6 Z" stroke="#22d3ee" stroke-width="1.6" fill="rgba(34,211,238,.06)"/><path d="M6.5 12.5 H9.2 L10.4 9.5 L12.2 15.5 L13.4 12.5 H17.5" stroke="#22d3ee" stroke-width="1.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>
      <div><div class="brand-name">SENTINEL AI</div><div class="brand-sub" id="brand-station">Station Console</div></div>
    </div>

    <div class="nav-item active" data-view="dashboard">
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="8" height="8" rx="1.3"/><rect x="13" y="3" width="8" height="5" rx="1.3"/><rect x="13" y="10" width="8" height="11" rx="1.3"/><rect x="3" y="13" width="8" height="8" rx="1.3"/></svg>
      <span class="nav-label">Dashboard</span>
    </div>
    <div class="nav-item" data-view="monitoring">
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="2" y="6" width="15" height="12" rx="1.5"/><path d="M17 10 L22 7 V17 L17 14 Z"/></svg>
      <span class="nav-label">Live Monitoring</span>
    </div>
    <div class="nav-item" data-view="map">
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="3" width="18" height="18" rx="1.5"/><path d="M3 9 H21 M3 15 H21 M9 3 V21 M15 3 V21"/></svg>
      <span class="nav-label">Spatial Map</span>
    </div>
    <div class="nav-item" data-view="simulation">
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="14" rx="1.5"/><path d="M9.5 8 L15 11 L9.5 14 Z" fill="currentColor" stroke="none"/><path d="M3 21 H21" stroke-linecap="round"/></svg>
      <span class="nav-label">Simulation</span>
    </div>
    <div class="nav-item" data-view="alerts">
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M18 8a6 6 0 1 0-12 0c0 7-3 8-3 8h18s-3-1-3-8"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/></svg>
      <span class="nav-label">Alerts &amp; Response</span>
      <a class="notif-bell" id="alerts-nav-dot-holder"></a>
    </div>
    <div class="nav-item" data-view="health">
      <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 21s-7-4.35-9.5-9C.7 8.2 3 4 7 4c2 0 3.6 1.2 5 3 1.4-1.8 3-3 5-3 4 0 6.3 4.2 4.5 8-2.5 4.65-9.5 9-9.5 9Z"/><path d="M4 12 H8 L10 9 L13 15 L15 12 H20"/></svg>
      <span class="nav-label">System Health</span>
    </div>

    <div class="nav-spacer"></div>
    <div class="nav-foot">SENTINEL AI &middot; Round 2</div>
  </nav>

  <header class="topbar">
    <div class="topbar-left">
      <b>SENTINEL AI</b><span class="sep">/</span><span id="topbar-view-name">Dashboard</span>
    </div>
    <div class="topbar-right">
      <div class="status-strip">
        <div class="status-chip"><span class="k">Camera</span><span class="v neutral" id="strip-camera">--</span></div>
        <div class="status-chip"><span class="k">AI</span><span class="v neutral" id="strip-ai">--</span></div>
        <div class="status-chip"><span class="k">WAN</span><span class="v neutral" id="strip-wan">--</span></div>
        <div class="status-chip"><span class="k">Sync</span><span class="v neutral" id="strip-sync">--</span></div>
      </div>
      <div class="mode-switch" id="mode-switch">
        <button class="mode-btn active" id="mode-btn-reality" onclick="setOperatingMode('REALITY')">REALITY</button>
        <button class="mode-btn" id="mode-btn-simulation" onclick="setOperatingMode('SIMULATION')">SIMULATION</button>
      </div>
      <span id="conn-badge" class="badge badge-ONLINE">CONNECTIVITY: --</span>
    </div>
  </header>

  <main class="main">

    <!-- ============================================================
         DASHBOARD -- the judge-first view. KPI strip, camera + crowd
         intelligence summary, dynamics + system health + incidents.
    =============================================================== -->
    <section class="view active" id="view-dashboard">
      <div class="banner" id="stale-banner-dashboard">INPUT STALE</div>

      <div class="grid-kpi">
        <div class="kpi risk" id="kpi-risk"><div class="label">System State</div><div class="value" id="kpi-risk-value">--</div><div class="note" id="kpi-risk-note">--</div></div>
        <div class="kpi"><div class="label">People</div><div class="value" id="db-people">--</div></div>
        <div class="kpi"><div class="label">Occupancy</div><div class="value" id="db-occ">--</div><div class="note">Relative index</div></div>
        <div class="kpi"><div class="label">Camera</div><div class="value" id="kpi-camera">--</div></div>
        <div class="kpi"><div class="label">AI Latency</div><div class="value" id="kpi-ai-latency">--</div></div>
        <div class="kpi"><div class="label">Pending Sync</div><div class="value" id="kpi-sync">--</div></div>
      </div>

      <div class="cols-2">
        <div class="card">
          <h2>Live Input</h2>
          <div class="camera-frame cam-slot" id="camera-slot-dashboard">
            <div class="cam-tag"><span id="cam-badge-dashboard">LIVE</span><span id="cam-source-dashboard">CAMERA</span></div>
          </div>
          <div class="cam-strip">
            <span>FRAME <b id="db-frame-id">--</b></span>
            <span>FRAME AGE <b id="db-frame-age">--</b></span>
            <span>AI LATENCY <b id="db-proc-latency">--</b></span>
            <span>AI UPDATE <b id="db-last-update">--</b></span>
          </div>
        </div>

        <div class="card">
          <h2>Crowd Intelligence</h2>
          <div class="subhead" style="margin-top:0;">Current Hotspot</div>
          <div class="behavior-value" id="db-hotspot">--</div>
          <div class="subhead">Current Crowd Behavior</div>
          <div class="behavior-value" id="db-scenario">--</div>
          <div class="subhead">Sentinel Recommended Response</div>
          <div class="response-block">
            <div class="response-action" id="db-response">--</div>
            <div class="response-zone" id="db-response-zone">--</div>
          </div>
        </div>
      </div>

      <div style="height:1rem"></div>

      <div class="cols-2">
        <div class="card">
          <h2>Crowd Dynamics</h2>
          <div id="db-lar-bars"></div>
          <div class="interpretation" id="db-lar-interp">--</div>
        </div>
        <div class="card">
          <h2>System Health</h2>
          <div class="grid2">
            <div class="stat"><div class="label">AI Engine</div><div class="value" id="db-ai-state">--</div></div>
            <div class="stat"><div class="label">Connectivity</div><div class="value" id="db-conn-state">--</div></div>
            <div class="stat"><div class="label">SQLite</div><div class="value" id="db-sqlite-state">--</div></div>
            <div class="stat"><div class="label">Remote Sync</div><div class="value" id="db-sync-state">--</div></div>
          </div>
          <div class="safety-note" id="db-safety-note"><b>SAFETY PLANE ACTIVE</b> — local detection, persistence and operator alerting continue independently of WAN state.</div>
        </div>
      </div>

      <div style="height:1rem"></div>
      <div class="card">
        <h2>Recent Incidents</h2>
        <table class="events-table"><thead><tr><th>Time</th><th>Source</th><th>Severity</th><th>Behavior</th><th>Hotspot</th><th>Local</th><th>Sync</th></tr></thead><tbody id="db-recent-events"></tbody></table>
      </div>
    </section>

    <!-- ============================================================
         LIVE MONITORING -- CCTV-style single large feed + full telemetry.
    =============================================================== -->
    <section class="view" id="view-monitoring">
      <div class="card">
        <h2>Live Camera Feed</h2>
        <div class="camera-frame cam-slot tall" id="camera-slot-monitoring">
          <div class="cam-tag"><span id="cam-badge-monitoring">LIVE</span><span id="cam-source-monitoring">CAMERA</span></div>
        </div>
      </div>
      <div style="height:1rem"></div>
      <div class="card">
        <h2>Input &amp; Inference Telemetry</h2>
        <div class="grid4">
          <div class="stat"><div class="label">Source</div><div class="value" id="mon-source" style="font-size:.85rem;">--</div></div>
          <div class="stat"><div class="label">Camera</div><div class="value" id="mon-camera">--</div></div>
          <div class="stat"><div class="label">Frame ID</div><div class="value" id="mon-frame-id">--</div></div>
          <div class="stat"><div class="label">Frame Age</div><div class="value" id="mon-frame-age">--</div></div>
          <div class="stat"><div class="label">AI Update Age</div><div class="value" id="mon-ai-age" style="font-size:.85rem;">--</div></div>
          <div class="stat"><div class="label">AI Inference Latency</div><div class="value" id="mon-proc-latency">--</div></div>
          <div class="stat"><div class="label">People</div><div class="value" id="mon-people">--</div></div>
          <div class="stat"><div class="label">Occupancy</div><div class="value" id="mon-occ">--</div></div>
          <div class="stat"><div class="label">Risk</div><div class="value" id="mon-risk">--</div></div>
          <div class="stat"><div class="label">Behavior</div><div class="value" id="mon-scenario" style="font-size:.8rem;">--</div></div>
          <div class="stat"><div class="label">Hotspot</div><div class="value" id="mon-hotspot">--</div></div>
          <div class="stat"><div class="label">Confidence</div><div class="value" id="mon-confidence">--</div></div>
        </div>
        <div class="note">Camera frame age reflects how fresh the picture itself is, independent of inference speed. AI inference latency is how long the last risk computation took to run on that frame.</div>
      </div>
    </section>

    <!-- ============================================================
         SPATIAL MAP -- full occupancy grid + hotspot + dynamics.
    =============================================================== -->
    <section class="view" id="view-map">
      <div class="card">
        <h2>Spatial Occupancy Schematic (4&times;6 Zone Grid)</h2>
        <div class="note" style="margin:-.4rem 0 .8rem 0;">Zone grid abstraction of the observed area, not a geographic floor plan. Each cell shows a zone ID and its relative occupancy count.</div>
        <div class="spatial-layout">
          <div class="zone-map-lg big" id="zone-map"></div>
          <div class="spatial-side">
            <div class="hotspot-block">
              <div class="label">Current Hotspot</div>
              <div class="value" id="zone-hotspot">--</div>
              <div class="sub" id="zone-hotspot-load">--</div>
            </div>
            <div class="top-zones-block">
              <div class="label">Top Loaded Zones</div>
              <div id="zone-top-loaded">--</div>
            </div>
          </div>
        </div>
      </div>
      <div style="height:1rem"></div>
      <div class="cols-2">
        <div class="card">
          <h2>Crowd Dynamics</h2>
          <div id="lar-bars"></div>
          <div class="interpretation" id="lar-interpretation">--</div>
        </div>
        <div class="card">
          <h2>Why This State?</h2>
          <div class="subhead" style="margin-top:0;">Current Crowd Behavior</div>
          <div class="behavior-value" id="zone-scenario">--</div>
          <div class="subhead">Explanation</div>
          <div class="why-text" id="why-state">--</div>
          <div class="subhead">Recommended Response</div>
          <div class="response-block">
            <div class="response-action" id="zone-response">--</div>
            <div class="response-zone" id="response-zone">--</div>
          </div>
        </div>
      </div>
    </section>

    <!-- ============================================================
         SIMULATION -- bundled demo clip processed through the SAME
         pipeline as Reality. Explicitly labeled; no fabricated results.
    =============================================================== -->
    <section class="view" id="view-simulation">
      <div class="explainer">SIMULATION MODE processes the bundled scenario clip through the identical SENTINEL pipeline used for the live camera -- real detection, real occupancy, real risk scoring. Nothing shown here is hard-coded.</div>

      <div id="sim-inactive" class="card sim-unavailable" style="display:none;">
        <div>Currently in REALITY mode. Switch to SIMULATION to view the demo scenario analysis.</div>
        <button class="btn" onclick="setOperatingMode('SIMULATION')">SWITCH TO SIMULATION</button>
      </div>

      <div id="sim-active-content">
        <div class="sim-source-card">
          <div>
            <div class="name" id="sim-source-name">--</div>
            <div class="meta" id="sim-source-meta">--</div>
          </div>
          <div style="display:flex; align-items:center; gap:.6rem; flex-wrap:wrap;">
            <span class="badge badge-ACTIVE" id="sim-loop-badge">LOOP: ON</span>
            <button class="btn secondary" onclick="setOperatingMode('SIMULATION')">RESTART DEMO</button>
            <label class="upload-btn" for="simulation-upload">UPLOAD A DIFFERENT SCENARIO
              <input type="file" id="simulation-upload" accept=".mp4,.avi,.mov,.mkv" onchange="uploadSimulationVideo(event)">
            </label>
          </div>
        </div>
        <div id="simulation-upload-status" style="margin-bottom:.8rem;"></div>

        <div class="flow-strip">
          <span class="node">SCENARIO VIDEO</span><span class="arrow">&rarr;</span>
          <span class="node">SENTINEL DETECTION + OCCUPANCY</span><span class="arrow">&rarr;</span>
          <span class="node">ADAPTIVE RISK</span><span class="arrow">&rarr;</span>
          <span class="node">RECOMMENDED RESPONSE</span>
        </div>

        <div class="cols-2">
          <div class="card">
            <h2>Scenario Feed</h2>
            <div class="camera-frame cam-slot" id="camera-slot-simulation">
              <div class="cam-tag"><span id="cam-badge-simulation">LIVE</span><span id="cam-source-simulation">SIMULATION</span></div>
            </div>
            <div class="cam-strip">
              <span>FRAME <b id="sim-frame-id">--</b></span>
              <span>AI LATENCY <b id="sim-proc-latency">--</b></span>
            </div>
          </div>
          <div class="card">
            <h2>Crowd Intelligence</h2>
            <div class="summary-row">
              <div class="stat"><div class="label">People</div><div class="value" id="sim-people">--</div></div>
              <div class="stat"><div class="label">Occupancy</div><div class="value" id="sim-occ">--</div></div>
              <div class="stat"><div class="label">Risk</div><div class="value" id="sim-risk">--</div></div>
            </div>
            <div class="subhead" style="margin-top:0;">Hotspot</div>
            <div class="behavior-value" id="sim-hotspot">--</div>
            <div class="subhead">Behavior</div>
            <div class="behavior-value" id="sim-scenario">--</div>
            <div class="subhead">Recommended Response</div>
            <div class="response-block"><div class="response-action" id="sim-response">--</div></div>
          </div>
        </div>
      </div>
    </section>

    <!-- ============================================================
         ALERTS & RESPONSE
    =============================================================== -->
    <section class="view" id="view-alerts">
      <div class="card">
        <div class="tab-row">
          <button class="tab-btn active" data-tab="ACTIVE" onclick="setAlertTab('ACTIVE')">Active</button>
          <button class="tab-btn" data-tab="ACKNOWLEDGED" onclick="setAlertTab('ACKNOWLEDGED')">Acknowledged</button>
          <button class="tab-btn" data-tab="ALL" onclick="setAlertTab('ALL')">All</button>
        </div>
        <div id="alerts-body"></div>
      </div>
      <div style="height:1rem"></div>
      <div class="card">
        <h2>Incident History (Reality + Simulation)</h2>
        <table class="events-table"><thead><tr><th>Created</th><th>Source</th><th>Severity</th><th>Behavior</th><th>Hotspot</th><th>Local</th><th>Sync</th><th>Retries</th></tr></thead><tbody id="events-body"></tbody></table>
      </div>
    </section>

    <!-- ============================================================
         SYSTEM HEALTH
    =============================================================== -->
    <section class="view" id="view-health">
      <div class="health-grid">
        <div class="health-card"><div class="name">Camera</div><span class="badge badge-UNKNOWN" id="health-camera">--</span><div class="age" id="health-camera-age">--</div></div>
        <div class="health-card"><div class="name">AI Engine</div><span class="badge badge-UNKNOWN" id="health-ai">--</span><div class="age" id="health-ai-age">--</div></div>
        <div class="health-card"><div class="name">Risk Engine</div><span class="badge badge-UNKNOWN" id="health-risk">--</span><div class="age" id="health-risk-age">--</div></div>
        <div class="health-card"><div class="name">SQLite</div><span class="badge badge-UNKNOWN" id="health-sqlite">--</span><div class="age" id="health-sqlite-age">--</div></div>
        <div class="health-card"><div class="name">Local Alerts</div><span class="badge badge-UNKNOWN" id="health-alerts">--</span><div class="age" id="health-alerts-age">--</div></div>
        <div class="health-card"><div class="name">Connectivity</div><span class="badge badge-UNKNOWN" id="health-conn">--</span><div class="age" id="health-conn-age">--</div></div>
        <div class="health-card"><div class="name">Remote Sync</div><span class="badge badge-UNKNOWN" id="health-sync">--</span><div class="age" id="health-sync-age">--</div></div>
        <div class="health-card"><div class="name">Operating Mode</div><span class="badge badge-ACTIVE" id="health-mode">--</span><div class="age" id="health-mode-age">--</div></div>
      </div>

      <div style="height:1rem"></div>
      <div class="card">
        <h2>Connectivity Detail</h2>
        <div class="grid4">
          <div class="stat"><div class="label">State</div><div class="value" id="conn-state">--</div></div>
          <div class="stat"><div class="label">Outage Duration</div><div class="value" id="outage-duration">--</div></div>
          <div class="stat"><div class="label">Last Remote Success</div><div class="value" id="last-remote-success" style="font-size:.8rem;">--</div></div>
          <div class="stat"><div class="label">Pending Sync</div><div class="value" id="pending-sync">--</div></div>
        </div>
        {{DEBUG_CONNECTIVITY_CONTROLS}}
      </div>

      <div style="height:1rem"></div>
      <div class="card">
        <h2>Event Recovery</h2>
        <div class="grid4">
          <div class="stat"><div class="label">Generated</div><div class="value" id="m-generated">--</div></div>
          <div class="stat"><div class="label">Persisted</div><div class="value" id="m-persisted">--</div></div>
          <div class="stat"><div class="label">Local Delivered</div><div class="value" id="m-local-delivered">--</div></div>
          <div class="stat"><div class="label">Lost</div><div class="value" id="m-lost">--</div></div>
        </div>
        <div class="grid4" style="margin-top:.6rem;">
          <div class="stat"><div class="label">Pending</div><div class="value" id="m-pending">--</div></div>
          <div class="stat"><div class="label">Syncing</div><div class="value" id="m-syncing">--</div></div>
          <div class="stat"><div class="label">Synced</div><div class="value" id="m-synced">--</div></div>
          <div class="stat"><div class="label">Failed</div><div class="value" id="m-failed">--</div></div>
        </div>
        <div class="grid2" style="margin-top:.6rem;">
          <div class="stat"><div class="label">Auth Blocked</div><div class="value" id="m-auth-blocked">--</div></div>
          <div class="stat"><div class="label">Remote Sync</div><div class="value" id="remote-sync-state">--</div></div>
        </div>
      </div>
    </section>

  </main>
</div>

<img id="camera-feed" src="/camera-feed" alt="SENTINEL local camera feed" style="display:none;">

<script>
const SEVERITIES = ['GREEN','YELLOW','RED','BLACK'];
let audioCtx = null;
function beep() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.connect(gain); gain.connect(audioCtx.destination);
    osc.frequency.value = 880; gain.gain.value = 0.05;
    osc.start(); setTimeout(() => osc.stop(), 180);
  } catch (e) { /* audio unsupported/blocked -- local visual alert already fired */ }
}
const soundedAlertStoragePrefix = 'sentinel-audible-event:';

function setText(id, val) { const el = document.getElementById(id); if (el) el.textContent = val; }
function setHtml(id, val) { const el = document.getElementById(id); if (el) el.innerHTML = val; }

function fmtMs(ms) {
  if (ms === null || ms === undefined || !isFinite(ms)) return '--';
  return ms < 1000 ? Math.round(ms) + ' ms' : (ms / 1000).toFixed(1) + ' s';
}
function fmtAgo(iso) {
  if (!iso) return '--';
  const secs = (Date.now() - new Date(iso).getTime()) / 1000;
  if (secs < 0) return 'just now';
  return secs < 60 ? secs.toFixed(1) + ' sec ago' : Math.round(secs / 60) + ' min ago';
}
function setBadgeClass(el, prefix, value) {
  if (!el) return;
  el.className = 'badge badge-' + (value || 'UNKNOWN');
  el.textContent = (prefix ? prefix + ': ' : '') + (value || 'UNKNOWN');
}

// Mirrors src/scenario.py's ScenarioEngine.evaluate thresholds exactly, for
// narration only -- never used to compute severity/scenario itself, which
// remains solely the runtime's authoritative output.
const SCENARIO_THRESHOLDS = { accumRed: 0.20, redistRed: 0.35, accumYellow: 0.08, redistYellow: 0.18, loadYellow: 0.25 };
function levelWord(value, yellowAt, redAt) {
  if (value >= redAt) return 'critical';
  if (value >= yellowAt) return 'elevated';
  return 'low';
}
function interpretDynamics(snap) {
  const t = SCENARIO_THRESHOLDS;
  return [
    `Accumulation ${levelWord(snap.accumulation, t.accumYellow, t.accumRed)}`,
    `Redistribution ${levelWord(snap.redistribution, t.redistYellow, t.redistRed)}`,
    `Load anomaly ${levelWord(snap.load_anomaly, t.loadYellow, t.redistRed)}`,
  ].join(' &middot; ');
}
function explainState(snap) {
  const L = snap.load_anomaly, A = snap.accumulation, R = snap.redistribution;
  const hotspotText = snap.hotspot ? `near ${snap.hotspot}` : 'without a concentrated zone';
  switch (snap.primary_scenario) {
    case 'LOCAL_BOTTLENECK':
      return `A single zone is carrying an extreme concentration of people ${hotspotText} (load anomaly ${L.toFixed(2)}).`;
    case 'ACCUMULATION':
      return `Accumulation (${A.toFixed(2)}) is building faster than it is dispersing, concentrating pressure ${hotspotText}.`;
    case 'MASS_REDISTRIBUTION':
      return `Redistribution (${R.toFixed(2)}) shows the crowd shifting spatially ${hotspotText} rather than staying settled.`;
    case 'STABLE_HIGH_OCCUPANCY':
      return `Occupancy is present, but load anomaly (${L.toFixed(2)}), accumulation (${A.toFixed(2)}), and redistribution (${R.toFixed(2)}) are all within normal range -- no abnormal transition detected.`;
    default:
      return 'No people currently detected in the observed area.';
  }
}
function larBarsHtml(snap) {
  const rows = [
    ['Load Anomaly (L)', snap.load_anomaly],
    ['Accumulation (A)', snap.accumulation],
    ['Redistribution (R)', snap.redistribution],
  ];
  return rows.map(function (row) {
    const pct = Math.max(0, Math.min(1, row[1])) * 100;
    return `<div class="lar-row"><span class="lar-name">${row[0]}</span><span class="lar-bar-track"><span class="lar-bar-fill" style="width:${pct.toFixed(0)}%"></span></span><span class="lar-val">${row[1].toFixed(2)}</span></div>`;
  }).join('');
}

// ------------------------------------------------------------------
// View / navigation switching (client-side only -- no page reload, one
// continuous /status poll drives every view whether visible or not).
// ------------------------------------------------------------------
const VIEW_NAMES = { dashboard: 'Dashboard', monitoring: 'Live Monitoring', map: 'Spatial Map', simulation: 'Simulation', alerts: 'Alerts & Response', health: 'System Health' };
let activeView = 'dashboard';

function moveCameraFeed(view) {
  const img = document.getElementById('camera-feed');
  const target = document.getElementById('camera-slot-' + view);
  if (!img || !target) return;
  target.insertBefore(img, target.firstChild ? target.firstChild.nextSibling : null);
  img.style.display = 'block';
}

function switchView(view) {
  activeView = view;
  document.querySelectorAll('.view').forEach(function (el) { el.classList.remove('active'); });
  const target = document.getElementById('view-' + view);
  if (target) target.classList.add('active');
  document.querySelectorAll('.nav-item').forEach(function (el) { el.classList.toggle('active', el.dataset.view === view); });
  setText('topbar-view-name', VIEW_NAMES[view] || view);
  if (view === 'dashboard' || view === 'monitoring' || view === 'simulation') {
    moveCameraFeed(view);
  }
}
document.querySelectorAll('.nav-item').forEach(function (el) {
  el.addEventListener('click', function () { switchView(el.dataset.view); });
});

// ------------------------------------------------------------------
// Alerts tab filtering (client-side; data already delivered by /status).
// ------------------------------------------------------------------
let alertTab = 'ACTIVE';
let lastLocalAlerts = [];
function setAlertTab(tab) {
  alertTab = tab;
  document.querySelectorAll('.tab-btn').forEach(function (el) { el.classList.toggle('active', el.dataset.tab === tab); });
  renderAlerts(lastLocalAlerts);
}
function renderAlerts(localAlerts) {
  lastLocalAlerts = localAlerts;
  const filtered = localAlerts.filter(function (e) {
    if (alertTab === 'ALL') return true;
    if (alertTab === 'ACTIVE') return e.local_status !== 'LOCAL_ACKNOWLEDGED';
    return e.local_status === 'LOCAL_ACKNOWLEDGED';
  });
  if (!filtered.length) {
    setHtml('alerts-body', `<div class="empty-note">No ${alertTab.toLowerCase()} alerts.</div>`);
    return;
  }
  setHtml('alerts-body', filtered.map(function (e) {
    const acknowledged = e.local_status === 'LOCAL_ACKNOWLEDGED';
    const sevClass = SEVERITIES.includes(e.severity) ? ('sev-' + e.severity) : '';
    return `<div class="alert-card ${sevClass} ${acknowledged ? 'acknowledged' : ''}">
      <div class="alert-head"><span class="alert-title">${e.severity} &mdash; ${e.primary_scenario}</span><span class="src-tag ${e.source_mode}">${e.source_mode || 'REALITY'}</span></div>
      <div class="alert-meta">Hotspot: ${e.hotspot || '--'} &middot; ${fmtAgo(e.created_at_utc)}</div>
      <div class="alert-meta">Recommended response: ${e.recommended_action || '--'}</div>
      <div class="alert-meta">Status: ${acknowledged ? 'ACKNOWLEDGED / HISTORY' : 'ACTIVE / UNACKNOWLEDGED'} &middot; Remote: ${e.sync_status}</div>
      ${acknowledged ? '' : `<button class="ack-btn" onclick="acknowledgeAlert('${e.event_id}')">ACKNOWLEDGE</button>`}
    </div>`;
  }).join(''));
}
function acknowledgeAlert(eventId) {
  fetch('/alerts/' + encodeURIComponent(eventId) + '/ack', { method: 'POST' }).then(function () { updateUI(); }).catch(function () {});
}

function forceConnectivity(state) {
  const url = '/debug/connectivity' + (state ? ('?state=' + state) : '');
  fetch(url, { method: 'POST' }).catch(function () {});
}

// ------------------------------------------------------------------
// Operating mode. The backend mode is authoritative; clicking a button
// always issues the real switch immediately -- never a local-only
// button-visual change -- so the next /status poll confirms it rather
// than silently reverting it.
// ------------------------------------------------------------------
let modeSwitchInFlight = false;
function setOperatingMode(mode) {
  if (modeSwitchInFlight) return;
  modeSwitchInFlight = true;
  const url = mode === 'REALITY' ? '/api/mode/reality' : '/api/mode/simulation';
  fetch(url, { method: 'POST' })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      if (!data.success) {
        setText('simulation-upload-status', mode === 'SIMULATION' ? ('Simulation unavailable: ' + (data.error || 'unknown error')) : '');
      }
    })
    .catch(function () {})
    .finally(function () { modeSwitchInFlight = false; });
}
function uploadSimulationVideo(event) {
  const file = event.target.files[0];
  if (!file) return;
  setText('simulation-upload-status', 'Uploading and validating ' + file.name + '...');
  const form = new FormData();
  form.append('video', file);
  fetch('/api/mode/simulation/upload', { method: 'POST', body: form })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      setText('simulation-upload-status', data.success ? ('Processing ' + data.file + ' through the SENTINEL pipeline.') : ('Upload failed: ' + (data.error || 'unknown error')));
    })
    .catch(function () { setText('simulation-upload-status', 'Upload failed: network error.'); });
}

// ------------------------------------------------------------------
// Polling loop -- single-flight, self-scheduling, never stacks requests.
// ------------------------------------------------------------------
async function updateUI() {
  const controller = new AbortController();
  const timeoutId = setTimeout(function () { controller.abort(); }, 4000);
  try {
    const response = await fetch('/status', { signal: controller.signal });
    const data = await response.json();
    clearTimeout(timeoutId);
    render(data);
    return 800;
  } catch (e) {
    clearTimeout(timeoutId);
    markStatusUnavailable();
    return 2000;
  }
}
function markStatusUnavailable() {
  const b1 = document.getElementById('stale-banner-dashboard');
  if (b1) { b1.textContent = 'LOCAL STATUS UNAVAILABLE -- last displayed values are stale. Current live risk is UNKNOWN.'; b1.classList.add('show'); }
  setText('kpi-risk-value', 'UNKNOWN');
  setBadgeClass(document.getElementById('conn-badge'), 'CONNECTIVITY', 'UNKNOWN');
  setText('conn-state', 'UNKNOWN');
}

function renderOperatingMode(data) {
  const mode = data.operating_mode || 'REALITY';
  const isSim = mode === 'SIMULATION';
  document.getElementById('mode-btn-reality').classList.toggle('active', !isSim);
  document.getElementById('mode-btn-simulation').classList.toggle('active', isSim);

  // Source TYPE (where the frames come from) is set here from the
  // authoritative operating mode. Source HEALTH (is the picture actually
  // fresh right now?) is a completely different question, computed in
  // render() from the real camera_health field -- REALITY does not mean
  // "LIVE" if the camera has actually stalled.
  ['dashboard', 'monitoring', 'simulation'].forEach(function (scope) {
    setText('cam-source-' + scope, isSim ? ('SIMULATION — ' + (data.simulation_source_label || data.simulation_source_name || 'Scenario Video')) : 'REALITY — Live Camera');
  });
  setText('mon-source', isSim ? ('SIMULATION -- ' + (data.simulation_source_label || 'Scenario Video')) : 'REALITY -- Live Camera');

  document.getElementById('sim-inactive').style.display = isSim ? 'none' : 'block';
  document.getElementById('sim-active-content').style.display = isSim ? 'block' : 'none';
  if (isSim) {
    const meta = data.default_simulation_metadata;
    setText('sim-source-name', data.simulation_source_label || data.simulation_source_name || 'Scenario Video');
    const dot = String.fromCharCode(183);
    setText('sim-source-meta', meta ? `${meta.width}x${meta.height} @ ${meta.fps} fps ${dot} ${meta.frame_count} frames ${dot} ${meta.duration_s}s source clip` : (data.simulation_source_name || '--'));
    const looping = data.simulation_loop_count > 0;
    setText('sim-loop-badge', looping ? ('LOOP: ON (cycle ' + (data.simulation_loop_count + 1) + ')') : 'LOOP: ON');
  }
  if (!isSim) setText('simulation-upload-status', '');
}

function render(data) {
  const snap = data.snapshot;
  const runtimeHealth = data.runtime_health || {};
  const conn = data.connectivity;
  const metrics = data.metrics;

  renderOperatingMode(data);

  const camHealth = runtimeHealth.camera_health || (snap ? snap.camera_health : 'CAMERA_LOST');
  const cameraStale = camHealth !== 'LIVE';
  const riskStale = !snap || !runtimeHealth.snapshot_fresh || ['DEGRADED', 'STALE', 'STOPPED', 'NOT_STARTED', 'STARTING'].includes(runtimeHealth.state);
  const riskSnap = riskStale ? null : snap;

  let staleMsg = '';
  if (runtimeHealth.state === 'DEGRADED') staleMsg = 'AI ENGINE DEGRADED -- camera input may still be live, but the last risk assessment is not current.';
  else if (riskStale) staleMsg = 'AI RISK OUTPUT STALE -- last valid assessment is historical. Current live risk is UNKNOWN.';
  else if (cameraStale) staleMsg = 'INPUT STALE -- showing last valid frame. Current live risk is UNKNOWN until the camera recovers.';
  const banner = document.getElementById('stale-banner-dashboard');
  banner.textContent = staleMsg;
  banner.classList.toggle('show', !!staleMsg);

  const effectiveSeverity = riskStale ? 'STALE' : (snap ? snap.severity : 'UNKNOWN');

  // ---------------- Camera badges: source HEALTH, not source type ----------------
  ['dashboard', 'monitoring', 'simulation'].forEach(function (scope) {
    const el = document.getElementById('cam-badge-' + scope);
    if (!el) return;
    el.textContent = camHealth || 'UNKNOWN';
    el.className = 'cam-health-' + (camHealth || 'UNKNOWN');
  });

  // ---------------- Topbar compact status strip ----------------
  const stripCamera = document.getElementById('strip-camera');
  stripCamera.textContent = camHealth || '--';
  stripCamera.className = 'v ' + (camHealth === 'LIVE' ? 'ok' : (camHealth === 'CAMERA_LOST' ? 'bad' : 'warn'));
  const stripAi = document.getElementById('strip-ai');
  stripAi.textContent = runtimeHealth.state || '--';
  stripAi.className = 'v ' + (runtimeHealth.state === 'HEALTHY' ? 'ok' : (runtimeHealth.state === 'DEGRADED' || runtimeHealth.state === 'STARTING' ? 'warn' : (runtimeHealth.state === 'STOPPED' || runtimeHealth.state === 'NOT_STARTED' ? 'bad' : 'neutral')));
  const stripWan = document.getElementById('strip-wan');
  stripWan.textContent = conn.state;
  stripWan.className = 'v ' + (conn.state === 'ONLINE' ? 'ok' : (conn.state === 'RECOVERY' ? 'ok' : (conn.state === 'DEGRADED' ? 'warn' : 'neutral')));
  const pendingCount = metrics.events_pending + metrics.events_retrying;
  const stripSync = document.getElementById('strip-sync');
  stripSync.textContent = pendingCount > 0 ? (pendingCount + ' pending') : 'clear';
  stripSync.className = 'v ' + (pendingCount > 0 ? 'warn' : 'ok');

  // ---------------- Dashboard KPI strip ----------------
  const riskKpi = document.getElementById('kpi-risk');
  riskKpi.className = 'kpi risk ' + (SEVERITIES.includes(effectiveSeverity) ? ('sev-' + effectiveSeverity) : '');
  setText('kpi-risk-value', effectiveSeverity);
  setText('kpi-risk-note', riskSnap ? `${(riskSnap.confidence * 100).toFixed(0)}% confidence` : (data.operating_mode === 'SIMULATION' ? 'Simulation' : 'Reality'));
  setText('db-people', riskSnap ? riskSnap.people_count : '--');
  setText('db-occ', riskSnap ? riskSnap.occupancy_index.toFixed(2) : '--');
  setText('kpi-camera', camHealth || '--');
  setText('kpi-ai-latency', snap ? fmtMs(snap.processing_latency_ms) : '--');
  setText('kpi-sync', metrics.events_pending + metrics.events_retrying);

  // ---------------- Dashboard camera strip ----------------
  setText('db-frame-id', snap ? snap.frame_id : '--');
  setText('db-frame-age', snap ? fmtMs(snap.frame_age_ms) : '--');
  setText('db-proc-latency', snap ? fmtMs(snap.processing_latency_ms) : '--');
  setText('db-last-update', snap ? fmtAgo(snap.timestamp_utc) : '--');

  // ---------------- Dashboard crowd intelligence summary ----------------
  setText('db-hotspot', riskSnap ? (riskSnap.hotspot || 'None') : '--');
  setText('db-scenario', riskSnap ? riskSnap.primary_scenario : '--');
  setText('db-response', riskSnap ? riskSnap.recommended_action : '--');
  setText('db-response-zone', riskSnap && riskSnap.hotspot ? ('Zone: ' + riskSnap.hotspot) : 'No specific zone');
  setHtml('db-lar-bars', riskSnap ? larBarsHtml(riskSnap) : '');
  setHtml('db-lar-interp', riskSnap ? interpretDynamics(riskSnap) : '--');

  // ---------------- Dashboard system health strip ----------------
  setText('db-ai-state', runtimeHealth.state || 'UNKNOWN');
  setText('db-conn-state', conn.state);
  setText('db-sqlite-state', metrics.latest_database_success !== null ? 'ACTIVE' : 'NO WRITES');
  setText('db-sync-state', metrics.events_auth_blocked ? 'AUTH BLOCKED' : (conn.state === 'OFFLINE' || conn.state === 'DEGRADED' ? 'DEFERRED' : 'READY'));

  // ---------------- Live Monitoring page ----------------
  setBadgeClass(null, null, null); // no-op keeps setBadgeClass referenced
  setText('mon-camera', camHealth || '--');
  setText('mon-frame-id', snap ? snap.frame_id : '--');
  setText('mon-frame-age', snap ? fmtMs(snap.frame_age_ms) : '--');
  setText('mon-ai-age', snap ? fmtAgo(snap.timestamp_utc) : '--');
  setText('mon-proc-latency', snap ? fmtMs(snap.processing_latency_ms) : '--');
  setText('mon-people', riskSnap ? riskSnap.people_count : '--');
  setText('mon-occ', riskSnap ? riskSnap.occupancy_index.toFixed(2) : '--');
  setText('mon-risk', effectiveSeverity);
  setText('mon-scenario', riskSnap ? riskSnap.primary_scenario : '--');
  setText('mon-hotspot', riskSnap ? (riskSnap.hotspot || 'None') : '--');
  setText('mon-confidence', riskSnap ? `${(riskSnap.confidence * 100).toFixed(0)}%` : '--');

  // ---------------- Spatial Map page (full grid) ----------------
  renderSpatialMap(riskSnap);

  // ---------------- Simulation page ----------------
  setText('sim-frame-id', snap ? snap.frame_id : '--');
  setText('sim-proc-latency', snap ? fmtMs(snap.processing_latency_ms) : '--');
  setText('sim-people', riskSnap ? riskSnap.people_count : '--');
  setText('sim-occ', riskSnap ? riskSnap.occupancy_index.toFixed(2) : '--');
  const simRiskEl = document.getElementById('sim-risk');
  if (simRiskEl) simRiskEl.textContent = effectiveSeverity;
  setText('sim-hotspot', riskSnap ? (riskSnap.hotspot || 'None') : '--');
  setText('sim-scenario', riskSnap ? riskSnap.primary_scenario : '--');
  setText('sim-response', riskSnap ? riskSnap.recommended_action : '--');

  // ---------------- Connectivity detail (System Health page) ----------------
  setBadgeClass(document.getElementById('conn-badge'), 'CONNECTIVITY', conn.state);
  setText('conn-state', conn.state);
  setText('outage-duration', conn.current_outage_duration_s > 0 ? conn.current_outage_duration_s.toFixed(0) + ' s' : '0 s');
  setText('last-remote-success', conn.last_success_at ? fmtAgo(conn.last_success_at) : 'never');
  setText('pending-sync', metrics.events_pending + metrics.events_retrying);

  // ---------------- System Health cards ----------------
  setBadgeClass(document.getElementById('health-camera'), '', camHealth || 'UNKNOWN');
  setText('health-camera-age', snap ? ('frame age ' + fmtMs(snap.frame_age_ms)) : '--');
  setBadgeClass(document.getElementById('health-ai'), '', runtimeHealth.state || 'UNKNOWN');
  setText('health-ai-age', runtimeHealth.last_success_at ? ('last success ' + fmtAgo(runtimeHealth.last_success_at)) : '--');
  setBadgeClass(document.getElementById('health-risk'), '', riskStale ? 'STALE' : effectiveSeverity);
  setText('health-risk-age', snap ? ('updated ' + fmtAgo(snap.timestamp_utc)) : '--');
  setBadgeClass(document.getElementById('health-sqlite'), '', metrics.latest_database_success !== null ? 'ACTIVE' : 'UNKNOWN');
  setText('health-sqlite-age', metrics.latest_database_success ? ('last write ' + fmtAgo(metrics.latest_database_success)) : 'no writes yet');
  setBadgeClass(document.getElementById('health-alerts'), '', metrics.events_local_unacknowledged > 0 ? 'ACTIVE' : 'STALE');
  setText('health-alerts-age', `${metrics.events_local_unacknowledged} unacknowledged ${String.fromCharCode(183)} ${metrics.events_local_acknowledged} acknowledged`);
  setBadgeClass(document.getElementById('health-conn'), '', conn.state);
  setText('health-conn-age', conn.last_success_at ? ('last remote success ' + fmtAgo(conn.last_success_at)) : 'never');
  const syncBadgeState = metrics.events_auth_blocked ? 'DEGRADED'
    : (conn.state === 'OFFLINE' || conn.state === 'DEGRADED') ? 'DEFERRED'
    : (metrics.latest_sync_success ? 'ONLINE' : conn.state);
  setBadgeClass(document.getElementById('health-sync'), '', syncBadgeState);
  setText('health-sync-age', metrics.events_auth_blocked ? 'AUTH BLOCKED' : (metrics.latest_sync_success ? ('last sync ' + fmtAgo(metrics.latest_sync_success)) : (conn.state === 'OFFLINE' || conn.state === 'DEGRADED' ? 'deferred while WAN is affected -- safety plane unaffected' : 'no successful sync yet')));
  setText('health-mode', data.operating_mode || 'REALITY');
  setText('health-mode-age', data.operating_mode === 'SIMULATION' ? (data.simulation_source_label || '--') : 'Live camera');

  // ---------------- Event recovery metrics ----------------
  setText('m-generated', metrics.events_generated);
  setText('m-persisted', metrics.events_persisted);
  setText('m-local-delivered', metrics.events_local_delivered);
  setText('m-lost', metrics.events_lost);
  setText('m-pending', metrics.events_pending);
  setText('m-syncing', metrics.events_syncing);
  setText('m-synced', metrics.events_synced);
  setText('m-failed', metrics.events_failed);
  setText('m-auth-blocked', metrics.events_auth_blocked);
  setText('remote-sync-state', metrics.events_auth_blocked ? 'AUTH BLOCKED' : (conn.state === 'OFFLINE' || conn.state === 'DEGRADED' ? 'DEFERRED (WAN affected)' : 'READY'));

  // ---------------- Alerts & Response ----------------
  const localAlerts = data.local_alerts || [];
  renderAlerts(localAlerts);
  const unackCount = metrics.events_local_unacknowledged || 0;
  document.querySelectorAll('.nav-item[data-view="alerts"] .notif-bell').forEach(function (el) {
    el.innerHTML = unackCount > 0 ? '<span class="notif-dot show"></span>' : '';
  });

  // ---------------- Incident history table (Dashboard + Alerts page) ----------------
  const recentRows = (data.recent_events || []).map(function (e) {
    return `<tr><td>${fmtAgo(e.created_at_utc)}</td><td><span class="src-tag ${e.source_mode}">${e.source_mode || 'REALITY'}</span></td><td>${e.severity}</td><td>${e.primary_scenario}</td><td>${e.hotspot || '--'}</td><td>${e.local_status}</td><td>${e.sync_status}</td><td>${e.retry_count}</td></tr>`;
  });
  setHtml('events-body', recentRows.join('') || '<tr><td colspan="8">No events yet.</td></tr>');
  setHtml('db-recent-events', (data.recent_events || []).slice(0, 5).map(function (e) {
    return `<tr><td>${fmtAgo(e.created_at_utc)}</td><td><span class="src-tag ${e.source_mode}">${e.source_mode || 'REALITY'}</span></td><td>${e.severity}</td><td>${e.primary_scenario}</td><td>${e.hotspot || '--'}</td><td>${e.local_status}</td><td>${e.sync_status}</td></tr>`;
  }).join('') || '<tr><td colspan="7">No events yet.</td></tr>');

  // ---------------- Audible cue on new RED/BLACK (zero-Internet local alert) ----------------
  localAlerts.forEach(function (alert) {
    const key = soundedAlertStoragePrefix + alert.event_id;
    if (alert.audible && !localStorage.getItem(key)) {
      localStorage.setItem(key, '1');
      beep();
    }
  });
}

function renderSpatialMap(snap) {
  const mapEl = document.getElementById('zone-map');
  const grid = snap && snap.occupancy_grid;
  if (!grid || !grid.length) {
    mapEl.innerHTML = '';
    setText('zone-hotspot', '--');
    setText('zone-hotspot-load', '--');
    setHtml('zone-top-loaded', '--');
    setText('zone-scenario', '--');
    setText('zone-response', '--');
    setText('response-zone', '');
    setText('why-state', '--');
    setHtml('lar-bars', '');
    setHtml('lar-interpretation', '--');
    return;
  }
  const rows = grid.length, cols = grid[0].length;
  let maxVal = 1;
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) maxVal = Math.max(maxVal, grid[r][c]);

  const cells = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const val = grid[r][c];
      const zoneId = `r${r}c${c}`;
      const isHotspot = snap.hotspot === zoneId && val > 0;
      const ratio = val / maxVal;
      let loadClass = 'load-low';
      if (isHotspot) loadClass = 'load-hotspot';
      else if (ratio > 0.66) loadClass = 'load-high';
      else if (ratio > 0.33) loadClass = 'load-med';
      cells.push({ zoneId: zoneId, val: val, loadClass: loadClass });
    }
  }
  mapEl.innerHTML = cells.map(function (cell) {
    return `<div class="zone-cell ${cell.loadClass}" title="${cell.zoneId}: ${cell.val}"><span class="zc-id">${cell.zoneId}</span><span class="zc-val">${cell.val}</span></div>`;
  }).join('');

  setText('zone-hotspot', snap.hotspot || 'None');
  const hotspotCell = snap.hotspot ? cells.find(function (c) { return c.zoneId === snap.hotspot; }) : null;
  setText('zone-hotspot-load', hotspotCell ? ('Local load: ' + hotspotCell.val) : (snap.hotspot ? '' : 'No concentrated zone currently'));

  const topLoaded = cells.filter(function (c) { return c.val > 0; }).sort(function (a, b) { return b.val - a.val; }).slice(0, 3);
  const topMax = topLoaded.length ? topLoaded[0].val : 1;
  setHtml('zone-top-loaded', topLoaded.length
    ? topLoaded.map(function (c) {
        const pct = Math.max(4, (c.val / topMax) * 100);
        return `<div class="top-zones-row"><span class="tz-id">${c.zoneId}</span><span class="tz-bar-track"><span class="tz-bar-fill" style="width:${pct.toFixed(0)}%"></span></span><span class="tz-val">${c.val}</span></div>`;
      }).join('')
    : '<div class="top-zones-row">No loaded zones</div>');

  setHtml('lar-bars', larBarsHtml(snap));
  setHtml('lar-interpretation', interpretDynamics(snap));
  setText('zone-scenario', snap.primary_scenario);
  setText('why-state', explainState(snap));
  setText('zone-response', snap.recommended_action);
  setText('response-zone', snap.hotspot ? ('Zone: ' + snap.hotspot) : 'No specific zone');
}

async function pollLoop() {
  const delay = await updateUI();
  setTimeout(pollLoop, delay);
}
moveCameraFeed('dashboard');
pollLoop();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    controls = """
      <div class=\"debug-controls\">
        <button class=\"debug-btn\" onclick=\"forceConnectivity('OFFLINE')\">Simulate OFFLINE</button>
        <button class=\"debug-btn\" onclick=\"forceConnectivity('')\">Clear Override (real checks)</button>
      </div>
      <div class=\"conn-note\">Simulation controls are for demoing the continuity loop where a real Wi-Fi toggle isn't available. They never affect crowd risk processing.</div>
    """ if ENABLE_DEBUG_CONNECTIVITY else ""
    return HTML_TEMPLATE.replace("{{DEBUG_CONNECTIVITY_CONTROLS}}", controls)


def _camera_mjpeg_stream():
    """Yield the latest runtime-owned frame as a local MJPEG stream.

    Independent of both the /status polling loop and inference: it only
    ever reads FrameSource's latest captured frame (never re-reads the
    camera itself), and skips re-encoding when no new frame has arrived
    since the last one sent, instead of busy-re-yielding an unchanged image.
    """
    import cv2

    last_sent_frame_id = -1
    while True:
        current_frame_id = runtime.source.get_latest_frame_id()
        if current_frame_id == last_sent_frame_id:
            time.sleep(0.02)
            continue
        frame = runtime.source.get_latest_frame()
        if frame is None:
            time.sleep(0.05)
            continue
        encoded, buffer = cv2.imencode(".jpg", frame)
        if not encoded:
            time.sleep(0.05)
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


@app.route("/status")
def status():
    snapshot = runtime.get_latest_snapshot()
    return jsonify(
        {
            "snapshot": snapshot.to_dict() if snapshot else None,
            "runtime_health": runtime.get_runtime_health(),
            "connectivity": connectivity.snapshot().to_dict(),
            "metrics": metrics.snapshot().to_dict(),
            # SQLite, not the in-memory presentation deque, is the durable
            # dashboard history.  ``audible`` only applies to alerts made
            # live by this process; historical rows never become new alarms.
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
            "default_simulation_available": _default_simulation_metadata is not None,
            "default_simulation_metadata": _default_simulation_metadata,
        }
    )


@app.route("/api/mode/reality", methods=["POST"])
def api_switch_to_reality():
    ok, error = switch_to_reality()
    if not ok:
        return jsonify({"success": False, "error": error}), 400
    return jsonify({"success": True, "mode": "REALITY"})


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
    safe_name = Path(file.filename).name  # strip any directory components
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
