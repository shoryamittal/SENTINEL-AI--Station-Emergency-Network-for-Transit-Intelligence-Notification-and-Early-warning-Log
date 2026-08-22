"""Localhost-only qualification store for proving sync idempotency.

This is a reference/qualification backend, NOT production remote
infrastructure. It intentionally owns a separate SQLite database from the
device-side ``IncidentJournal``.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Iterator


class QualificationResult:
    ACCEPTED = "ACCEPTED"
    ALREADY_ACCEPTED = "ALREADY_ACCEPTED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    MALFORMED = "MALFORMED"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    received_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qualification_events_received ON events(received_at_utc);
"""


class QualificationEventStore:
    """Tiny authoritative store used only for deterministic qualification."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._write_lock = Lock()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.executescript(_SCHEMA)
            conn.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def canonicalize(payload: dict) -> str:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def accept_event(self, payload: dict) -> tuple[str, str | None]:
        event_id = payload.get("event_id") if isinstance(payload, dict) else None
        if not isinstance(event_id, str) or not event_id.strip():
            return QualificationResult.MALFORMED, None
        canonical = self.canonicalize(payload)
        payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                existing = conn.execute("SELECT payload_hash FROM events WHERE event_id = ?", (event_id,)).fetchone()
                if existing is not None:
                    conn.execute("COMMIT;")
                    result = QualificationResult.ALREADY_ACCEPTED if existing["payload_hash"] == payload_hash else QualificationResult.IDEMPOTENCY_CONFLICT
                    return result, event_id
                conn.execute(
                    "INSERT INTO events (event_id, payload_json, payload_hash, received_at_utc) VALUES (?, ?, ?, ?)",
                    (event_id, canonical, payload_hash, datetime.now(timezone.utc).isoformat()),
                )
                conn.execute("COMMIT;")
                return QualificationResult.ACCEPTED, event_id
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def get_event(self, event_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT event_id, payload_json, received_at_utc FROM events WHERE event_id = ?", (event_id,)).fetchone()
        if row is None:
            return None
        return {"event_id": row["event_id"], "payload": json.loads(row["payload_json"]), "received_at_utc": row["received_at_utc"]}

    def list_events(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT event_id, payload_json, received_at_utc FROM events ORDER BY received_at_utc DESC LIMIT ?", (limit,)).fetchall()
        return [{"event_id": row["event_id"], "payload": json.loads(row["payload_json"]), "received_at_utc": row["received_at_utc"]} for row in rows]

    def count_events(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
