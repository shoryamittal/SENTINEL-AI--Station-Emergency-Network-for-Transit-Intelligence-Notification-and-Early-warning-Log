#!/usr/bin/env python3
"""
PREEMPT AI - System Verification Script
Verifies all components are properly installed and configured
"""

import sys
import os
from pathlib import Path


def print_header(text):
    """Print formatted header."""
    print("\n" + "="*70)
    print(f"  {text}")
    print("="*70)


def print_success(text):
    """Print success message."""
    print(f"[OK] {text}")


def print_error(text):
    """Print error message."""
    print(f"[X] {text}")


def print_info(text):
    """Print info message."""
    print(f"  -> {text}")


def check_python_version():
    """Check Python version."""
    print_header("Python Version Check")
    version = sys.version_info
    print_info(f"Python {version.major}.{version.minor}.{version.micro}")
    
    if version.major >= 3 and version.minor >= 8:
        print_success("Python version is compatible")
        return True
    else:
        print_error("Python 3.8+ required")
        return False


def check_dependencies():
    """Check required dependencies."""
    print_header("Dependency Check")
    
    required = [
        ("cv2", "opencv-python"),
        ("numpy", "numpy"),
        ("ultralytics", "ultralytics"),
        ("torch", "torch"),
        ("matplotlib", "matplotlib"),
        ("pandas", "pandas"),
        ("flask", "flask"),
        ("streamlit", "streamlit"),
    ]
    
    all_ok = True
    for module_name, package_name in required:
        try:
            __import__(module_name)
            print_success(f"{package_name} installed")
        except ImportError:
            print_error(f"{package_name} NOT installed")
            all_ok = False
    
    return all_ok


def check_project_structure():
    """Check project structure."""
    print_header("Project Structure Check")
    
    required_dirs = [
        "src/core",
        "src/config",
        "docs",
    ]

    required_files = [
        "main.py",
        "src/__init__.py",
        "src/core/__init__.py",
        "src/core/camera_feed.py",
        "src/core/crowd_density.py",
        "src/core/occupancy_mapping.py",
        "src/core/flow_simulation.py",
        "src/core/prediction.py",
        "src/core/classification.py",
        "src/core/action_executor.py",
        "src/core/notifications.py",
        "src/core/railway_integration.py",
        "src/core/monitor.py",
        "src/config/config.ini",
        "requirements.txt",
        "README.md",
        "QUICKSTART.md"
    ]
    
    all_ok = True
    
    for directory in required_dirs:
        if Path(directory).exists():
            print_success(f"Directory: {directory}/")
        else:
            print_error(f"Missing: {directory}/")
            all_ok = False
    
    for file in required_files:
        if Path(file).exists():
            print_success(f"File: {file}")
        else:
            print_error(f"Missing: {file}")
            all_ok = False
    
    return all_ok


def check_modules():
    """Check if all modules can be imported."""
    print_header("Module Import Check")
    
    sys.path.insert(0, str(Path.cwd()))
    
    modules = [
        ("CameraFeed", "src.core.camera_feed"),
        ("CrowdDensityAnalyzer", "src.core.crowd_density"),
        ("OccupancyMapper", "src.core.occupancy_mapping"),
        ("FlowSimulator", "src.core.flow_simulation"),
        ("DensityPredictor", "src.core.prediction"),
        ("SituationClassifier", "src.core.classification"),
        ("ActionExecutor", "src.core.action_executor"),
        ("ContinuousMonitor", "src.core.monitor"),
    ]
    
    all_ok = True
    
    for class_name, module_name in modules:
        try:
            module = __import__(module_name, fromlist=[class_name])
            getattr(module, class_name)
            print_success(f"{class_name} from {module_name}")
        except (ImportError, AttributeError) as e:
            print_error(f"{class_name} from {module_name}: {e}")
            all_ok = False
    
    return all_ok


def check_config():
    """Check configuration file."""
    print_header("Configuration Check")
    
    config_file = Path("src/config/config.ini")
    
    if not config_file.exists():
        print_error("config.ini not found")
        return False
    
    try:
        import configparser
        config = configparser.ConfigParser()
        config.read(config_file)
        
        sections = ["CAMERA", "YOLO", "DENSITY", "CLASSIFICATION", "MONITORING", "PREDICTION"]
        
        for section in sections:
            if section in config:
                print_success(f"Section: [{section}]")
            else:
                print_error(f"Missing section: [{section}]")
                
        return True
        
    except Exception as e:
        print_error(f"Config parsing error: {e}")
        return False


def main():
    """Run all checks."""
    print("\n")
    print("+" + "="*68 + "+")
    print("|" + " "*15 + "PREEMPT AI SYSTEM VERIFICATION" + " "*17 + "|")
    print("+" + "="*68 + "+")
    
    checks = [
        ("Python Version", check_python_version),
        ("Dependencies", check_dependencies),
        ("Project Structure", check_project_structure),
        ("Configuration", check_config),
        ("Modules", check_modules),
    ]
    
    results = {}
    
    for check_name, check_func in checks:
        try:
            results[check_name] = check_func()
        except Exception as e:
            print_error(f"{check_name} check failed: {e}")
            results[check_name] = False
    
    print_header("VERIFICATION SUMMARY")
    
    for check_name, result in results.items():
        status = "PASSED" if result else "FAILED"
        symbol = "[OK]" if result else "[X]"
        print(f"{symbol} {check_name}: {status}")
    
    all_passed = all(results.values())
    
    print("\n" + "+" + "="*68 + "+")
    if all_passed:
        print("SUCCESS: ALL CHECKS PASSED - System is ready to run!")
        print("+" + "="*68 + "+")
        print("\nTo start the system:")
        print("  python main.py")
        print("\nFor help:")
        print("  python main.py --help")
        print("\n" + "+" + "="*68 + "+")
        return 0
    else:
        print("FAILURE: SOME CHECKS FAILED - Please fix the issues above")
        print("+" + "="*68 + "+")
        return 1


if __name__ == "__main__":
    sys.exit(main())
