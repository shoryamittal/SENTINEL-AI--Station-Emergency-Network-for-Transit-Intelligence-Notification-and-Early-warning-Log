"""Durable local incident journal (SQLite/WAL).

This is the continuity plane's single source of truth for incidents. It
knows nothing about YOLO, occupancy, or risk scoring -- it only accepts
``IncidentCandidate`` objects (see ``src.contracts``) and tracks their local
and remote delivery lifecycle.

Design contract:
    IncidentCandidate -> SQLite INSERT/COMMIT -> PERSISTED -> local alert ->
    SYNC_PENDING -> remote worker.

The remote side is NEVER contacted before the local commit succeeds. An
event's ``event_id`` (assigned once, by Person 1's contract layer) is both
the SQLite primary key and the sync idempotency key -- it is never
regenerated here.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

from .contracts import IncidentCandidate

SCHEMA_VERSION = 1

DEFAULT_DB_PATH = Path("data") / "sentinel.db"


class LocalStatus:
    """Lifecycle of local (on-device) handling of an event."""

    CREATED = "CREATED"
    PERSISTED = "PERSISTED"
    LOCAL_DELIVERED = "LOCAL_DELIVERED"


class SyncStatus:
    """Lifecycle of remote synchronization of an event."""

    PENDING = "SYNC_PENDING"
    SYNCING = "SYNCING"
    SYNCED = "SYNCED"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    AUTH_BLOCKED = "AUTH_BLOCKED"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,

    schema_version INTEGER NOT NULL,
    created_at_utc TEXT NOT NULL,

    severity TEXT NOT NULL,
    primary_scenario TEXT NOT NULL,
    contributing_conditions TEXT,

    hotspot TEXT,

    load_anomaly REAL,
    accumulation REAL,
    redistribution REAL,

    recommended_action TEXT,
    action_code TEXT,

    source_mode TEXT,
    frame_id INTEGER,
    model_version TEXT,

    connectivity_state TEXT,

    local_status TEXT NOT NULL,
    sync_status TEXT NOT NULL,

    retry_count INTEGER NOT NULL DEFAULT 0,

    last_attempt_at TEXT,
    next_retry_at TEXT,
    synced_at TEXT,

    payload_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_sync_status ON events(sync_status);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at_utc);
"""


@dataclass(frozen=True, slots=True)
class EventRecord:
    """Read-model for a persisted row; a plain dict-like view of the table."""

    event_id: str
    schema_version: int
    created_at_utc: str
    severity: str
    primary_scenario: str
    contributing_conditions: list
    hotspot: str | None
    load_anomaly: float | None
    accumulation: float | None
    redistribution: float | None
    recommended_action: str | None
    action_code: str | None
    source_mode: str | None
    frame_id: int | None
    model_version: str | None
    connectivity_state: str | None
    local_status: str
    sync_status: str
    retry_count: int
    last_attempt_at: str | None
    next_retry_at: str | None
    synced_at: str | None
    payload: dict

    def to_dict(self) -> dict[str, Any]:
        data = {
            "event_id": self.event_id,
            "schema_version": self.schema_version,
            "created_at_utc": self.created_at_utc,
            "severity": self.severity,
            "primary_scenario": self.primary_scenario,
            "contributing_conditions": self.contributing_conditions,
            "hotspot": self.hotspot,
            "load_anomaly": self.load_anomaly,
            "accumulation": self.accumulation,
            "redistribution": self.redistribution,
            "recommended_action": self.recommended_action,
            "action_code": self.action_code,
            "source_mode": self.source_mode,
            "frame_id": self.frame_id,
            "model_version": self.model_version,
            "connectivity_state": self.connectivity_state,
            "local_status": self.local_status,
            "sync_status": self.sync_status,
            "retry_count": self.retry_count,
            "last_attempt_at": self.last_attempt_at,
            "next_retry_at": self.next_retry_at,
            "synced_at": self.synced_at,
        }
        return data


def _row_to_record(row: sqlite3.Row) -> EventRecord:
    return EventRecord(
        event_id=row["event_id"],
        schema_version=row["schema_version"],
        created_at_utc=row["created_at_utc"],
        severity=row["severity"],
        primary_scenario=row["primary_scenario"],
        contributing_conditions=json.loads(row["contributing_conditions"] or "[]"),
        hotspot=row["hotspot"],
        load_anomaly=row["load_anomaly"],
        accumulation=row["accumulation"],
        redistribution=row["redistribution"],
        recommended_action=row["recommended_action"],
        action_code=row["action_code"],
        source_mode=row["source_mode"],
        frame_id=row["frame_id"],
        model_version=row["model_version"],
        connectivity_state=row["connectivity_state"],
        local_status=row["local_status"],
        sync_status=row["sync_status"],
        retry_count=row["retry_count"],
        last_attempt_at=row["last_attempt_at"],
        next_retry_at=row["next_retry_at"],
        synced_at=row["synced_at"],
        payload=json.loads(row["payload_json"]),
    )


class IncidentJournal:
    """SQLite-backed durable journal of incident candidates.

    Thread-safety: each call opens a short-lived connection (SQLite
    connections are cheap and this project's write volume is a handful of
    incidents, not video frames). WAL mode lets concurrent readers (Flask
    dashboard) run alongside the single sync worker without blocking the
    writer. A process-wide lock serializes writes so retry/backoff state
    transitions stay atomic even if multiple threads call journal methods.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self._write_lock = Lock()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript(_SCHEMA)
            conn.commit()
        # SYNCING is transient process-owned state. If the previous process
        # died after marking an event SYNCING, no worker remains to complete
        # that attempt. Requeue the same immutable event_id on startup.
        self.recover_interrupted_syncs()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------
    def save_event(self, candidate: IncidentCandidate, connectivity_state: str) -> bool:
        """Persist a new incident. Returns True if newly inserted.

        Idempotent: if ``candidate.event_id`` already exists, this is a
        no-op (returns False) rather than raising or duplicating the row.
        The event_id is Person 1's contract -- never regenerated here.
        """
        payload = candidate.to_dict()
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO events (
                        event_id, schema_version, created_at_utc,
                        severity, primary_scenario, contributing_conditions, hotspot,
                        load_anomaly, accumulation, redistribution,
                        recommended_action, action_code,
                        source_mode, frame_id, model_version,
                        connectivity_state,
                        local_status, sync_status, retry_count,
                        last_attempt_at, next_retry_at, synced_at,
                        payload_json
                    ) VALUES (
                        :event_id, :schema_version, :created_at_utc,
                        :severity, :primary_scenario, :contributing_conditions, :hotspot,
                        :load_anomaly, :accumulation, :redistribution,
                        :recommended_action, :action_code,
                        :source_mode, :frame_id, :model_version,
                        :connectivity_state,
                        :local_status, :sync_status, 0,
                        NULL, NULL, NULL,
                        :payload_json
                    );
                    """,
                    {
                        "event_id": payload["event_id"],
                        "schema_version": SCHEMA_VERSION,
                        "created_at_utc": payload["created_at_utc"],
                        "severity": payload["severity"],
                        "primary_scenario": payload["primary_scenario"],
                        "contributing_conditions": json.dumps(payload["contributing_conditions"]),
                        "hotspot": payload["hotspot"],
                        "load_anomaly": payload["load_anomaly"],
                        "accumulation": payload["accumulation"],
                        "redistribution": payload["redistribution"],
                        "recommended_action": payload["recommended_action"],
                        "action_code": payload["action_code"],
                        "source_mode": payload["source_mode"],
                        "frame_id": payload["frame_id"],
                        "model_version": payload["model_version"],
                        "connectivity_state": connectivity_state,
                        "local_status": LocalStatus.PERSISTED,
                        "sync_status": SyncStatus.PENDING,
                        "payload_json": json.dumps(payload),
                    },
                )
                conn.execute("COMMIT;")
                return cursor.rowcount > 0
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def mark_local_delivered(self, event_id: str) -> None:
        self._update(event_id, "local_status = ?", (LocalStatus.LOCAL_DELIVERED,))

    def mark_sync_pending(self, event_id: str) -> None:
        self._update(event_id, "sync_status = ?", (SyncStatus.PENDING,))

    def mark_syncing(self, event_id: str) -> None:
        self._update(
            event_id,
            "sync_status = ?, last_attempt_at = ?",
            (SyncStatus.SYNCING, _utc_now_iso()),
        )

    def mark_synced(self, event_id: str, synced_at: str | None = None) -> None:
        self._update(
            event_id,
            "sync_status = ?, synced_at = ?, next_retry_at = NULL",
            (SyncStatus.SYNCED, synced_at or _utc_now_iso()),
        )

    def mark_retryable_failure(self, event_id: str, retry_count: int, next_retry_at: str) -> None:
        self._update(
            event_id,
            "sync_status = ?, retry_count = ?, next_retry_at = ?, last_attempt_at = ?",
            (SyncStatus.RETRYABLE_FAILURE, retry_count, next_retry_at, _utc_now_iso()),
        )

    def mark_permanent_failure(self, event_id: str) -> None:
        self._update(
            event_id,
            "sync_status = ?, last_attempt_at = ?, next_retry_at = NULL",
            (SyncStatus.PERMANENT_FAILURE, _utc_now_iso()),
        )

    def mark_auth_blocked(self, event_id: str) -> None:
        self._update(
            event_id,
            "sync_status = ?, last_attempt_at = ?, next_retry_at = NULL",
            (SyncStatus.AUTH_BLOCKED, _utc_now_iso()),
        )

    def requeue_auth_blocked(self, limit: int | None = None) -> int:
        """Explicitly return durable auth-blocked events to the outbox."""
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                if limit is None:
                    cursor = conn.execute("UPDATE events SET sync_status = ?, next_retry_at = NULL WHERE sync_status = ?", (SyncStatus.PENDING, SyncStatus.AUTH_BLOCKED))
                else:
                    cursor = conn.execute(
                        "UPDATE events SET sync_status = ?, next_retry_at = NULL WHERE event_id IN (SELECT event_id FROM events WHERE sync_status = ? ORDER BY created_at_utc ASC LIMIT ?)",
                        (SyncStatus.PENDING, SyncStatus.AUTH_BLOCKED, limit),
                    )
                conn.execute("COMMIT;")
                return max(0, cursor.rowcount)
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def recover_interrupted_syncs(self) -> int:
        """Requeue rows stranded in SYNCING by a terminated process.

        The prior remote request may already have succeeded, so this preserves
        the exact event ID and payload for backend-enforced idempotent replay.
        """
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                cursor = conn.execute(
                    """
                    UPDATE events
                    SET sync_status = ?, next_retry_at = NULL
                    WHERE sync_status = ?;
                    """,
                    (SyncStatus.PENDING, SyncStatus.SYNCING),
                )
                conn.execute("COMMIT;")
                return max(0, cursor.rowcount)
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def _update(self, event_id: str, set_clause: str, params: tuple) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                conn.execute(f"UPDATE events SET {set_clause} WHERE event_id = ?;", (*params, event_id))
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------
    def get_event(self, event_id: str) -> EventRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM events WHERE event_id = ?;", (event_id,)).fetchone()
            return _row_to_record(row) if row else None

    def list_pending_events(self, limit: int = 50) -> list[EventRecord]:
        """Events eligible for a sync attempt right now (pending or due retry)."""
        now = _utc_now_iso()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE sync_status = ?
                   OR (sync_status = ? AND (next_retry_at IS NULL OR next_retry_at <= ?))
                ORDER BY created_at_utc ASC
                LIMIT ?;
                """,
                (SyncStatus.PENDING, SyncStatus.RETRYABLE_FAILURE, now, limit),
            ).fetchall()
            return [_row_to_record(row) for row in rows]

    def list_auth_blocked_events(self, limit: int = 50) -> list[EventRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM events WHERE sync_status = ? ORDER BY created_at_utc ASC LIMIT ?", (SyncStatus.AUTH_BLOCKED, limit)).fetchall()
            return [_row_to_record(row) for row in rows]

    def get_recent_events(self, limit: int = 50) -> list[EventRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY created_at_utc DESC LIMIT ?;", (limit,)
            ).fetchall()
            return [_row_to_record(row) for row in rows]

    def count_events(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM events;").fetchone()[0])

    def count_by_sync_status(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute("SELECT sync_status, COUNT(*) FROM events GROUP BY sync_status;").fetchall()
            return {row[0]: row[1] for row in rows}
