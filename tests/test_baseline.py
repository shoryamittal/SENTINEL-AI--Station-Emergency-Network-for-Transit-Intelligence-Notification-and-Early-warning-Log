from src.baseline import AdaptiveBaseline
from src.contracts import BaselineState

def test_baseline_freezes_then_recovers():
    baseline = AdaptiveBaseline(calibration_samples=3, recovery_samples=2)
    for _ in range(3): baseline.update(((2,),))
    before = baseline.values().copy(); baseline.update(((20,),), abnormal=True)
    assert baseline.state is BaselineState.FROZEN and baseline.values()[0,0] == before[0,0]
    baseline.update(((2,),)); assert baseline.state is BaselineState.RECOVERING
    baseline.update(((2,),)); assert baseline.state is BaselineState.ACTIVE
