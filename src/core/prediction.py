"""Simple linear-trend density predictor based on recent history."""
# DEPRECATED: This module is superseded by the Round-2 pipeline in src/. Retained for reference only.
from __future__ import annotations

import time
from collections import deque


def _linear_forecast(xs: list, ys: list, future_x: float) -> float:
    """Least-squares line through (xs, ys), evaluated at future_x. Clamped at 0."""
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    denom = sum((x - mean_x) ** 2 for x in xs)
    slope = 0.0 if denom == 0 else sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    intercept = mean_y - slope * mean_x
    return max(0.0, slope * future_x + intercept)


class DensityPredictor:
    """Tracks recent density statistics and extrapolates a short-term forecast."""

    def __init__(self, history_length: int = 30, prediction_horizon: float = 1.0,
                 use_lstm: bool = False):
        self.history_length = history_length
        self.prediction_horizon = prediction_horizon
        # Reserved for a future learned model; the linear fit below is used either way.
        self.use_lstm = use_lstm
        self.history = deque(maxlen=history_length)

    def update_history(self, statistics: dict) -> None:
        self.history.append({
            "timestamp": time.time(),
            "max_density": statistics.get("max_density", 0.0),
            "avg_density": statistics.get("avg_density", 0.0),
            "total_people": statistics.get("total_people", 0),
        })

    def predict_future_density(self, time_minutes: float = 5.0) -> dict:
        """Extrapolate density/headcount using a linear fit over history; classify the trend."""
        if len(self.history) < 2:
            latest = self.history[-1] if self.history else {"max_density": 0.0, "total_people": 0}
            return {
                "trend": "stable",
                "predicted_max_density": latest["max_density"],
                "current_max_density": latest["max_density"],
                "predicted_total_people": float(latest["total_people"]),
            }

        timestamps = [h["timestamp"] for h in self.history]
        densities = [h["max_density"] for h in self.history]
        people = [h["total_people"] for h in self.history]

        t0 = timestamps[0]
        xs = [t - t0 for t in timestamps]
        current = densities[-1]

        # A slope fit needs the samples spread over real time. When history was
        # captured effectively all at once (sub-frame timescale), the regression
        # denominator is near-zero and the slope explodes; there's nothing
        # meaningful to extrapolate, so just hold steady.
        observed_span = xs[-1] - xs[0]
        if observed_span < 0.05:
            return {
                "trend": "stable",
                "predicted_max_density": current,
                "current_max_density": current,
                "predicted_total_people": float(people[-1]),
            }

        # Extrapolating a slope fit on a short history out to a much larger
        # horizon (e.g. 5 min ahead from ~1s of frames) blows up any tiny slope.
        # Cap how far past the observed window we trust the fit.
        max_lookahead = observed_span * 4.0
        target_x = xs[-1] + time_minutes * 60.0
        future_x = min(target_x, xs[-1] + max_lookahead)

        predicted_density = _linear_forecast(xs, densities, future_x)
        predicted_people = _linear_forecast(xs, people, future_x)
        if predicted_density > current + 0.15:
            trend = "increasing"
        elif predicted_density < current - 0.15:
            trend = "decreasing"
        else:
            trend = "stable"

        return {
            "trend": trend,
            "predicted_max_density": predicted_density,
            "current_max_density": current,
            "predicted_total_people": predicted_people,
        }
