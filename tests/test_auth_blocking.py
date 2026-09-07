"""Authentication blocking is durable, distinct from connectivity, and explicit."""
from __future__ import annotations

import json

from src.connectivity import ConnectivityManager, ConnectivityState
from src.persistence import IncidentJournal, SyncStatus
from src.qualification_backend import QualificationEventStore
from src.sync import HttpSyncAdapter, SyncResult, SyncWorker
from src.metrics import ContinuityMetrics


def _online_worker(tmp_path, adapter):
    journal = IncidentJournal(tmp_path / "local.db")
    journal.initialize()
    connectivity = ConnectivityManager(check_fn=lambda: (True, 1.0))
    connectivity.check_once()
    return journal, connectivity, SyncWorker(journal, connectivity, adapter)


def test_qualification_api_token_rejects_missing_or_wrong_and_allows_correct(tmp_path, incident_factory):
    from qualification_server import create_app

    store = QualificationEventStore(tmp_path / "qualification.db")
    app = create_app(store, api_token="token-A")
    client = app.test_client()
    payload = incident_factory().to_dict()

    assert client.get("/health").status_code == 200
    assert client.post("/api/events", json=payload).status_code == 401
    assert client.post("/api/events", json=payload, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/api/events", json=payload, headers={"Authorization": "Bearer token-A"}).status_code == 201


def test_http_adapter_sends_bearer_token_without_exposing_it(monkeypatch, incident_factory):
    import src.sync as sync_module

    class Response:
        status = 201
        def read(self): return b'{"result":"ACCEPTED"}'
        def __enter__(self): return self
        def __exit__(self, *args): return False

    def fake_urlopen(req, timeout):
        assert req.get_header("Authorization") == "Bearer token-A"
        return Response()

    monkeypatch.setattr(sync_module.request, "urlopen", fake_urlopen)
    adapter = HttpSyncAdapter("http://qualification.invalid/api/events", bearer_token="token-A")
    assert adapter.send_event(incident_factory().to_dict()) == SyncResult.ACCEPTED


def test_auth_expiry_blocks_worker_and_explicit_reauth_resumes_same_event(tmp_path, incident_factory):
    from qualification_server import create_app

    store = QualificationEventStore(tmp_path / "qualification.db")
    client = create_app(store, api_token="token-A").test_client()
    calls = []
    adapter = HttpSyncAdapter("http://qualification.invalid/api/events", bearer_token="wrong-token")

    def protected_transport(url, body, timeout):
        payload = json.loads(body.decode("utf-8"))
        calls.append(payload["event_id"])
        response = client.post("/api/events", json=payload, headers={"Authorization": f"Bearer {adapter.bearer_token}"})
        return response.status_code, response.data

    adapter.transport = protected_transport
    journal, connectivity, worker = _online_worker(tmp_path, adapter)
    metrics = ContinuityMetrics(journal, connectivity)
    worker.metrics = metrics
    candidate = incident_factory()
    journal.save_event(candidate, ConnectivityState.ONLINE)

    assert worker.run_once() is True
    assert connectivity.snapshot().state == ConnectivityState.ONLINE
    assert journal.get_event(candidate.event_id).sync_status == SyncStatus.AUTH_BLOCKED
    assert journal.get_event(candidate.event_id).event_id == candidate.event_id
    assert worker.auth_blocked is True
    assert metrics.snapshot().events_auth_blocked == 1
    assert calls == [candidate.event_id]
    assert worker.run_once() is False
    assert worker.run_once() is False
    assert calls == [candidate.event_id]

    adapter.bearer_token = "token-A"
    assert worker.resume_after_auth_refresh() == 1
    assert journal.get_event(candidate.event_id).sync_status == SyncStatus.PENDING
    assert worker.run_once() is True
    assert calls == [candidate.event_id, candidate.event_id]
    assert journal.get_event(candidate.event_id).sync_status == SyncStatus.SYNCED
    assert store.count_events() == 1
    assert store.get_event(candidate.event_id)["payload"] == candidate.to_dict()


def test_dashboard_defaults_to_local_bind_and_debug_endpoint_disabled(monkeypatch):
    import deploy

    assert deploy.SENTINEL_BIND_HOST == "127.0.0.1"
    monkeypatch.setattr(deploy, "AUTH_ENABLED", False)
    client = deploy.app.test_client()
    assert client.post("/debug/connectivity?state=OFFLINE").status_code == 404
    assert "Simulate OFFLINE" not in client.get("/").get_data(as_text=True)

    monkeypatch.setattr(deploy, "ENABLE_DEBUG_CONNECTIVITY", True)
    assert client.post("/debug/connectivity?state=OFFLINE").status_code == 200
    assert "Simulate OFFLINE" in client.get("/").get_data(as_text=True)
    deploy.connectivity.set_manual_override(None)
