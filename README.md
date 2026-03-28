# PatStat Backend

FastAPI backend for the PatStat patient status tracking system.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        PatStat Backend                           │
│                                                                  │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────────────┐   │
│  │  Family App │   │  Doctor App │   │    Admin Dashboard  │   │
│  └──────┬──────┘   └──────┬──────┘   └──────────┬──────────┘   │
│         │                 │                       │              │
│         └────────────┬────┘                       │              │
│                      │  REST + WebSocket           │              │
│              ┌───────▼──────────────────────────┐ │              │
│              │           FastAPI                │◄┘              │
│              │   /api/v1/auth                   │                │
│              │   /api/v1/family                 │                │
│              │   /api/v1/clinical               │                │
│              │   /api/v1/admin                  │                │
│              │   /ws/patient/{id}   ◄─ WS       │                │
│              └──────┬──────────────┬────────────┘                │
│                     │              │                              │
│              ┌──────▼──┐    ┌──────▼──────────────────────┐     │
│              │PostgreSQL│    │   Redis (2 DBs)              │     │
│              │          │    │   DB0: pub/sub + cache       │     │
│              │ patients │    │   DB1: celery broker         │     │
│              │ updates  │    │   DB2: refresh tokens        │     │
│              │ users    │    └──────────────┬───────────────┘     │
│              └──────────┘                   │                     │
│                                    ┌────────▼──────────────┐     │
│                                    │    Celery Worker       │     │
│                                    │  notify_family_of_     │     │
│                                    │  update task           │     │
│                                    │       │                │     │
│                                    │  ┌────▼────────────┐  │     │
│                                    │  │  Firebase FCM   │  │     │
│                                    │  │  Push to family │  │     │
│                                    │  │  mobile devices │  │     │
│                                    │  └─────────────────┘  │     │
│                                    └───────────────────────┘     │
└──────────────────────────────────────────────────────────────────┘
```

## Real-Time Flow (the key pipeline)

```
Doctor POSTs to /api/v1/clinical/patients/{id}/updates
  │
  ├─ 1. Write PatientUpdate row to PostgreSQL
  ├─ 2. Update Patient.status in PostgreSQL
  ├─ 3. await publish_patient_update(patient_id, event)
  │       └─ Redis PUBLISH patient:{id}:updates  <JSON event>
  │               └─ All WS connections subscribed wake up
  │                       └─ Each sends JSON to connected client
  └─ 4. notify_family_of_update.delay(...)
          └─ Celery picks up task
                ├─ Queries FamilyPatientLink for all family user_ids
                ├─ Queries DeviceToken for all FCM tokens
                ├─ Writes NotificationLog rows
                └─ Fires FCM MulticastMessage to all tokens
```

## Project Structure

```
patstat-backend/
├── app/
│   ├── main.py              # FastAPI app, lifespan, routers
│   ├── config.py            # Pydantic settings (reads .env)
│   ├── database.py          # Async SQLAlchemy engine + session
│   ├── models/
│   │   └── __init__.py      # All SQLAlchemy models
│   ├── schemas/
│   │   └── __init__.py      # All Pydantic request/response schemas
│   ├── core/
│   │   ├── auth.py          # JWT, password hashing, role guards
│   │   ├── redis.py         # Redis pool, pub/sub, cache helpers
│   │   └── websocket_manager.py  # WS connection tracking
│   ├── api/
│   │   ├── auth.py          # /auth/* routes
│   │   ├── family.py        # /family/* routes (family dashboard)
│   │   ├── clinical.py      # /clinical/* routes (doctors/nurses)
│   │   ├── admin.py         # /admin/* routes
│   │   └── websocket.py     # /ws/patient/{id} WebSocket
│   └── tasks/
│       └── celery_app.py    # Celery app + FCM tasks
├── tests/
│   └── test_pipeline.py     # Integration tests
├── alembic/                 # DB migrations
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

## Quick Start

### 1. Clone and configure
```bash
cp .env.example .env
# Edit .env with your values
```

Config note:
- Runtime config is now string-backed from `.env` via `src/core/config.py`.
- `.env.example` lists all required keys and marks keys moved from code defaults.
- DB ORM uses SQLAlchemy declarative mappings in `src/domains/*/models.py` (not FastAPI schemas).

### 2. Add Firebase credentials
```bash
mkdir secrets
# Place your firebase-service-account.json in secrets/
```

### 3. Start everything
```bash
docker compose up --build
```

Services started:
| Service | URL |
|---|---|
| FastAPI | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |
| Flower (Celery UI) | http://localhost:5555 |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |

### 4. Run without Docker (dev)
```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Start API
uvicorn app.main:app --reload --port 8000

# Start Celery worker (separate terminal)
celery -A app.tasks.celery_app.celery_app worker --loglevel=info

# Start Celery beat scheduler (separate terminal)
celery -A app.tasks.celery_app.celery_app beat --loglevel=info
```

### 5. First-time setup
```bash
# Bootstrap the first Pat-Stat Super Admin (Headquarters role)
python scripts/create_super_admin.py
```

### 6. Database migrations
```bash
# Create migration after model changes
alembic revision --autogenerate -m "describe change"

# Apply migrations
alembic upgrade head

# Rollback one step
alembic downgrade -1
```

### 7. Tests
```bash
pytest tests/ -v
```

### 8. Backup and restore
```powershell
# Create a timestamped backup dump
./scripts/backup_db.ps1

# Restore from a dump file
./scripts/restore_db.ps1 -BackupFile backups/patstat-YYYYMMDD-HHMMSS.dump
```

See BACKUP_STRATEGY.md for retention and validation guidelines.

## WebSocket Client Example (JavaScript)

```javascript
const patientId = "uuid-here";
const token = "your-jwt-access-token";

const ws = new WebSocket(
  `ws://localhost:8000/ws/patient/${patientId}?token=${token}`
);

ws.onopen = () => console.log("Connected to PatStat real-time feed");

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);

  if (data.event === "connected") {
    console.log("Watching:", data.patient_name);
  }

  if (data.event === "status_changed") {
    console.log(`${data.patient_name}: ${data.new_status}`);
    // Update your React state here
    updatePatientStatus(data.update);
  }

  if (data.type === "ping") {
    ws.send(JSON.stringify({ type: "ping" }));
  }
};

// Keep-alive
setInterval(() => ws.send(JSON.stringify({ type: "ping" })), 25000);
```

## API Endpoints Summary

### Auth
| Method | Path | Description |
|---|---|---|
| POST | /api/v1/auth/register | Create account |
| POST | /api/v1/auth/login | Login, get tokens |
| POST | /api/v1/auth/refresh | Rotate access token |
| POST | /api/v1/auth/logout | Revoke refresh token |
| POST | /api/v1/auth/change-password | Change current user's password |
| GET | /api/v1/auth/me | Current user info |
| PATCH | /api/v1/auth/me | Update current user profile |
| POST | /api/v1/auth/device-token | Register FCM token |

### Family (role: family)
| Method | Path | Description |
|---|---|---|
| GET | /api/v1/family/patients | My linked patients |
| GET | /api/v1/family/patients/{id} | Patient detail + care team |
| GET | /api/v1/family/patients/{id}/updates | Update timeline (paginated) |
| GET | /api/v1/family/patients/{id}/care-team | Care team list |
| GET | /api/v1/family/notifications | My notifications |
| POST | /api/v1/family/notifications/{id}/read | Mark read |
| POST | /api/v1/family/notifications/read-all | Mark all read |

### Clinical (role: doctor, nurse)
| Method | Path | Description |
|---|---|---|
| POST | /api/v1/clinical/patients | Admit patient |
| GET | /api/v1/clinical/patients | List all patients |
| GET | /api/v1/clinical/patients/page | List patients (paginated metadata) |
| POST | /api/v1/clinical/patients/{id}/updates | **Post status update** |
| PATCH | /api/v1/clinical/patients/{id}/discharge | Discharge patient |
| POST | /api/v1/clinical/patients/{id}/care-team | Assign staff |
| DELETE | /api/v1/clinical/patients/{id}/care-team/{staff_id} | Unassign |

### Admin (role: admin)
| Method | Path | Description |
|---|---|---|
| GET | /api/v1/admin/stats | Dashboard statistics |
| GET | /api/v1/admin/staff | List all staff |
| GET | /api/v1/admin/staff/page | List staff (paginated metadata) |
| POST | /api/v1/admin/family-links | Link family to patient |
| DELETE | /api/v1/admin/family-links/{uid}/{pid} | Unlink |

### WebSocket
| Path | Description |
|---|---|
| /ws/patient/{id}?token= | Real-time patient feed |
| /ws/admin?token= | All-patients admin feed |

## Beautiful and Intuitive Frontend Starter (HTML + CSS)

If you want a clean, modern, and intuitive first UI for PatStat, use the following
template files. This gives you:

- A clear dashboard hierarchy (hero, metrics, patient cards, timeline)
- Strong visual direction (warm gradients, high contrast text, card depth)
- Mobile-friendly responsive behavior

Suggested placement:

```text
src/
  static/
    index.html
    styles.css
```

### index.html

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>PatStat Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link
      href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Space+Grotesk:wght@400;500;700&display=swap"
      rel="stylesheet"
    />
    <link rel="stylesheet" href="styles.css" />
  </head>
  <body>
    <div class="bg-orb orb-a" aria-hidden="true"></div>
    <div class="bg-orb orb-b" aria-hidden="true"></div>

    <header class="topbar">
      <div class="brand">
        <span class="brand-mark" aria-hidden="true">PS</span>
        <div>
          <p class="brand-eyebrow">Ward Control</p>
          <h1>PatStat</h1>
        </div>
      </div>
      <nav class="top-actions" aria-label="Quick actions">
        <button class="btn btn-soft">Live Feed</button>
        <button class="btn btn-primary">+ Add Update</button>
      </nav>
    </header>

    <main class="layout">
      <section class="hero card">
        <p class="eyebrow">Hospital Operations</p>
        <h2>Patient status at a glance, with zero confusion.</h2>
        <p>
          Track critical changes, identify urgent cases, and keep families informed in
          real time.
        </p>
        <div class="stat-row" role="list" aria-label="Key metrics">
          <article class="stat" role="listitem">
            <h3>42</h3>
            <p>Active Admissions</p>
          </article>
          <article class="stat" role="listitem">
            <h3>7</h3>
            <p>Emergency Flags</p>
          </article>
          <article class="stat" role="listitem">
            <h3>19s</h3>
            <p>Avg Notification Delay</p>
          </article>
        </div>
      </section>

      <section class="panel card">
        <div class="panel-head">
          <h3>Priority Patients</h3>
          <a href="#">View all</a>
        </div>
        <ul class="patient-list">
          <li class="patient-item">
            <div>
              <p class="patient-name">Amina Yusuf</p>
              <p class="patient-meta">Ward A3 · Dr. Oke</p>
            </div>
            <span class="pill pill-critical">Critical</span>
          </li>
          <li class="patient-item">
            <div>
              <p class="patient-name">Michael Chen</p>
              <p class="patient-meta">ICU 2 · Dr. Bello</p>
            </div>
            <span class="pill pill-watch">Under Observation</span>
          </li>
          <li class="patient-item">
            <div>
              <p class="patient-name">Ruth Adeyemi</p>
              <p class="patient-meta">Ward C1 · Dr. Martins</p>
            </div>
            <span class="pill pill-stable">Stable</span>
          </li>
        </ul>
      </section>

      <section class="panel card">
        <div class="panel-head">
          <h3>Recent Timeline</h3>
          <a href="#">Open stream</a>
        </div>
        <ol class="timeline">
          <li>
            <p class="time">08:41</p>
            <p>Emergency flag raised for Amina Yusuf.</p>
          </li>
          <li>
            <p class="time">08:37</p>
            <p>Family notified for Michael Chen status change.</p>
          </li>
          <li>
            <p class="time">08:31</p>
            <p>Care team reassigned in Ward C1.</p>
          </li>
        </ol>
      </section>
    </main>
  </body>
</html>
```

### styles.css

```css
:root {
  --bg: #f6f3ee;
  --panel: #fffdf9;
  --text: #1b1a18;
  --muted: #5a5752;
  --line: #e4ddd2;
  --brand: #c4492d;
  --brand-deep: #7d2a1d;
  --ok: #2f7a4b;
  --warn: #9d6a0f;
  --danger: #9f2f2f;
  --radius: 18px;
  --shadow: 0 12px 30px rgba(57, 34, 14, 0.12);
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  min-height: 100vh;
  font-family: "Space Grotesk", "Segoe UI", sans-serif;
  color: var(--text);
  background:
    radial-gradient(circle at 15% 8%, #f6cfb5 0%, transparent 35%),
    radial-gradient(circle at 92% 90%, #d8e5ce 0%, transparent 30%),
    var(--bg);
  padding: 24px;
}

h1,
h2,
h3 {
  margin: 0;
  line-height: 1.15;
}

p {
  margin: 0;
}

.bg-orb {
  position: fixed;
  border-radius: 999px;
  filter: blur(24px);
  z-index: -1;
  opacity: 0.6;
}

.orb-a {
  width: 220px;
  height: 220px;
  top: -60px;
  right: 10%;
  background: #f1b89e;
}

.orb-b {
  width: 260px;
  height: 260px;
  bottom: -100px;
  left: 5%;
  background: #b8d4b3;
}

.topbar {
  max-width: 1120px;
  margin: 0 auto 18px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-mark {
  display: inline-grid;
  place-items: center;
  width: 46px;
  height: 46px;
  border-radius: 14px;
  background: linear-gradient(140deg, var(--brand), var(--brand-deep));
  color: #fff;
  font-weight: 700;
}

.brand h1 {
  font-family: "Fraunces", Georgia, serif;
  font-size: 1.7rem;
}

.brand-eyebrow,
.eyebrow {
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  font-size: 0.75rem;
}

.top-actions {
  display: flex;
  gap: 10px;
}

.btn {
  border: 0;
  border-radius: 999px;
  padding: 10px 16px;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

.btn-soft {
  background: #ede7dd;
  color: #2e2a24;
}

.btn-primary {
  color: #fff;
  background: linear-gradient(140deg, var(--brand), var(--brand-deep));
  box-shadow: 0 8px 18px rgba(159, 47, 47, 0.25);
}

.layout {
  max-width: 1120px;
  margin: 0 auto;
  display: grid;
  gap: 16px;
  grid-template-columns: 1.4fr 1fr;
  animation: fade-up 500ms ease-out;
}

.card {
  background: color-mix(in srgb, var(--panel) 94%, white 6%);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 18px;
  box-shadow: var(--shadow);
}

.hero {
  grid-column: 1 / -1;
}

.hero h2 {
  margin-top: 8px;
  max-width: 15ch;
  font-family: "Fraunces", Georgia, serif;
  font-size: clamp(1.6rem, 3.1vw, 2.5rem);
}

.hero > p {
  margin-top: 10px;
  color: var(--muted);
  max-width: 56ch;
}

.stat-row {
  margin-top: 16px;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.stat {
  background: #f1ece4;
  border-radius: 14px;
  border: 1px solid #e2d6c5;
  padding: 12px;
}

.stat h3 {
  font-size: 1.5rem;
}

.stat p {
  color: var(--muted);
  margin-top: 4px;
}

.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 10px;
}

.panel-head a {
  color: var(--brand-deep);
  text-decoration: none;
  font-weight: 600;
}

.patient-list,
.timeline {
  list-style: none;
  margin: 0;
  padding: 0;
}

.patient-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  padding: 12px 0;
  border-top: 1px dashed var(--line);
}

.patient-item:first-child {
  border-top: 0;
}

.patient-name {
  font-weight: 700;
}

.patient-meta {
  color: var(--muted);
  margin-top: 2px;
}

.pill {
  white-space: nowrap;
  font-size: 0.8rem;
  padding: 6px 10px;
  border-radius: 999px;
  font-weight: 700;
}

.pill-critical {
  background: #f7d8d8;
  color: var(--danger);
}

.pill-watch {
  background: #f7ebd4;
  color: var(--warn);
}

.pill-stable {
  background: #deeedf;
  color: var(--ok);
}

.timeline li {
  padding: 12px 0;
  border-top: 1px dashed var(--line);
}

.timeline li:first-child {
  border-top: 0;
}

.timeline .time {
  color: var(--brand-deep);
  font-weight: 700;
  font-size: 0.85rem;
  margin-bottom: 3px;
}

@keyframes fade-up {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@media (max-width: 860px) {
  body {
    padding: 16px;
  }

  .layout {
    grid-template-columns: 1fr;
  }

  .stat-row {
    grid-template-columns: 1fr;
  }

  .topbar {
    flex-direction: column;
    align-items: flex-start;
  }
}
```

### Optional FastAPI static mount

If you want FastAPI to serve this static folder directly:

```python
from fastapi.staticfiles import StaticFiles

app.mount("/", StaticFiles(directory="src/static", html=True), name="static")
```

Note: keep API routes under `/api/v1/*` and WebSocket routes under `/ws/*` so
you can use this UI as a simple landing dashboard without affecting backend APIs.
