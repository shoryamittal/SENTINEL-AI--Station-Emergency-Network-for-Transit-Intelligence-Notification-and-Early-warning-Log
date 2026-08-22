"""RPF/station-control alerting for RED and BLACK crowd situations.

Safe by default: notifications are logged and recorded but no real SMS is
sent unless both FAST2SMS_API_KEY and SENTINEL_SEND_REAL_SMS=1 are set in
the environment (see docs/SECURITY_PRIVACY.md - never send real alerts from
automated tests or CI).
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger("sentinel.notifications")

_PLACEHOLDER_CONTACT = "+910000000000"


class NotificationSystem:
    """Builds and dispatches RPF alert messages for high-risk crowd states."""

    def __init__(self, station_name: str = "Central Station",
                 primary_contact: str | None = None,
                 fast2sms_api_key: str | None = None,
                 history_length: int = 50):
        self.station_name = station_name
        self.primary_contact = (
            primary_contact
            or os.environ.get("NOTIFICATION_PRIMARY_CONTACT")
            or os.environ.get("TEST_PRIMARY_CONTACT")
            or _PLACEHOLDER_CONTACT
        )
        self.fast2sms_api_key = fast2sms_api_key or os.environ.get("FAST2SMS_API_KEY")
        self._send_real = os.environ.get("SENTINEL_SEND_REAL_SMS") == "1"
        self.history: list = []
        self.history_length = history_length

    def build_message(self, state: str, density: float, people_count: int) -> str:
        return (
            f"[SENTINEL AI] {self.station_name}: {state} zone - "
            f"density {density:.2f} p/m2, {people_count} people detected. "
            f"RPF response required."
        )

    def send_rpf_notification(self, state: str, density: float, people_count: int) -> dict:
        message = self.build_message(state, density, people_count)
        record = {
            "timestamp": time.time(),
            "contact": self.primary_contact,
            "state": state,
            "density": density,
            "people_count": people_count,
            "message": message,
            "sent": False,
        }

        if self._send_real and self.fast2sms_api_key:
            record["sent"] = self._send_sms(message)
        else:
            logger.info("[SIMULATED SMS to %s] %s", self.primary_contact, message)

        self.history.append(record)
        if len(self.history) > self.history_length:
            self.history.pop(0)
        return record

    def _send_sms(self, message: str) -> bool:  # pragma: no cover - network call
        try:
            import requests

            response = requests.post(
                "https://www.fast2sms.com/dev/bulkV2",
                headers={"authorization": self.fast2sms_api_key},
                data={
                    "route": "q",
                    "message": message,
                    "numbers": self.primary_contact.lstrip("+"),
                },
                timeout=10,
            )
            return response.ok
        except Exception as exc:
            logger.warning("Fast2SMS dispatch failed: %s", exc)
            return False
