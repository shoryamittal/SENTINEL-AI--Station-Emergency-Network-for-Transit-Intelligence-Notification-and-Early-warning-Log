from src.adaptive_risk import AdaptiveRisk

def test_accumulation_ignores_noise_then_detects_surge():
    risk = AdaptiveRisk(window=5)
    for n in (40,41,39,40,41): _, a, _ = risk.update(((n,),), ((40,),))
    assert a < .05
    for n in (25,29,35,43,54): _, a, _ = risk.update(((n,),), ((25,),))
    assert a > .15

def test_redistribution_detects_shift_with_same_total():
    risk = AdaptiveRisk(redistribution_window=3)
    for grid in (((10,0),), ((5,5),), ((0,10),)): _, _, r = risk.update(grid, grid)
    assert r > .8
