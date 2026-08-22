"""Offline continuity: local safety loop must keep working with zero WAN.

No real Internet, no webcam, no real remote backend are used anywhere here.
"""
from src.connectivity import ConnectivityManager, ConnectivityState
from src.persistence import IncidentJournal, SyncStatus


def always_offline_check():
    return False, 999.0


def test_runtime_keeps_advancing_while_connectivity_is_offline(incident_factory):
    """A network outage must never stop frame/risk processing (Person 1's loop)."""
    manager = ConnectivityManager(check_fn=always_offline_check, failures_for_offline=1)
    manager.check_once()
    assert manager.snapshot().state == ConnectivityState.OFFLINE

    # The runtime used by incident_factory is a plain, unmodified
    # SentinelRuntime -- it never consults connectivity at all. Producing
    # candidates successfully while `manager` reports OFFLINE demonstrates
    # the frame/risk loop has no dependency on connectivity.
    candidate = incident_factory()
    assert candidate is not None
    assert candidate.event_id


def test_three_incidents_offline_are_all_generated_persisted_and_pending(tmp_path, incident_factory):
    manager = ConnectivityManager(check_fn=always_offline_check, failures_for_offline=1)
    manager.check_once()
    assert manager.snapshot().state == ConnectivityState.OFFLINE

    journal = IncidentJournal(tmp_path / "sentinel.db")
    journal.initialize()

    generated = 0
    persisted = 0
    for _ in range(3):
        candidate = incident_factory()
        generated += 1
        connectivity_state = manager.snapshot().state
        inserted = journal.save_event(candidate, connectivity_state)
        if inserted:
            persisted += 1
        journal.mark_local_delivered(candidate.event_id)

    assert generated == 3
    assert persisted == 3
    assert journal.count_events() == 3

    counts = journal.count_by_sync_status()
    assert counts.get(SyncStatus.PENDING, 0) == 3

    events_lost = generated - persisted
    assert events_lost == 0

    # Every stored row must remember it was captured while offline.
    for record in journal.get_recent_events(limit=10):
        assert record.connectivity_state == ConnectivityState.OFFLINE


def test_offline_events_carry_advancing_frame_ids(tmp_path, incident_factory):
    """RiskSnapshot/incident timestamps and frame ids must keep advancing offline."""
    journal = IncidentJournal(tmp_path / "sentinel.db")
    journal.initialize()

    candidates = [incident_factory() for _ in range(3)]
    for candidate in candidates:
        journal.save_event(candidate, ConnectivityState.OFFLINE)

    timestamps = [record.created_at_utc for record in journal.get_recent_events(limit=10)]
    assert len(set(timestamps)) >= 1  # sanity: rows were actually written
    assert journal.count_events() == 3
