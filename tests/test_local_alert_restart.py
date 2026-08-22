"""Restart-safe local delivery and operator acknowledgement coverage."""
from types import SimpleNamespace

import deploy
import pytest

from src.alerts import LocalAlertCenter
from src.connectivity import ConnectivityManager
from src.contracts import Severity
from src.metrics import ContinuityMetrics
from src.persistence import IncidentJournal, LocalStatus, SyncStatus


class _Runtime:
    def __init__(self, snapshot=None, fresh=True):
        self.snapshot = snapshot
        self.fresh = fresh

    def get_latest_snapshot(self):
        return self.snapshot

    def get_runtime_health(self):
        return {"snapshot_fresh": self.fresh}


def _wire_recovery(monkeypatch, journal, runtime):
    connectivity = ConnectivityManager(check_fn=lambda: (True, 1.0))
    connectivity.check_once()
    monkeypatch.setattr(deploy, "journal", journal)
    monkeypatch.setattr(deploy, "runtime", runtime)
    monkeypatch.setattr(deploy, "alert_center", LocalAlertCenter())
    monkeypatch.setattr(deploy, "metrics", ContinuityMetrics(journal, connectivity))


def test_crash_after_persist_recovers_same_row_and_uuid(tmp_path, incident_factory, monkeypatch):
    db_path = tmp_path / "sentinel.db"
    candidate = incident_factory()
    first = IncidentJournal(db_path)
    first.initialize()
    assert first.save_event(candidate, "OFFLINE") is True  # simulated death before delivery
    assert first.get_event(candidate.event_id).local_status == LocalStatus.PERSISTED

    restarted = IncidentJournal(db_path)
    restarted.initialize()
    _wire_recovery(monkeypatch, restarted, _Runtime())  # scene has already recovered

    assert deploy.recover_local_delivery() == 1
    recovered = restarted.get_event(candidate.event_id)
    assert recovered.event_id == candidate.event_id
    assert recovered.local_status == LocalStatus.LOCAL_DELIVERED
    assert restarted.count_events() == 1
    assert deploy.metrics.snapshot().events_lost == 0
    # Historical recovery is durable history, not a new live/audible alert.
    assert deploy.alert_center.recent() == []


def test_current_restart_recovery_is_locally_presented_without_remote_notifier(tmp_path, incident_factory, monkeypatch):
    journal = IncidentJournal(tmp_path / "sentinel.db")
    journal.initialize()
    candidate = incident_factory()
    journal.save_event(candidate, "OFFLINE")
    snapshot = SimpleNamespace(
        severity=Severity.RED,
        primary_scenario=candidate.primary_scenario,
        hotspot=candidate.hotspot,
    )
    _wire_recovery(monkeypatch, journal, _Runtime(snapshot))

    deploy.recover_local_delivery()

    assert journal.get_event(candidate.event_id).local_status == LocalStatus.LOCAL_DELIVERED
    assert deploy.alert_center.latest()["event_id"] == candidate.event_id
    assert journal.count_events() == 1


def test_acknowledgement_is_idempotent_and_independent_from_sync(tmp_path, incident_factory, monkeypatch):
    journal = IncidentJournal(tmp_path / "sentinel.db")
    journal.initialize()
    candidate = incident_factory()
    journal.save_event(candidate, "OFFLINE")
    journal.mark_local_delivered(candidate.event_id)
    journal.mark_auth_blocked(candidate.event_id)
    _wire_recovery(monkeypatch, journal, _Runtime())
    client = deploy.app.test_client()

    first = client.post(f"/alerts/{candidate.event_id}/ack")
    second = client.post(f"/alerts/{candidate.event_id}/ack")
    record = journal.get_event(candidate.event_id)

    assert first.status_code == second.status_code == 200
    assert record.local_status == LocalStatus.LOCAL_ACKNOWLEDGED
    assert record.sync_status == SyncStatus.AUTH_BLOCKED
    assert journal.count_events() == 1


@pytest.mark.parametrize("sync_status", [SyncStatus.PENDING, SyncStatus.SYNCED])
def test_acknowledgement_preserves_pending_and_synced_transport_state(tmp_path, incident_factory, monkeypatch, sync_status):
    journal = IncidentJournal(tmp_path / "sentinel.db")
    journal.initialize()
    candidate = incident_factory()
    journal.save_event(candidate, "OFFLINE")
    journal.mark_local_delivered(candidate.event_id)
    if sync_status == SyncStatus.SYNCED:
        journal.mark_synced(candidate.event_id)
    _wire_recovery(monkeypatch, journal, _Runtime())

    assert deploy.app.test_client().post(f"/alerts/{candidate.event_id}/ack").status_code == 200
    record = journal.get_event(candidate.event_id)
    assert record.local_status == LocalStatus.LOCAL_ACKNOWLEDGED
    assert record.sync_status == sync_status


def test_ack_rejects_unknown_and_undelivered_events(tmp_path, incident_factory, monkeypatch):
    journal = IncidentJournal(tmp_path / "sentinel.db")
    journal.initialize()
    candidate = incident_factory()
    journal.save_event(candidate, "OFFLINE")
    _wire_recovery(monkeypatch, journal, _Runtime())
    client = deploy.app.test_client()

    assert client.post("/alerts/not-an-event/ack").status_code == 404
    assert client.post(f"/alerts/{candidate.event_id}/ack").status_code == 409
    assert journal.get_event(candidate.event_id).local_status == LocalStatus.PERSISTED


def test_durable_alert_history_survives_restart_and_ack(tmp_path, incident_factory):
    db_path = tmp_path / "sentinel.db"
    candidate = incident_factory()
    journal = IncidentJournal(db_path)
    journal.initialize()
    journal.save_event(candidate, "OFFLINE")
    journal.mark_local_delivered(candidate.event_id)
    assert candidate.event_id in {event.event_id for event in journal.list_unacknowledged_local_events()}
    journal.mark_local_acknowledged(candidate.event_id)

    restarted = IncidentJournal(db_path)
    restarted.initialize()
    record = restarted.get_event(candidate.event_id)
    assert record.event_id == candidate.event_id
    assert record.local_status == LocalStatus.LOCAL_ACKNOWLEDGED
    counts = restarted.count_by_local_status()
    assert counts[LocalStatus.LOCAL_ACKNOWLEDGED] == 1


def test_browser_audible_deduplication_is_immutable_event_id_based():
    assert "sentinel-audible-event:" in deploy.HTML_TEMPLATE
    assert "alert.event_id" in deploy.HTML_TEMPLATE
    assert "localStorage.getItem(key)" in deploy.HTML_TEMPLATE
