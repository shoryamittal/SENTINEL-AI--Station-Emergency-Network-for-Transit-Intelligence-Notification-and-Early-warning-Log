from setuptools import setup, find_packages

import os

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

req_file = "requirements-full.txt" if os.path.exists("requirements-full.txt") else ("requirements.txt" if os.path.exists("requirements.txt") else None)
if req_path := req_file:
    with open(req_path, "r", encoding="utf-8") as fh:
        requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]
else:
    requirements = []

setup(
    name="sentinel-ai",
    version="1.0.0",
    author="PREEMPT AI Team",
    description="SENTINEL-AI: Station Emergency Network for Transit Intelligence, Notification, and Early-warning Log - Real-time AI crowd safety monitoring for Indian Railways",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/shoryamittal/SENTINEL-AI--Station-Emergency-Network-for-Transit-Intelligence-Notification-and-Early-warning-Log",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Security",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.8",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "sentinel-ai=main:main",
        ],
    },
    include_package_data=True,
    zip_safe=False,
)
