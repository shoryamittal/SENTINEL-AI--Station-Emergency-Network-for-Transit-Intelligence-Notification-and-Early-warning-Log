# SENTINEL AI — Performance Benchmarks and Real-Time Budget

**Status:** Benchmark specification  
**Important:** Values marked **Target** are proposed engineering goals, not measured repository results.

## 1. Performance Objective

SENTINEL AI is described as a real-time early-warning system and the pitch emphasizes intervention before crowd formation, including a 90-second decision window. That claim should be backed by measured latency, throughput, and alert-delivery data.

The most important metric is not only FPS. It is:

> **capture-to-action latency** — the elapsed time from a frame/event being observed to a usable risk result and operator recommendation being available.

## 2. Metrics That Must Be Measured

### Video pipeline

- camera capture FPS
- processed FPS
- dropped-frame percentage
- frame age at inference start
- inference latency
- density calculation latency
- prediction latency
- risk classification latency
- total capture-to-risk latency

### System

- CPU utilization
- GPU utilization
- RAM usage
- VRAM usage
- process uptime
- camera reconnect time
- model startup time

### Alerting

- risk-to-dashboard latency
- risk-to-local-alert latency
- risk-to-SMS request latency
- SMS provider success/failure
- operator acknowledgement time

## 3. Proposed Latency Budget

The following is a design target for a balanced edge/GPU system and must be validated on actual hardware.

| Stage | Target |
|---|---:|
| Capture + decode | <= 10 ms |
| Resize/preprocess | <= 5 ms |
| Person detection | <= 35 ms |
| Zone/density update | <= 5 ms |
| Short prediction | <= 10 ms |
| Risk classification | <= 2 ms |
| State publication | <= 5 ms |
| **Pipeline target** | **<= 72 ms** |

A device that cannot meet this budget can still be useful if it maintains acceptable capture-to-risk latency using frame sampling and bounded queues.

## 4. Throughput Targets

### Minimum useful real-time target

- processed FPS: >= 10 FPS
- P95 capture-to-risk: <= 250 ms
- frame queue age: <= 500 ms

### Preferred target

- processed FPS: >= 15 FPS
- P95 capture-to-risk: <= 150 ms
- dropped frames: controlled and intentional under overload

### Dedicated GPU target

- processed FPS: >= 25 FPS where hardware permits
- P95 capture-to-risk: <= 100 ms

These are engineering targets, not safety certification criteria.

## 5. 90-Second Intervention Timing

The system should translate the pitch concept into measurable milestones.

Proposed requirement after a platform-change trigger at `T0`:

```text
T0 + 0–1 s     current crowd state updated
T0 + 0–2 s     short-horizon prediction updated
T0 + 0–3 s     candidate bottleneck / diversion computed
T0 + 0–4 s     operator/RPF recommendation displayed
T0 + 0–5 s     local critical alert dispatched if required
Remaining time operator and staff use recommendation before surge develops
```

The exact operational window must be validated against real scenarios; the software should minimize its own share of the delay.

## 6. Benchmark Matrix

Fill this table with measured data.

| Device | Model | Resolution | Input | Processed FPS | P50 latency | P95 latency | RAM | VRAM | Notes |
|---|---|---:|---|---:|---:|---:|---:|---:|---|
| CPU laptop | YOLOv8n | 640x480 | video | TBD | TBD | TBD | TBD | N/A | baseline |
| Edge device | YOLOv8n | 640x480 | RTSP | TBD | TBD | TBD | TBD | TBD | deployment candidate |
| GPU workstation | YOLOv8s | 640x480 | RTSP | TBD | TBD | TBD | TBD | TBD | balanced |
| GPU workstation | YOLOv8m | 1280x720 | RTSP | TBD | TBD | TBD | TBD | TBD | accuracy profile |

## 7. Benchmark Procedure

### A. Warm-up

1. Start the process.
2. Load model.
3. Run at least 100 inference frames before collecting results.
4. Record model load time separately.

### B. Stable run

Run for at least 10 minutes per configuration and record:

- per-stage latency
- FPS
- resource usage
- dropped frames
- state transitions

### C. Stress run

Test:

- 1080p input
- high person count
- multiple simultaneous streams if supported
- slow network/RTSP source
- notification provider timeout
- dashboard connected and disconnected

### D. Failure run

Measure recovery after:

- camera disconnect
- camera freeze
- model exception
- GPU unavailable
- network outage

## 8. Optimization Order

Do not optimize randomly. Use this order:

1. measure end-to-end latency
2. determine largest stage
3. verify accuracy is acceptable
4. reduce wasted frame work
5. reduce model/inference cost
6. optimize visualization
7. optimize non-critical analytics

## 9. High-Value Runtime Optimizations

### Frame sampling

If camera input is 30 FPS but inference can sustain 12 FPS, process the newest available frame rather than building a 30 FPS backlog.

### Resolution control

Inference resolution should be benchmarked. Higher resolution improves some detection cases but increases latency. Store the chosen size per camera profile.

### Conditional visualization

Heatmap compositing and drawing should be optional in headless/production inference. The pipeline should calculate state even if no UI frame is rendered.

### Asynchronous alerts

SMS/email requests must execute outside the inference loop.

### Event-triggered simulation

Do not run heavy digital-twin simulation on every frame unless benchmarking proves it is cheap enough and necessary.

### Model warm-up

Load and warm the model before monitoring is marked healthy.

## 10. Regression Rule

Every performance-affecting pull request should compare against the previous baseline.

Suggested failure criteria:

- >10% P95 latency regression without justified accuracy gain
- >10% processed-FPS regression without justified accuracy gain
- increased dropped-frame backlog
- materially higher memory use causing instability

## 11. Accuracy Must Be Paired With Performance

Every benchmark should include at least one accuracy metric. A faster model that misses critical people/crowd conditions is not an optimization.

Recommended paired metrics:

```text
people count MAE
person detection precision/recall
density MAE
risk-state recall
critical-event recall
false alerts per hour
P95 latency
```

## 12. Benchmark Output Format

Store machine-readable results such as:

```json
{
  "git_commit": "...",
  "device": "...",
  "model": "yolov8n.pt",
  "resolution": "640x480",
  "processed_fps": 0,
  "p50_capture_to_risk_ms": 0,
  "p95_capture_to_risk_ms": 0,
  "people_count_mae": 0,
  "critical_recall": 0
}
```

This allows charts and regression checks to be generated automatically later.

