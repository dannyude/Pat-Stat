# Security

PatStat handles patient health information (PHI). This document covers the design choices, code paths, and patterns that protect that data.

For runbook-level concerns (test DB safety, env vars), see [Operations](operations.md).
For why the API returns specific status codes, see [API Reference → Standard Error Shapes](api.md#standard-error-shapes).

---

## Authentication & Sessions

### JWT model

| Token | TTL | Server-side state | Used at |
|---|---|---|---|
| `access` | 30 min | None — stateless JWT | Every authenticated request (`Authorization: Bearer …`) |
| `refresh` | 7 days | `jti` tracked in Redis DB 2 | `/auth/refresh` and `/auth/logout` only |

**Why this split:** the access token is small, fast to validate (no DB hit), and short-lived enough that a leak has limited blast radius. The refresh token is the longer-lived credential, but its `jti` is recorded in Redis — revoking is one `DEL` away. `/auth/logout-all` revokes every key under `refresh:{user_id}:*`.

### Password hashing

Bcrypt via `passlib`. The `deprecated="auto"` flag means if bcrypt's parameters are ever upgraded, old passwords get re-hashed transparently on next login.

### Status-code semantics

Per RFC 7235:

| Scenario | Code | `WWW-Authenticate` header |
|---|---:|---|
| Missing or invalid auth | **401** | ✅ `Bearer` |
| Authenticated, role not permitted | **403** | (no header) |

The 401 + header pattern matters for non-browser clients: healthcare interop tools (FHIR, third-party SSO) branch on `WWW-Authenticate` to decide whether to retry with credentials.

---

## Authorisation Model

### Role-based access control

```
super_admin     → platform-wide; not tied to a hospital; hard cap of 3
admin           → manages one hospital (creates staff, links family)
doctor          → admits patients, posts updates, manages care teams
nurse           → posts updates, writes notes, views assigned patients
family          → read-only access to linked patients only
```

Every protected endpoint declares its required roles via `require_roles(...)`. The `require_super_admin`, `require_admin`, `require_clinical`, and `require_doctor` shortcuts are pre-computed and used widely. New endpoints must pick one of these at definition time.

### Hospital isolation

Every read query that touches `Admission`, `ClinicalUpdate`, `EmergencyFlag`, `ShiftHandover`, `StaffNote`, or any patient-scoped resource adds `WHERE hospital_id = current_user.hospital_id`. This is enforced in:

- `src/api/v1/patient_helpers.py:get_active_admission`
- `src/api/v1/patients.py` (`list_patients`, `get_patient`, etc.)
- All emergency-flag, shift-handover, and clinical-update list queries

Pinned by tests `test_patients_edge_cases.py::TestGetSinglePatient::test_get_patient_from_other_hospital_returns_404` and similar.

### Family-link authorisation

Family users see only patients they're linked to via `FamilyPatientLink`. Two layers:

1. **Explicit auth check** — `assert_family_link_or_404` in `src/domains/family/services.py` runs at the top of every family-scoped endpoint. Raises 404 (uniform message) if no link exists.
2. **Defense-in-depth JOIN** — the data query also joins through `family_patient_links` so even if the explicit check were dropped, the underlying SQL still wouldn't return cross-family rows.

---

## OWASP Non-Disclosure Pattern

Several endpoints can fail with either *"resource doesn't exist"* or *"resource exists but you can't see it."* The HTTP response must be **byte-identical** in both cases — otherwise an attacker can enumerate IDs by comparing responses.

**The rule:** uniform 404 with the same `detail` string. No "for this account" qualifier, no "not authorized" wording, no hints.

| Endpoint | Both branches return |
|---|---|
| `GET /patients/{id}` | `404 {"detail": "Patient or active admission not found"}` |
| `GET /family/me/patients/{id}/overview` | `404 {"detail": "Patient not found"}` |
| `GET /family/me/patients/{id}/updates` | `404 {"detail": "Patient not found"}` |
| `GET /family/me/patients/{id}/mobile-dashboard` | `404 {"detail": "Patient not found"}` |

Pinned by `tests/test_family_dashboard.py::TestPatientUpdates::test_updates_for_unknown_patient_returns_404` — the test asserts the response is identical for a fake UUID and an unowned-but-real patient ID.

References:
- [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html)
- [OWASP API Security Top 10 — Broken Object Property Level Authorization](https://owasp.org/API-Security/editions/2023/en/0xa3-broken-object-property-level-authorization/)

---

## WebSocket Security

WS auth happens at the application layer (no built-in browser cookie auth), via the first JSON frame after the upgrade.

### Connection-time checks

1. JWT signature + expiry valid
2. `type === "access"` (refresh tokens rejected)
3. User exists and `is_active`
4. If role is `family`: a `FamilyPatientLink` to this `patient_id` exists
5. (Implicit) Hospital scope — clinical staff can only connect to patients in their `hospital_id`

### Periodic re-validation

The WS connection is long-lived (often hours). We can't trust the handshake-time token forever:

- A background asyncio task wakes every 30 s during the live phase
- Compares wall-clock to the JWT's `exp` claim
- Closes the socket with code `1008` and reason `"Token expired"` once expired
- Frontend handles 1008 by refreshing the access token and reconnecting

Without this, a stolen-but-valid 30-min access token could keep a WS open indefinitely as long as nothing dropped the connection.

---

## PHI in Push Notifications

Apple/Google do **not** contractually guarantee end-to-end encryption of FCM/APNs payloads, and the visible body is rendered on a locked screen. Therefore:

| Layer | Carries PHI? | What gets sent |
|---|:---:|---|
| FCM `notification.title` / `notification.body` (visible) | **No** | `"Pat-Stat: Urgent update"` / `"Tap to open Pat-Stat for details."` |
| FCM `data` payload (encrypted in transit, app-rendered after unlock) | Yes | Patient name, status, update_id, event_kind |
| Stored `notification_logs.title` (in-app bell) | Limited | Patient first name + status (e.g. `"John — Critical"`) |
| Stored `notification_logs.body` (in-app bell) | **No** | `"Tap to view the latest update."` (generic; UI fetches detail via `update_id`) |
| Source of truth for clinical detail | Yes | `clinical_updates.note` — gated by RBAC + hospital scope |

This split protects the OLTP audit table from being a future PHI export risk: if `notification_logs` gets shipped to a BI/analytics warehouse, the clinical free-text doesn't go with it. The full clinical content lives in `clinical_updates`, behind the same access controls as the rest of the chart.

---

## Sanitised 5xx Responses

The global exception handler in `src/main.py` catches any `Exception` not handled by FastAPI's built-ins (HTTPException, RequestValidationError, RateLimitExceeded). Behaviour:

1. **Log** with full traceback (`logger.exception(...)`) — server-side only
2. **Return** `500 {"detail": "An unexpected error occurred. Our engineering team has been notified."}`
3. **Never** include `str(exc)` in the response — exception messages can leak table names, stack frames, secrets-in-error-strings

Pinned by `tests/test_global_exception_handler.py::test_unhandled_typeerror_returns_500_with_safe_message` — the test asserts the raw exception text *never* appears anywhere in the response body.

---

## Rate Limiting

SlowAPI middleware applies per-IP rate limits:

| Bucket | Default | Used by |
|---|---|---|
| `API_RATE_LIMIT_DEFAULT` | 120 / minute | All `/api/v1/*` endpoints (catch-all) |
| `AUTH_RATE_LIMIT` | 100 / minute | Login, refresh — slows brute-force enumeration |
| `WRITE_RATE_LIMIT` | 30 / minute | Clinical updates, emergency flags, shift handovers — protects against runaway scripts |

Rate-limited responses return 429 with a `Retry-After` header.

---

## Test Database Safety Guard

Pytest's session-scoped fixture runs `DROP SCHEMA public CASCADE` to start each test session from a clean schema. Without protection, anyone running `pytest` with the wrong env var pointing at the dev or production database would obliterate every table — including the bootstrapped super-admin.

`tests/conftest.py` raises a `RuntimeError` at collect time (before any test or fixture runs) unless `DATABASE_URL_SYNC` contains one of `test`, `_test`, `patstat_test`. The error message is loud and includes the override command. See `tests/conftest.py:_TEST_DB_MARKERS`.

---

## Super-admin Cap

The platform enforces a hard cap of **3 super-admin accounts**, set at `MAX_SUPER_ADMINS = 3` in `src/domains/backoffice/services.py`. Both the bootstrap CLI (`scripts/seed_super_admin.py`) and the API (`POST /api/v1/backoffice/super-admins`) check this before inserting.

The cap is deliberately small. Super-admins have platform-wide authority — every additional account is a credential surface.

---

## Reporting Vulnerabilities

If you find a security issue, please **do not** open a public GitHub issue. Email the maintainer (or your organisation's security contact) with:

1. A description of the issue
2. Steps to reproduce (or a proof-of-concept)
3. Impact (what could an attacker achieve?)
4. Affected version / commit SHA

We aim to respond within 48 hours and have a fix landed within 14 days for high-severity issues.

---

## Security Tests

| Concern | Test file | Coverage |
|---|---|---|
| 401 vs 403 status-code semantics | `tests/test_auth.py`, `tests/test_auth_extended.py`, `tests/test_auth_edge_cases.py` | Missing auth → 401; wrong role → 403 |
| Hospital scope | `tests/test_patients_edge_cases.py` | Cross-hospital read/write returns 404 |
| Family-link auth | `tests/test_family_dashboard.py` | Cross-family read returns 404 with uniform message |
| WS token watchdog | `tests/test_ws_token_watchdog.py` | Socket closes 1008 when access token expires |
| PHI sanitisation | `tests/test_notification_delivery_log.py::TestPHISanitisation` | Patient name & note never in FCM `title`/`body` |
| Stored body sanitisation | `tests/test_notification_delivery_log.py::TestStoredBodyDoesNotCarryClinicalNote` | Clinical free-text never in `notification_logs.body` |
| Sanitised 500 responses | `tests/test_global_exception_handler.py` | Raw exception text never reaches client |
| Test DB guard | manual — running `pytest` with non-test URL refuses to collect | Documented behaviour |

Run `.\scripts\test.ps1 -k "security or phi or auth or hospital or family"` to run just the security-relevant tests.
