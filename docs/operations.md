# Operations

Day-to-day operational concerns: environment, testing, CI/CD, migrations, backups, and the background task topology.

For architectural context, see [Architecture](architecture.md).

---

## Environment Variables

See `.env.example` for the complete list. Key groups:

| Group | Variables | Notes |
|---|---|---|
| App | `APP_ENV`, `SECRET_KEY`, `DEBUG`, `APP_HOST`, `APP_PORT` | `SECRET_KEY` must be ≥ 32 chars; `DEBUG=false` enforced in production |
| Database | `DATABASE_URL` (asyncpg), `DATABASE_URL_SYNC` (psycopg2) | Two URLs because Celery uses sync SQLAlchemy and FastAPI uses async |
| Redis | `REDIS_URL`, `REDIS_CELERY_URL`, `REDIS_SESSION_DB` | Three logical DBs: 0 = pub/sub & cache, 1 = Celery, 2 = refresh tokens |
| JWT | `JWT_ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS` | Defaults: HS256 / 30 min / 7 days |
| Firebase | `FIREBASE_CREDENTIALS_PATH`, `FCM_PROJECT_ID` | Path inside container; `secrets/` is volume-mounted to `/secrets` |
| Celery | `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Both point at Redis DB 1 |
| CORS | `ALLOWED_ORIGINS` | Comma-separated; production refuses `*` |
| Limits | `API_RATE_LIMIT_DEFAULT`, `AUTH_RATE_LIMIT`, `WRITE_RATE_LIMIT` | SlowAPI format: `120/minute`, `30/minute`, etc |

Production startup will fail fast (Pydantic ValidationError) if any required field is missing.

---

## Service Topology (Docker Compose)

| Service | Container | Host port | Container port | Notes |
|---|---|---:|---:|---|
| API | `patstat_api` | **8080** | 8000 | uvicorn with `--reload` in dev |
| PostgreSQL | `patstat-db` | **6432** | 5432 | non-standard host port to dodge Windows reserved ranges |
| Redis | `patstat_redis` | 6379 | 6379 | persistent volume `redis_data` |
| Celery worker | `patstat_celery` | — | — | concurrency=4, prefork |
| Celery Beat | `patstat_beat` | — | — | scheduler — env block must mirror `api` (Settings imports at module load) |
| Flower | `patstat_flower` | **8888** | 5555 | Celery monitoring UI |

### Why non-default host ports?

Windows Hyper-V/WSL reserves port ranges that frequently collide with common services. Verified ranges on a typical Windows machine include `5178–5277`, `5434–6333`, `5534–5633` — which would block Postgres at `5432/5434` and Flower at `5555`.

Diagnostic command:

```powershell
netsh interface ipv4 show excludedportrange protocol=tcp
```

If you see a different reserved range than what's shipped, edit `docker-compose.yml` and pick a port outside every range. Block-comments at each port mapping explain the workaround.

---

## Testing

The project ships ~248 pytest tests covering HTTP endpoints, WebSocket lifecycle, notification policy, and security boundaries. The same suite runs in CI on every push.

### Local convenience script (recommended)

```powershell
# Full suite (Windows / PowerShell)
.\scripts\test.ps1

# Single file
.\scripts\test.ps1 tests\test_auth.py -v

# Filter by name
.\scripts\test.ps1 -k "dispatch"

# Verbose with short tracebacks
.\scripts\test.ps1 -v --tb=short
```

The script wraps `docker exec patstat_api pytest` with the test-database env vars so you don't need to remember the escaping.

### One-time setup

The test suite **refuses to run against the dev database** to prevent accidental data loss (see `tests/conftest.py`). Create the dedicated test DB once:

```powershell
docker exec patstat-db psql -U postgres -c "CREATE DATABASE patstat_test_db;"
```

### Direct invocation (Linux / macOS / WSL)

```bash
DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:6432/patstat_test_db' \
DATABASE_URL_SYNC='postgresql://postgres:postgres@localhost:6432/patstat_test_db' \
pytest --tb=short -q
```

### Coverage

```bash
pytest tests/ --cov=src --cov-report=term-missing
```

---

## Continuous Integration

Workflow: `.github/workflows/ci.yml`

Triggers on every push and pull request to `main` and `development`. Each run:

1. Boots `postgres:16-alpine` + `redis:7-alpine` as service containers (with health checks).
2. Installs Python 3.12 and project deps via `uv` (cached on `uv.lock` hash).
3. Creates `patstat_test_db`.
4. Runs the full pytest suite.

Total run: ~1–2 minutes on first run, ~45–60s with cache.

### Branch protection (recommended)

CI is informational by default. To make it actually gate merges:

1. **Settings → Branches → Add classic branch protection rule**
2. Branch name pattern: `main` (and optionally `development`)
3. ✅ **Require status checks to pass before merging** → search for `Run pytest`
4. Optionally: ✅ **Require a pull request before merging** (forces review even for solo devs)

Without this, anyone with push access can merge red CI to `main`.

### CD policy

Continuous Deployment is intentionally **not** automated. Production releases go through a manual review step — appropriate for healthcare-grade release control. When you have a deploy target (VPS, managed PaaS, Kubernetes), wire a separate `cd.yml` that builds and pushes a Docker image; do the production restart manually.

---

## Database Migrations

```bash
# Create migration after model changes
alembic revision --autogenerate -m "describe change"

# Apply (safe — runs only un-applied revisions)
alembic upgrade head

# Rollback one step
alembic downgrade -1
```

### Migration chain (May 2026)

The current chain is linear ending at `f7a8b9c0d1e2`:

```text
093268312883  (root)
    ↓
69638fe44fc3
    ↓
… intermediate revisions …
    ↓
e5f6a7b8c9d0  (staff invites)
    ↓
f6a7b8c9d0e1  (notification category column)
    ↓
a1f2b3c4d5e6  (platform settings table)
    ↓
f7a8b9c0d1e2  (notification delivery audit columns) ← head
```

If `alembic upgrade head` fails with `multiple head revisions`, run `alembic heads` to see which two need merging, then `alembic merge -m "merge heads" <rev1> <rev2>`.

---

## Background Tasks

Celery topology: workers + Beat scheduler + Flower monitoring UI.

| Task | Trigger | Behavior |
|---|---|---|
| `notify_family_of_update` | Clinical update / emergency flag / shift handover posted | Routes through tier policy; creates `NotificationLog`; dispatches FCM for critical/important tiers |
| `_send_fcm_multicast` | Subtask of above | Sends multicast FCM, persists per-row delivery outcome, prunes invalid tokens |
| `send_family_invite_email` | Family invite created | Sends invite email (provider pluggable) |
| `send_staff_invite_email` | Staff invite created | Sends invite email |
| `cleanup_old_notifications` | Daily at 02:00 UTC (Beat) | Deletes notifications older than 30 days |
| `reconcile_stuck_queued_notifications` | Every 5 minutes (Beat) | Marks rows stuck in `delivery_status='queued'` past 10-min threshold as `unknown_outcome` — fail-loud audit signal when a worker crashed mid-task |

### Notification policy (anti-spam tiering)

Push notifications are tiered to prevent the "nurse logs 15 vitals → family phone buzzes 15 times" failure mode:

| Event | Tier | Result |
|---|---|---|
| Emergency flag raised | `critical` | Push immediately, every device |
| Status change to "Critical" | `critical` | Push immediately |
| Status change (any other) | `important` | Push immediately |
| Shift handover recorded | `important` | Push immediately |
| Discharge | `important` | Push immediately |
| Vitals or notes (no status change) | `routine` | In-app inbox only — **no push** |

Source of truth: `src/domains/notifications/policy.py`. The mapping is centralised so a future per-user "Do Not Disturb" feature only touches that one file.

### FCM retry policy

- 3 attempts, 30-second delay between retries
- Invalid device tokens (FCM `UNREGISTERED` errors) are auto-pruned
- Every push attempt persists outcome to `notification_logs.delivery_status` — answer "did this go out?" retrospectively

---

## Backup and Restore

```powershell
# Backup (creates timestamped dump in backups/)
./scripts/backup_db.ps1

# Restore from a specific dump
./scripts/restore_db.ps1 -BackupFile backups/patstat-YYYYMMDD-HHMMSS.dump
```

Backups use Postgres custom format (`pg_dump -Fc`) — compressed, restorable to any compatible major version, supports parallel restore.

In production, schedule `backup_db.ps1` (or its bash equivalent on the server) via cron + offsite copy. Healthcare retention requirements vary by jurisdiction — set the rotation policy accordingly.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `An attempt was made to access a socket in a way forbidden by its access permissions` | Windows Hyper-V/WSL reserved port range | Run `netsh interface ipv4 show excludedportrange protocol=tcp`; pick host ports outside every range |
| `Couldn't import 'src.tasks.celery_app.celery_app': 1 validation error for Settings` on `celery_beat` | Missing env vars in the `celery_beat` block | Mirror env block from `api` / `celery_worker` (already fixed in `docker-compose.yml`) |
| pytest refuses to run with `REFUSING TO RUN TESTS — database URL does not appear to be a test DB.` | Working as designed — guard against accidental schema drop | Use `.\scripts\test.ps1` or set `DATABASE_URL` to a URL containing `test` |
| Alembic `Multiple head revisions` | Two migration files share a parent | `alembic merge -m "merge heads" <rev1> <rev2>` |
| Family member doesn't receive push when status changes | FCM token might be `UNREGISTERED` | Check `notification_logs.delivery_status` for that user; if `failed` repeatedly, ask them to re-register the device token |
| `notification_logs` row stuck in `queued` for over 10 min | Worker crashed mid-task | Reconciler will mark it `unknown_outcome` on next 5-min sweep; check worker logs for the crash window |
