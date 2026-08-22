"""Logs and tracks the response actions recommended for a given situation."""
from __future__ import annotations

import logging
import time

from .notifications import NotificationSystem

logger = logging.getLogger("sentinel.action_executor")

_NOTIFY_STATES = ("RED", "BLACK")


class ActionExecutor:
    """Records executed actions and alerts RPF/station control for high-risk states."""

    def __init__(self, station_name: str = "Central Station", history_length: int = 100,
                 fast2sms_api_key: str | None = None):
        self.station_name = station_name
        self.history: list = []
        self.history_length = history_length
        self.notification_system = NotificationSystem(
            station_name=station_name, fast2sms_api_key=fast2sms_api_key
        )

    def execute_actions(self, actions: list, situation: str, max_density: float = 0.0,
                         people_count: int = 0) -> dict:
        """Split actions into auto-executed vs queued-for-approval, and notify on RED/BLACK.

        Each action may be a plain string (treated as auto-executed) or a
        {"description", "auto_execute"} dict.
        """
        executed, queued = [], []
        for action in actions:
            if isinstance(action, dict):
                (executed if action.get("auto_execute", True) else queued).append(action)
            else:
                executed.append(action)

        record = {
            "timestamp": time.time(),
            "station": self.station_name,
            "situation": situation,
            "max_density": max_density,
            "people_count": people_count,
            "executed": executed,
            "queued": queued,
        }

        if situation in _NOTIFY_STATES:
            record["notification"] = self.notification_system.send_rpf_notification(
                situation, max_density, people_count
            )

        self.history.append(record)
        if len(self.history) > self.history_length:
            self.history.pop(0)

        logger.info(
            "[%s] situation=%s people=%s max_density=%.2f actions=%s",
            self.station_name, situation, people_count, max_density, actions,
        )
        return record

    def get_history(self) -> list:
        return list(self.history)
