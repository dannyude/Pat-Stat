# API Reference

The authoritative API reference is the live Swagger UI:

| Environment | Swagger URL |
|---|---|
| Local Docker | <http://localhost:8080/docs> |
| Local Docker (ReDoc style) | <http://localhost:8080/redoc> |
| Production | `/docs` is disabled in production; use the OpenAPI JSON spec for tooling |

This page covers the bits Swagger doesn't render well: authentication flow, the WebSocket protocol, and the standard error shapes.

---

## Authentication

All `/api/v1/*` endpoints (except `/auth/login`, `/auth/refresh`, `/hospitals/apply`, `/family/invites/accept`, and a few public read endpoints) require:

```
Authorization: Bearer <access_token>
```

### Token lifecycle

```
POST /api/v1/auth/login                       (public)
  └─► { access_token, refresh_token, user }

POST /api/v1/auth/refresh                     (public; body has refresh_token)
  └─► { access_token, refresh_token }   (rotated — old refresh is revoked)

POST /api/v1/auth/logout                      (Bearer + refresh in body)
POST /api/v1/auth/logout-all                  (Bearer)  — revokes EVERY refresh
```

| Token type | TTL | Where it lives |
|---|---|---|
| `access` | 30 min | Sent on every request in `Authorization` header. Stateless JWT — server doesn't track. |
| `refresh` | 7 days | Sent only to `/auth/refresh` and `/auth/logout`. Server tracks the JWT ID (`jti`) in Redis DB 2 — revoking deletes the key. |

### Status codes for auth failures

| Scenario | Status | Headers |
|---|---:|---|
| No `Authorization` header | 401 | `WWW-Authenticate: Bearer` |
| Invalid / expired JWT | 401 | `WWW-Authenticate: Bearer` |
| Refresh token used as access token | 401 | `WWW-Authenticate: Bearer` |
| Deleted / inactive user | 401 | `WWW-Authenticate: Bearer` |
| Valid token, role denied | 403 | (no header) |
| Valid token, hospital scope denies access | 404 | (uniform — see [Security](security.md#owasp-non-disclosure-pattern)) |

---

## Capability Map

Endpoints grouped by *who* uses them. Use Swagger for the full request/response schemas.

| Group | Prefix | Used by |
|---|---|---|
| Authentication | `/api/v1/auth/*` | All authenticated clients |
| Patients (CRUD) | `/api/v1/patients/*` | Clinical staff |
| Clinical updates | `/api/v1/patients/{id}/updates` | Clinical staff |
| Staff notes | `/api/v1/patients/{id}/notes` | Clinical staff (internal — never visible to family) |
| Emergency flags | `/api/v1/emergency-flags/*` | Clinical staff |
| Shift handovers | `/api/v1/shift-handovers/*` | Clinical staff |
| Dashboard | `/api/v1/dashboard/*` | Clinical staff |
| Notifications | `/api/v1/notifications/*` | All authenticated users |
| Family portal — invites | `/api/v1/family/patients/{id}/invites/*` | Hospital admins (sending), public (accepting) |
| Family portal — dashboard | `/api/v1/family/me/*` | Family users only |
| Hospital onboarding | `/api/v1/hospitals/apply` | Public (hospital sign-up) |
| Backoffice | `/api/v1/backoffice/*` | Super-admins only |
| Contact sales | `/api/v1/contact-sales` | Public (rate-limited) |
| WebSocket | `/ws/patient/{id}` | Clinical staff or linked family |

---

## WebSocket Protocol

The WebSocket endpoint is `/ws/patient/{patient_id}` (no `/api/v1` prefix). It does not appear in Swagger because OpenAPI doesn't model WebSockets well.

### Connection lifecycle

```
1. Client connects to /ws/patient/{patient_id}
   └─► server: TCP upgrade succeeds; awaits auth frame (10s timeout)

2. Client sends auth handshake:
   {"type": "auth", "token": "<access_token>", "last_synced_at": "<ISO timestamp>"}
                                                ^ optional — used for catch-up

3. Server validates:
     • JWT signature + expiry
     • Token type === "access" (refresh rejected)
     • User exists and is active
     • If role === family: verify FamilyPatientLink exists for this patient
   On any failure → close 1008 with reason

4. Server subscribes to Redis channel `patient:{id}:updates` BEFORE the
   catch-up DB query, then emits:
   {"type": "connected", "patient_id": "..."}

5. (If last_synced_at supplied) Server replays missed events from Postgres:
   - ClinicalUpdate, EmergencyFlag, ShiftHandover with created_at > last_synced_at
   - Each replayed event arrives as {"type": "missed_update", "event_type": "...", ...}
   - Capped at 24 hours of history

6. Live phase begins. Server forwards every Redis pub/sub message to the client
   while holding the line open. Client keeps the connection alive with pings
   every ~25s.
```

### Inbound events (server → client)

```json
{ "type": "connected",       "patient_id": "..." }
{ "type": "missed_update",   "event_type": "status_changed | emergency_flag_raised | handover_recorded", ... }
{ "type": "status_changed",  "patient_id": "...", "status": "...", "note": "...", "update_id": "...", "created_at": "..." }
{ "type": "emergency_flag_raised", "patient_id": "...", "flag_id": "...", "priority": "...", "reason": "...", "created_at": "..." }
{ "type": "handover_recorded", "patient_id": "...", "handover_id": "...", "summary": "...", "created_at": "..." }
{ "type": "catchup_failed",  "message": "Some events may have been missed. Please refresh." }
{ "type": "pong" }
```

### Outbound (client → server)

```json
{ "type": "ping" }
```

The client must send a ping every ~25 seconds; the server replies `{"type": "pong"}`. Without keepalives some networks (mobile carriers, corporate proxies) close idle connections after 60 s.

### Token re-validation

The server re-checks the JWT's `exp` claim every 30 seconds during the live phase. When the access token expires mid-connection, the server closes with code 1008 and reason `"Token expired"`. The client should reconnect with a refreshed access token — the existing 1008 close handler in the frontend already handles this case.

### Client example (browser)

```javascript
const ws = new WebSocket(`ws://localhost:8080/ws/patient/${patientId}`);

ws.onopen = () => {
  ws.send(JSON.stringify({
    type: "auth",
    token: accessToken,
    last_synced_at: lastSeenTimestamp,  // optional
  }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  switch (data.type) {
    case "connected":
      console.log("WS connected for", data.patient_id);
      break;
    case "missed_update":
      handleCatchUp(data);
      break;
    case "status_changed":
      handleLiveUpdate(data);
      break;
    case "emergency_flag_raised":
      raiseEmergencyAlert(data);
      break;
    case "pong":
      // keepalive ack — no action needed
      break;
  }
};

// Keep connection alive
const pingInterval = setInterval(() => {
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "ping" }));
  }
}, 25000);

ws.onclose = (event) => {
  clearInterval(pingInterval);
  if (event.code === 1008 && event.reason === "Token expired") {
    // Refresh access token, then reconnect
    refreshAndReconnect(patientId);
  }
};
```

---

## Standard Error Shapes

| Status | Body | When |
|---|---|---|
| 401 | `{"detail": "..."}` | Auth missing/invalid (sets `WWW-Authenticate: Bearer`) |
| 403 | `{"detail": "Access denied. Required roles: [...]"}` | Authenticated but wrong role |
| 404 | `{"detail": "Patient not found"}` (or similar uniform string) | Resource doesn't exist OR caller lacks access — see [Security](security.md#owasp-non-disclosure-pattern) |
| 422 | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` | Pydantic request body validation failed |
| 429 | `{"detail": "Rate limit exceeded: ..."}` | SlowAPI rate limit hit |
| 500 | `{"detail": "An unexpected error occurred. Our engineering team has been notified."}` | Anything unexpected — sanitised; full traceback only in server logs |

The 500 message is intentionally generic — it never leaks `str(exc)` to the client. See `src/main.py` for the global handler implementation.
