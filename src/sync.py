"""Remote sync abstraction and idempotent store-and-forward worker.

There is no production cloud backend yet. ``SyncAdapter`` is the seam a real
one would plug into later; ``MockSyncAdapter`` is a deterministic stand-in
for development and tests, clearly labeled as a mock, never to be confused
with a production integration.

This module only ever talks to ``IncidentJournal`` (SQLite) and a
``SyncAdapter``. It never imports detector/occupancy/baseline/risk code, and
it never generates a "live" alert -- see the module docstring note below.

IMPORTANT (stale alert protection): this worker replays *historical*
incidents that are already sitting in SQLite, possibly minutes or hours old.
Successfully syncing one of them means "the remote history now has a record
of what happened" -- it must NEVER be interpreted as "a new emergency is
happening right now." Live operator/SMS alerting happens exactly once, at
the moment an ``IncidentCandidate`` is first produced by the runtime (see
``deploy.py``'s incident consumer). This worker does not call that code
path.
"""
from __future__ import annotations

import random
import time
import json
from urllib import error, request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from typing import Any

from .connectivity import ConnectivityManager
from .persistence import IncidentJournal


class SyncResult:
    ACCEPTED = "ACCEPTED"
    ALREADY_ACCEPTED = "ALREADY_ACCEPTED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"


class SyncAdapter(ABC):
    """Seam for a real remote backend. Implement ``send_event`` only."""

    @abstractmethod
    def send_event(self, payload: dict[str, Any]) -> str:
        """Send one event payload; return one of the ``SyncResult`` values."""
        raise NotImplementedError


class MockSyncAdapter(SyncAdapter):
    """MOCK remote endpoint for development/demo/tests. NOT a production backend.

    Modes:
      NORMAL              -- always ACCEPTED.
      SLOW                -- ACCEPTED after ``slow_delay_s`` (still fast enough for tests).
      TIMEOUT             -- always RETRYABLE_FAILURE (simulates a hung/unreachable endpoint).
      OFFLINE              -- always RETRYABLE_FAILURE, no delay (simulates DNS/connection refused).
      DUPLICATE_ACCEPTED   -- first send of an event_id -> ACCEPTED, any replay -> ALREADY_ACCEPTED.
    """

    NORMAL = "NORMAL"
    SLOW = "SLOW"
    TIMEOUT = "TIMEOUT"
    OFFLINE = "OFFLINE"
    DUPLICATE_ACCEPTED = "DUPLICATE_ACCEPTED"

    def __init__(self, mode: str = NORMAL, slow_delay_s: float = 0.05):
        self.mode = mode
        self.slow_delay_s = slow_delay_s
        self._seen_event_ids: set[str] = set()

    def send_event(self, payload: dict[str, Any]) -> str:
        event_id = payload.get("event_id")

        if self.mode == self.TIMEOUT:
            return SyncResult.RETRYABLE_FAILURE
        if self.mode == self.OFFLINE:
            return SyncResult.RETRYABLE_FAILURE
        if self.mode == self.SLOW:
            time.sleep(self.slow_delay_s)

        already_seen = event_id in self._seen_event_ids
        self._seen_event_ids.add(event_id)

        if self.mode == self.DUPLICATE_ACCEPTED and already_seen:
            return SyncResult.ALREADY_ACCEPTED
        return SyncResult.ACCEPTED


class HttpSyncAdapter(SyncAdapter):
    """HTTP adapter for the localhost qualification backend, not production.

    ``transport`` is an optional deterministic test seam returning
    ``(status_code, response_body)``. The default transport uses urllib.
    """

    def __init__(self, endpoint_url: str, timeout_s: float = 2.0, transport=None):
        self.endpoint_url = endpoint_url
        self.timeout_s = timeout_s
        self.transport = transport or self._urllib_transport

    def _urllib_transport(self, endpoint_url: str, body: bytes, timeout_s: float):
        req = request.Request(endpoint_url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with request.urlopen(req, timeout=timeout_s) as response:
                return response.status, response.read()
        except error.HTTPError as exc:
            return exc.code, exc.read()

    def send_event(self, payload: dict[str, Any]) -> str:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        try:
            status, response_body = self.transport(self.endpoint_url, body, self.timeout_s)
        except Exception:
            return SyncResult.RETRYABLE_FAILURE
        try:
            response = json.loads(response_body.decode("utf-8") if isinstance(response_body, bytes) else response_body)
        except (TypeError, ValueError, UnicodeDecodeError):
            response = {}
        result = response.get("result")
        if status == 201 and result == SyncResult.ACCEPTED:
            return SyncResult.ACCEPTED
        if status == 200 and result == SyncResult.ALREADY_ACCEPTED:
            return SyncResult.ALREADY_ACCEPTED
        if status in (400, 409):
            return SyncResult.PERMANENT_FAILURE
        # Includes 401/403 until the later auth-aware blocking tranche.
        return SyncResult.RETRYABLE_FAILURE


@dataclass(slots=True)
class BackoffConfig:
    base_delay_s: float = 1.0
    max_delay_s: float = 60.0
    jitter_s: float = 0.25
    max_retries: int | None = None  # None = retry forever (RETRYABLE_FAILURE never becomes permanent on its own)


def compute_backoff_delay(retry_count: int, config: BackoffConfig) -> float:
    """1s, 2s, 4s, 8s, ... capped at ``max_delay_s``, with a little jitter."""
    delay = min(config.base_delay_s * (2 ** max(0, retry_count - 1)), config.max_delay_s)
    jitter = random.uniform(0.0, config.jitter_s) if config.jitter_s > 0 else 0.0
    return delay + jitter


class SyncWorker:
    """Background store-and-forward loop. Never runs on the runtime thread."""

    def __init__(
        self,
        journal: IncidentJournal,
        connectivity: ConnectivityManager,
        adapter: SyncAdapter,
        poll_interval_s: float = 1.0,
        backoff: BackoffConfig | None = None,
        on_synced: "callable | None" = None,
        metrics: "Any | None" = None,
    ):
        self.journal = journal
        self.connectivity = connectivity
        self.adapter = adapter
        self.poll_interval_s = poll_interval_s
        self.backoff = backoff or BackoffConfig()
        self.on_synced = on_synced
        self.metrics = metrics

        self._stop = Event()
        self._thread: Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                # A sync-worker bug must never take down the process; the
                # event stays PENDING/RETRYABLE and will be retried.
                pass
            self._stop.wait(self.poll_interval_s)

    def run_once(self) -> bool:
        """Attempt to sync exactly one pending event. Returns True if work was done."""
        if not self.connectivity.permits_sync():
            return False

        events = self.journal.list_pending_events(limit=1)
        if not events:
            return False

        event = events[0]
        self.journal.mark_syncing(event.event_id)
        if self.metrics is not None:
            self.metrics.record_sync_attempt()

        try:
            result = self.adapter.send_event(event.payload)
        except Exception:
            # An adapter may raise on transport failures. Convert that into
            # the normal durable retry path so the row cannot remain SYNCING.
            result = SyncResult.RETRYABLE_FAILURE

        if result in (SyncResult.ACCEPTED, SyncResult.ALREADY_ACCEPTED):
            synced_at = datetime.now(timezone.utc).isoformat()
            self.journal.mark_synced(event.event_id, synced_at)
            if self.metrics is not None:
                self.metrics.record_sync_success()
            if self.on_synced is not None:
                self.on_synced(event)
            return True

        if result == SyncResult.PERMANENT_FAILURE:
            self.journal.mark_permanent_failure(event.event_id)
            if self.metrics is not None:
                self.metrics.record_sync_permanent_failure()
            return True

        # RETRYABLE_FAILURE (and anything unrecognised, treated the same way)
        retry_count = event.retry_count + 1
        delay_s = compute_backoff_delay(retry_count, self.backoff)
        next_retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay_s)).isoformat()
        self.journal.mark_retryable_failure(event.event_id, retry_count, next_retry_at)
        if self.metrics is not None:
            self.metrics.record_retry_attempt()
        return True
