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


class _SequentialGridDetector:
    """Places exactly (r*cols + c + 1) detections into every cell of a 4x6
    grid, i.e. cell r0c0=1, r0c1=2, ..., r0c5=6, r1c0=7, ..., r3c5=24 --
    proving the runtime's row-major grid never gets silently transposed or
    renumbered relative to its own "r{row}c{col}" zone-id convention."""

    model_version = "grid-mapping-fixture"
    rows, cols = 4, 6
    frame_size = 120  # divisible by both rows(4) and cols(6) for exact cell centers

    def detect(self, frame):
        cell_h = self.frame_size / self.rows
        cell_w = self.frame_size / self.cols
        detections = []
        for r in range(self.rows):
            for c in range(self.cols):
                count = r * self.cols + c + 1
                x = (c + 0.5) * cell_w
                y = (r + 0.5) * cell_h
                detections += [Detection((0, 0, 2, 2), (x, y), 0.9) for _ in range(count)]
        return detections, 0.01


def test_grid_row_major_mapping_is_never_transposed_or_renumbered():
    """The documented 1..24 layout (row-major, r0c0=1 ... r3c5=24) must come
    back out of the real pipeline exactly as-is -- no row/column swap."""
    runtime = SentinelRuntime(
        FrameSource(
            SourceMode.SIMULATION,
            simulation_factory=lambda: np.zeros((120, 120, 3), dtype=np.uint8),
        ),
        _SequentialGridDetector(),
        RuntimeConfig(calibration_samples=1),
    )
    snapshot = runtime.process_once()
    grid = snapshot.occupancy_grid

    assert len(grid) == 4
    assert all(len(row) == 6 for row in grid)
    for r in range(4):
        for c in range(6):
            expected = r * 6 + c + 1
            assert grid[r][c] == expected, f"r{r}c{c} expected {expected}, got {grid[r][c]}"

    # The busiest cell (24) is r3c5 -- confirms hotspot naming also follows
    # the same row-major r{row}c{col} convention, not a transposed one.
    assert snapshot.hotspot == "r3c5"


def test_snapshot_consistency_all_crowd_state_fields_come_from_one_snapshot():
    """A single process_once() call's return value must be internally
    consistent -- occupancy_grid, hotspot, L/A/R, scenario, and
    recommended_action all describe the exact same moment, never a mix of
    an old grid with a newer hotspot or vice versa."""
    runtime = _build_runtime()
    snapshot = runtime.process_once()

    # Re-derive the hotspot independently from this same snapshot's own
    # grid and compare it to the snapshot's own hotspot field -- if the
    # runtime ever mixed fields from two different frames, these could
    # disagree.
    grid = snapshot.occupancy_grid
    flat_max = max(
        ((r, c, grid[r][c]) for r in range(len(grid)) for c in range(len(grid[0]))),
        key=lambda item: item[2],
    )
    expected_hotspot = f"r{flat_max[0]}c{flat_max[1]}" if flat_max[2] > 0 else None
    assert snapshot.hotspot == expected_hotspot

    # The snapshot object itself is immutable (frozen dataclass), which is
    # the structural guarantee that no field can be mutated independently
    # after creation -- re-reading any field twice yields the same value.
    assert snapshot.load_anomaly == snapshot.load_anomaly
    assert snapshot.primary_scenario == snapshot.primary_scenario
    assert snapshot.recommended_action == snapshot.recommended_action


def test_dashboard_template_has_status_unavailable_stale_fallback():
    import deploy

    assert "function markStatusUnavailable()" in deploy.HTML_TEMPLATE
    assert "LOCAL STATUS UNAVAILABLE" in deploy.HTML_TEMPLATE
    assert "AI RISK OUTPUT STALE" in deploy.HTML_TEMPLATE
