"""Explainable scenario, severity, hysteresis, and recommendation policy."""
from __future__ import annotations
from .contracts import Scenario, Severity

class ScenarioEngine:
    def __init__(self, escalate: int = 2, deescalate: int = 4, guardrail: int = 20):
        self.escalate, self.deescalate, self.guardrail = escalate, deescalate, guardrail
        self.severity = Severity.GREEN; self._candidate = Severity.GREEN; self._n = 0
    def evaluate(self, grid, load, accumulation, redistribution, hotspot):
        maximum = max((max(row) for row in grid), default=0)
        if maximum >= self.guardrail: scenario, proposed = Scenario.LOCAL_BOTTLENECK, Severity.BLACK
        elif maximum >= 6 and (load >= .35 or accumulation >= .12): scenario, proposed = Scenario.LOCAL_BOTTLENECK, Severity.RED
        elif accumulation >= .20: scenario, proposed = Scenario.ACCUMULATION, Severity.RED
        elif redistribution >= .35: scenario, proposed = Scenario.MASS_REDISTRIBUTION, Severity.RED
        elif accumulation >= .08 or redistribution >= .18 or load >= .25: scenario, proposed = Scenario.ACCUMULATION if accumulation >= max(redistribution, load) else Scenario.MASS_REDISTRIBUTION, Severity.YELLOW
        elif sum(sum(r) for r in grid) > 0: scenario, proposed = Scenario.STABLE_HIGH_OCCUPANCY, Severity.GREEN
        else: scenario, proposed = Scenario.UNKNOWN, Severity.GREEN
        if proposed == self.severity: self._n = 0
        elif proposed == self._candidate: self._n += 1
        else: self._candidate, self._n = proposed, 1
        ranks = {Severity.GREEN: 0, Severity.YELLOW: 1, Severity.RED: 2, Severity.BLACK: 3}
        needed = self.escalate if ranks[proposed] > ranks[self.severity] else self.deescalate
        if proposed != self.severity and self._n >= needed: self.severity, self._n = proposed, 0
        conditions = tuple(x for x, value in ((Scenario.ACCUMULATION, accumulation >= .08), (Scenario.MASS_REDISTRIBUTION, redistribution >= .18)) if value and x != scenario)
        code, text = self._recommendation(scenario, self.severity)
        confidence = min(.95, .45 + max(load, accumulation, redistribution))
        return scenario, conditions, self.severity, confidence, code, text
    def _recommendation(self, scenario, severity):
        if severity is Severity.BLACK: return "ESCALATE_LOCAL_EMERGENCY", "Escalate to station control and restrict inflow to the critical zone."
        mapping = {(Scenario.ACCUMULATION, Severity.YELLOW): ("PREPARE_INFLOW_CONTROL", "Prepare inflow control and monitor affected zone."), (Scenario.ACCUMULATION, Severity.RED): ("RESTRICT_INFLOW", "Restrict additional inflow toward the affected zone."), (Scenario.MASS_REDISTRIBUTION, Severity.YELLOW): ("PREPARE_DIVERSION", "Prepare alternate routing away from emerging hotspot."), (Scenario.MASS_REDISTRIBUTION, Severity.RED): ("DIVERT_FROM_HOTSPOT", "Divert passenger movement away from the emerging hotspot."), (Scenario.LOCAL_BOTTLENECK, Severity.RED): ("ISOLATE_HOTSPOT", "Restrict additional entry to hotspot and use alternate path.")}
        return mapping.get((scenario, severity), ("MONITOR", "Continue monitoring; no abnormal crowd transition detected."))
