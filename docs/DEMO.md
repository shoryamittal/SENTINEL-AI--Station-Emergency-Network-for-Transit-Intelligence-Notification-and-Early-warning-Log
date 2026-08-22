# Judge demo guide

## Pre-demo checklist

- [ ] Branch is `main`; working tree is clean
- [ ] `pytest -q tests` and `python verify_system.py` pass
- [ ] Camera is available, or the bundled SIMULATION video is available
- [ ] Qualification backend is started when showing remote proof
- [ ] Dashboard loads; frame ID advances; AI reports healthy
- [ ] Events Lost is zero

## Start the qualification backend

Git Bash / Linux shell:

```bash
python qualification_server.py
```

Windows PowerShell:

```powershell
python qualification_server.py
```

## Start SENTINEL in a second terminal

Git Bash / Linux shell:

```bash
SYNC_ADAPTER_TYPE=HTTP SYNC_ENDPOINT_URL=http://127.0.0.1:5051/api/events python deploy.py
```

Windows PowerShell:

```powershell
$env:SYNC_ADAPTER_TYPE="HTTP"
$env:SYNC_ENDPOINT_URL="http://127.0.0.1:5051/api/events"
python deploy.py
```

Open `http://localhost:5000`. REALITY selects the configured live camera. SIMULATION selects the bundled video and runs it through the same detector and risk pipeline; it is a useful demo backup, not a claim of railway deployment accuracy.

## Three-minute judge flow

| Time | Show | Explain |
| --- | --- | --- |
| 0:00–0:25 | Problem | Headcount alone misses accumulation, local bottlenecks, and redistribution. |
| 0:25–0:55 | Dashboard L/A/R and grid | Explainable signals and hotspot make the warning understandable. |
| 0:55–1:20 | REALITY or SIMULATION scene | Both modes use real YOLO/risk logic; only input changes. |
| 1:20–2:05 | Disable WAN / controlled debug override | Camera and AI remain healthy; local warning and SQLite persist; sync becomes pending; lost events remain zero. |
| 2:05–2:35 | Restore connectivity | Show OFFLINE → RECOVERY → ONLINE and the same UUID becoming `SYNCED`. |
| 2:35–2:50 | Acknowledge warning | Show local ACK while remote status remains independently visible. |
| 2:50–3:00 | Architecture close | Use the final line below. |

For a physical demonstration, disconnect WAN after the dashboard is running; the local safety path continues. `ENABLE_DEBUG_CONNECTIVITY=1` exposes a clearly labelled demo override, but it does not prove physical network loss. Verify remote rows at the qualification backend when demonstrating idempotency.

> Connectivity is a dependency for synchronization, not a dependency for safety. The Internet can fail. The warning chain cannot.
