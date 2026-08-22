# SENTINEL AI — Security and Privacy Standard

**Status:** Minimum security baseline  
**Scope:** CCTV, station integrations, dashboard, notifications, logs, model artifacts, and operational data

## 1. Security Goals

SENTINEL AI should protect:

- camera credentials
- API keys
- operator accounts
- station network details
- notification contacts
- configuration
- audit logs
- model integrity
- any stored video or images

The project should collect the minimum passenger-related data necessary for crowd-safety functions.

## 2. No Secrets in Git

Never commit:

```text
SMS API keys
email passwords
database passwords
RTSP usernames/passwords
private station IP addresses if sensitive
JWT/session secrets
private phone numbers
operator credentials
cloud credentials
```

The repository already provides `.env.example`; it should contain placeholders only.

## 3. Remove Personal Contact Data From Tests

The current comprehensive test code contains a real-looking phone number directly in test messages. Replace such values with:

```text
TEST_PRIMARY_CONTACT=+910000000000
```

or a mocked notification provider.

Automated CI must never send real emergency alerts.

## 4. Secret Loading

Production-like deployments should load secrets from environment variables or an approved secret manager.

Code should use:

```text
FAST2SMS_API_KEY
SMTP_PASSWORD
RTSP_USERNAME
RTSP_PASSWORD
DATABASE_URL
```

and redact them from logs.

## 5. CCTV Privacy Principle

Default design preference:

> Process live video in memory and store aggregate crowd metrics rather than raw identifiable footage unless storage is explicitly required.

Possible aggregate outputs:

- people count
- zone density
- heatmap
- risk state
- prediction
- timestamps

## 6. Video Retention

If raw frames/video are stored:

- define purpose
- define retention duration
- limit access
- encrypt storage where appropriate
- record access
- automatically delete expired data

Do not create an indefinite raw-video archive by default.

## 7. Identity Minimization

The MVP does not require face recognition or passenger identity to perform crowd density monitoring.

Do not add:

- facial recognition
- biometric identification
- person-name databases
- cross-camera identity tracking tied to real identities

unless there is a separate lawful, reviewed requirement.

## 8. Dashboard Access

A production/pilot dashboard should require authentication.

Recommended roles:

```text
Viewer          read-only monitoring
Operator        acknowledge alerts and execute approved actions
Administrator   configuration/user management
Auditor         read audit history
```

Use least privilege.

## 9. Human Action Audit

Record:

- alert created
- recommendation generated
- operator viewed
- operator acknowledged
- operator approved/overrode
- notification sent/failed

Audit events should be append-oriented and timestamped.

## 10. Network Design

Prefer station-local processing where possible.

Recommended separation:

```text
camera network
edge inference service
operator dashboard network
external notification gateway
```

Do not expose RTSP cameras directly to the public internet.

## 11. API Security

If Flask/REST APIs are exposed:

- bind only to required interfaces
- authenticate requests
- validate input
- rate-limit sensitive endpoints
- use TLS outside a trusted local-only environment
- do not return internal stack traces to clients
- restrict CORS

## 12. File Upload Security

The current dashboard allows a video-file workflow. Validate uploads:

- allowed extension is not sufficient by itself
- enforce size limits
- store with generated names
- never execute uploaded content
- delete temporary files after use
- prevent path traversal

## 13. Model Integrity

Treat `.pt` weights as executable-like supply-chain artifacts.

Maintain:

```text
model source
expected checksum
version
approved location
```

Do not automatically replace production weights from an unverified source.

## 14. Dependency Security

Current dependencies are expressed with broad `>=` ranges. For deployment reproducibility and supply-chain control:

- use a known-good locked environment
- review dependency updates
- run vulnerability scanning where available
- separate dev-only packages from runtime packages

## 15. Logging Rules

### Allowed

- camera ID
- station zone ID
- counts/densities
- risk states
- latency
- provider status
- incident IDs

### Avoid

- API keys
- passwords
- full RTSP URLs with credentials
- personal phone numbers
- raw access tokens
- unnecessary passenger images

## 16. Notification Security

Emergency notifications should include enough information to act but not unnecessary sensitive data.

Recommended payload:

```text
Station
Zone
Risk state
Timestamp
Current/predicted density
Recommended action
Incident ID
```

## 17. Offline Resilience

Local safety monitoring should not require external cloud connectivity.

External notification outages must be visible to the operator so a failed SMS is not mistaken for a delivered alert.

## 18. Security Incident Response

If a secret is accidentally committed:

1. revoke/rotate it immediately
2. remove it from current code
3. assess repository history exposure
4. rotate dependent credentials
5. document incident

Deleting the line in a later commit is not sufficient if the secret remains valid in Git history.

## 19. Privacy-by-Design Checklist

Before adding a new sensor or integration, ask:

- Is this data necessary for crowd safety?
- Can aggregate data solve the problem?
- How long is it retained?
- Who can access it?
- Is it transmitted externally?
- Is there a deletion policy?
- Does the feature materially increase identification capability?

## 20. Release Security Gate

No demo/pilot release should ship until:

- no real secrets are committed
- no real test phone numbers are hard-coded
- `.env` is ignored
- dashboard authentication decision is documented
- API exposure is documented
- raw-video retention is documented
- notification failures are visible
- model checksum/version is recorded
- dependency set is reproducible

