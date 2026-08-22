from src.contracts import Scenario, Severity
from src.scenario import ScenarioEngine

def test_stable_high_occupancy_stays_green():
    engine = ScenarioEngine(escalate=1)
    primary, _, severity, *_ = engine.evaluate(((5,5),), 0, 0, 0, "r0c0")
    assert primary is Scenario.STABLE_HIGH_OCCUPANCY and severity is Severity.GREEN

def test_scenario_and_severity_are_distinct():
    engine = ScenarioEngine(escalate=1)
    primary, _, severity, *_ = engine.evaluate(((1,1),), 0, .25, 0, "r0c0")
    assert primary is Scenario.ACCUMULATION and severity is Severity.RED
