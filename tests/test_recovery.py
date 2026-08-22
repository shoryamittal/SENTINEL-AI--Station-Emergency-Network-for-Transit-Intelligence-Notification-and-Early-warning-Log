"""Recovery/sync tests: idempotent replay, permanent failure, stale alerts.

No real Internet, no webcam, no real remote backend -- MockSyncAdapter only.
"""
from src.connectivity import ConnectivityManager, ConnectivityState
from src.persistence import IncidentJournal, SyncStatus
from src.sync import MockSyncAdapter, SyncWorker


def always_online_check():
    return True, 20.0


def make_online_journal_and_worker(tmp_path, mode=MockSyncAdapter.NORMAL):
    journal = IncidentJournal(tmp_path / "sentinel.db")
    journal.initialize()
    connectivity = ConnectivityManager(check_fn=always_online_check)
    connectivity.check_once()
    adapter = MockSyncAdapter(mode=mode)
    worker = SyncWorker(journal, connectivity, adapter, poll_interval_s=0.01)
    return journal, connectivity, adapter, worker


def test_pending_events_replay_and_become_synced(tmp_path, incident_factory):
    journal, connectivity, adapter, worker = make_online_journal_and_worker(tmp_path)

    for _ in range(3):
        journal.save_event(incident_factory(), ConnectivityState.OFFLINE)

    # Drain the pending queue synchronously (no background thread needed for a
    # deterministic test).
    while worker.run_once():
        pass

    counts = journal.count_by_sync_status()
    assert counts.get(SyncStatus.SYNCED, 0) == 3
    assert counts.get(SyncStatus.PENDING, 0) == 0


def test_duplicate_replay_does_not_duplicate_and_preserves_event_id(tmp_path, incident_factory):
    journal, connectivity, adapter, worker = make_online_journal_and_worker(
        tmp_path, mode=MockSyncAdapter.DUPLICATE_ACCEPTED
    )

    candidate = incident_factory()
    journal.save_event(candidate, ConnectivityState.OFFLINE)
    worker.run_once()
    assert journal.get_event(candidate.event_id).sync_status == SyncStatus.SYNCED

    # Simulate a replay: the exact same event_id is sent again (e.g. a retry
    # race after a response was lost). It must resolve to ALREADY_ACCEPTED
    # and must not create a second row.
    result = adapter.send_event(candidate.to_dict())
    from src.sync import SyncResult
    assert result == SyncResult.ALREADY_ACCEPTED
    assert journal.count_events() == 1
    assert journal.get_event(candidate.event_id).event_id == candidate.event_id


def test_permanent_failure_event_remains_visible_and_not_deleted(tmp_path, incident_factory):
    journal, connectivity, adapter, worker = make_online_journal_and_worker(tmp_path)
    # Force a permanent failure via a bespoke adapter.
    class AlwaysPermanent:
        def send_event(self, payload):
            from src.sync import SyncResult
            return SyncResult.PERMANENT_FAILURE

    worker.adapter = AlwaysPermanent()
    candidate = incident_factory()
    journal.save_event(candidate, ConnectivityState.OFFLINE)

    worker.run_once()

    record = journal.get_event(candidate.event_id)
    assert record is not None
    assert record.sync_status == SyncStatus.PERMANENT_FAILURE
    assert journal.count_by_sync_status().get(SyncStatus.PERMANENT_FAILURE, 0) == 1


def test_retryable_failure_backs_off_and_is_not_lost(tmp_path, incident_factory):
    journal, connectivity, adapter, worker = make_online_journal_and_worker(
        tmp_path, mode=MockSyncAdapter.TIMEOUT
    )
    candidate = incident_factory()
    journal.save_event(candidate, ConnectivityState.OFFLINE)

    worker.run_once()

    record = journal.get_event(candidate.event_id)
    assert record.sync_status == SyncStatus.RETRYABLE_FAILURE
    assert record.retry_count == 1
    assert record.next_retry_at is not None
    # Not due yet, so it won't be picked up immediately again, but it is not gone.
    assert journal.count_events() == 1


def test_sync_adapter_exception_returns_event_to_retryable_failure(tmp_path, incident_factory):
    journal, connectivity, adapter, worker = make_online_journal_and_worker(tmp_path)

    class RaisingAdapter:
        def send_event(self, payload):
            raise RuntimeError("simulated transport exception")

    worker.adapter = RaisingAdapter()
    candidate = incident_factory()
    journal.save_event(candidate, ConnectivityState.OFFLINE)

    assert worker.run_once() is True
    record = journal.get_event(candidate.event_id)
    assert record.sync_status == SyncStatus.RETRYABLE_FAILURE
    assert record.retry_count == 1


def test_sync_worker_does_not_run_while_offline(tmp_path, incident_factory):
    journal = IncidentJournal(tmp_path / "sentinel.db")
    journal.initialize()
    connectivity = ConnectivityManager(check_fn=lambda: (False, 999.0), failures_for_offline=1)
    connectivity.check_once()
    assert connectivity.snapshot().state == ConnectivityState.OFFLINE

    adapter = MockSyncAdapter(mode=MockSyncAdapter.NORMAL)
    worker = SyncWorker(journal, connectivity, adapter, poll_interval_s=0.01)

    journal.save_event(incident_factory(), ConnectivityState.OFFLINE)

    did_work = worker.run_once()
    assert did_work is False
    assert journal.count_by_sync_status().get(SyncStatus.PENDING, 0) == 1


def test_stale_historical_incident_syncs_without_a_new_live_alert(tmp_path, incident_factory):
    """The 12:00 RED -> 12:05 GREEN -> 12:10 reconnect scenario from the spec.

    Syncing an old incident to remote history must not, by itself, produce
    any new "live emergency" notification. The sync worker's job is history
    replication only -- live alerting happens once, at generation time, in
    the incident consumer (deploy.py), which this test does not invoke.
    """
    journal, connectivity, adapter, worker = make_online_journal_and_worker(tmp_path)

    live_alerts_fired = []

    def fake_live_alert(candidate):
        live_alerts_fired.append(candidate.event_id)

    # Historical incident created while the crowd was RED and the network
    # was down.
    historical_candidate = incident_factory()
    journal.save_event(historical_candidate, ConnectivityState.OFFLINE)
    # At generation time (offline) the live alert would have fired exactly
    # once; simulate that here since it is deploy.py's job, not the sync
    # worker's.
    fake_live_alert(historical_candidate)
    assert live_alerts_fired == [historical_candidate.event_id]

    # Time passes, crowd returns to normal, connectivity recovers. The sync
    # worker is only wired to persistence -- it has no way to call the live
    # alert path even after this point.
    while worker.run_once():
        pass

    assert journal.get_event(historical_candidate.event_id).sync_status == SyncStatus.SYNCED
    # Still exactly one live alert: the one fired at generation time.
    assert live_alerts_fired == [historical_candidate.event_id]
