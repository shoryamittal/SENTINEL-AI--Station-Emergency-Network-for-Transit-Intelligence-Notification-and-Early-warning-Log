"""Continuity metrics: runtime-derived counters for the operator dashboard.

Every number here is either a live counter incremented by the code that
actually did the work, or read straight from ``IncidentJournal`` /
``ConnectivityManager`` at snapshot time. Nothing is hard-coded for demo
purposes.

``events_lost`` follows the spec's definition exactly:

    events_lost = events_generated - events_successfully_persisted

A pending (not-yet-synced) event is never "lost" -- it is safely on disk.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from .connectivity import ConnectivityManager
from .persistence import IncidentJournal, SyncStatus


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class ContinuityMetricsSnapshot:
    system_started_at: str

    connectivity_state: str
    outage_started_at: str | None
    current_outage_duration_s: float
    total_outage_duration_s: float

    events_generated: int
    events_persisted: int
    events_local_delivered: int

    events_pending: int
    events_syncing: int
    events_synced: int
    events_failed: int  # PERMANENT_FAILURE only; RETRYABLE_FAILURE counts as pending-ish work-in-progress
    events_retrying: int
    events_auth_blocked: int
    events_lost: int

    sync_attempts: int
    retry_attempts: int

    latest_database_success: str | None
    latest_sync_success: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "system_started_at": self.system_started_at,
            "connectivity_state": self.connectivity_state,
            "outage_started_at": self.outage_started_at,
            "current_outage_duration_s": round(self.current_outage_duration_s, 2),
            "total_outage_duration_s": round(self.total_outage_duration_s, 2),
            "events_generated": self.events_generated,
            "events_persisted": self.events_persisted,
            "events_local_delivered": self.events_local_delivered,
            "events_pending": self.events_pending,
            "events_syncing": self.events_syncing,
            "events_synced": self.events_synced,
            "events_failed": self.events_failed,
            "events_retrying": self.events_retrying,
            "events_auth_blocked": self.events_auth_blocked,
            "events_lost": self.events_lost,
            "sync_attempts": self.sync_attempts,
            "retry_attempts": self.retry_attempts,
            "latest_database_success": self.latest_database_success,
            "latest_sync_success": self.latest_sync_success,
        }


class ContinuityMetrics:
    """Thread-safe counters, combined at read-time with live journal/connectivity state."""

    def __init__(self, journal: IncidentJournal, connectivity: ConnectivityManager):
        self.journal = journal
        self.connectivity = connectivity
        self._lock = Lock()

        self._started_at = _utc_now_iso()
        self._events_generated = 0
        self._generated_event_ids: set[str] = set()
        self._events_persisted = 0
        self._events_local_delivered = 0
        self._sync_attempts = 0
        self._retry_attempts = 0
        self._latest_database_success: str | None = None
        self._latest_sync_success: str | None = None

    # ------------------------------------------------------------------
    # Writers -- called by the incident consumer / sync worker as work happens.
    # ------------------------------------------------------------------
    def record_generated(self, event_id: str | None = None) -> None:
        with self._lock:
            if event_id is not None:
                if event_id in self._generated_event_ids:
                    return
                self._generated_event_ids.add(event_id)
            self._events_generated += 1

    def record_persisted(self) -> None:
        with self._lock:
            self._events_persisted += 1
            self._latest_database_success = _utc_now_iso()

    def record_local_delivered(self) -> None:
        with self._lock:
            self._events_local_delivered += 1

    def record_sync_attempt(self) -> None:
        with self._lock:
            self._sync_attempts += 1

    def record_sync_success(self) -> None:
        with self._lock:
            self._latest_sync_success = _utc_now_iso()

    def record_sync_permanent_failure(self) -> None:
        return  # tracked via journal counts; nothing to increment here

    def record_retry_attempt(self) -> None:
        with self._lock:
            self._retry_attempts += 1

    # ------------------------------------------------------------------
    def snapshot(self) -> ContinuityMetricsSnapshot:
        counts = self.journal.count_by_sync_status()
        pending = counts.get(SyncStatus.PENDING, 0)
        syncing = counts.get(SyncStatus.SYNCING, 0)
        synced = counts.get(SyncStatus.SYNCED, 0)
        retrying = counts.get(SyncStatus.RETRYABLE_FAILURE, 0)
        auth_blocked = counts.get(SyncStatus.AUTH_BLOCKED, 0)
        failed = counts.get(SyncStatus.PERMANENT_FAILURE, 0)

        connectivity = self.connectivity.snapshot()

        with self._lock:
            events_generated = self._events_generated
            events_persisted = self._events_persisted
            events_lost = max(0, events_generated - events_persisted)
            return ContinuityMetricsSnapshot(
                system_started_at=self._started_at,
                connectivity_state=connectivity.state,
                outage_started_at=connectivity.offline_started_at.isoformat() if connectivity.offline_started_at else None,
                current_outage_duration_s=connectivity.current_outage_duration_s,
                total_outage_duration_s=connectivity.total_outage_duration_s,
                events_generated=events_generated,
                events_persisted=events_persisted,
                events_local_delivered=self._events_local_delivered,
                events_pending=pending,
                events_syncing=syncing,
                events_synced=synced,
                events_failed=failed,
                events_retrying=retrying,
                events_auth_blocked=auth_blocked,
                events_lost=events_lost,
                sync_attempts=self._sync_attempts,
                retry_attempts=self._retry_attempts,
                latest_database_success=self._latest_database_success,
                latest_sync_success=self._latest_sync_success,
            )
