# Judge Q&A bank

## AI and computer vision

**Isn’t this just YOLO?**  
No. YOLO is the person-detection layer. SENTINEL then uses 4×6 spatial occupancy, an adaptive baseline, L/A/R dynamics, scenario classification, severity, and a recommended response.

**Why not just crowd density?**  
Raw density cannot distinguish a crowded stable scene from abnormal accumulation or redistribution. SENTINEL uses relative spatial and temporal change to make that distinction.

**Do you measure people/m²?**  
No. Current occupancy is relative. Geometrically calibrated people/m² requires camera calibration and is future work.

**How accurate is it?**  
We do not claim an unmeasured percentage. Current qualification validates functional and system behavior; station-specific accuracy claims require labelled railway datasets and calibration.

**Can it predict stampedes?**  
No guarantee is claimed. It detects explainable crowd-risk precursors such as accumulation, redistribution, and local bottlenecks.

**Can it detect counterflow?**  
Current R measures spatial redistribution. Full tracker-based directional Flow Conflict/counterflow analysis is future work.

## Distributed systems and reliability

**Why SQLite?**  
It is a simple local durable journal suited to the prototype’s edge-first continuity requirement. WAL mode supports short transactional writes and concurrent dashboard reads.

**What happens when Internet fails?**  
Camera processing, AI, SQLite persistence, local warning, and acknowledgement continue. The durable event remains `SYNC_PENDING` until remote work can resume.

**What happens on restart?**  
Interrupted sync is requeued with the same UUID. A locally `PERSISTED` event is recovered from its stored payload: current incidents are presented locally; historical incidents become handled history without a new audible emergency.

**What if the server saves an event but its response is lost?**  
SENTINEL retries the same UUID. The qualification backend returns `ALREADY_ACCEPTED` for the same canonical payload, leaving one remote row.

**How do you prevent duplicates? Why immutable UUIDs?**  
The UUID is the SQLite primary key and remote idempotency key. One incident episode keeps that identity through retry, outage, restart, and recovery.

**What if the same UUID arrives with a different payload?**  
The qualification backend returns `IDEMPOTENCY_CONFLICT` and preserves the original canonical payload.

**What happens if the application dies during `SYNCING`?**  
Startup recovery returns the same event to `SYNC_PENDING`; remote idempotency makes replay safe.

**Does acknowledgement affect synchronization?**  
No. Local acknowledgement is a separate lifecycle. `LOCAL_ACKNOWLEDGED + SYNC_PENDING`, `AUTH_BLOCKED`, and `SYNCED` are all valid.

## Security and operations

**What happens if credentials expire?**  
Connectivity may remain ONLINE while sync becomes `AUTH_BLOCKED`. Camera, AI, SQLite, and local alerts continue, and blind retry stops until credentials are refreshed.

**Is this production-secure?**  
No. The prototype has safe local-first defaults, but production needs TLS, operator IAM and authorization, secret management, auditing, and operator-controlled infrastructure.

## Railway and domain

**Are these Indian Railways thresholds?**  
No. They are prototype calibration parameters, not certified railway thresholds.

**Can it replace station staff?**  
No. It is decision support for authorized operators, not operational authority.

**Can it prevent every crowd accident?**  
No. It provides risk signals and continuity mechanisms; it does not guarantee outcomes.

**What happens at very crowded stations?**  
The system still distinguishes relative spatial and temporal behavior, but site-specific calibration, validated datasets, and operational procedures are required for deployment.

**How would it integrate with existing CCTV?**  
The edge input boundary is a configured camera/video source. Production integration needs site validation, camera calibration, and operator-controlled deployment.

**What about privacy?**  
The prototype uses person detection and local processing; production must define retention, access control, notice, and privacy governance with the deploying operator.

## Product and scale

**Why would Railways deploy this?**  
To give operators an explainable, resilient local risk view when network conditions are unreliable—not as a replacement for their authority.

**What infrastructure is required?**  
An edge host near a camera group, local storage, and an operator-controlled remote service if centralized incident synchronization is required.

**How does it scale?**  
Camera groups or station zones map to local edge SENTINEL nodes that send compact incident metadata to a central operator-controlled service. Hundreds-camera validation is not yet claimed.

**Why edge computing rather than stream everything to cloud?**  
Local processing keeps the immediate safety path available during a WAN outage and can limit transport to compact incident metadata.

**What is the cost advantage?**  
The architecture can avoid making uninterrupted video uplink a prerequisite for local warning. Cost must be measured per deployment; no cost saving is claimed here.

**What is the deployment path?**  
Start with station-specific camera calibration and labelled validation, then edge-node operation, security/identity controls, remote-service integration, monitoring, retention policy, and formal operational approval.

## Questions designed to catch overclaims

**Is your localhost backend your cloud?**  
No. It is a reference qualification backend.

**Are you measuring density?**  
Relative occupancy, not calibrated people/m².

**Can your AI guarantee no stampede?**  
No.

**Are recommendations approved by Indian Railways?**  
No. They are prototype decision support.

**Why should I believe simulation?**  
Simulation changes only the frame source. The real YOLO/runtime/grid/L/A/R/scenario pipeline processes the video.

**What if the video was chosen to make the AI look good?**  
The scenario clip is a demonstration input, not a statistical accuracy benchmark. Deployment requires validated railway datasets.
