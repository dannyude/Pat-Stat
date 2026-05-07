# Architecture

This document covers the high-level shape of PatStat: components, request flow, the real-time pipeline, and the data model.

For runbook-level operations (env vars, migrations, backup), see [Operations](operations.md).
For security and compliance details, see [Security](security.md).

---

## System Overview

```text
                ┌─────────────┐  ┌────────────┐  ┌───────────────┐
                │ Family App  │  │ Doctor App │  │ Admin Panel   │
                └──────┬──────┘  └─────┬──────┘  └──────┬────────┘
                       │               │                │
                       └───────┬───────┘                │
                               │   REST + WebSocket     │
                       ┌───────▼────────────────────────▼──┐
                       │            FastAPI                 │
                       │                                    │
                       │   /api/v1/auth      Authentication │
                       │   /api/v1/patients  Patient CRUD   │
                       │   /api/v1/dashboard Stats & Alerts │
                       │   /api/v1/family    Family Portal  │
                       │   /api/v1/backoffice Super-admin   │
                       │   /ws/patient/{id}  Live Feed      │
                       └──────┬─────────────────┬───────────┘
                              │                 │
                    ┌─────────▼───┐   ┌─────────▼──────────────┐
                    │ PostgreSQL  │   │ Redis                  │
                    │             │   │  DB 0: pub/sub + cache │
                    │  patients   │   │  DB 1: Celery broker   │
                    │  admissions │   │  DB 2: refresh tokens  │
                    │  users      │   └─────────┬──────────────┘
                    └─────────────┘             │
                                        ┌───────▼──────────────┐
                                        │  Celery Worker       │
                                        │   FCM push → Family  │
                                        │   Email   → Invites  │
                                        │   Cleanup → Stale    │
                                        │   Reconciler → Audit │
                                        └──────────────────────┘
```

PatStat is a single FastAPI process backed by Postgres + Redis, with Celery workers handling out-of-band work (push notifications, emails, scheduled cleanup). The deployment unit is one hospital — multi-tenant boundaries are enforced at the application layer via `hospital_id` scoping on every query.

---

## Real-Time Pipeline

When a doctor posts a clinical update, this is the exact execution path:

```text
POST /api/v1/patients/{id}/updates
  │
  ├─ 1. Write ClinicalUpdate row to PostgreSQL
  ├─ 2. Update Admission.status (if changed)
  ├─ 3. Redis PUBLISH patient:{id}:updates
  │      └─ All WebSocket subscribers receive the event instantly
  └─ 4. Celery task: notify_family_of_update
         ├─ Per linked family member, evaluate notification policy
         │   (critical → push, important → push, routine → in-app only)
         ├─ Insert NotificationLog row(s)
         └─ Subtask: _send_fcm_multicast
              ├─ Firebase FCM multicast to registered devices
              ├─ Persist delivery outcome to NotificationLog.delivery_status
              └─ Prune device tokens FCM reports as UNREGISTERED
```

The two channels (live WebSocket pub/sub + offline FCM push) are intentionally independent. The WS layer reaches anyone whose app is open *right now*; the FCM layer reaches everyone else. Family members who are offline at update time receive the push when the device reconnects (FCM holds the message). When they next open the app and connect a fresh WebSocket with `last_synced_at`, the catch-up replay fills in any events they missed during the offline window.

For the security guarantees of the FCM payload (no PHI in title/body), see [Security → PHI policy](security.md#phi-in-push-notifications).

---

## Roles

| Role | Scope |
|---|---|
| `super_admin` | Platform-level, not tied to a hospital. Hard cap: 3 accounts. |
| `admin` | Manages one hospital, creates staff, links families. |
| `doctor` | Admits patients, posts updates, manages care teams. |
| `nurse` | Posts updates, writes notes, views assigned patients. |
| `family` | Read-only access to linked patients only. |

Role denial returns 403; missing/invalid auth returns 401 (per RFC 7235).

---

## Patient Statuses

`Being Monitored` → `Stable` → `Getting Better` → `Discharged`

`Being Monitored` → `Critical` (the alarming transition — always pushes to family regardless of OS quiet hours)

---

## Data Model

### Core Entities and Relationships

```text
Hospital 1──* User (staff, admin, family)
    │
    └──* Admission ──1 PatientProfile
            │
            ├──* ClinicalUpdate (status + vitals)
            ├──* StaffNote (internal, hidden from family)
            ├──* EmergencyFlag (high / critical priority)
            ├──* ShiftHandover
            └──* CareAssignment (doctor, nurse)

PatientProfile 1──* FamilyPatientLink *──1 User (family)
User 1──* DeviceToken (FCM)
User 1──* NotificationLog
```

### Key Design Notes

- **`PatientProfile` vs `Admission`** — A patient is a long-lived person; an admission is one episode of care. A patient can have multiple admissions over time. Most operations are scoped to the *active* admission (`discharged_at IS NULL`).
- **`FamilyPatientLink` is many-to-many** — A family user can be linked to multiple patients (e.g. parent of two siblings in the same hospital), and a patient can have multiple linked family members.
- **`StaffNote` is internal** — Routes that read notes apply role filters so family users never see them. Pinned by tests in `tests/test_staff_notes.py`.
- **`NotificationLog`** powers the in-app bell *and* serves as the FCM delivery audit log. Every push attempt persists `delivery_status` (queued / sent / failed / no_devices / skipped_routine / unknown_outcome).

For the full table schema, run `alembic upgrade head` and inspect via pgAdmin or `psql \d`.

---

## Component-Level Boundaries

| Component | Responsibility | Won't do |
|---|---|---|
| **FastAPI handlers** (`src/api/v1/*`) | HTTP routing, request validation, RBAC enforcement, calling services | Business logic, ORM queries inline |
| **Domain services** (`src/domains/*/services.py`) | Business rules, transactional logic | HTTP concerns, response formatting |
| **Domain models** (`src/domains/*/models/`) | SQLAlchemy ORM mappings | Behaviour or business rules |
| **Notification policy** (`src/domains/notifications/policy.py`) | Pure decisions: which events push, which don't | Side effects, DB access |
| **Notification dispatch** (`src/domains/notifications/dispatch.py`) | Thin shim from HTTP → Celery | Knowing about FCM, Postgres, or task implementation |
| **Celery tasks** (`src/tasks/`) | Out-of-band work (FCM, email, cleanup) | HTTP request context |

This layering is what makes the test suite tractable: 75% of the tests don't need Celery or Redis at all; only the dispatch tests do.
