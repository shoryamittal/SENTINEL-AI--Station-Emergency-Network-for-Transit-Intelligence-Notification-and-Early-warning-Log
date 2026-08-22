"""Guaranteed local alerting -- works with zero Internet.

The local alert path is: an IncidentCandidate is generated -> it is
persisted to SQLite (src.persistence) -> it is announced here. This module
never depends on connectivity; the dashboard reads ``LocalAlertCenter``
state over localhost regardless of WAN status.

Fast2SMS (or any other remote notifier) is strictly optional and
best-effort: a failure there must never raise, never block, and never
prevent the local alert from having already fired.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from .contracts import IncidentCandidate, Severity


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class LocalAlert:
    event_id: str
    severity: str
    primary_scenario: str
    hotspot: str | None
    recommended_action: str
    created_at_utc: str
    audible: bool  # RED/BLACK -> True, suggests the dashboard should sound a cue

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "severity": self.severity,
            "primary_scenario": self.primary_scenario,
            "hotspot": self.hotspot,
            "recommended_action": self.recommended_action,
            "created_at_utc": self.created_at_utc,
            "audible": self.audible,
        }


_AUDIBLE_SEVERITIES = (Severity.RED, Severity.BLACK)


class LocalAlertCenter:
    """In-memory, zero-dependency local alert feed for the dashboard.

    Guaranteed to work offline: no network call is on this path. A remote
    notifier (e.g. Fast2SMS via src.core.notifications) may additionally be
    attached, but its failure is swallowed and never affects local delivery.
    """

    def __init__(self, history_length: int = 50, remote_notifier=None):
        self._lock = Lock()
        self._history: deque[LocalAlert] = deque(maxlen=history_length)
        self.remote_notifier = remote_notifier

    def raise_alert(self, candidate: IncidentCandidate) -> LocalAlert:
        alert = LocalAlert(
            event_id=candidate.event_id,
            severity=candidate.severity.value,
            primary_scenario=candidate.primary_scenario.value,
            hotspot=candidate.hotspot,
            recommended_action=candidate.recommended_action,
            created_at_utc=_utc_now_iso(),
            audible=candidate.severity in _AUDIBLE_SEVERITIES,
        )
        with self._lock:
            self._history.appendleft(alert)

        # Best-effort, optional, and isolated: never let a remote notifier
        # failure propagate back through the local alert path.
        if self.remote_notifier is not None:
            try:
                self.remote_notifier(candidate)
            except Exception:
                pass

        return alert

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            return [alert.to_dict() for alert in list(self._history)[:limit]]

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return self._history[0].to_dict() if self._history else None


def optional_fast2sms_notifier(station_name: str = "Central Station"):
    """Build a best-effort remote notifier backed by the existing (safe-by-
    default, no-real-SMS-without-opt-in) NotificationSystem. Any failure to
    import or send is swallowed by LocalAlertCenter -- this function itself
    never raises during construction beyond a clean fallback to None.
    """
    try:
        from .core.notifications import NotificationSystem
    except Exception:
        return None

    system = NotificationSystem(station_name=station_name)

    def _notify(candidate: IncidentCandidate) -> None:
        system.send_rpf_notification(
            candidate.severity.value,
            candidate.load_anomaly + candidate.accumulation + candidate.redistribution,
            0,
        )

    return _notify
