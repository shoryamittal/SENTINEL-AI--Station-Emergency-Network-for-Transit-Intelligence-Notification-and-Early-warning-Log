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
