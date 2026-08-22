"""Public contracts for the SENTINEL local safety plane.

The runtime implementation is intentionally exported separately once it is
available. Continuity-plane consumers should depend on ``src.contracts`` or
these re-exports, never on camera or detector internals.
"""

from .contracts import (
    BaselineState,
    CameraHealth,
    IncidentCandidate,
    RiskSnapshot,
    Scenario,
    Severity,
    SourceMode,
)

__all__ = [
    "BaselineState",
    "CameraHealth",
    "IncidentCandidate",
    "RiskSnapshot",
    "Scenario",
    "Severity",
    "SourceMode",
]
