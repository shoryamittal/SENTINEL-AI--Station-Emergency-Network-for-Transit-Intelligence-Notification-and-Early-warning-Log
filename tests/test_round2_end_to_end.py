"""Deterministic, end-to-end proof of the Round 2 continuity requirement:

    CONNECTIVITY IS A DEPENDENCY FOR SYNCHRONIZATION.
    IT IS NOT A DEPENDENCY FOR SAFETY.

This exercises the real production objects deploy.py wires together
(IncidentJournal, ConnectivityManager, SyncWorker, ContinuityMetrics,
LocalAlertCenter, and deploy._persist_and_deliver_incident -- the same
persist-then-alert boundary tested in tests/test_incident_consumer.py) end
to end: ONLINE -> incident -> OFFLINE -> multiple incidents -> restart ->
RECOVERY -> SYNCED -> idempotent replay -> stale-alert protection ->
events_lost == 0.

No webcam, no Internet, no real remote backend:
  - incidents are produced through the real, public SentinelRuntime/
    scenario/classification pipeline, using the same deterministic
    simulation-source + fake-detector pattern as tests/conftest.py's
    ``incident_factory`` fixture (not private detector/camera internals).
  - connectivity is driven via ConnectivityManager's existing test seam
    (an injectable check_fn and the demo-only set_manual_override), never
    a real socket.
  - remote sync is driven via the existing MockSyncAdapter, never a real
    server.
"""
from __future__ import annotations

import numpy as np

from src.camera import FrameSource
from src.config import RuntimeConfig
from src.connectivity import ConnectivityManager, ConnectivityState
from src.contracts import SourceMode
from src.detector import Detection
from src.persistence import IncidentJournal, LocalStatus, SyncStatus
from src.runtime import SentinelRuntime
from src.sync import MockSyncAdapter, SyncResult, SyncWorker


class _CrowdedFakeDetector:
    """Same deterministic fixture shape as tests/conftest.py's incident_factory:
    a dense single-cell crowd so the very first processed frame confirms a
    non-GREEN incident. This is the public detect() contract, not a private
    detector internal.
    """

    model_version = "round2-e2e-fixture"

    def detect(self, frame):
        return [Detection((0, 0, 2, 2), (10, 10), 0.9) for _ in range(5)], 0.01


def _new_deterministic_runtime() -> SentinelRuntime:
    return SentinelRuntime(
        FrameSource(SourceMode.SIMULATION, simulation_factory=lambda: np.zeros((100, 100, 3), dtype=np.uint8)),
        _CrowdedFakeDetector(),
        RuntimeConfig(calibration_samples=1, extreme_occupancy_guardrail=1, escalation_confirmations=1),
    )


def _generate_incident():
    """Produce one real IncidentCandidate through the actual public runtime
    path (process_once + get_next_incident) -- never fabricated by hand."""
    runtime = _new_deterministic_runtime()
    runtime.process_once()
    candidate = runtime.get_next_incident(timeout=0.1)
    assert candidate is not None, "deterministic fixture failed to produce an incident"
    return candidate


class _ScriptedCheck:
    """Deterministic, injectable connectivity probe. No socket is ever touched."""

    def __init__(self, results):
        self._results = list(results)

    def __call__(self):
        if self._results:
            return self._results.pop(0)
        return True, 10.0  # once the script is exhausted, stay healthy


def test_full_round2_offline_restart_recovery(tmp_path, monkeypatch):
    import deploy

    db_path = tmp_path / "sentinel.db"

    # ---- Step 1: start ONLINE ----
    journal = IncidentJournal(db_path)
    journal.initialize()
    connectivity = ConnectivityManager(check_fn=lambda: (True, 10.0))
    connectivity.check_once()
    assert connectivity.snapshot().state == ConnectivityState.ONLINE

    adapter = MockSyncAdapter(mode=MockSyncAdapter.DUPLICATE_ACCEPTED)
    from src.alerts import LocalAlertCenter
    from src.metrics import ContinuityMetrics

    metrics = ContinuityMetrics(journal, connectivity)
    alert_center = LocalAlertCenter()
    sync_worker = SyncWorker(journal, connectivity, adapter, metrics=metrics)

    # Wire deploy.py's real singletons to these isolated, deterministic test
    # doubles -- the same monkeypatch pattern tests/test_incident_consumer.py
    # and tests/test_camera_feed.py already use. deploy.runtime (the real,
    # camera-configured SentinelRuntime) is left untouched and untouched by
    # this test; it is never started, so no webcam is ever opened.
    monkeypatch.setattr(deploy, "journal", journal)
    monkeypatch.setattr(deploy, "connectivity", connectivity)
    monkeypatch.setattr(deploy, "sync_adapter", adapter)
    monkeypatch.setattr(deploy, "metrics", metrics)
    monkeypatch.setattr(deploy, "alert_center", alert_center)
    monkeypatch.setattr(deploy, "sync_worker", sync_worker)

    # ---- Steps 2-4: generate + persist one incident through the real
    # public incident path, confirm local alert ----
    first_candidate = _generate_incident()
    metrics.record_generated()
    assert deploy._persist_and_deliver_incident(first_candidate) is True

    first_record = journal.get_event(first_candidate.event_id)
    assert first_record is not None
    assert first_record.event_id == first_candidate.event_id
    first_created_at = first_record.created_at_utc
    first_severity = first_record.severity
    first_scenario = first_record.primary_scenario
    assert first_record.local_status == LocalStatus.LOCAL_DELIVERED
    assert alert_center.latest()["event_id"] == first_candidate.event_id
    # exactly one database row for this event_id
    assert journal.count_events() == 1

    # ---- Step 8: local commit happens before any sync attempt ----
    assert first_record.sync_status == SyncStatus.PENDING
    assert first_candidate.event_id not in adapter._seen_event_ids

    # ---- Step 5: force OFFLINE via the existing test seam (never the real NIC) ----
    connectivity.set_manual_override(ConnectivityState.OFFLINE)
    assert connectivity.snapshot().state == ConnectivityState.OFFLINE

    # ---- Steps 6-7: generate >=3 incidents while OFFLINE; local safety continues ----
    offline_candidates = [_generate_incident() for _ in range(3)]
    assert len({c.event_id for c in offline_candidates}) == 3  # three distinct incidents

    for candidate in offline_candidates:
        metrics.record_generated()
        assert deploy._persist_and_deliver_incident(candidate) is True

    for candidate in offline_candidates:
        record = journal.get_event(candidate.event_id)
        assert record is not None
        assert record.local_status == LocalStatus.LOCAL_DELIVERED  # local alert continued
        assert record.sync_status == SyncStatus.PENDING            # not lost, just pending
        assert record.connectivity_state == ConnectivityState.OFFLINE

    alerted_ids = {alert["event_id"] for alert in alert_center.recent(10)}
    for candidate in offline_candidates:
        assert candidate.event_id in alerted_ids  # local alerts continued while offline

    # events_lost stays 0 through the offline period
    offline_snapshot = metrics.snapshot()
    assert offline_snapshot.events_generated == 4
    assert offline_snapshot.events_persisted == 4
    assert offline_snapshot.events_lost == 0
    assert offline_snapshot.events_pending == 4  # PENDING != LOST
    assert offline_snapshot.events_synced == 0

    # the sync worker must refuse to run at all while OFFLINE
    assert sync_worker.run_once() is False
    assert journal.count_by_sync_status().get(SyncStatus.PENDING, 0) == 4

    # ---- Step 14: Flask status integration reflects the same isolated state ----
    client = deploy.app.test_client()
    status_response = client.get("/status")
    assert status_response.status_code == 200
    status_json = status_response.get_json()
    assert set(("snapshot", "runtime_health", "connectivity", "metrics", "local_alerts", "recent_events")) <= status_json.keys()
    assert status_json["connectivity"]["state"] == ConnectivityState.OFFLINE
    assert status_json["metrics"]["events_pending"] == 4
    assert status_json["metrics"]["events_lost"] == 0
    # connectivity/system state is a distinct object from crowd-risk data --
    # never the same field.
    assert "state" in status_json["connectivity"] and "state" not in (status_json["snapshot"] or {})

    pending_response = client.get("/events/pending")
    assert pending_response.status_code == 200
    assert len(pending_response.get_json()) == 4

    # ---- Step 9: simulate a hard restart ----
    # Close this process's handle to the database entirely and open a brand
    # new IncidentJournal against the same file, exactly as deploy.py would
    # on a fresh process start. No historical incident is regenerated.
    all_event_ids = [first_candidate.event_id] + [c.event_id for c in offline_candidates]
    del journal

    restarted_journal = IncidentJournal(db_path)
    restarted_journal.initialize()

    assert restarted_journal.count_events() == 4
    restarted_first = restarted_journal.get_event(first_candidate.event_id)
    assert restarted_first.event_id == first_candidate.event_id
    assert restarted_first.created_at_utc == first_created_at
    assert restarted_first.severity == first_severity
    assert restarted_first.primary_scenario == first_scenario
    for event_id in all_event_ids:
        record = restarted_journal.get_event(event_id)
        assert record is not None
        assert record.sync_status == SyncStatus.PENDING  # still pending, still present

    # A fresh ConnectivityManager, exactly as module-level deploy.py code
    # would construct on restart. It defaults to ONLINE, but the WAN is
    # still down at this instant -- script a few failures first, then a
    # recovery run, so the OFFLINE -> RECOVERY -> ONLINE path is driven by
    # real (scripted) check results, not by the demo override.
    restart_check = _ScriptedCheck(
        [(False, 999.0), (False, 999.0), (False, 999.0)]  # still offline right after restart
        + [(True, 10.0), (True, 10.0), (True, 10.0)]       # then connectivity is restored
    )
    restarted_connectivity = ConnectivityManager(check_fn=restart_check, failures_for_offline=3)
    for _ in range(3):
        restarted_connectivity.check_once()
    assert restarted_connectivity.snapshot().state == ConnectivityState.OFFLINE

    restarted_metrics = ContinuityMetrics(restarted_journal, restarted_connectivity)
    restarted_alert_center = LocalAlertCenter()
    restarted_sync_worker = SyncWorker(restarted_journal, restarted_connectivity, adapter, metrics=restarted_metrics)

    monkeypatch.setattr(deploy, "journal", restarted_journal)
    monkeypatch.setattr(deploy, "connectivity", restarted_connectivity)
    monkeypatch.setattr(deploy, "metrics", restarted_metrics)
    monkeypatch.setattr(deploy, "alert_center", restarted_alert_center)
    monkeypatch.setattr(deploy, "sync_worker", restarted_sync_worker)

    # nothing can sync while still (scripted-)offline post-restart
    assert restarted_sync_worker.run_once() is False

    # ---- Step 10: recovery ----
    restarted_connectivity.check_once()  # 1st success
    assert restarted_connectivity.snapshot().state == ConnectivityState.OFFLINE
    restarted_connectivity.check_once()  # 2nd success -> RECOVERY
    assert restarted_connectivity.snapshot().state == ConnectivityState.RECOVERY
    restarted_connectivity.check_once()  # 3rd success -> ONLINE
    assert restarted_connectivity.snapshot().state == ConnectivityState.ONLINE

    # drain the entire pending queue synchronously and deterministically
    drained = 0
    while restarted_sync_worker.run_once():
        drained += 1
    assert drained == 4

    for event_id in all_event_ids:
        record = restarted_journal.get_event(event_id)
        assert record.sync_status == SyncStatus.SYNCED
        assert record.synced_at is not None
        assert record.retry_count == 0

    # historical records were never deleted, only transitioned
    assert restarted_journal.count_events() == 4

    # ---- Step 11: idempotent replay ----
    synced_record = restarted_journal.get_event(first_candidate.event_id)
    replay_result = adapter.send_event(synced_record.payload)
    assert replay_result == SyncResult.ALREADY_ACCEPTED
    assert restarted_journal.count_events() == 4          # no new row
    assert restarted_journal.get_event(first_candidate.event_id).event_id == first_candidate.event_id

    # ---- Step 12: stale historical incident does not fire a new live alert ----
    # first_candidate/offline_candidates were all generated, alerted, and
    # persisted BEFORE the restart; the alert center they were delivered to
    # (`alert_center`, pre-restart) is not the same object as
    # `restarted_alert_center` (post-restart) -- exactly mirroring a real
    # process restart, where in-memory alert history does not survive but
    # the durable SQLite journal does. The sync that just happened above
    # replayed these four historical incidents to "remote" (the mock
    # adapter) without ever touching any LocalAlertCenter: SyncWorker has no
    # reference to one at all.
    assert restarted_alert_center.recent(10) == []
    assert not hasattr(sync_worker, "alert_center")
    assert not hasattr(restarted_sync_worker, "alert_center")

    # ---- Step 13: final event-loss accounting ----
    final_metrics = restarted_metrics.snapshot()
    assert final_metrics.events_pending == 0
    assert final_metrics.events_synced == 4
    assert final_metrics.events_failed == 0
    # events_lost is generated-vs-persisted for events THIS metrics instance
    # observed; restarted_metrics only observed generation of 0 new events
    # (all 4 were generated before restart, under the pre-restart `metrics`
    # instance, which itself already reported events_lost == 0 above).
    assert final_metrics.events_lost == 0

    # ---- Flask integration again, now against the recovered state ----
    recovered_status = client.get("/status").get_json()
    assert recovered_status["connectivity"]["state"] == ConnectivityState.ONLINE
    assert recovered_status["metrics"]["events_synced"] == 4
    assert recovered_status["metrics"]["events_pending"] == 0
    assert recovered_status["metrics"]["events_lost"] == 0

    # ---- Step 20: final connectivity state ----
    assert restarted_connectivity.snapshot().state == ConnectivityState.ONLINE
