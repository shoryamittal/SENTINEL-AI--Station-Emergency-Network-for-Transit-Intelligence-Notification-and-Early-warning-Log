# SENTINEL AI Competition Release

Competition release source commit: the commit resolved by annotated tag `sentinel-ai-competition-v1` after final validation.

Baseline before release hygiene: `c0ce487f96e0ebd2d2c104d5cf4e63e2b0da90b3`

## Qualification

- Full automated suite: 96 passed
- `verify_system.py`: PASS
- Product code: feature-frozen
- REALITY mode: supported
- SIMULATION mode: supported
- Local restart recovery: supported
- Operator acknowledgement: supported
- Idempotent recovery sync: supported

This release-hygiene commit does not modify runtime behavior.

## Competition-machine recovery

This repository’s [requirements-competition-lock.txt](../requirements-competition-lock.txt) is the exact snapshot generated on the known-good competition machine/environment. It is not the general development requirements file and must not be treated as cross-platform: regenerate it on the actual competition machine when its OS, Python, or hardware environment differs.

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Git Bash / Linux (use the path present in the environment):

```bash
source .venv/Scripts/activate
# or
source .venv/bin/activate
```

Then install the snapshot:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements-competition-lock.txt
```
