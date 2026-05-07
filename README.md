<p align="center">
  <img src="https://raw.githubusercontent.com/microsoft/fluentui-emoji/main/assets/Stethoscope/3D/stethoscope_3d.png" width="80" alt="PatStat Logo">
</p>

<h1 align="center">P A T S T A T</h1>

<p align="center">
  <em>Real-time patient status tracking and family notification system</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/PostgreSQL-316192?style=for-the-badge&logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/Redis-DC382D?style=for-the-badge&logo=redis&logoColor=white" alt="Redis">
  <img src="https://img.shields.io/badge/Celery-37814A?style=for-the-badge&logo=celery&logoColor=white" alt="Celery">
</p>

<p align="center">
  <a href="https://github.com/dannyude/Pat-Stat/actions/workflows/ci.yml">
    <img src="https://github.com/dannyude/Pat-Stat/actions/workflows/ci.yml/badge.svg" alt="CI status">
  </a>
</p>

---

PatStat is a hospital-grade backend that lets clinical staff track patient statuses in real time while keeping families informed through push notifications and a dedicated mobile API. Each hospital runs in an isolated tenant context, and all access paths enforce hospital boundaries.

## Quick Start

### Prerequisites

- Docker and Docker Compose
- A Firebase service account JSON file for push notifications

### Five steps

```bash
# 1. Configure environment
cp .env.example .env

# 2. Add Firebase credentials
mkdir secrets
# Place firebase-service-account.json in secrets/

# 3. Launch services
docker compose up --build

# 4. Bootstrap the first super-admin (one-time)
python scripts/seed_super_admin.py \
  --email admin@patstat.io --password '<your-password>' --full-name 'Platform Admin'

# 5. (Optional) Create the test database for the test suite
docker exec patstat-db psql -U postgres -c "CREATE DATABASE patstat_test_db;"
```

### Endpoints once running

| Service | URL |
|---|---|
| API | <http://localhost:8080> |
| Swagger UI | <http://localhost:8080/docs> |
| Flower (Celery monitoring) | <http://localhost:8888> |
| PostgreSQL | localhost:6432 |
| Redis | localhost:6379 |

> Postgres is on `6432` and Flower on `8888` to dodge Windows reserved port ranges. See [Operations → Service Topology](docs/operations.md#service-topology-docker-compose) for the diagnostic steps if your machine reserves different ranges.

## Documentation

| | |
|---|---|
| 🏗️ [**Architecture**](docs/architecture.md) | Components, real-time pipeline, data model, roles |
| 🔌 [**API Reference**](docs/api.md) | Auth flow, WebSocket protocol, error shapes (full route list lives in Swagger at `/docs`) |
| 🛠️ [**Operations**](docs/operations.md) | Env vars, testing, CI/CD, migrations, backup, background tasks, troubleshooting |
| 🔒 [**Security**](docs/security.md) | Auth, RBAC, hospital isolation, OWASP patterns, PHI policy, rate limits |
| 👩‍💻 [**Development**](docs/development.md) | Project structure, adding endpoints / migrations / tests, conventions |

## Status

- **Tests:** 248 passing — run with `.\scripts\test.ps1`
- **Python:** 3.12+
- **Stack:** FastAPI · PostgreSQL · Redis · Celery · Firebase Cloud Messaging
- **License:** Proprietary. All rights reserved.

---

<p align="center"><sub>Healthcare-grade real-time backend for clinical staff and family.</sub></p>
