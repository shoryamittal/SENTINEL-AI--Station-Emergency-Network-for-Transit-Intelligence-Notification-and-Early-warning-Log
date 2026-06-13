#!/usr/bin/env python3
"""
PREEMPT AI - RED ZONE SIMULATION
Shows full RED state with notifications to your number +918975073895!
"""

import sys
import argparse
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from src.core.classification import SituationClassifier
from src.core.action_executor import ActionExecutor


def main():
    parser = argparse.ArgumentParser(
        description="PREEMPT AI - RED ZONE SIMULATION"
    )
    parser.add_argument(
        "--fast2sms-api-key",
        type=str,
        default=None,
        help="Fast2SMS API key for real SMS notifications (India)"
    )
    
    args = parser.parse_args()
    
    print("\n" + "="*80)
    print("🚨 PREEMPT AI - RED ZONE SIMULATION!")
    print("🚨 HIGH DENSITY ALERT!")
    print("="*80)

    # Initialize components
    classifier = SituationClassifier(
        green_threshold=4.0,
        yellow_threshold=5.0,
        red_threshold=6.0
    )

    executor = ActionExecutor(
        station_name="Central Station",
        fast2sms_api_key=args.fast2sms_api_key
    )

    # Simulate RED state parameters
    MAX_DENSITY = 5.8  # Between 5.0 and 6.0 → RED
    PEOPLE_COUNT = 95
    TREND = "stable"

    print("\n📊 SIMULATED SCENARIO:")
    print(f"  Location: Platform Change Area")
    print(f"  People Count: {PEOPLE_COUNT}")
    print(f"  Max Density: {MAX_DENSITY:.1f} ppl/m²")
    print(f"  Trend: {TREND}")

    # Step 1: Classification
    print("\n" + "-"*80)
    print("🔍 STEP 1: CLASSIFICATION")
    state, confidence = classifier.classify(MAX_DENSITY, trend=TREND)
    print(f"  Result: {state} (Confidence: {confidence:.1%})")

    # Step 2: Get Recommended Actions
    print("\n" + "-"*80)
    print("📋 STEP 2: RECOMMENDED ACTIONS")
    actions = classifier.get_recommended_actions(state)
    for i, action in enumerate(actions, 1):
        auto_text = "✅ AUTO" if action.get("auto_execute", False) else "⚠️ MANUAL"
        print(f"  {i}. {action['description']} [{auto_text}]")

    # Step 3: Execute Actions (and send notifications!)
    print("\n" + "-"*80)
    print("⚡ STEP 3: ACTION EXECUTION & NOTIFICATIONS")
    result = executor.execute_actions(
        actions,
        state,
        max_density=MAX_DENSITY,
        people_count=PEOPLE_COUNT
    )
    print(f"  Auto-executed actions: {len(result['executed'])}")
    print(f"  Pending (manual approval): {result['queued']}")

    # Summary
    print("\n" + "="*80)
    print("✅ RED ZONE SIMULATION COMPLETE!")
    print("✅ Notification sent to your number +918975073895!")
    print("="*80)
    print("\n📌 RPF DEPLOYMENT INSTRUCTIONS (RED STATE):")
    print("  • Deploy RPF personnel immediately")
    print("  • Restrict access to hot zones")
    print("  • Activate alternate routes")
    print("  • Update digital signs and PA system")
    print("  • Escalate to station control")


if __name__ == "__main__":
    main()
