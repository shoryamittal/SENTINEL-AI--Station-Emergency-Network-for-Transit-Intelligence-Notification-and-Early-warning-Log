# Judge scorecard

| Criterion | What the judge should notice | Demo evidence | Code/test evidence | Speaker sentence |
| --- | --- | --- | --- | --- |
| Task implementation | Real detection, crowd-state reasoning, warning chain | Camera/video through incident | 96 passing tests; verifier PASS | “This is a full local warning chain, not a detector screen.” |
| Task complexity | CV, temporal signals, state machines, persistence, recovery | Grid, L/A/R, outage/recovery | Runtime, persistence, sync qualification | “The risk and continuity problems are solved together.” |
| Technical execution | System is intentionally broken and safely recovers | WAN-off then same UUID sync | End-to-end and timeout/recovery tests | “Only remote synchronization is deferred.” |
| Innovation and creativity | L/A/R plus local-first safety architecture | Explain why risk changes | Scenario and continuity modules | “Headcount can hide local danger.” |
| Functionality and reliability | Camera/video → incident → outage → recovery | Live dashboard, ACK, recovery | 96 tests; canonical verifier | “The Internet failed; the warning chain did not.” |
| Documentation and presentation | Reproducible, honest technical narrative | Pitch, demo checklist, Q&A | Architecture and qualification docs | “Every claim is paired with a visible proof or stated boundary.” |
| Architecture | Safety Plane separated from Continuity Plane | Diagram and local-first flow | `IncidentJournal`, `SyncWorker`, metrics | “Connectivity is not on the safety critical path.” |
| Code quality | Clear contracts and isolated responsibilities | Explain component boundaries | Tests and verifier | “The runtime, journal, sync worker, and dashboard have explicit roles.” |
| User experience | Hotspot, explanation, response, acknowledgement | Operator dashboard | Stale-risk and dashboard tests | “Operators see why the state changed, not only a colour.” |
| Scalability | Edge-horizontal direction | Camera-group diagram | Documented production path | “Scale by camera groups with compact incident metadata.” |
| Technical sophistication | UUID identity, idempotency, auth isolation, restart recovery | Same UUID and independent ACK | Qualification/auth/local-recovery tests | “Ambiguous delivery failures replay safely with the same identity.” |

## Show, don’t tell

- Do not say “we have persistence”; show event UUID and local status.
- Do not say “it works offline”; turn Wi-Fi off or explicitly use the documented deterministic proof.
- Do not say “we avoid duplicates”; show the same UUID and remote count of one.
- Do not say “it is robust”; show recovery.
- Do not say “it is explainable”; point to L/A/R, scenario, and “Why This State?”.

## Language discipline

Use: prototype, decision support, relative occupancy, risk precursor, failure-tolerant, local-first, edge processing, immutable event identity, idempotent recovery.

Avoid: perfect, 100% safe, guaranteed, prevents stampedes, railway certified, production ready, our cloud, exact density.
