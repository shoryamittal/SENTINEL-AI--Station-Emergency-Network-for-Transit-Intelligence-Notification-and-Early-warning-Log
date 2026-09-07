"""Serializable, dependency-free contracts shared with the continuity plane."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class _StringEnum(str, Enum):
    """Enum whose values serialize naturally in JSON-oriented consumers."""


class SourceMode(_StringEnum):
    CAMERA = "CAMERA"
    VIDEO = "VIDEO"
    SIMULATION = "SIMULATION"


class CameraHealth(_StringEnum):
    LIVE = "LIVE"
    STALE = "STALE"
    CAMERA_LOST = "CAMERA_LOST"
    INPUT_RECOVERING = "INPUT_RECOVERING"


class BaselineState(_StringEnum):
    UNINITIALIZED = "UNINITIALIZED"
    CALIBRATING = "CALIBRATING"
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    RECOVERING = "RECOVERING"


class Severity(_StringEnum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"
    BLACK = "BLACK"


class Scenario(_StringEnum):
    STABLE_HIGH_OCCUPANCY = "STABLE_HIGH_OCCUPANCY"
    ACCUMULATION = "ACCUMULATION"
    MASS_REDISTRIBUTION = "MASS_REDISTRIBUTION"
    LOCAL_BOTTLENECK = "LOCAL_BOTTLENECK"
    UNKNOWN = "UNKNOWN"


def utc_now() -> datetime:
    """Return an aware UTC timestamp for use in contracts."""
    return datetime.now(timezone.utc)


def _serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value


@dataclass(frozen=True, slots=True)
class RiskSnapshot:
    """A point-in-time local risk assessment, safe to hand to Person 2.

    ``occupancy_index`` is a relative prototype measure, not people per square
    metre. ``occupancy_grid`` is represented by immutable rows for safe queue
    hand-off and converts to a JSON list-of-lists via :meth:`to_dict`.
    """

    timestamp_utc: datetime
    frame_id: int
    source_mode: SourceMode
    people_count: int
    occupancy_index: float
    occupancy_grid: tuple[tuple[int, ...], ...]
    baseline_state: BaselineState
    load_anomaly: float
    accumulation: float
    redistribution: float
    primary_scenario: Scenario
    contributing_conditions: tuple[Scenario, ...]
    severity: Severity
    confidence: float
    hotspot: str | None
    recommended_action: str
    action_code: str
    camera_health: CameraHealth
    frame_age_ms: float
    processing_latency_ms: float
    model_version: str

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(frozen=True, slots=True)
class IncidentCandidate:
    """A meaningful local event; persistence is deliberately external."""

    severity: Severity
    primary_scenario: Scenario
    contributing_conditions: tuple[Scenario, ...]
    hotspot: str | None
    load_anomaly: float
    accumulation: float
    redistribution: float
    recommended_action: str
    action_code: str
    model_version: str
    people_count: int = 0
    source_mode: SourceMode | None = None
    frame_id: int | None = None
    event_id: str = field(default_factory=lambda: str(uuid4()))
    created_at_utc: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))
