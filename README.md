<p align="center">
  <strong>P A T S T A T</strong><br>
  <em>Real-time patient status tracking & family notification system</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue?style=flat-square" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square" alt="FastAPI">
  <img src="https://img.shields.io/badge/PostgreSQL-16-336791?style=flat-square" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/Redis-7-DC382D?style=flat-square" alt="Redis">
  <img src="https://img.shields.io/badge/Celery-5.4-37814A?style=flat-square" alt="Celery">
</p>

---

PatStat is a hospital-grade backend that lets clinical staff track patient statuses in real time while keeping families informed through push notifications and a dedicated mobile API. Each hospital runs its own isolated instance — no data crosses boundaries.

## Architecture

```
                    ┌─────────────┐  ┌────────────┐  ┌───────────────┐
                    │ Family App  │  │ Doctor App │  │ Admin Panel   │
                    └──────┬──────┘  └─────┬──────┘  └──────┬────────┘
                           │               │                 │
                           └───────┬───────┘                 │
                                   │  REST + WebSocket       │
                           ┌───────▼─────────────────────────▼──┐
                           │            FastAPI                  │
                           │                                     │
                           │   /api/v1/auth      Authentication  │
                           │   /api/v1/patients  Patient CRUD    │
                           │   /api/v1/dashboard Stats & Alerts  │
                           │   /api/v1/family    Family Portal   │
                           │   /ws/patient/{id}  Live Feed       │
                           └──────┬─────────────────┬────────────┘
                                  │                 │
                        ┌─────────▼───┐   ┌─────────▼──────────────┐
                        │ PostgreSQL  │   │ Redis                  │
                        │             │   │  DB 0 — pub/sub + cache│
                        │  patients   │   │  DB 1 — Celery broker  │
                        │  admissions │   │  DB 2 — refresh tokens │
                        │  users      │   └─────────┬──────────────┘
                        └─────────────┘             │
                                            ┌───────▼───────────┐
                                            │  Celery Worker     │
                                            │                    │
                                            │  FCM Push ──► Family
                                            │  Email     ──► Invites
                                            │  Cleanup   ──► Stale data
                                            └────────────────────┘
```

## Real-Time Pipeline

When a doctor posts a clinical update, this is what happens:

```
POST /api/v1/patients/{id}/updates
  │
  ├─ 1. Write ClinicalUpdate row to PostgreSQL
  ├─ 2. Update Admission.status
  ├─ 3. Redis PUBLISH patient:{id}:updates
  │       └─ All WebSocket subscribers receive the event instantly
  └─ 4. Celery task: notify_family_of_update
          ├─ Create NotificationLog for each linked family member
          └─ Firebase FCM multicast to all registered devices
```

---

## Quick Start

### Prerequisites

- Docker & Docker Compose
- A Firebase service account JSON (for push notifications)

### 1. Configure

```bash
cp .env.example .env
# Edit .env with your database, Redis, JWT, and Firebase settings
```

### 2. Add Firebase credentials

```bash
mkdir secrets
# Place your firebase-service-account.json in secrets/
```

### 3. Launch

```bash
docker compose up --build
```

| Service    | URL                          |
|------------|------------------------------|
| API        | http://localhost:8000        |
| Swagger UI | http://localhost:8000/docs   |
| Flower     | http://localhost:5555        |
| PostgreSQL | localhost:5432               |
| Redis      | localhost:6379               |

### 4. Bootstrap the first admin

```bash
python scripts/seed_super_admin.py
```

### 5. Run migrations

```bash
alembic upgrade head
```

### 6. Run without Docker (dev)

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Terminal 1 — API
uvicorn src.main:app --reload --port 8000

# Terminal 2 — Worker
celery -A src.tasks.celery_app.celery_app worker --loglevel=info

# Terminal 3 — Beat scheduler
celery -A src.tasks.celery_app.celery_app beat --loglevel=info
```

---

## API Reference

All endpoints are prefixed with `/api/v1`. Auth is via `Authorization: Bearer <token>`.

### Authentication `/auth`

| Method | Endpoint              | Description                  | Auth     |
|--------|-----------------------|------------------------------|----------|
| POST   | `/login`              | Get access + refresh tokens  | Public   |
| POST   | `/refresh`            | Rotate access token          | Public   |
| POST   | `/logout`             | Revoke refresh token         | Bearer   |
| POST   | `/logout-all`         | Revoke all sessions          | Bearer   |
| GET    | `/me`                 | Current user profile         | Bearer   |
| PATCH  | `/me`                 | Update profile               | Bearer   |
| POST   | `/change-password`    | Change password              | Bearer   |
| POST   | `/device-token`       | Register FCM device token    | Bearer   |
| POST   | `/register-staff`     | Create doctor/nurse/admin    | Admin    |

### Patients `/patients`

| Method | Endpoint                           | Description                        | Auth            |
|--------|------------------------------------|------------------------------------|-----------------|
| GET    | `/`                                | List active patients (searchable)  | Clinical, Family|
| POST   | `/`                                | Admit new patient                  | Doctor, Admin   |
| GET    | `/{id}`                            | Patient detail + active admission  | Clinical, Family|
| PATCH  | `/{id}`                            | Update patient or admission        | Clinical        |
| POST   | `/{id}/discharge`                  | Discharge patient                  | Doctor, Admin   |
| GET    | `/note-categories`                 | Available note categories          | Clinical        |

**Search params:** `q` (name, ID, ward, diagnosis), `active_only`, `skip`, `limit`

### Clinical Updates `/patients/{id}/updates`

| Method | Endpoint | Description                           | Auth     |
|--------|----------|---------------------------------------|----------|
| POST   | `/`      | Post status update + optional vitals  | Clinical |
| GET    | `/`      | Update timeline (paginated)           | Clinical, Family |

**Vitals captured:** blood pressure, heart rate, temperature, oxygen level

Setting `mark_emergency: true` simultaneously creates an emergency flag.

### Staff Notes `/patients/{id}/notes`

| Method | Endpoint     | Description            | Auth     |
|--------|--------------|------------------------|----------|
| POST   | `/`          | Create internal note   | Clinical |
| GET    | `/`          | List notes             | Clinical |
| GET    | `/{note_id}` | Single note            | Clinical |

> Staff notes are **never visible** to family users.

**Categories:** General, Handover, Procedure, Consultation, Lab Result, Urgent

### Emergency Flags `/emergency-flags`

| Method | Endpoint            | Description            | Auth     |
|--------|---------------------|------------------------|----------|
| GET    | `/`                 | List flags             | Clinical |
| POST   | `/`                 | Raise flag             | Clinical |
| GET    | `/{id}`             | Flag detail            | Clinical |
| PATCH  | `/{id}/resolve`     | Resolve flag           | Clinical |
| GET    | `/count`            | Active flag count      | Clinical |

**Priority levels:** High, Critical

### Dashboard `/dashboard`

| Method | Endpoint             | Description                        | Auth     |
|--------|----------------------|------------------------------------|----------|
| GET    | `/summary`           | Stats overview (cached 30s)        | Clinical |
| GET    | `/critical-patients` | Patients in critical state         | Clinical |
| GET    | `/needs-attention`   | No update in >12 hours             | Clinical |
| GET    | `/recent-activity`   | Latest activity feed               | Clinical |

**Summary returns:** `my_patients`, `critical_count`, `updates_today`, `needs_attention`

### Shift Handover `/shift-handovers`

| Method | Endpoint     | Description            | Auth     |
|--------|--------------|------------------------|----------|
| GET    | `/`          | List handovers         | Clinical |
| POST   | `/`          | Record handover        | Clinical |
| GET    | `/{id}`      | Handover detail        | Clinical |

### Staff `/staff`

| Method | Endpoint   | Description              | Auth     |
|--------|------------|--------------------------|----------|
| GET    | `/`        | List all staff           | Admin    |
| GET    | `/me`      | Current user             | Bearer   |
| GET    | `/doctors` | List doctors             | Clinical |
| GET    | `/nurses`  | List nurses              | Clinical |

### Notifications `/notifications`

| Method | Endpoint            | Description          | Auth   |
|--------|---------------------|----------------------|--------|
| GET    | `/`                 | List notifications   | Bearer |
| GET    | `/unread-count`     | Unread count         | Bearer |
| PATCH  | `/{id}/read`        | Mark read            | Bearer |
| POST   | `/read-all`         | Mark all read        | Bearer |

### Hospitals `/hospitals`

| Method | Endpoint | Description                        | Auth   |
|--------|----------|------------------------------------|--------|
| POST   | `/apply` | Apply for hospital registration    | Public |

### Family Portal

**Invitations** `/family/patients/{id}/invites`

| Method | Endpoint          | Description             | Auth   |
|--------|-------------------|-------------------------|--------|
| POST   | `/`               | Send invite to family   | Admin  |
| POST   | `/invites/accept` | Accept invite & sign up | Public |

**Dashboard** `/family/me/patients`

| Method | Endpoint                    | Description                     | Auth   |
|--------|-----------------------------|---------------------------------|--------|
| GET    | `/`                         | My linked patients              | Family |
| GET    | `/{id}/overview`            | Patient overview + care team    | Family |
| GET    | `/{id}/updates`             | Update history                  | Family |
| GET    | `/{id}/mobile-dashboard`    | Combined view (mobile-optimized)| Family |

### WebSocket `/ws/patient/{patient_id}`

**Connection flow:**

```
1. Client connects to /ws/patient/{patient_id}
2. Client sends: {"type": "auth", "token": "<access_token>"}
3. Server confirms: {"type": "connected", "patient_id": "..."}
```

**Events received:**
- `status_changed` — Patient status update
- `emergency_flag_raised` — Emergency flag created
- `handover_recorded` — Shift handover logged

**Keep-alive:** Send `{"type": "ping"}` every 25 seconds.

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

---

## Data Model

### Core Entities

```
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

| Role          | Scope                                                   |
|---------------|---------------------------------------------------------|
| `super_admin` | Platform-level, no hospital                             |
| `admin`       | Manages one hospital, creates staff, links families     |
| `doctor`      | Admits patients, posts updates, manages care teams      |
| `nurse`       | Posts updates, writes notes, views assigned patients    |
| `family`      | Read-only access to linked patients via family portal   |

### Patient Statuses

`Being Monitored` → `Stable` → `Getting Better` → `Discharged`
&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;↘ `Critical`

---

## Environment Variables

See `.env.example` for the full list. Key groups:

| Group    | Variables                                                       |
|----------|-----------------------------------------------------------------|
| App      | `APP_ENV`, `SECRET_KEY`, `DEBUG`, `APP_HOST`, `APP_PORT`        |
| Database | `DATABASE_URL`, `DATABASE_URL_SYNC`                             |
| Redis    | `REDIS_URL`, `REDIS_CELERY_URL`, `REDIS_SESSION_DB`            |
| JWT      | `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS` |
| Firebase | `FIREBASE_CREDENTIALS_PATH`, `FCM_PROJECT_ID`                  |
| Celery   | `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`                   |
| CORS     | `ALLOWED_ORIGINS`                                               |
| Limits   | `API_RATE_LIMIT_DEFAULT`, `AUTH_RATE_LIMIT`, `WRITE_RATE_LIMIT` |

---

## Background Tasks

| Task                         | Trigger                         | What it does                                    |
|------------------------------|---------------------------------|-------------------------------------------------|
| `notify_family_of_update`    | Clinical update posted          | Creates NotificationLog + FCM push to family    |
| `send_family_invite_email`   | Family invite created           | Sends invite email (plug in SES/SendGrid)       |
| `cleanup_old_notifications`  | Daily at 02:00 UTC (Beat)       | Deletes notifications older than 30 days        |

FCM tasks retry up to 3 times with 30s delay. Invalid device tokens are automatically pruned.

---

## Security

- **JWT** access + refresh token rotation with Redis-backed revocation
- **Bcrypt** password hashing via passlib
- **RBAC** enforced at the route level with `require_roles()` dependency
- **Hospital isolation** — every query scopes to `hospital_id`
- **Rate limiting** via SlowAPI (120/min default, 30/min writes)
- **WebSocket auth** — token validated + family link verified before streaming
- **Super-admin cap** — max 3 super-admins enforced at creation time

---

## Testing

```bash
# All tests
pytest tests/ -v

# Specific suite
pytest tests/test_auth.py -v

# With coverage
pytest tests/ --cov=src
```

16 test modules covering auth, RBAC, patients, clinical updates, dashboard, emergency flags, shift handovers, staff, notifications, hospitals, family management, and WebSocket connections.

---

## Database Migrations

```bash
# Create migration after model changes
alembic revision --autogenerate -m "describe change"

# Apply
alembic upgrade head

# Rollback one step
alembic downgrade -1
```

---

## Backup & Restore

```powershell
# Backup
./scripts/backup_db.ps1

# Restore
./scripts/restore_db.ps1 -BackupFile backups/patstat-YYYYMMDD-HHMMSS.dump
```

---

## Project Structure

```
src/
├── main.py                        # App entry, lifespan, router mount
├── api/v1/
│   ├── api_router.py              # Aggregates all route modules
│   ├── auth.py                    # Login, tokens, staff registration
│   ├── patients.py                # Patient CRUD + discharge
│   ├── clinical_updates.py        # Status updates + vitals
│   ├── staff_notes.py             # Internal clinical notes
│   ├── emergency_flags.py         # Flag raise / resolve
│   ├── shift_handover.py          # Handover recording
│   ├── dashboard.py               # Stats, critical list, activity
│   ├── staffs.py                  # Staff listing
│   ├── notifications.py           # Notification management
│   ├── hospitals.py               # Hospital registration
│   ├── family_invites.py          # Invite flow
│   ├── family_dashboard.py        # Family patient views
│   └── ws.py                      # WebSocket handler
├── core/
│   ├── config.py                  # Pydantic Settings (.env)
│   ├── database.py                # Async SQLAlchemy engine
│   ├── security.py                # JWT + password + RBAC guards
│   ├── redis_client.py            # Redis pool, pub/sub, cache
│   ├── websockets.py              # Connection manager
│   └── rate_limit.py              # SlowAPI setup
├── domains/
│   ├── users/                     # User, DeviceToken, UserRole
│   ├── patients/                  # Patient, Admission, ClinicalUpdate, etc.
│   ├── hospital/                  # Hospital, HospitalIdentifier
│   ├── family/                    # FamilyInvite, FamilyPatientLink
│   ├── assignments/               # CareAssignment
│   └── backoffice/                # Super-admin operations
├── tasks/
│   ├── celery_app.py              # Celery config + Beat schedule
│   ├── notifications.py           # FCM + notification tasks
│   └── providers/firebase_push.py # FCM integration
tests/                             # 16 test modules
alembic/                           # Migration versions
scripts/                           # seed_super_admin, backup, restore
```

---

## License

Proprietary. All rights reserved.
