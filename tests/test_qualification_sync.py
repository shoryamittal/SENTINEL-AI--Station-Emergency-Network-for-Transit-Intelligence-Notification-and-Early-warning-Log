"""Deterministic proof of qualification-server idempotent recovery."""
from __future__ import annotations

import json
from dataclasses import replace

from src.connectivity import ConnectivityManager, ConnectivityState
from src.persistence import IncidentJournal, SyncStatus
from src.qualification_backend import QualificationEventStore, QualificationResult
from src.sync import BackoffConfig, HttpSyncAdapter, SyncResult, SyncWorker


def _store(tmp_path):
    store = QualificationEventStore(tmp_path / "qualification.db")
    store.initialize()
    return store


def test_qualification_store_is_idempotent_and_preserves_canonical_payload(tmp_path, incident_factory):
    store = _store(tmp_path)
    payload = incident_factory().to_dict()
    assert store.accept_event(payload)[0] == QualificationResult.ACCEPTED
    assert store.accept_event(payload)[0] == QualificationResult.ALREADY_ACCEPTED
    assert store.count_events() == 1
    assert store.get_event(payload["event_id"])["payload"] == payload


def test_qualification_store_rejects_same_id_with_different_payload(tmp_path, incident_factory):
    store = _store(tmp_path)
    payload = incident_factory().to_dict()
    conflicting = dict(payload, action_code="CONFLICTING_ACTION")
    assert store.accept_event(payload)[0] == QualificationResult.ACCEPTED
    assert store.accept_event(conflicting)[0] == QualificationResult.IDEMPOTENCY_CONFLICT
    assert store.count_events() == 1
    assert store.get_event(payload["event_id"])["payload"] == payload


def test_qualification_http_server_contract(tmp_path, incident_factory):
    from qualification_server import create_app

    store = _store(tmp_path)
    client = create_app(store).test_client()
    payload = incident_factory().to_dict()
    assert client.get("/health").get_json()["service"] == "sentinel-qualification-sync"
    assert client.post("/api/events", json=payload).status_code == 201
    assert client.post("/api/events", json=payload).status_code == 200
    assert client.post("/api/events", json=dict(payload, action_code="CONFLICTING_ACTION")).status_code == 409
    assert client.post("/api/events", json={}).status_code == 400
    assert client.get(f"/api/events/{payload['event_id']}").get_json()["payload"] == payload
    assert client.get("/api/events/missing").status_code == 404


def _online_worker(tmp_path, adapter):
    journal = IncidentJournal(tmp_path / "local.db")
    journal.initialize()
    connectivity = ConnectivityManager(check_fn=lambda: (True, 1.0))
    connectivity.check_once()
    worker = SyncWorker(journal, connectivity, adapter, backoff=BackoffConfig(base_delay_s=0, max_delay_s=0, jitter_s=0))
    return journal, worker


def test_timeout_after_server_success_retries_same_event_id_and_syncs(tmp_path, incident_factory):
    store = _store(tmp_path)
    calls = []

    def transport(url, body, timeout):
        payload = json.loads(body.decode("utf-8"))
        calls.append(payload["event_id"])
        result, event_id = store.accept_event(payload)
        if len(calls) == 1:
            assert result == QualificationResult.ACCEPTED
            raise TimeoutError("response lost after server commit")
        return (200, json.dumps({"result": result, "event_id": event_id}).encode())

    adapter = HttpSyncAdapter("http://qualification.invalid/api/events", transport=transport)
    journal, worker = _online_worker(tmp_path, adapter)
    candidate = incident_factory()
    journal.save_event(candidate, ConnectivityState.ONLINE)

    assert worker.run_once() is True
    assert journal.get_event(candidate.event_id).sync_status == SyncStatus.RETRYABLE_FAILURE
    assert store.count_events() == 1
    assert store.get_event(candidate.event_id) is not None

    assert worker.run_once() is True
    record = journal.get_event(candidate.event_id)
    assert calls == [candidate.event_id, candidate.event_id]
    assert store.count_events() == 1
    assert store.get_event(candidate.event_id)["payload"] == candidate.to_dict()
    assert record.sync_status == SyncStatus.SYNCED
    assert record.retry_count == 1


def test_http_connection_failure_is_retryable_and_keeps_local_event(tmp_path, incident_factory):
    adapter = HttpSyncAdapter("http://qualification.invalid/api/events", transport=lambda *args: (_ for _ in ()).throw(ConnectionError()))
    journal, worker = _online_worker(tmp_path, adapter)
    candidate = incident_factory()
    journal.save_event(candidate, ConnectivityState.ONLINE)
    assert worker.run_once() is True
    record = journal.get_event(candidate.event_id)
    assert record.sync_status == SyncStatus.RETRYABLE_FAILURE
    assert record.retry_count == 1


def test_http_idempotency_conflict_becomes_permanent_local_failure(tmp_path, incident_factory):
    store = _store(tmp_path)
    original = incident_factory()
    store.accept_event(original.to_dict())
    conflicting = replace(original, severity=type(original.severity).BLACK)

    def transport(url, body, timeout):
        result, event_id = store.accept_event(json.loads(body.decode("utf-8")))
        return 409, json.dumps({"result": result, "event_id": event_id}).encode()

    journal, worker = _online_worker(tmp_path, HttpSyncAdapter("http://qualification.invalid/api/events", transport=transport))
    journal.save_event(conflicting, ConnectivityState.ONLINE)
    assert worker.run_once() is True
    assert journal.get_event(conflicting.event_id).sync_status == SyncStatus.PERMANENT_FAILURE
    assert store.count_events() == 1
    assert store.get_event(original.event_id)["payload"] == original.to_dict()
