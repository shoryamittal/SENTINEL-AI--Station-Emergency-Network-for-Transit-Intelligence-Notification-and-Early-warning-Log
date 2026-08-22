"""Regression coverage for the persist-before-local-alert boundary."""
from src.connectivity import ConnectivityManager, ConnectivityState
from src.persistence import IncidentJournal


def test_duplicate_candidate_does_not_fire_second_local_alert(tmp_path, incident_factory, monkeypatch):
    import deploy

    journal = IncidentJournal(tmp_path / "sentinel.db")
    journal.initialize()
    connectivity = ConnectivityManager(check_fn=lambda: (True, 1.0))
    connectivity.check_once()

    class Metrics:
        persisted = 0
        local_delivered = 0
        def record_persisted(self): self.persisted += 1
        def record_local_delivered(self): self.local_delivered += 1

    class Alerts:
        delivered = []
        def raise_alert(self, candidate): self.delivered.append(candidate.event_id)

    metrics, alerts = Metrics(), Alerts()
    monkeypatch.setattr(deploy, "journal", journal)
    monkeypatch.setattr(deploy, "connectivity", connectivity)
    monkeypatch.setattr(deploy, "metrics", metrics)
    monkeypatch.setattr(deploy, "alert_center", alerts)

    candidate = incident_factory()
    assert deploy._persist_and_deliver_incident(candidate) is True
    assert deploy._persist_and_deliver_incident(candidate) is False
    assert alerts.delivered == [candidate.event_id]
    assert metrics.persisted == metrics.local_delivered == 1
    assert journal.get_event(candidate.event_id).connectivity_state == ConnectivityState.ONLINE


def test_canonical_sink_retries_sqlite_failure_without_uuid_or_metric_inflation(tmp_path, monkeypatch):
    """The deploy sink is local-only and accepts one retained candidate once."""
    import numpy as np
    import deploy

    from src.camera import FrameSource
    from src.config import RuntimeConfig
    from src.contracts import Scenario, Severity, SourceMode
    from src.detector import Detection
    from src.metrics import ContinuityMetrics
    from src.runtime import SentinelRuntime

    journal = IncidentJournal(tmp_path / "sentinel.db")
    journal.initialize()
    connectivity = ConnectivityManager(check_fn=lambda: (True, 1.0))
    connectivity.check_once()
    metrics = ContinuityMetrics(journal, connectivity)
    monkeypatch.setattr(deploy, "journal", journal)
    monkeypatch.setattr(deploy, "connectivity", connectivity)
    monkeypatch.setattr(deploy, "metrics", metrics)

    real_save = journal.save_event
    attempts = []

    def flaky_save(candidate, connectivity_state):
        attempts.append(candidate.event_id)
        if len(attempts) < 3:
            raise RuntimeError("simulated local SQLite failure")
        return real_save(candidate, connectivity_state)

    monkeypatch.setattr(journal, "save_event", flaky_save)

    class Detector:
        model_version = "sink-test"
        def detect(self, frame): return [Detection((0, 0, 2, 2), (10, 10), .9)], .1

    class ScenarioEngine:
        def evaluate(self, *args, **kwargs):
            return Scenario.ACCUMULATION, (), Severity.RED, 1.0, "TEST", "Test"

    runtime = SentinelRuntime(
        FrameSource(SourceMode.SIMULATION, simulation_factory=lambda: np.zeros((100, 100, 3), dtype=np.uint8)),
        Detector(), RuntimeConfig(calibration_samples=1), incident_sink=deploy._durably_accept_incident,
    )
    runtime.scenario = ScenarioEngine()
    runtime.process_once()
    for _ in range(2):
        runtime._last_sink_attempt_mono = 0.0
        runtime.process_once()

    candidate = runtime.get_next_incident(timeout=0.1)
    assert candidate is not None
    assert attempts == [candidate.event_id] * 3
    assert journal.count_events() == 1
    assert journal.get_event(candidate.event_id).event_id == candidate.event_id
    snapshot = metrics.snapshot()
    assert snapshot.events_generated == 1
    assert snapshot.events_persisted == 1
    # The sink itself never invokes a remote adapter; sync remains pending.
    assert journal.get_event(candidate.event_id).sync_status == "SYNC_PENDING"
    assert runtime.get_latest_snapshot() is not None
