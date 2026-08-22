"""Contract tests that run without cameras, models, or network access."""

from datetime import datetime, timezone

from src.contracts import (
    BaselineState,
    CameraHealth,
    IncidentCandidate,
    RiskSnapshot,
    Scenario,
    Severity,
    SourceMode,
)


def test_risk_snapshot_serializes_cleanly():
    snapshot = RiskSnapshot(
        timestamp_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
        frame_id=3, source_mode=SourceMode.SIMULATION, people_count=4,
        occupancy_index=0.12, occupancy_grid=((1, 0), (0, 3)),
        baseline_state=BaselineState.CALIBRATING, load_anomaly=0.0,
        accumulation=0.0, redistribution=0.0, primary_scenario=Scenario.UNKNOWN,
        contributing_conditions=(), severity=Severity.GREEN, confidence=0.5,
        hotspot="r1c1", recommended_action="Continue monitoring.",
        action_code="MONITOR", camera_health=CameraHealth.LIVE,
        frame_age_ms=2.0, processing_latency_ms=4.0, model_version="test",
    )
    payload = snapshot.to_dict()
    assert payload["source_mode"] == "SIMULATION"
    assert payload["occupancy_grid"] == [[1, 0], [0, 3]]
    assert payload["timestamp_utc"].endswith("+00:00")


def test_incident_candidate_has_stable_serializable_id():
    candidate = IncidentCandidate(
        severity=Severity.YELLOW, primary_scenario=Scenario.ACCUMULATION,
        contributing_conditions=(), hotspot="r0c2", load_anomaly=0.2,
        accumulation=0.4, redistribution=0.0,
        recommended_action="Prepare inflow control and monitor affected zone.",
        action_code="PREPARE_INFLOW_CONTROL", model_version="test",
    )
    assert candidate.to_dict()["event_id"] == candidate.event_id
    assert candidate.to_dict()["severity"] == "YELLOW"
