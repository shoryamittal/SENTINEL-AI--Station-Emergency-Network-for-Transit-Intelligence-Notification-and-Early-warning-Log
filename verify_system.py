#!/usr/bin/env python3
"""Verify the current Round 2 local safety-plane architecture."""
from __future__ import annotations
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIRED = ("src/contracts.py", "src/camera.py", "src/detector.py", "src/occupancy.py", "src/baseline.py", "src/adaptive_risk.py", "src/scenario.py", "src/health.py", "src/runtime.py")

def check(label, condition):
    print(f"[{'OK' if condition else 'X'}] {label}")
    return bool(condition)

def main():
    results = [check("Python 3.8+", sys.version_info >= (3, 8)), *[check(path, (ROOT / path).is_file()) for path in REQUIRED]]
    for module in ("src.contracts", "src.camera", "src.detector", "src.occupancy", "src.baseline", "src.adaptive_risk", "src.scenario", "src.health", "src.runtime"):
        try: importlib.import_module(module); results.append(check(f"import {module}", True))
        except Exception as exc: print(f"[X] import {module}: {exc}"); results.append(False)
    try:
        from src.contracts import SourceMode
        from src.camera import FrameSource
        from src.config import RuntimeConfig
        from src.detector import Detection
        from src.runtime import SentinelRuntime
        import numpy as np
        class FakeDetector:
            model_version = "verification-fake"
            def detect(self, frame): return [Detection((0, 0, 1, 1), (4, 4), .9)], .1
        source = FrameSource(SourceMode.SIMULATION, simulation_factory=lambda: np.zeros((10, 10, 3), dtype=np.uint8))
        runtime = SentinelRuntime(source, FakeDetector(), RuntimeConfig(calibration_samples=2))
        results.append(check("offline deterministic runtime smoke test", runtime.process_once() is not None))
    except Exception as exc: print(f"[X] offline smoke test: {exc}"); results.append(False)
    return 0 if all(results) else 1

if __name__ == "__main__": raise SystemExit(main())
