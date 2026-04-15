<p align="center">
  <img src="https://raw.githubusercontent.com/microsoft/fluentui-emoji/main/assets/Stethoscope/3D/stethoscope_3d.png" width="80" alt="PatStat Logo">
</p>

<h1 align="center">P A T S T A T</h1>

<p align="center">
  <em>Real-time patient status tracking and family notification system</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/PostgreSQL-316192?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/Redis-DC382D?style=for-the-badge&logo=redis&logoColor=white" alt="Redis">
  <img src="https://img.shields.io/badge/Celery-37814A?style=for-the-badge&logo=celery&logoColor=white" alt="Celery">
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> |
  <a href="#api-reference">API</a> |
  <a href="#data-model">Data Model</a> |
  <a href="#security">Security</a> |
  <a href="#operations">Operations</a>
</p>

---

PatStat is a hospital-grade backend that lets clinical staff track patient statuses in real time while keeping families informed through push notifications and a dedicated mobile API.

Each hospital runs in an isolated tenant context, and all access paths enforce hospital boundaries.

## Architecture

```text
                    ┌─────────────┐  ┌────────────┐  ┌───────────────┐
                    │ Family App  │  │ Doctor App │  │ Admin Panel   │
                    └──────┬──────┘  └─────┬──────┘  └──────-┬───────┘
                           │               │                 │
                           └───────┬───────┘                 │
                                   │   REST + WebSocket      │
                           ┌───────▼─────────────────────────▼──-┐
                           │            FastAPI                  │
                           │                                     │
                           │   /api/v1/auth      Authentication  │
                           │   /api/v1/patients  Patient CRUD    │
                           │   /api/v1/dashboard Stats and Alerts│
                           │   /api/v1/family    Family Portal   │
                           │   /ws/patient/{id}  Live Feed       │
                           └──────┬─────────────────┬────────────┘
                                  │                 │
                        ┌─────────▼───┐   ┌─────────▼──────────────┐
                        │ PostgreSQL  │   │ Redis                  │
                        │             │   │  DB 0: pub/sub + cache │
                        │  patients   │   │  DB 1: Celery broker   │
                        │  admissions │   │  DB 2: refresh tokens  │
                        │  users      │   └─────────┬──────────────┘
                        └─────────────┘             │
                                            ┌───────▼───────────┐
                                            │  Celery Worker     │
                                            │                    │
                                            │  FCM Push -> Family
                                            │  Email    -> Invites
                                            │  Cleanup  -> Stale data
                                            └────────────────────┘
```

## Real-Time Pipeline

When a doctor posts a clinical update, this is the exact execution path:

```text
POST /api/v1/patients/{id}/updates
  │
  ├─ 1. Write ClinicalUpdate row to PostgreSQL
  ├─ 2. Update Admission.status
  ├─ 3. Redis PUBLISH patient:{id}:updates
  │      └─ All WebSocket subscribers receive the event instantly
  └─ 4. Celery task: notify_family_of_update
         ├─ Create NotificationLog per linked family member
         └─ Firebase FCM multicast to registered devices
```

## Quick Start

### Prerequisites

- Docker and Docker Compose
- A Firebase service account JSON file for push notifications

### 1. Configure environment

```bash
cp .env.example .env
# Edit .env with your database, Redis, JWT, and Firebase settings
```

### 2. Add Firebase credentials

```bash
mkdir secrets
# Place firebase-service-account.json in secrets/
```

### 3. Launch services

```bash
docker compose up --build
```

| Service | URL |
|---|---|
| API | <http://localhost:8000> |
| Swagger UI | <http://localhost:8000/docs> |
| Flower | <http://localhost:5555> |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |

### 4. Database setup

```bash
# Bootstrap the first super admin
python scripts/seed_super_admin.py

# Run migrations
alembic upgrade head
```

### 5. Run without Docker (development)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Terminal 1: API
uvicorn src.main:app --reload --port 8000

# Terminal 2: Worker
celery -A src.tasks.celery_app.celery_app worker --loglevel=info

# Terminal 3: Beat scheduler
celery -A src.tasks.celery_app.celery_app beat --loglevel=info
```

## API Reference

All endpoints are prefixed with `/api/v1`.
Authentication uses `Authorization: Bearer <token>`.

### Authentication (`/auth`)

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| POST | `/login` | Get access and refresh tokens | Public |
| POST | `/refresh` | Rotate access token | Public |
| POST | `/logout` | Revoke refresh token | Bearer |
| POST | `/logout-all` | Revoke all sessions | Bearer |
| GET | `/me` | Current user profile | Bearer |
| PATCH | `/me` | Update profile | Bearer |
| POST | `/change-password` | Change password | Bearer |
| POST | `/device-token` | Register FCM token | Bearer |
| POST | `/register-staff` | Create doctor/nurse/admin user | Admin |

### Patients (`/patients`)

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| GET | `/` | List active patients (searchable) | Clinical, Family |
| POST | `/` | Admit new patient | Doctor, Admin |
| GET | `/{id}` | Patient detail and active admission | Clinical, Family |
| PATCH | `/{id}` | Update patient or admission | Clinical |
| POST | `/{id}/discharge` | Discharge patient | Doctor, Admin |
| GET | `/note-categories` | Available note categories | Clinical |

Search parameters: `q`, `active_only`, `skip`, `limit`

### Clinical Updates (`/patients/{id}/updates`)

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| POST | `/` | Post status update with optional vitals | Clinical |
| GET | `/` | Update timeline (paginated) | Clinical, Family |

Vitals captured: blood pressure, heart rate, temperature, oxygen level

Setting `mark_emergency: true` during update creation also opens an emergency flag.

### Staff Notes (`/patients/{id}/notes`)

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| POST | `/` | Create internal note | Clinical |
| GET | `/` | List notes | Clinical |
| GET | `/{note_id}` | Get note detail | Clinical |

Privacy guard: staff notes are internal and never visible to family users.

### Emergency Flags (`/emergency-flags`)

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| GET | `/` | List flags | Clinical |
| POST | `/` | Raise flag | Clinical |
| GET | `/{id}` | Flag detail | Clinical |
| PATCH | `/{id}/resolve` | Resolve flag | Clinical |
| GET | `/count` | Active flag count | Clinical |

Priority levels: High, Critical

### Dashboard (`/dashboard`)

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| GET | `/summary` | Stats overview (cached 30 seconds) | Clinical |
| GET | `/critical-patients` | Patients in critical state | Clinical |
| GET | `/needs-attention` | No update in more than 12 hours | Clinical |
| GET | `/recent-activity` | Latest activity feed | Clinical |

Summary fields: `my_patients`, `critical_count`, `updates_today`, `needs_attention`

### Shift Handover (`/shift-handovers`)

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| GET | `/` | List handovers | Clinical |
| POST | `/` | Record handover | Clinical |
| GET | `/{id}` | Handover detail | Clinical |

### Staff (`/staff`)

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| GET | `/` | List all staff | Admin |
| GET | `/me` | Current user | Bearer |
| GET | `/doctors` | List doctors | Clinical |
| GET | `/nurses` | List nurses | Clinical |

### Notifications (`/notifications`)

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| GET | `/` | List notifications | Bearer |
| GET | `/unread-count` | Unread count | Bearer |
| PATCH | `/{id}/read` | Mark as read | Bearer |
| POST | `/read-all` | Mark all as read | Bearer |

### Hospitals (`/hospitals`)

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| POST | `/apply` | Submit hospital registration | Public |

### Family Portal

Invitations (`/family/patients/{id}/invites`)

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| POST | `/` | Send invite to family | Admin |
| POST | `/invites/accept` | Accept invite and sign up | Public |

Dashboard (`/family/me/patients`)

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| GET | `/` | List linked patients | Family |
| GET | `/{id}/overview` | Patient overview and care team | Family |
| GET | `/{id}/updates` | Update timeline | Family |
| GET | `/{id}/mobile-dashboard` | Combined mobile view | Family |

### WebSocket (`/ws/patient/{patient_id}`)

Connection flow:

```text
1. Connect to /ws/patient/{patient_id}
2. Send: {"type": "auth", "token": "<access_token>"}
3. Receive: {"type": "connected", "patient_id": "..."}
```

Event types:

- `status_changed`
- `emergency_flag_raised`
- `handover_recorded`

Client example:

```javascript
const ws = new WebSocket(`ws://localhost:8000/ws/patient/${patientId}`);

ws.onopen = () => {
  ws.send(JSON.stringify({ type: "auth", token: accessToken }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.type === "status_changed") {
    console.log(`${data.patient_name}: ${data.new_status}`);
  }
};

setInterval(() => ws.send(JSON.stringify({ type: "ping" })), 25000);
```

## Data Model

### Core Entities and Relationships

```text
Hospital 1──* User (staff, admin, family)
    │
    └──* Admission ──1 PatientProfile
            │
            ├──* ClinicalUpdate (status + vitals)
            ├──* StaffNote (internal, hidden from family)
            ├──* EmergencyFlag (high / critical)
            ├──* ShiftHandover
            └──* CareAssignment (doctor, nurse)

PatientProfile 1──* FamilyPatientLink *──1 User (family)
User 1──* DeviceToken (FCM)
User 1──* NotificationLog
```

### Roles

| Role | Scope |
|---|---|
| `super_admin` | Platform-level, not tied to a hospital |
| `admin` | Manages one hospital, creates staff, links families |
| `doctor` | Admits patients, posts updates, manages care teams |
| `nurse` | Posts updates, writes notes, views assigned patients |
| `family` | Read-only access to linked patients |

### Patient Statuses

`Being Monitored` -> `Stable` -> `Getting Better` -> `Discharged`

`Being Monitored` -> `Critical`

## Environment Variables

See `.env.example` for the complete list. Key groups:

| Group | Variables |
|---|---|
| App | `APP_ENV`, `SECRET_KEY`, `DEBUG`, `APP_HOST`, `APP_PORT` |
| Database | `DATABASE_URL`, `DATABASE_URL_SYNC` |
| Redis | `REDIS_URL`, `REDIS_CELERY_URL`, `REDIS_SESSION_DB` |
| JWT | `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS` |
| Firebase | `FIREBASE_CREDENTIALS_PATH`, `FCM_PROJECT_ID` |
| Celery | `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` |
| CORS | `ALLOWED_ORIGINS` |
| Limits | `API_RATE_LIMIT_DEFAULT`, `AUTH_RATE_LIMIT`, `WRITE_RATE_LIMIT` |

## Background Tasks

| Task | Trigger | Behavior |
|---|---|---|
| `notify_family_of_update` | Clinical update posted | Creates NotificationLog and sends FCM push |
| `send_family_invite_email` | Family invite created | Sends invite email (provider pluggable) |
| `cleanup_old_notifications` | Daily at 02:00 UTC (Beat) | Deletes notifications older than 30 days |

FCM tasks retry up to 3 times with a 30-second delay.
Invalid device tokens are automatically pruned.

## Security

- JWT access and refresh token rotation with Redis-backed revocation
- Bcrypt password hashing via passlib
- Route-level RBAC enforced with `require_roles()`
- Strict hospital isolation using `hospital_id` scoping
- SlowAPI rate limiting (120/min default, 30/min writes)
- WebSocket token validation and family-link authorization checks
- Super-admin count capped to 3 users

## Operations

### Testing

```bash
# Run all tests
pytest tests/ -v

# Run a specific suite
pytest tests/test_auth.py -v

# Run with coverage
pytest tests/ --cov=src
```

### Database Migrations

```bash
# Create migration after model changes
alembic revision --autogenerate -m "describe change"

# Apply
alembic upgrade head

# Rollback one step
alembic downgrade -1
```

### Backup and Restore

```powershell
# Backup
./scripts/backup_db.ps1

# Restore
./scripts/restore_db.ps1 -BackupFile backups/patstat-YYYYMMDD-HHMMSS.dump
```

<details>
<summary><strong>Project Structure</strong></summary>

```text
src/
├── main.py                        # App entry, lifespan, router mount
├── api/v1/
│   ├── api_router.py              # Aggregates route modules
│   ├── auth.py                    # Login, tokens, staff registration
│   ├── patients.py                # Patient CRUD and discharge
│   ├── clinical_updates.py        # Status updates and vitals
│   ├── staff_notes.py             # Internal clinical notes
│   ├── emergency_flags.py         # Flag raise and resolve
│   ├── shift_handover.py          # Handover recording
│   ├── dashboard.py               # Stats, critical list, activity
│   ├── staffs.py                  # Staff listing
│   ├── notifications.py           # Notification management
│   ├── hospitals.py               # Hospital registration
│   ├── family_invites.py          # Invite flow
│   ├── family_dashboard.py        # Family patient views
│   └── ws.py                      # WebSocket handler
├── core/
│   ├── config.py                  # Pydantic settings
│   ├── database.py                # Async SQLAlchemy engine
│   ├── security.py                # JWT, password, RBAC guards
│   ├── redis_client.py            # Redis pool, pub/sub, cache
│   ├── websockets.py              # Connection manager
│   └── rate_limit.py              # SlowAPI setup
├── domains/
│   ├── users/                     # User, DeviceToken, UserRole
│   ├── patients/                  # Patient, Admission, ClinicalUpdate
│   ├── hospital/                  # Hospital, HospitalIdentifier
│   ├── family/                    # FamilyInvite, FamilyPatientLink
│   ├── assignments/               # CareAssignment
│   └── backoffice/                # Super-admin operations
├── tasks/
│   ├── celery_app.py              # Celery config and beat schedule
│   ├── notifications.py           # Notification tasks
│   └── providers/firebase_push.py # FCM integration
tests/                             # Test suites
alembic/                           # Migration versions
scripts/                           # Seed and DB utility scripts
```

</details>

---

<p align="center"><sub><strong>License:</strong> Proprietary. All rights reserved.</sub></p>
