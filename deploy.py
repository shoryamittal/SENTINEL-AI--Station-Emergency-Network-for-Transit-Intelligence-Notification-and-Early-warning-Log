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
from pathlib import Path
from threading import Event, Thread

from flask import Flask, Response, jsonify, request

from src import SentinelRuntime
from src.alerts import LocalAlertCenter, optional_fast2sms_notifier
from src.camera import FrameSource
from src.config import RuntimeConfig
from src.connectivity import ConnectivityManager, ConnectivityState
from src.contracts import SourceMode
from src.metrics import ContinuityMetrics
from src.persistence import IncidentJournal
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
CONNECTIVITY_INTERVAL_S = _env_float("CONNECTIVITY_CHECK_INTERVAL_S", 5.0)
ENABLE_FAST2SMS = os.environ.get("ENABLE_FAST2SMS", "0") == "1"

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
    sync_adapter = HttpSyncAdapter(SYNC_ENDPOINT_URL, timeout_s=SYNC_HTTP_TIMEOUT_S)
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


runtime = SentinelRuntime(
    FrameSource(_source_mode, _source_value),
    config=runtime_config,
    incident_sink=_durably_accept_incident,
)


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


def initialize_system() -> None:
    journal.initialize()
    runtime.start()
    connectivity.start()
    sync_worker.start()

    global _consumer_thread
    _consumer_stop.clear()
    _consumer_thread = Thread(target=_incident_consumer, daemon=True)
    _consumer_thread.start()


# ----------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SENTINEL AI - Continuity Dashboard</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:'Segoe UI', system-ui, -apple-system, sans-serif; background:#0f172a; color:#e2e8f0; min-height:100vh; }
  .header { background:linear-gradient(135deg,#1e293b 0%,#0f172a 100%); border-bottom:1px solid #334155; padding:1.25rem 2rem; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.75rem; }
  .header h1 { font-size:1.5rem; color:#34d399; letter-spacing:.02em; }
  .header .subtitle { font-size:.8rem; color:#94a3b8; }
  .container { max-width:1600px; margin:0 auto; padding:1.5rem 2rem; display:grid; grid-template-columns:1fr 1fr; gap:1.25rem; }
  @media (max-width:1000px){ .container{ grid-template-columns:1fr; } }
  .card { background:#1e293b; border:1px solid #334155; border-radius:.75rem; padding:1.25rem; }
  .card h2 { font-size:.8rem; text-transform:uppercase; letter-spacing:.06em; color:#94a3b8; margin-bottom:1rem; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:.75rem; }
  .grid4 { display:grid; grid-template-columns:repeat(4,1fr); gap:.75rem; }
  .stat { background:#0f172a; border:1px solid #334155; border-radius:.5rem; padding:.75rem; }
  .stat .label { font-size:.65rem; text-transform:uppercase; color:#64748b; letter-spacing:.04em; margin-bottom:.25rem; }
  .stat .value { font-size:1.25rem; font-weight:700; color:#f1f5f9; }

  /* Crowd severity palette: greens/reds -- deliberately distinct from connectivity colors */
  .sev-GREEN .value { color:#34d399; }
  .sev-YELLOW .value { color:#fbbf24; }
  .sev-RED .value { color:#f87171; }
  .sev-BLACK .value { color:#cbd5e1; }
  .badge { display:inline-block; padding:.35rem .75rem; border-radius:.4rem; font-weight:700; font-size:.85rem; letter-spacing:.03em; }
  .badge-GREEN { background:rgba(16,185,129,.15); color:#34d399; border:1px solid #065f46; }
  .badge-YELLOW { background:rgba(245,158,11,.15); color:#fbbf24; border:1px solid #92400e; }
  .badge-RED { background:rgba(239,68,68,.15); color:#f87171; border:1px solid #991b1b; }
  .badge-BLACK { background:rgba(100,116,139,.2); color:#e2e8f0; border:1px solid #475569; }
  .badge-UNKNOWN, .badge-STALE { background:rgba(100,116,139,.15); color:#94a3b8; border:1px dashed #475569; }

  /* Connectivity palette: blue/amber/neutral -- never red/black, so it can
     never be mistaken for crowd danger. */
  .badge-ONLINE { background:rgba(59,130,246,.15); color:#60a5fa; border:1px solid #1d4ed8; }
  .badge-DEGRADED { background:rgba(245,158,11,.15); color:#fbbf24; border:1px solid #92400e; }
  .badge-OFFLINE { background:rgba(100,116,139,.2); color:#cbd5e1; border:1px solid #475569; }
  .badge-RECOVERY { background:rgba(56,189,248,.15); color:#38bdf8; border:1px solid #0369a1; }

  .stale-banner { display:none; background:rgba(100,116,139,.15); border:1px dashed #64748b; color:#cbd5e1; padding:.75rem 1rem; border-radius:.5rem; margin-bottom:1rem; font-size:.85rem; }
  .pulse-row { display:flex; justify-content:space-between; align-items:center; padding:.5rem 0; border-bottom:1px solid #263248; font-size:.85rem; }
  .pulse-row:last-child { border-bottom:none; }
  .pulse-dot { display:inline-block; width:.5rem; height:.5rem; border-radius:50%; margin-right:.5rem; }
  .pulse-active { background:#34d399; }
  .pulse-stale { background:#64748b; }
  .events-table { width:100%; border-collapse:collapse; font-size:.8rem; }
  .events-table th { text-align:left; color:#64748b; text-transform:uppercase; font-size:.65rem; padding:.4rem; border-bottom:1px solid #334155; }
  .events-table td { padding:.4rem; border-bottom:1px solid #1e293b; }
  .full-row { grid-column:1 / -1; }
  .conn-note { font-size:.7rem; color:#64748b; margin-top:.5rem; }
  .debug-controls { display:flex; gap:.5rem; margin-top:.75rem; }
  .debug-btn { padding:.4rem .75rem; border-radius:.4rem; border:1px solid #334155; background:#0f172a; color:#94a3b8; font-size:.75rem; cursor:pointer; }
  .debug-btn:hover { border-color:#475569; color:#e2e8f0; }
  .camera-preview { width:100%; max-height:440px; object-fit:contain; background:#000; border-radius:.5rem; display:block; margin-top:.85rem; }

  /* Crowd State -- "Station Crowd Intelligence" console. Deliberately its
     own load palette (green/amber/red tint + a distinct hotspot outline),
     not reused from connectivity's blue/amber/neutral so it can't be
     confused with system health, and kept lower-contrast than the crowd
     severity badges so spatial detail never reads as a second severity
     indicator. */
  .crowd-console h2 { margin-bottom: .9rem; }
  .exec-metrics { display:grid; grid-template-columns:1fr 1fr 1.3fr; gap:1rem; margin-bottom:1.25rem; }
  .exec-card { background:#0f172a; border:1px solid #334155; border-radius:.6rem; padding:1rem 1.1rem; }
  .exec-label { font-size:.7rem; text-transform:uppercase; letter-spacing:.05em; color:#64748b; margin-bottom:.35rem; }
  .exec-value { font-size:2.1rem; font-weight:800; color:#f1f5f9; line-height:1.1; }
  .exec-note { font-size:.68rem; color:#64748b; margin-top:.3rem; }
  .exec-card.exec-risk { border-width:2px; }
  .exec-card.exec-risk .exec-value { font-size:1.9rem; }
  .exec-risk.sev-GREEN { border-color:#065f46; background:rgba(16,185,129,.08); }
  .exec-risk.sev-GREEN .exec-value { color:#34d399; }
  .exec-risk.sev-YELLOW { border-color:#92400e; background:rgba(245,158,11,.08); }
  .exec-risk.sev-YELLOW .exec-value { color:#fbbf24; }
  .exec-risk.sev-RED { border-color:#991b1b; background:rgba(239,68,68,.1); }
  .exec-risk.sev-RED .exec-value { color:#f87171; }
  .exec-risk.sev-BLACK { border-color:#475569; background:rgba(100,116,139,.12); }
  .exec-risk.sev-BLACK .exec-value { color:#e2e8f0; }

  .console-body { display:grid; grid-template-columns:1.15fr 1fr; gap:1.5rem; }
  @media (max-width:1100px) { .console-body { grid-template-columns:1fr; } }
  .console-subheader { font-size:.7rem; text-transform:uppercase; letter-spacing:.06em; color:#64748b; margin-bottom:.6rem; }

  .spatial-layout { display:flex; gap:1.1rem; align-items:flex-start; flex-wrap:wrap; }
  .zone-map-lg { display:grid; grid-template-columns:repeat(6, 1fr); grid-auto-rows:1fr; gap:4px; width:100%; max-width:400px; aspect-ratio:6/4; flex-shrink:0; }
  .zone-cell { border-radius:.3rem; background:#161f30; border:1px solid #232f45; display:flex; align-items:center; justify-content:center; font-size:.72rem; color:#3f4c63; font-weight:500; transition:background .2s; }
  .zone-cell.load-low { color:#64748b; background:#182130; }
  .zone-cell.load-med { background:#241d0c; border-color:#78350f; color:#fbbf24; font-size:.8rem; font-weight:700; }
  .zone-cell.load-high { background:#241010; border-color:#7f1d1d; color:#f87171; font-size:.85rem; font-weight:800; }
  .zone-cell.load-hotspot { background:#3b0f0f; border-color:#f87171; color:#fecaca; font-size:.9rem; font-weight:800; box-shadow:0 0 0 1px #f87171 inset; }
  .spatial-side { flex:1; min-width:170px; display:flex; flex-direction:column; gap:.9rem; }
  .spatial-side .label { font-size:.65rem; text-transform:uppercase; color:#64748b; letter-spacing:.04em; margin-bottom:.25rem; }
  .hotspot-block .value { font-size:1.4rem; font-weight:800; color:#f87171; }
  .hotspot-block .sub { font-size:.75rem; color:#94a3b8; margin-top:.15rem; }
  .top-zones-row { display:flex; align-items:center; gap:.5rem; font-size:.78rem; color:#cbd5e1; margin-bottom:.4rem; }
  .top-zones-row .tz-id { width:2.6rem; color:#94a3b8; font-weight:600; }
  .top-zones-row .tz-bar-track { flex:1; height:6px; background:#1a2332; border-radius:3px; overflow:hidden; }
  .top-zones-row .tz-bar-fill { height:100%; background:linear-gradient(90deg,#f59e0b,#f87171); }
  .top-zones-row .tz-val { width:2.4rem; text-align:right; font-weight:700; color:#f1f5f9; }

  .lar-row { display:flex; align-items:center; gap:.6rem; font-size:.78rem; color:#94a3b8; margin-bottom:.5rem; }
  .lar-row .lar-name { width:8.5rem; text-transform:uppercase; letter-spacing:.03em; font-size:.68rem; }
  .lar-row .lar-bar-track { flex:1; height:8px; background:#1a2332; border-radius:4px; overflow:hidden; }
  .lar-row .lar-bar-fill { height:100%; background:linear-gradient(90deg,#38bdf8,#818cf8); }
  .lar-row .lar-val { width:3rem; text-align:right; font-weight:700; color:#f1f5f9; }
  .interpretation { font-size:.8rem; color:#cbd5e1; background:#0f172a; border:1px solid #263248; border-radius:.5rem; padding:.6rem .75rem; margin-top:.3rem; }

  .behavior-value { font-size:1.05rem; font-weight:700; color:#f1f5f9; background:#0f172a; border:1px solid #263248; border-radius:.5rem; padding:.65rem .8rem; }
  .why-text { font-size:.83rem; color:#cbd5e1; line-height:1.5; background:#0f172a; border:1px solid #263248; border-radius:.5rem; padding:.65rem .8rem; }
  .response-block { background:#0f172a; border:1px solid #263248; border-left:3px solid #34d399; border-radius:.5rem; padding:.7rem .85rem; }
  .response-action { font-size:.92rem; font-weight:700; color:#f1f5f9; }
  .response-zone { font-size:.75rem; color:#94a3b8; margin-top:.3rem; }
</style>
</head>
<body>
  <div class="header">
    <div>
      <h1>SENTINEL AI</h1>
      <div class="subtitle">Station Emergency Network for Transit Intelligence, Notification and Early-warning &mdash; Continuity Plane</div>
    </div>
    <div>
      <span id="conn-badge" class="badge badge-ONLINE">CONNECTIVITY: --</span>
    </div>
  </div>

  <div class="container">

    <!-- Section 1: Input health -->
    <div class="card">
      <h2>Input Health</h2>
      <div id="stale-banner" class="stale-banner">
        INPUT STALE &mdash; showing last valid frame. Current live risk is UNKNOWN until the camera recovers.
      </div>
      <div class="grid4">
        <div class="stat"><div class="label">Camera</div><div class="value" id="camera-health">--</div></div>
        <div class="stat"><div class="label">Frame ID</div><div class="value" id="frame-id">--</div></div>
        <div class="stat"><div class="label">Camera Frame Age</div><div class="value" id="frame-age">--</div></div>
        <div class="stat"><div class="label">AI Inference Latency</div><div class="value" id="proc-latency">--</div></div>
      </div>
      <div class="conn-note" id="last-update">AI update: --</div>
      <div class="conn-note">Camera frame age reflects how fresh the picture itself is (independent of inference speed). AI inference latency is how long the last risk computation took to run on that frame.</div>
      <div class="camera-preview-label">LIVE CAMERA FEED</div>
      <img class="camera-preview" src="/camera-feed" id="camera-feed" alt="SENTINEL local camera feed">
    </div>

    <!-- Section 4: Connectivity (paired with Input Health so row 1 has no
         empty gap before the full-width Crowd State console below) -->
    <div class="card">
      <h2>Connectivity</h2>
      <div class="grid4">
        <div class="stat"><div class="label">State</div><div class="value" id="conn-state">--</div></div>
        <div class="stat"><div class="label">Outage Duration</div><div class="value" id="outage-duration">--</div></div>
        <div class="stat"><div class="label">Last Remote Success</div><div class="value" id="last-remote-success" style="font-size:.85rem;">--</div></div>
        <div class="stat"><div class="label">Pending Sync</div><div class="value" id="pending-sync">--</div></div>
      </div>
      <div class="debug-controls">
        <button class="debug-btn" onclick="forceConnectivity('OFFLINE')">Simulate OFFLINE</button>
        <button class="debug-btn" onclick="forceConnectivity('')">Clear Override (real checks)</button>
      </div>
      <div class="conn-note">Simulation controls are for demoing the continuity loop where a real Wi-Fi toggle isn't available. They never affect crowd risk processing.</div>
    </div>

    <!-- Section 2: Crowd state -- Station Crowd Intelligence console -->
    <div class="card full-row crowd-console">
      <h2>Crowd State &mdash; Station Crowd Intelligence</h2>

      <!-- Executive metrics -->
      <div class="exec-metrics">
        <div class="exec-card">
          <div class="exec-label">People</div>
          <div class="exec-value" id="people-count">--</div>
        </div>
        <div class="exec-card">
          <div class="exec-label">Relative Occupancy</div>
          <div class="exec-value" id="occupancy-index">--</div>
          <div class="exec-note">Not calibrated people/m&sup2;</div>
        </div>
        <div class="exec-card exec-risk" id="severity-stat">
          <div class="exec-label">Current Risk <span id="confidence-note"></span></div>
          <div class="exec-value" id="severity">--</div>
        </div>
      </div>

      <div class="console-body">
        <!-- Spatial intelligence -->
        <div class="console-col spatial-col">
          <div class="console-subheader">Spatial Crowd Map (4&times;6 occupancy grid)</div>
          <div class="spatial-layout">
            <div class="zone-map-lg" id="zone-map"></div>
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

        <!-- Crowd dynamics + operator interpretation -->
        <div class="console-col dynamics-col">
          <div class="console-subheader">Crowd Dynamics</div>
          <div id="lar-bars"></div>
          <div class="interpretation" id="lar-interpretation">--</div>

          <div class="console-subheader" style="margin-top:1.1rem;">Current Crowd Behavior</div>
          <div class="behavior-value" id="zone-scenario">--</div>

          <div class="console-subheader" style="margin-top:1.1rem;">Why This State?</div>
          <div class="why-text" id="why-state">--</div>

          <div class="console-subheader" style="margin-top:1.1rem;">Recommended Response</div>
          <div class="response-block">
            <div class="response-action" id="zone-response">--</div>
            <div class="response-zone" id="response-zone">--</div>
          </div>
        </div>
      </div>
    </div>

    <!-- Section 5: Sentinel pulse -->
    <div class="card">
      <h2>Sentinel Pulse</h2>
      <div id="pulse-list"></div>
    </div>

    <!-- Section 6: Event recovery -->
    <div class="card">
      <h2>Event Recovery</h2>
      <div class="grid4">
        <div class="stat"><div class="label">Generated</div><div class="value" id="m-generated">--</div></div>
        <div class="stat"><div class="label">Persisted</div><div class="value" id="m-persisted">--</div></div>
        <div class="stat"><div class="label">Local Delivered</div><div class="value" id="m-local-delivered">--</div></div>
        <div class="stat"><div class="label">Lost</div><div class="value" id="m-lost">--</div></div>
      </div>
      <div class="grid4" style="margin-top:.75rem;">
        <div class="stat"><div class="label">Pending</div><div class="value" id="m-pending">--</div></div>
        <div class="stat"><div class="label">Syncing</div><div class="value" id="m-syncing">--</div></div>
        <div class="stat"><div class="label">Synced</div><div class="value" id="m-synced">--</div></div>
        <div class="stat"><div class="label">Failed</div><div class="value" id="m-failed">--</div></div>
      </div>
    </div>

    <div class="card full-row">
      <h2>Recent Events</h2>
      <table class="events-table">
        <thead><tr><th>Created</th><th>Severity</th><th>Scenario</th><th>Hotspot</th><th>Local</th><th>Sync</th><th>Retries</th></tr></thead>
        <tbody id="events-body"></tbody>
      </table>
    </div>

  </div>

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

let lastAlertId = null;

function fmtMs(ms) {
  if (ms === null || ms === undefined || !isFinite(ms)) return '--';
  return ms < 1000 ? Math.round(ms) + ' ms' : (ms/1000).toFixed(1) + ' s';
}
function fmtAgo(iso) {
  if (!iso) return '--';
  const secs = (Date.now() - new Date(iso).getTime()) / 1000;
  if (secs < 0) return 'just now';
  return secs < 60 ? secs.toFixed(1) + ' sec ago' : Math.round(secs/60) + ' min ago';
}
function setBadgeClass(el, prefix, value) {
  el.className = 'badge badge-' + (value || 'UNKNOWN');
  el.textContent = (prefix ? prefix + ': ' : '') + (value || 'UNKNOWN');
}
function pulseRow(name, active, detail) {
  return '<div class="pulse-row"><span><span class="pulse-dot ' + (active ? 'pulse-active' : 'pulse-stale') +
    '"></span>' + name + '</span><span>' + detail + '</span></div>';
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
  const parts = [
    'Accumulation ' + levelWord(snap.accumulation, t.accumYellow, t.accumRed),
    'Redistribution ' + levelWord(snap.redistribution, t.redistYellow, t.redistRed),
    'Load anomaly ' + levelWord(snap.load_anomaly, t.loadYellow, t.redistRed),
  ];
  return parts.join(' &middot; ');
}

function explainState(snap) {
  const L = snap.load_anomaly, A = snap.accumulation, R = snap.redistribution;
  const hotspotText = snap.hotspot ? ('near ' + snap.hotspot) : 'without a concentrated zone';
  switch (snap.primary_scenario) {
    case 'LOCAL_BOTTLENECK':
      return 'A single zone is carrying an extreme concentration of people ' + hotspotText + ' (load anomaly ' + L.toFixed(2) + ').';
    case 'ACCUMULATION':
      return 'Accumulation (' + A.toFixed(2) + ') is building faster than it is dispersing, concentrating pressure ' + hotspotText + '.';
    case 'MASS_REDISTRIBUTION':
      return 'Redistribution (' + R.toFixed(2) + ') shows the crowd shifting spatially ' + hotspotText + ' rather than staying settled.';
    case 'STABLE_HIGH_OCCUPANCY':
      return 'Occupancy is present, but load anomaly (' + L.toFixed(2) + '), accumulation (' + A.toFixed(2) + '), and redistribution (' +
        R.toFixed(2) + ') are all within normal range -- no abnormal transition detected.';
    default:
      return 'No people currently detected in the observed area.';
  }
}

function renderLarBars(snap) {
  const rows = [
    ['Load Anomaly (L)', snap.load_anomaly],
    ['Accumulation (A)', snap.accumulation],
    ['Redistribution (R)', snap.redistribution],
  ];
  document.getElementById('lar-bars').innerHTML = rows.map(function (row) {
    const pct = Math.max(0, Math.min(1, row[1])) * 100;
    return '<div class="lar-row"><span class="lar-name">' + row[0] + '</span>' +
      '<span class="lar-bar-track"><span class="lar-bar-fill" style="width:' + pct.toFixed(0) + '%"></span></span>' +
      '<span class="lar-val">' + row[1].toFixed(2) + '</span></div>';
  }).join('');
}

function renderCrowdConsole(snap) {
  const mapEl = document.getElementById('zone-map');
  const hotspotEl = document.getElementById('zone-hotspot');
  const hotspotLoadEl = document.getElementById('zone-hotspot-load');
  const topLoadedEl = document.getElementById('zone-top-loaded');
  const scenarioEl = document.getElementById('zone-scenario');
  const responseEl = document.getElementById('zone-response');
  const responseZoneEl = document.getElementById('response-zone');
  const whyEl = document.getElementById('why-state');
  const larBarsEl = document.getElementById('lar-bars');
  const larInterpEl = document.getElementById('lar-interpretation');

  const grid = snap && snap.occupancy_grid;
  if (!grid || !grid.length) {
    mapEl.innerHTML = '';
    hotspotEl.textContent = '--';
    hotspotLoadEl.textContent = '--';
    topLoadedEl.innerHTML = '--';
    scenarioEl.textContent = responseEl.textContent = '--';
    responseZoneEl.textContent = '';
    whyEl.textContent = '--';
    larBarsEl.innerHTML = '';
    larInterpEl.textContent = '--';
    return;
  }

  const rows = grid.length;
  const cols = grid[0].length;
  let maxVal = 1;
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) maxVal = Math.max(maxVal, grid[r][c]);

  // Row-major (r0c0..r{rows-1}c{cols-1}) iteration order preserves the
  // runtime's own zone-id semantics and keeps any value ties in a
  // deterministic, stable order.
  const cells = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const val = grid[r][c];
      const zoneId = 'r' + r + 'c' + c;
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
    // Zero/near-zero zones are honest (never hidden) but visually
    // subordinate -- smaller, dimmer text via .load-low -- so a mostly
    // empty grid doesn't dominate the reader's attention.
    return '<div class="zone-cell ' + cell.loadClass + '" title="' + cell.zoneId + ': ' + cell.val + '">' + cell.val + '</div>';
  }).join('');

  hotspotEl.textContent = snap.hotspot || 'None';
  const hotspotCell = snap.hotspot ? cells.find(function (c) { return c.zoneId === snap.hotspot; }) : null;
  hotspotLoadEl.textContent = hotspotCell ? ('Local load: ' + hotspotCell.val) : (snap.hotspot ? '' : 'No concentrated zone currently');

  const topLoaded = cells.filter(function (c) { return c.val > 0; })
    .sort(function (a, b) { return b.val - a.val; }) // stable sort -> deterministic ties
    .slice(0, 3);
  const topMax = topLoaded.length ? topLoaded[0].val : 1;
  topLoadedEl.innerHTML = topLoaded.length
    ? topLoaded.map(function (c) {
        const pct = Math.max(4, (c.val / topMax) * 100);
        return '<div class="top-zones-row"><span class="tz-id">' + c.zoneId + '</span>' +
          '<span class="tz-bar-track"><span class="tz-bar-fill" style="width:' + pct.toFixed(0) + '%"></span></span>' +
          '<span class="tz-val">' + c.val + '</span></div>';
      }).join('')
    : '<div class="top-zones-row">No loaded zones</div>';

  renderLarBars(snap);
  larInterpEl.innerHTML = interpretDynamics(snap);

  scenarioEl.textContent = snap.primary_scenario;
  whyEl.textContent = explainState(snap);
  responseEl.textContent = snap.recommended_action;
  responseZoneEl.textContent = snap.hotspot ? ('Zone: ' + snap.hotspot) : 'No specific zone';
}

async function updateUI() {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch('/status', { signal: controller.signal });
    const data = await response.json();
    clearTimeout(timeoutId);
    render(data);
    return 800; // healthy cadence
  } catch (e) {
    clearTimeout(timeoutId);
    markStatusUnavailable();
    return 2000;
  }
}

function markStatusUnavailable() {
  document.getElementById('stale-banner').style.display = 'block';
  document.getElementById('stale-banner').textContent = 'LOCAL STATUS UNAVAILABLE — last displayed values are stale. Current live risk is UNKNOWN.';
  document.getElementById('severity').textContent = 'UNKNOWN';
  document.getElementById('severity-stat').className = 'exec-card exec-risk';
  document.getElementById('last-update').textContent = 'AI update: local status unavailable';
  setBadgeClass(document.getElementById('conn-badge'), 'CONNECTIVITY', 'UNKNOWN');
  document.getElementById('conn-state').textContent = 'UNKNOWN';
  document.getElementById('pulse-list').innerHTML = pulseRow('AI Engine', false, 'local status unavailable');
}

function render(data) {
  const snap = data.snapshot;
  const runtimeHealth = data.runtime_health || {};
  const conn = data.connectivity;
  const metrics = data.metrics;

  // --- Input health / stale handling ---
  const staleBanner = document.getElementById('stale-banner');
  const camHealth = runtimeHealth.camera_health || (snap ? snap.camera_health : 'CAMERA_LOST');
  const cameraStale = camHealth !== 'LIVE';
  const riskStale = !snap || !runtimeHealth.snapshot_fresh || ['DEGRADED','STALE','STOPPED','NOT_STARTED','STARTING'].includes(runtimeHealth.state);
  if (runtimeHealth.state === 'DEGRADED') {
    staleBanner.textContent = 'AI ENGINE DEGRADED — camera input may still be live, but the last risk assessment is not current.';
  } else if (riskStale) {
    staleBanner.textContent = 'AI RISK OUTPUT STALE — last valid assessment is historical. Current live risk is UNKNOWN.';
  } else if (cameraStale) {
    staleBanner.textContent = 'INPUT STALE — showing last valid frame. Current live risk is UNKNOWN until the camera recovers.';
  }
  staleBanner.style.display = (riskStale || cameraStale) ? 'block' : 'none';

  document.getElementById('camera-health').textContent = camHealth || '--';
  document.getElementById('frame-id').textContent = snap ? snap.frame_id : '--';
  document.getElementById('frame-age').textContent = snap ? fmtMs(snap.frame_age_ms) : '--';
  document.getElementById('proc-latency').textContent = snap ? fmtMs(snap.processing_latency_ms) : '--';
  document.getElementById('last-update').textContent = 'AI update: ' + (snap ? fmtAgo(snap.timestamp_utc) : '--');

  // A single coherent snapshot (riskSnap) drives every Crowd State element
  // below -- when input/risk is stale, ALL of them fall back together
  // (never a stale hotspot next to a fresh people count).
  const riskSnap = riskStale ? null : snap;
  document.getElementById('people-count').textContent = riskSnap ? riskSnap.people_count : '--';
  document.getElementById('occupancy-index').textContent = riskSnap ? riskSnap.occupancy_index.toFixed(2) : '--';
  document.getElementById('confidence-note').textContent = riskSnap ? ('(' + (riskSnap.confidence * 100).toFixed(0) + '% confidence)') : '';
  renderCrowdConsole(riskSnap);

  const severityEl = document.getElementById('severity');
  const severityStat = document.getElementById('severity-stat');
  const effectiveSeverity = riskStale ? 'STALE' : (snap ? snap.severity : 'UNKNOWN');
  severityEl.textContent = effectiveSeverity;
  severityStat.className = 'exec-card exec-risk sev-' + (SEVERITIES.includes(effectiveSeverity) ? effectiveSeverity : '');

  // --- Connectivity (visually distinct from crowd severity) ---
  setBadgeClass(document.getElementById('conn-badge'), 'CONNECTIVITY', conn.state);
  document.getElementById('conn-state').textContent = conn.state;
  document.getElementById('outage-duration').textContent = conn.current_outage_duration_s > 0
    ? conn.current_outage_duration_s.toFixed(0) + ' s' : '0 s';
  document.getElementById('last-remote-success').textContent = conn.last_success_at ? fmtAgo(conn.last_success_at) : 'never';
  document.getElementById('pending-sync').textContent = metrics.events_pending + metrics.events_retrying;

  // --- Sentinel pulse ---
  const pulses = [
    pulseRow('Camera', !cameraStale, camHealth),
    pulseRow('AI Engine', runtimeHealth.state === 'HEALTHY', runtimeHealth.state || 'UNKNOWN'),
    pulseRow('Risk Engine', !riskStale, riskStale ? 'STALE / UNKNOWN' : effectiveSeverity),
    pulseRow('SQLite', metrics.latest_database_success !== null, metrics.latest_database_success ? fmtAgo(metrics.latest_database_success) : 'no writes yet'),
    pulseRow('Remote Sync', conn.state === 'ONLINE' || conn.state === 'RECOVERY', metrics.latest_sync_success ? fmtAgo(metrics.latest_sync_success) + ' since last success' : (conn.state + ' -- no successful sync yet')),
  ];
  document.getElementById('pulse-list').innerHTML = pulses.join('');

  // --- Event recovery metrics ---
  document.getElementById('m-generated').textContent = metrics.events_generated;
  document.getElementById('m-persisted').textContent = metrics.events_persisted;
  document.getElementById('m-local-delivered').textContent = metrics.events_local_delivered;
  document.getElementById('m-lost').textContent = metrics.events_lost;
  document.getElementById('m-pending').textContent = metrics.events_pending;
  document.getElementById('m-syncing').textContent = metrics.events_syncing;
  document.getElementById('m-synced').textContent = metrics.events_synced;
  document.getElementById('m-failed').textContent = metrics.events_failed;

  // --- Recent events table ---
  const rows = (data.recent_events || []).map(function(e) {
    return '<tr><td>' + fmtAgo(e.created_at_utc) + '</td><td>' + e.severity + '</td><td>' + e.primary_scenario +
      '</td><td>' + (e.hotspot || '--') + '</td><td>' + e.local_status + '</td><td>' + e.sync_status +
      '</td><td>' + e.retry_count + '</td></tr>';
  });
  document.getElementById('events-body').innerHTML = rows.join('') || '<tr><td colspan="7">No events yet.</td></tr>';

  // --- Local alert (audible cue on new RED/BLACK, works with zero Internet) ---
  const alerts = data.local_alerts || [];
  if (alerts.length && alerts[0].event_id !== lastAlertId) {
    lastAlertId = alerts[0].event_id;
    if (alerts[0].audible) beep();
  }
}

function forceConnectivity(state) {
  const url = '/debug/connectivity' + (state ? ('?state=' + state) : '');
  fetch(url, { method: 'POST' }).catch(() => {});
}

// Self-scheduling poll loop: never stacks overlapping requests, and backs
// off automatically if a single poll is slow/fails.
async function pollLoop() {
  const delay = await updateUI();
  setTimeout(pollLoop, delay);
}
pollLoop();
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return HTML_TEMPLATE


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
            "local_alerts": alert_center.recent(10),
            "recent_events": [record.to_dict() for record in journal.get_recent_events(15)],
        }
    )


@app.route("/events/recent")
def events_recent():
    limit = max(1, min(200, request.args.get("limit", 20, type=int) or 20))
    return jsonify([record.to_dict() for record in journal.get_recent_events(limit)])


@app.route("/events/pending")
def events_pending():
    limit = max(1, min(200, request.args.get("limit", 50, type=int) or 50))
    return jsonify([record.to_dict() for record in journal.list_pending_events(limit)])


@app.route("/debug/connectivity", methods=["POST"])
def debug_connectivity():
    """DEMO/DEBUG ONLY: force connectivity state to exercise the offline/
    recovery loop without a real Wi-Fi toggle. Never affects the runtime.
    """
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
    print(f"Local URL: http://localhost:{port}")
    print(f"Database:  {journal.db_path}")
    print(f"Camera:    {_source_mode.value} source={_source_value!r}")
    print("=" * 80 + "\n")
    try:
        app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
    finally:
        _consumer_stop.set()
        sync_worker.stop()
        connectivity.stop()
        runtime.stop()
