"""Railway operations data integration for station crowd context."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class TrainSchedule:
    train_id: str
    platform: int
    scheduled_departure: float
    scheduled_arrival: Optional[float] = None
    status: str = "on_time"
    passenger_load: float = 0.6
    cars: int = 8


@dataclass
class PlatformAlert:
    platform: int
    alert_type: str
    message: str
    severity: str = "info"
    created_at: float = field(default_factory=time.time)


class RailwayIntegration:
    """Facade for pulling railway schedule / platform status into the crowd pipeline.

    In a real deployment this would wrap an Indian Railways / IRCTC API.
    For demos and testing the module ships with a synthetic sample dataset.
    """

    def __init__(self, station_code: str = "CST") -> None:
        self.station_code = station_code
        self.schedules: list[TrainSchedule] = []
        self.alerts: list[PlatformAlert] = []
        self.platform_counts: dict[int, int] = {}
        self._data_loaded = False

    # ------------------------------------------------------------------
    # Sample data (demo mode)
    # ------------------------------------------------------------------
    def load_sample_data(self, num_trains: int = 12) -> None:
        """Populate the integration with realistic-looking synthetic schedule data."""
        now = time.time()
        routes = [
            ("MUMBAI-Local", 0.75),
            ("Pune-Express", 0.6),
            ("Thane-Fast", 0.85),
            ("Kalyan-Local", 0.55),
            ("Lonavala-Slow", 0.4),
            ("Churchgate-Fast", 0.9),
        ]
        self.schedules = []
        for i in range(num_trains):
            name, load = routes[i % len(routes)]
            platform = (i % 6) + 1
            self.schedules.append(
                TrainSchedule(
                    train_id=f"{name[:2].upper()}-{1000 + i}",
                    platform=platform,
                    scheduled_departure=now + (i * 8 * 60) + np.random.randint(-60, 60),
                    status=np.random.choice(
                        ["on_time", "delayed", "boarding"],
                        p=[0.55, 0.2, 0.25],
                    ),
                    passenger_load=float(np.clip(load + np.random.uniform(-0.15, 0.2), 0.1, 3.0)),
                    cars=int(np.random.choice([12, 15, 16, 20, 22, 24])),
                )
            )

        self.platform_counts = {p: int(np.random.randint(50, 400)) for p in range(1, 7)}
        self.alerts = [
            PlatformAlert(
                platform=3,
                alert_type="boarding",
                message="Platform 3 boarding in progress - expect crowd surge",
                severity="warning",
            ),
            PlatformAlert(
                platform=5,
                alert_type="delay",
                message="Train delayed 10 min - dispersal available",
                severity="info",
            ),
        ]
        self._data_loaded = True

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------
    @property
    def is_ready(self) -> bool:
        return self._data_loaded

    def _refresh_if_stale(self) -> None:
        if self.schedules and all(s.scheduled_departure < time.time() for s in self.schedules):
            self.load_sample_data()

    def upcoming_trains(
        self,
        within_minutes: float = 15.0,
        now_ts: Optional[float] = None,
    ) -> list[TrainSchedule]:
        """Return trains scheduled to depart within the next N minutes."""
        self._refresh_if_stale()
        now = now_ts if now_ts is not None else time.time()
        cutoff = now + within_minutes * 60
        return [s for s in self.schedules if now <= s.scheduled_departure <= cutoff]

    def platform_passenger_estimate(self, platform: int) -> int:
        return int(self.platform_counts.get(platform, 0))

    def active_alerts(self) -> list[PlatformAlert]:
        return list(self.alerts)

    def crowd_multiplier(self, within_minutes: float = 10.0) -> float:
        """Scalar multiplier indicating expected platform density based on schedules.

        >=1.2 implies an incoming crowd-risk window from boarding/delayed trains.
        """
        self._refresh_if_stale()
        trains = self.upcoming_trains(within_minutes=within_minutes)
        if not trains:
            return 1.0
        load_factor = sum(s.passenger_load for s in trains) / max(1, len(trains))
        boarding = sum(1 for s in trains if s.status == "boarding")
        delayed = sum(1 for s in trains if s.status == "delayed")
        multiplier = 1.0 + 0.35 * load_factor + 0.15 * boarding + 0.1 * delayed
        return float(np.clip(multiplier, 1.0, 2.5))
