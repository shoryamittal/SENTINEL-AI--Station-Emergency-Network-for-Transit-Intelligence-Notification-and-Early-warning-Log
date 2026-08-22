"""Classifies crowd density into GREEN / YELLOW / RED / BLACK zones."""


class SituationClassifier:
    """Thresholds max density (people/m^2) into a named situation state."""

    # Each action is {"description": str, "auto_execute": bool}. auto_execute=False
    # marks actions that require a human decision (deploying staff, escalation)
    # rather than something the system can trigger on its own.
    ZONE_ACTIONS = {
        "GREEN": [
            {"description": "Continue monitoring crowd movement", "auto_execute": True},
            {"description": "Track density trends", "auto_execute": True},
            {"description": "Watch for transition to YELLOW zone", "auto_execute": True},
        ],
        "YELLOW": [
            {"description": "Compute shortest alternate paths", "auto_execute": True},
            {"description": "Identify crowd diversion routes", "auto_execute": True},
            {"description": "Prepare digital display updates", "auto_execute": True},
            {"description": "Pre-position RPF staff at key points", "auto_execute": False},
        ],
        "RED": [
            {"description": "Activate dynamic route recommendations", "auto_execute": True},
            {"description": "Update all passenger information displays", "auto_execute": True},
            {"description": "Deploy RPF personnel to critical zones", "auto_execute": False},
            {"description": "Activate alternate access routes", "auto_execute": True},
        ],
        "BLACK": [
            {"description": "Restrict inflow at entry gates/staircases", "auto_execute": False},
            {"description": "Hold automatic announcements", "auto_execute": True},
            {"description": "Escalate to Station Control & RPF Command", "auto_execute": False},
            {"description": "Initiate emergency crowd management protocols", "auto_execute": False},
        ],
    }

    def __init__(self, green_threshold: float = 4.0, yellow_threshold: float = 5.0,
                 red_threshold: float = 6.0):
        self.green_threshold = green_threshold
        self.yellow_threshold = yellow_threshold
        self.red_threshold = red_threshold

    def classify(self, max_density: float, trend: str = "stable", prediction: dict = None):
        """Return (state, confidence)."""
        if max_density < self.green_threshold:
            state = "GREEN"
            margin = self.green_threshold - max_density
            confidence = min(0.99, 0.75 + margin / max(self.green_threshold, 1e-6) * 0.24)
        elif max_density < self.yellow_threshold:
            state = "YELLOW"
            span = self.yellow_threshold - self.green_threshold
            confidence = 0.8 + (max_density - self.green_threshold) / max(span, 1e-6) * 0.15
        elif max_density < self.red_threshold:
            state = "RED"
            span = self.red_threshold - self.yellow_threshold
            confidence = 0.85 + (max_density - self.yellow_threshold) / max(span, 1e-6) * 0.12
        else:
            state = "BLACK"
            confidence = 0.97

        if trend == "increasing":
            confidence = min(0.99, confidence + 0.02)

        return state, round(min(confidence, 0.99), 3)

    def get_recommended_actions(self, situation: str) -> list:
        """Return a list of {"description", "auto_execute"} dicts for this zone."""
        actions = self.ZONE_ACTIONS.get(situation, self.ZONE_ACTIONS["GREEN"])
        return [dict(a) for a in actions]
