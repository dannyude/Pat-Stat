# Development

How to set up a local environment, navigate the codebase, and contribute changes.

For testing and CI specifics, see [Operations](operations.md). For architectural context, see [Architecture](architecture.md).

---

## Local Setup

### Option A — Docker (recommended)

```bash
cp .env.example .env       # edit secrets
docker compose up --build
```

This is the path the rest of the docs assume. Everything (Postgres, Redis, API, Celery, Beat, Flower) starts together.

### Option B — Without Docker

If you'd rather run the API on the host (faster reload, easier debugger):

```bash
# Install uv (the project's package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh   # Linux/macOS
# or:  powershell -c "irm https://astral.sh/uv/install.ps1 | iex"   # Windows

# Sync dependencies (uv reads pyproject.toml + uv.lock)
uv sync --all-extras --dev

# Start Postgres + Redis only via Docker
docker compose up -d postgres redis

# Terminal 1 — API
uv run uvicorn src.main:app --reload --port 8000

# Terminal 2 — Celery worker
uv run celery -A src.tasks.celery_app.celery_app worker --loglevel=info

# Terminal 3 — Beat scheduler
uv run celery -A src.tasks.celery_app.celery_app beat --loglevel=info
```

You'll need `.env` to point `DATABASE_URL` / `REDIS_URL` at `localhost:6432` / `localhost:6379` for the host-based services to reach the Docker containers.

### One-time database bootstrap

```bash
# Apply all migrations
alembic upgrade head

# Bootstrap the first super-admin
python scripts/seed_super_admin.py \
  --email admin@patstat.io \
  --password '<your-password>' \
  --full-name 'Platform Admin'
```

The seed script is bootstrap-only — it refuses to run once any super-admin exists. Subsequent super-admins go through `POST /api/v1/backoffice/super-admins` (authenticated).

---

## Project Structure

```text
src/
├── main.py                           # App entry, lifespan, middleware, global exception handler
├── api/v1/
│   ├── api_router.py                 # Aggregates all v1 routers
│   ├── auth.py                       # Login, tokens, password change, device tokens
│   ├── patients.py                   # Patient CRUD, discharge
│   ├── clinical_updates.py           # POST/GET status updates and vitals
│   ├── staff_notes.py                # Internal clinical notes (hidden from family)
│   ├── emergency_flags.py            # Flag raise / resolve / count
│   ├── shift_handover.py             # Handover recording
│   ├── dashboard.py                  # Stats, critical list, activity feed
│   ├── staffs.py                     # Staff listing
│   ├── notifications.py              # Bell list, mark-read, unread count
│   ├── hospitals.py                  # Public hospital sign-up
│   ├── family_invites.py             # Invite create / accept
│   ├── family_dashboard.py           # Family read-model views
│   ├── family_access.py              # Admin: link/unlink family members
│   ├── contact_sales.py              # Public contact form
│   ├── ws.py                         # WebSocket endpoint + catch-up + token watchdog
│   ├── update_history.py             # Patient timeline
│   ├── clinical_notes_global.py      # Cross-patient notes feed
│   ├── staff_invites.py              # Admin: send / verify staff invites
│   ├── admin.py                      # Staff registration
│   └── patient_helpers.py            # Shared serialisers + admission lookup
├── core/
│   ├── config.py                     # Pydantic Settings — single source of truth
│   ├── database.py                   # Async SQLAlchemy engine + session factory
│   ├── security.py                   # JWT, password, RBAC guards (require_roles)
│   ├── redis_client.py               # Pool, pub/sub, refresh-token revocation, cache
│   ├── websockets.py                 # In-memory connection manager (bookkeeping)
│   ├── rate_limit.py                 # SlowAPI setup
│   └── mixins.py                     # UUIDPrimaryKey, TimestampMixin, utcnow
├── domains/
│   ├── users/                        # User, DeviceToken, UserRole
│   ├── patients/                     # PatientProfile, Admission, ClinicalUpdate, EmergencyFlag,
│   │                                 # ShiftHandover, StaffNote, NotificationLog, FamilyPatientLink
│   ├── hospital/                     # Hospital, HospitalIdentifier
│   ├── family/                       # FamilyInvite + services (incl. assert_family_link_or_404)
│   ├── assignments/                  # CareAssignment (doctor/nurse → admission)
│   ├── notifications/                # policy.py (tier mapping), dispatch.py (HTTP→Celery shim)
│   ├── backoffice/                   # Super-admin operations: hospitals, audit log, settings
│   ├── staff_invites/                # StaffInvite model + flow
│   └── contact_sales/                # ContactSalesSubmission model + service
├── tasks/
│   ├── celery_app.py                 # Celery config + Beat schedule
│   ├── notifications.py              # notify_family_of_update, _send_fcm_multicast,
│   │                                 # cleanup_old_notifications, reconcile_stuck_queued_notifications
│   ├── contact_sales.py              # Outbound email task
│   └── providers/firebase_push.py    # Firebase Admin SDK wrapper
├── models.py                         # Master ORM registry (imports every model)
└── ...

tests/                                 # Test suites — see Operations → Testing
alembic/                               # Migration versions + env config
scripts/
├── seed_super_admin.py                # Bootstrap-only first super-admin
├── test.ps1                           # PowerShell wrapper for pytest in Docker
├── backup_db.ps1                      # pg_dump wrapper
└── restore_db.ps1                     # pg_restore wrapper
.github/workflows/ci.yml                # GitHub Actions CI
docs/                                   # This documentation
notes/                                  # Engineering notes (gitignored — local only)
```

---

## Adding a New Endpoint

The codebase follows a thin-handler / thick-service pattern. A new endpoint typically touches three files:

1. **Schema** — `src/domains/<domain>/schemas.py`
   Define the request and response Pydantic models. Use `EmailStr`, `UUID`, `Literal[...]` etc. for validation.

2. **Service** — `src/domains/<domain>/services.py`
   Write the business logic. Take an `AsyncSession`, return domain models or DTOs. No HTTP concerns.

3. **Handler** — `src/api/v1/<domain>.py`
   Wire the route. Pick the right `Depends(require_roles(...))`. Call into the service. Translate exceptions into HTTPException if needed.

4. **Test** — `tests/test_<domain>_edge_cases.py`
   At minimum: happy path, role denial, cross-hospital denial, validation error.

5. **Update API docs** — usually nothing to do; Swagger picks it up automatically. If the endpoint is non-trivial (WebSocket, file upload), add a section to [`docs/api.md`](api.md).

---

## Adding a Database Migration

```bash
# 1. Edit the ORM model
$EDITOR src/domains/<domain>/models/...

# 2. Generate the migration
alembic revision --autogenerate -m "describe what changed"

# 3. Inspect the generated file — Alembic's autogenerate is good but not perfect
$EDITOR alembic/versions/<new>.py

# 4. Apply locally
alembic upgrade head

# 5. Run the test suite to make sure nothing broke
.\scripts\test.ps1
```

**Things autogenerate often misses:**
- Server-side defaults (`server_default=text("now()")`)
- Custom indexes (composite, partial, GIN/GIST)
- Enum value changes (Postgres `ALTER TYPE` is irreversible — be careful)
- Default values for new NOT NULL columns on existing rows

If you add a NOT NULL column with no default, the migration will fail on any DB with existing rows. Either provide a `server_default` or do a two-phase migration (add nullable → backfill → make NOT NULL).

---

## Adding a Test

Use the closest-matching existing test file as a template:

| Concern | Pattern file |
|---|---|
| New CRUD endpoint | `tests/test_patients_edge_cases.py` |
| New role-gated endpoint | `tests/test_rbac.py` or any `test_*_edge_cases.py` |
| Cross-hospital scope check | `tests/test_patients_edge_cases.py::TestGetSinglePatient::test_get_patient_from_other_hospital_returns_404` |
| Cross-family link check | `tests/test_family_dashboard.py::TestPatientOverview::test_overview_for_unlinked_patient_returns_404` |
| New Celery task | `tests/test_notification_delivery_log.py` (use `celery_eager` fixture for sync execution) |
| WebSocket behaviour | `tests/test_ws.py` |

The shared test helpers live in `tests/helpers.py`:

| Helper | What it does |
|---|---|
| `seed_user(db_session, email, role, ...)` | Creates a User row with the test password hash |
| `seed_hospital(db_session, name)` | Creates a Hospital row |
| `seed_admission(db_session, hospital_id, ...)` | Creates a PatientProfile + Admission |
| `seed_family_link(patient_id, family_user_id, ...)` | Creates a FamilyPatientLink |
| `login_for_token(api_client, email, password='Password123')` | Returns an access token |
| `unique_email(prefix)` | Returns `prefix-<random>@test.com` for test isolation |

Always use `unique_email(...)` rather than hard-coded strings — pytest can parallelise and you'll hit unique-constraint conflicts otherwise.

---

## Code Style

The project doesn't enforce a single formatter or linter (yet). Conventions in practice:

- **Imports**: stdlib → third-party → first-party (`src.*`), separated by blank lines
- **Docstrings**: triple-quoted, first line is a one-liner, blank line, then detail. Block-comments explain *why*, code shows *what*
- **Type hints**: required on public functions; encouraged elsewhere. We don't run mypy in CI — yet.
- **Logging**: use `logger.exception()` (not `logger.error()`) when there's a traceback to capture. Use `%s` lazy formatting, not f-strings, for log lines that might be filtered out.

If you want strict linting, pick a baseline (ruff is the obvious choice — fast, comprehensive). It's not in CI today because the codebase isn't formatted to a single style yet — adding strict linting now would require a bulk-format commit first.

---

## Conventional Commits

Recent commit messages follow a loose Conventional Commits format:

```
<type>(<scope>): <subject>
```

| Type | When |
|---|---|
| `feat` | New feature |
| `fix` | Bug fix |
| `test` | Test additions / improvements |
| `docs` | Documentation only |
| `ci` | CI workflow changes |
| `refactor` | Internal change, no user-visible behaviour delta |
| `chore` | Tooling, deps, build |

Example: `fix(security): return 401 + WWW-Authenticate for missing auth`

This isn't enforced by a hook, but adopting it consistently makes `git log --oneline` readable at a glance and makes future automation (release notes, semver bump) easier to add.

---

## Engineering Notes

`notes/` (gitignored) holds dated engineering notes — Problem / Root Cause / Solution write-ups for non-trivial bug fixes and architectural decisions. They're a useful breadcrumb for future-you trying to understand why something was done. The format is loose; see `notes/2026-04-30.md` and `notes/2026-05-01.md` for examples.

If you want these in the repo (they're more "documentation" than "personal"), edit `.gitignore` to drop the `notes/` exclusion and commit them.
