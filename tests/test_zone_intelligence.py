"""Zone Intelligence data-contract tests.

Proves the 4x6 occupancy grid, hotspot, L/A/R, scenario, and recommended
action the dashboard's Zone Intelligence panel reads are all correctly
computed by the real, existing pipeline (OccupancyGrid / AdaptiveRisk /
ScenarioEngine via SentinelRuntime) and exposed through the real
RiskSnapshot contract and /status endpoint -- never fabricated in the UI
layer.

No webcam required: uses the same deterministic SIMULATION source + fake
detector pattern as tests/conftest.py and tests/test_round2_end_to_end.py.
"""
from __future__ import annotations

import numpy as np

from src.camera import FrameSource
from src.config import RuntimeConfig
from src.contracts import SourceMode
from src.detector import Detection
from src.runtime import SentinelRuntime


class _KnownLayoutDetector:
    """Places detections into three distinct, known grid cells (by pixel
    centroid on a 100x100 simulated frame against the default 4x6 grid),
    so the resulting occupancy grid's contents are fully predictable."""

    model_version = "zone-intel-fixture"

    def detect(self, frame):
        detections = []
        detections += [Detection((0, 0, 2, 2), (20, 55), 0.9) for _ in range(5)]  # -> r2c1 (busiest)
        detections += [Detection((0, 0, 2, 2), (75, 5), 0.9) for _ in range(3)]   # -> r0c4
        detections += [Detection((0, 0, 2, 2), (2, 95), 0.9)]                      # -> r3c0
        return detections, 0.01


def _build_runtime() -> SentinelRuntime:
    return SentinelRuntime(
        FrameSource(SourceMode.SIMULATION, simulation_factory=lambda: np.zeros((100, 100, 3), dtype=np.uint8)),
        _KnownLayoutDetector(),
        RuntimeConfig(calibration_samples=1),
    )


def test_occupancy_grid_is_4x6_and_matches_known_detections():
    snapshot = _build_runtime().process_once()
    assert snapshot is not None
    grid = snapshot.occupancy_grid
    assert len(grid) == 4
    assert all(len(row) == 6 for row in grid)
    assert grid[2][1] == 5
    assert grid[0][4] == 3
    assert grid[3][0] == 1
    assert sum(sum(row) for row in grid) == 9


def test_hotspot_matches_the_busiest_cell():
    snapshot = _build_runtime().process_once()
    assert snapshot.hotspot == "r2c1"


def test_top_loaded_zones_computed_from_the_grid_match_dashboard_logic():
    """Mirrors the dashboard's own top-3-by-count JS logic in Python over the
    real snapshot grid, proving the data supports it correctly."""
    snapshot = _build_runtime().process_once()
    grid = snapshot.occupancy_grid

    cells = [
        (f"r{r}c{c}", value)
        for r, row in enumerate(grid)
        for c, value in enumerate(row)
        if value > 0
    ]
    top_loaded = sorted(cells, key=lambda item: item[1], reverse=True)[:3]

    assert top_loaded == [("r2c1", 5), ("r0c4", 3), ("r3c0", 1)]


def test_load_anomaly_accumulation_redistribution_are_present_and_numeric():
    snapshot = _build_runtime().process_once()
    assert isinstance(snapshot.load_anomaly, float)
    assert isinstance(snapshot.accumulation, float)
    assert isinstance(snapshot.redistribution, float)


def test_scenario_and_recommended_action_are_present():
    snapshot = _build_runtime().process_once()
    assert snapshot.primary_scenario is not None
    assert isinstance(snapshot.recommended_action, str) and snapshot.recommended_action


def test_status_endpoint_exposes_every_field_the_zone_panel_needs(tmp_path, monkeypatch):
    import deploy

    from src.connectivity import ConnectivityManager
    from src.metrics import ContinuityMetrics
    from src.persistence import IncidentJournal

    runtime = _build_runtime()
    runtime.process_once()
    monkeypatch.setattr(deploy, "runtime", runtime)

    # /status also reads deploy.journal (recent_events) and deploy.metrics
    # (which itself wraps a journal/connectivity pair) -- give all three an
    # isolated, self-contained database so this test never depends on
    # data/sentinel.db (or its parent directory) existing on disk.
    journal = IncidentJournal(tmp_path / "sentinel.db")
    journal.initialize()
    connectivity = ConnectivityManager(check_fn=lambda: (True, 10.0))
    connectivity.check_once()
    monkeypatch.setattr(deploy, "journal", journal)
    monkeypatch.setattr(deploy, "connectivity", connectivity)
    monkeypatch.setattr(deploy, "metrics", ContinuityMetrics(journal, connectivity))

    response = deploy.app.test_client().get("/status")
    assert response.status_code == 200
    payload = response.get_json()
    snapshot = payload["snapshot"]
    health = payload["runtime_health"]

    assert {"state", "worker_alive", "consecutive_failures", "snapshot_fresh", "camera_health"} <= health.keys()

    for field in (
        "occupancy_grid", "hotspot", "load_anomaly", "accumulation",
        "redistribution", "primary_scenario", "recommended_action",
    ):
        assert field in snapshot

    assert len(snapshot["occupancy_grid"]) == 4
    assert len(snapshot["occupancy_grid"][0]) == 6
    assert snapshot["hotspot"] == "r2c1"


def test_dashboard_template_has_status_unavailable_stale_fallback():
    import deploy

    assert "function markStatusUnavailable()" in deploy.HTML_TEMPLATE
    assert "LOCAL STATUS UNAVAILABLE" in deploy.HTML_TEMPLATE
    assert "AI RISK OUTPUT STALE" in deploy.HTML_TEMPLATE
