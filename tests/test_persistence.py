"""Durable local incident journal tests. No network, no camera required."""
import sqlite3

import pytest

from src.contracts import IncidentCandidate, Scenario, Severity, SourceMode
from src.persistence import IncidentJournal, LocalStatus, SyncStatus


def make_candidate(**overrides) -> IncidentCandidate:
    defaults = dict(
        severity=Severity.RED,
        primary_scenario=Scenario.ACCUMULATION,
        contributing_conditions=(),
        hotspot="r1c2",
        load_anomaly=0.3,
        accumulation=0.25,
        redistribution=0.1,
        recommended_action="Restrict additional inflow toward the affected zone.",
        action_code="RESTRICT_INFLOW",
        model_version="test",
        source_mode=SourceMode.SIMULATION,
        frame_id=42,
    )
    defaults.update(overrides)
    return IncidentCandidate(**defaults)


@pytest.fixture
def journal(tmp_path):
    j = IncidentJournal(tmp_path / "sentinel.db")
    j.initialize()
    return j


def test_initialize_creates_database_with_wal(tmp_path):
    journal = IncidentJournal(tmp_path / "nested" / "sentinel.db")
    journal.initialize()

    assert journal.db_path.is_file()
    conn = sqlite3.connect(journal.db_path)
    mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    conn.close()
    assert mode.lower() == "wal"


def test_save_event_persists_exact_incident_candidate_event_id(journal):
    candidate = make_candidate()
    inserted = journal.save_event(candidate, connectivity_state="ONLINE")

    assert inserted is True
    record = journal.get_event(candidate.event_id)
    assert record is not None
    assert record.event_id == candidate.event_id
    assert record.severity == Severity.RED.value
    assert record.local_status == LocalStatus.PERSISTED
    assert record.sync_status == SyncStatus.PENDING


def test_save_event_is_idempotent_on_same_event_id(journal):
    candidate = make_candidate()
    first = journal.save_event(candidate, connectivity_state="OFFLINE")
    second = journal.save_event(candidate, connectivity_state="OFFLINE")

    assert first is True
    assert second is False
    assert journal.count_events() == 1


def test_event_survives_new_connection_simulating_restart(tmp_path):
    db_path = tmp_path / "sentinel.db"
    candidate = make_candidate()

    first_process = IncidentJournal(db_path)
    first_process.initialize()
    first_process.save_event(candidate, connectivity_state="OFFLINE")

    restarted_process = IncidentJournal(db_path)
    restarted_process.initialize()
    record = restarted_process.get_event(candidate.event_id)

    assert record is not None
    assert record.event_id == candidate.event_id
    assert record.sync_status == SyncStatus.PENDING


def test_list_pending_events_includes_pending_and_due_retries(journal):
    pending = make_candidate()
    journal.save_event(pending, connectivity_state="OFFLINE")

    synced = make_candidate()
    journal.save_event(synced, connectivity_state="ONLINE")
    journal.mark_synced(synced.event_id)

    pending_ids = {record.event_id for record in journal.list_pending_events()}
    assert pending.event_id in pending_ids
    assert synced.event_id not in pending_ids


def test_state_transitions_local_and_sync(journal):
    candidate = make_candidate()
    journal.save_event(candidate, connectivity_state="ONLINE")

    journal.mark_local_delivered(candidate.event_id)
    assert journal.get_event(candidate.event_id).local_status == LocalStatus.LOCAL_DELIVERED

    journal.mark_syncing(candidate.event_id)
    assert journal.get_event(candidate.event_id).sync_status == SyncStatus.SYNCING

    journal.mark_synced(candidate.event_id)
    record = journal.get_event(candidate.event_id)
    assert record.sync_status == SyncStatus.SYNCED
    assert record.synced_at is not None


def test_retryable_failure_keeps_event_visible(journal):
    candidate = make_candidate()
    journal.save_event(candidate, connectivity_state="OFFLINE")
    journal.mark_retryable_failure(candidate.event_id, retry_count=1, next_retry_at="2999-01-01T00:00:00+00:00")

    record = journal.get_event(candidate.event_id)
    assert record.sync_status == SyncStatus.RETRYABLE_FAILURE
    assert record.retry_count == 1
    # Not due yet -> excluded from the pending worklist, but never deleted.
    assert candidate.event_id not in {r.event_id for r in journal.list_pending_events()}
    assert journal.get_event(candidate.event_id) is not None


def test_permanent_failure_event_is_never_deleted(journal):
    candidate = make_candidate()
    journal.save_event(candidate, connectivity_state="OFFLINE")
    journal.mark_permanent_failure(candidate.event_id)

    record = journal.get_event(candidate.event_id)
    assert record.sync_status == SyncStatus.PERMANENT_FAILURE
    assert journal.count_events() == 1


def test_count_by_sync_status_reflects_current_state(journal):
    a, b, c = make_candidate(), make_candidate(), make_candidate()
    journal.save_event(a, "OFFLINE")
    journal.save_event(b, "OFFLINE")
    journal.save_event(c, "OFFLINE")
    journal.mark_synced(a.event_id)

    counts = journal.count_by_sync_status()
    assert counts.get(SyncStatus.SYNCED) == 1
    assert counts.get(SyncStatus.PENDING) == 2
    assert journal.count_events() == 3


def test_get_recent_events_orders_newest_first(journal):
    older = make_candidate()
    journal.save_event(older, "ONLINE")
    newer = make_candidate()
    journal.save_event(newer, "ONLINE")

    recent = journal.get_recent_events(limit=10)
    ids = [r.event_id for r in recent]
    assert ids.index(newer.event_id) < ids.index(older.event_id)
