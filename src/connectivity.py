"""Background connectivity monitor.

CONNECTIVITY IS A DEPENDENCY FOR SYNCHRONIZATION. IT IS NOT A DEPENDENCY FOR
SAFETY. This module runs its own health-check loop in a dedicated thread so
that a slow/hanging remote endpoint can never block the frame/risk loop
(``SentinelRuntime``) or the Flask dashboard.

Nothing in here imports detector/occupancy/baseline/risk code.
"""
from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event, Lock, Thread
from typing import Callable

CheckResult = tuple[bool, float]  # (success, latency_ms)


class ConnectivityState:
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"
    RECOVERY = "RECOVERY"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def default_check(url: str = "https://www.gstatic.com/generate_204", timeout_s: float = 3.0) -> CheckResult:
    """Best-effort reachability probe. Never raises; failures just return False."""
    start = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as response:
            response.read(1)
        return True, (time.monotonic() - start) * 1000.0
    except (urllib.error.URLError, socket.timeout, OSError, ValueError):
        return False, (time.monotonic() - start) * 1000.0


@dataclass(frozen=True, slots=True)
class ConnectivitySnapshot:
    state: str
    last_success_at: datetime | None
    last_failure_at: datetime | None
    consecutive_successes: int
    consecutive_failures: int
    latest_latency_ms: float | None
    offline_started_at: datetime | None
    current_outage_duration_s: float
    total_outage_duration_s: float
    remote_endpoint_status: str

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_failure_at": self.last_failure_at.isoformat() if self.last_failure_at else None,
            "consecutive_successes": self.consecutive_successes,
            "consecutive_failures": self.consecutive_failures,
            "latest_latency_ms": self.latest_latency_ms,
            "offline_started_at": self.offline_started_at.isoformat() if self.offline_started_at else None,
            "current_outage_duration_s": round(self.current_outage_duration_s, 2),
            "total_outage_duration_s": round(self.total_outage_duration_s, 2),
            "remote_endpoint_status": self.remote_endpoint_status,
        }


class ConnectivityManager:
    """Runs health checks on a background thread and exposes hysteresis-smoothed state.

    Hysteresis (prototype configuration, not a safety standard):
      - ``failures_for_offline`` consecutive failures -> OFFLINE
      - ``successes_for_recovery`` consecutive successes while OFFLINE -> RECOVERY
      - ``successes_for_online`` consecutive successes while RECOVERY -> ONLINE
      - a lone failure while ONLINE does not flap the state back to OFFLINE
        (DEGRADED absorbs an isolated failure or high latency first).

    ``check_fn`` is injected so tests never need a real network call.
    """

    def __init__(
        self,
        check_fn: Callable[[], CheckResult] = default_check,
        interval_s: float = 5.0,
        failures_for_offline: int = 3,
        successes_for_recovery: int = 2,
        successes_for_online: int = 3,
        degraded_latency_ms: float = 1500.0,
    ):
        self._check_fn = check_fn
        self.interval_s = interval_s
        self.failures_for_offline = failures_for_offline
        self.successes_for_recovery = successes_for_recovery
        self.successes_for_online = successes_for_online
        self.degraded_latency_ms = degraded_latency_ms

        self._lock = Lock()
        self._state = ConnectivityState.ONLINE
        self._last_success_at: datetime | None = None
        self._last_failure_at: datetime | None = None
        self._consecutive_successes = 0
        self._consecutive_failures = 0
        self._latest_latency_ms: float | None = None
        self._offline_started_at: datetime | None = None
        self._total_outage_duration_s = 0.0
        self._recovery_successes = 0
        self._remote_endpoint_status = "UNKNOWN"

        self._stop = Event()
        self._thread: Thread | None = None

    # ------------------------------------------------------------------
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
            self.check_once()
            self._stop.wait(self.interval_s)

    # ------------------------------------------------------------------
    def check_once(self) -> ConnectivitySnapshot:
        """Run a single check synchronously and update state. Safe to call from tests."""
        success, latency_ms = self._check_fn()
        with self._lock:
            self._apply_result(success, latency_ms)
            return self._snapshot_locked()

    def _apply_result(self, success: bool, latency_ms: float) -> None:
        now = _utc_now()
        self._latest_latency_ms = latency_ms
        self._remote_endpoint_status = "REACHABLE" if success else "UNREACHABLE"

        if success:
            self._last_success_at = now
            self._consecutive_successes += 1
            self._consecutive_failures = 0
        else:
            self._last_failure_at = now
            self._consecutive_failures += 1
            self._consecutive_successes = 0

        self._transition(success, latency_ms, now)

    def _transition(self, success: bool, latency_ms: float, now: datetime) -> None:
        state = self._state

        if state == ConnectivityState.ONLINE:
            if not success and self._consecutive_failures >= self.failures_for_offline:
                self._enter_offline(now)
            elif not success or latency_ms >= self.degraded_latency_ms:
                # A single failure or high latency degrades service but does
                # not flap straight to OFFLINE.
                self._state = ConnectivityState.DEGRADED
            return

        if state == ConnectivityState.DEGRADED:
            if not success and self._consecutive_failures >= self.failures_for_offline:
                self._enter_offline(now)
            elif success and latency_ms < self.degraded_latency_ms and self._consecutive_successes >= 1:
                self._state = ConnectivityState.ONLINE
            return

        if state == ConnectivityState.OFFLINE:
            if success and self._consecutive_successes >= self.successes_for_recovery:
                self._state = ConnectivityState.RECOVERY
                self._recovery_successes = self._consecutive_successes
                self._exit_offline(now)
            return

        if state == ConnectivityState.RECOVERY:
            if not success:
                # Recovery wasn't stable; go straight back to OFFLINE bookkeeping.
                self._enter_offline(now)
                return
            self._recovery_successes += 1
            if self._recovery_successes >= self.successes_for_online:
                self._state = ConnectivityState.ONLINE
            return

    def _enter_offline(self, now: datetime) -> None:
        if self._state != ConnectivityState.OFFLINE:
            self._offline_started_at = now
        self._state = ConnectivityState.OFFLINE
        self._recovery_successes = 0

    def _exit_offline(self, now: datetime) -> None:
        if self._offline_started_at is not None:
            self._total_outage_duration_s += (now - self._offline_started_at).total_seconds()
            self._offline_started_at = None

    # ------------------------------------------------------------------
    def snapshot(self) -> ConnectivitySnapshot:
        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> ConnectivitySnapshot:
        current_outage = 0.0
        if self._offline_started_at is not None:
            current_outage = (_utc_now() - self._offline_started_at).total_seconds()
        return ConnectivitySnapshot(
            state=self._state,
            last_success_at=self._last_success_at,
            last_failure_at=self._last_failure_at,
            consecutive_successes=self._consecutive_successes,
            consecutive_failures=self._consecutive_failures,
            latest_latency_ms=self._latest_latency_ms,
            offline_started_at=self._offline_started_at,
            current_outage_duration_s=current_outage,
            total_outage_duration_s=self._total_outage_duration_s + current_outage,
            remote_endpoint_status=self._remote_endpoint_status,
        )

    def permits_sync(self) -> bool:
        """Whether the sync worker should attempt a send right now."""
        return self._state in (ConnectivityState.ONLINE, ConnectivityState.DEGRADED, ConnectivityState.RECOVERY)
