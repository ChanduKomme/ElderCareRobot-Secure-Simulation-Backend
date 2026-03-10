# ElderCareRobot Secure Simulation Backend

Security-focused backend prototype for the **ElderCareRobot simulation platform**.

It provides:
- JWT authentication with RBAC for `admin`, `relative`, and `provider`
- patient-to-user linking checks
- consent-driven data minimization at the API layer
- deterministic event simulation with rule-based alert creation
- append-only audit logging for authenticated requests, including denied requests
- critical alert evidence packs with SHA-256 integrity hashing and verification
- Docker Compose local runtime and FastAPI Swagger/OpenAPI out of the box

---

## Stack
- Python 3.11
- FastAPI
- SQLAlchemy
- SQLite
- Docker Compose

---

## Project structure

```text
eldercarerobot_backend/
├── app/
│   ├── main.py
│   ├── auth.py
│   ├── rbac.py
│   ├── models.py
│   ├── db.py
│   ├── simulation.py
│   ├── alerts.py
│   ├── audit.py
│   ├── evidence.py
│   └── schemas.py
├── scripts/
│   ├── demo_cli.py
│   └── verify_pack.py
├── data/
├── demo/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## Exact run steps

### 1) Extract and enter the project

```bash
unzip eldercarerobot_backend.zip
cd eldercarerobot_backend
```

You should see:

```text
app/  scripts/  data/  Dockerfile  docker-compose.yml  README.md  requirements.txt
```

### 2) Start the project with Docker Compose

```bash
docker compose up --build
```

When the server starts, open:

- Swagger UI: `http://localhost:8000/docs`
- OpenAPI JSON: `http://localhost:8000/openapi.json`
- Health check: `http://localhost:8000/health`

Default seeded admin account:
- username: `admin`
- password: `Admin123!`

### 3) Optional: reset to a clean database before testing

The SQLite database is stored at:

```text
data/eldercare.db
```

To start fresh:

```bash
rm -f data/eldercare.db
docker compose up --build
```

### 4) First sanity check

Open in browser or call with curl:

```bash
curl http://localhost:8000/health
```

Expected:

```json
{"status":"ok"}
```

---

## Full end-to-end test flow in Swagger

### 5) Login as admin

In Swagger, open:

```text
POST /auth/login
```

Use this request body:

```json
{
  "username": "admin",
  "password": "Admin123!"
}
```

Expected:
- you get `access_token`
- role should be `admin`

Now click the **Authorize** button in Swagger and paste:

```text
Bearer <YOUR_ACCESS_TOKEN>
```

### 6) Create one relative user

Call:

```text
POST /users
```

Body:

```json
{
  "username": "relative1",
  "password": "Relative123!",
  "role": "relative"
}
```

Expected:
- HTTP `201`
- response contains user `id`

Save the returned `id`.

### 7) Create one provider user

Call:

```text
POST /users
```

Body:

```json
{
  "username": "provider1",
  "password": "Provider123!",
  "role": "provider"
}
```

Expected:
- HTTP `201`
- response contains user `id`

Save this `id` too.

### 8) Create a patient

Call:

```text
POST /patients
```

Body:

```json
{
  "external_ref": "PAT-001",
  "full_name": "Eleanor Rigby"
}
```

Expected:
- HTTP `201`
- response contains patient `id`

Save the patient id, for example `1`.

### 9) Link the relative to the patient

Call:

```text
POST /patients/{patient_id}/link-user
```

Example for patient `1`:

```text
/patients/1/link-user
```

Body:

```json
{
  "user_id": 2
}
```

Use the actual relative user id.

Expected:
- HTTP `201`
- `"message": "User linked"`

### 10) Link the provider to the patient

Call:

```text
POST /patients/{patient_id}/link-user
```

Example:

```text
/patients/1/link-user
```

Body:

```json
{
  "user_id": 3
}
```

Use the actual provider user id.

Expected:
- HTTP `201`
- `"message": "User linked"`

### 11) Set consents

Call:

```text
POST /patients/{patient_id}/consents
```

Example:

```text
/patients/1/consents
```

Use this body to test privacy restrictions:

```json
{
  "location_visibility": false,
  "interaction_visibility": false,
  "adherence_visibility": true,
  "incidents_visibility": true
}
```

Expected:
- location data hidden
- interaction logs hidden
- adherence events still visible
- incident events still visible

### 12) Run the simulation

Call:

```text
POST /patients/{patient_id}/simulate
```

Example:

```text
/patients/1/simulate?seed=123&hours=24
```

Expected response similar to:

```json
{
  "created_event_count": 72,
  "created_alert_count": 3,
  "event_types": [
    "medication_due",
    "medication_confirmed",
    "medication_missed",
    "hydration_prompted",
    "no_response",
    "fall_suspected",
    "wandering_night",
    "normal_activity"
  ]
}
```

The exact counts may vary by seed logic, but the included build was validated with:
- 72 events
- 3 alerts

### 13) Check patient status as admin

Call:

```text
GET /patients/{patient_id}/status
```

Example:

```text
/patients/1/status
```

Expected:
- latest event
- risk score
- open alerts count
- admin sees more than restricted users

### 14) Check events as admin

Call:

```text
GET /patients/{patient_id}/events
```

Example:

```text
/patients/1/events?limit=100
```

Expected:
- all visible event records for admin
- admin is not restricted by patient consent

### 15) List alerts as admin

Call:

```text
GET /alerts?patient_id=1
```

Expected:
- admin sees all alerts for that patient
- critical alerts are included

---

## Test each role properly

Best way:
- keep Swagger admin session in one browser tab
- use another browser/incognito window for relative/provider login
- or use curl for separate tokens

### 16) Login as the relative

Call:

```text
POST /auth/login
```

Body:

```json
{
  "username": "relative1",
  "password": "Relative123!"
}
```

Authorize Swagger with this relative token.

### 17) Relative reads patient status

Call:

```text
GET /patients/1/status
```

Expected:
- allowed
- location fields redacted or omitted
- interaction summary hidden because `interaction_visibility=false`

### 18) Relative reads patient events

Call:

```text
GET /patients/1/events?limit=50
```

Expected:
- allowed
- no location details like coordinates or room names
- no interaction logs if consent blocks them

### 19) Relative reads alerts

Call:

```text
GET /alerts?patient_id=1
```

Expected:
- allowed
- alerts filtered by consent visibility
- location-driven alerts may be hidden if `location_visibility=false`

### 20) Relative tries to acknowledge an alert

Pick one alert id visible to the relative and call:

```text
POST /alerts/{alert_id}/ack
```

Expected:
- denied
- HTTP `403`

This is one of the required role restriction checks.

### 21) Login as the provider

Call:

```text
POST /auth/login
```

Body:

```json
{
  "username": "provider1",
  "password": "Provider123!"
}
```

Authorize Swagger with provider token.

### 22) Provider reads alerts

Call:

```text
GET /alerts?patient_id=1
```

Expected:
- allowed
- provider sees alerts allowed by consent and link rules

### 23) Provider acknowledges an alert

Pick a provider-visible alert id and call:

```text
POST /alerts/{alert_id}/ack
```

Expected:
- HTTP `200`
- alert status becomes `ack`

### 24) Provider adds a note

Call:

```text
POST /patients/{patient_id}/notes
```

Example body:

```json
{
  "note": "Initial clinical review completed.",
  "alert_id": 2
}
```

Expected:
- HTTP `201`
- note created

### 25) Provider resolves an alert

Call:

```text
POST /alerts/{alert_id}/resolve
```

Body:

```json
{
  "resolution_note": "Patient contacted and clinically stable."
}
```

Expected:
- HTTP `200`
- alert status becomes `resolved`

---

## Evidence pack verification

### 26) Get a critical alert’s evidence pack

Use a critical alert id and call:

```text
GET /alerts/{alert_id}/evidence-pack
```

Expected response shape:

```json
{
  "algorithm": "sha256",
  "pack": {
    "alert": { ... },
    "events": [ ... ]
  },
  "content_hash": "....",
  "signature": "...."
}
```

Only critical alerts support this endpoint.

### 27) Verify the evidence pack via API

Call:

```text
POST /verify-evidence-pack
```

Paste the full response from the evidence-pack endpoint as the request body.

Expected:

```json
{
  "valid": true,
  "reason": "ok",
  "computed_hash": "...",
  "provided_hash": "...",
  "signature_valid": true,
  "computed_signature": "...",
  "provided_signature": "..."
}
```

This proves integrity verification is working.

---

## Audit logging check

### 28) Login back as admin

Use admin token again.

### 29) Read audit logs

Call:

```text
GET /audit-logs?limit=50
```

Expected:
- all authenticated requests appear here
- allowed and denied actions are both logged
- denied relative ack attempt should be present
- fields include:
  - actor user id
  - role
  - endpoint
  - method
  - timestamp
  - patient scope
  - decision
  - reason
  - http status

This is the append-only audit trail requirement.

---

## Fastest automated demo flow

### 30) Run the included demo CLI

With the API already running:

```bash
BASE_URL=http://localhost:8000 OUTPUT_DIR=./demo python scripts/demo_cli.py
```

What it does automatically:
- logs in as admin
- creates relative and provider
- creates patient
- links users
- applies consent restrictions
- simulates 24 hours
- shows relative denial on alert ack
- shows provider ack, note, evidence retrieval, verification, and resolution
- writes evidence pack to:

```text
demo/evidence_pack_demo.json
```

### 31) Verify the saved evidence pack locally with the script

```bash
python scripts/verify_pack.py demo/evidence_pack_demo.json
```

Expected:
- printed JSON result
- `"valid": true`

---

## Run without Docker

### 32) Create venv and install

Linux/macOS:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Then run:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open:

```text
http://localhost:8000/docs
```

---

## One-command execution summary

If you only want the shortest possible full run:

```bash
unzip eldercarerobot_backend.zip
cd eldercarerobot_backend
docker compose up --build
```

Then in another terminal:

```bash
BASE_URL=http://localhost:8000 OUTPUT_DIR=./demo python scripts/demo_cli.py
python scripts/verify_pack.py demo/evidence_pack_demo.json
```

---

## What to capture for your assignment demo video

Record these in order:
1. login as admin
2. create relative, provider, patient
3. link users
4. set restrictive consent
5. simulate events
6. login as relative and show restricted view
7. show relative denied on `POST /alerts/{id}/ack`
8. login as provider and show ack/resolve works
9. open `/audit-logs` and show growth
10. get critical evidence pack
11. verify it using `/verify-evidence-pack`

---

## Common issues

### If port 8000 is busy

```bash
lsof -i :8000
```

Then stop the process, or change the port mapping in `docker-compose.yml`.

### If you want a fresh state

```bash
rm -f data/eldercare.db
docker compose up --build
```

### If Docker build cache causes confusion

```bash
docker compose down
docker compose build --no-cache
docker compose up
```

---

## Optional: run the whole flow from terminal

```bash
BASE_URL=http://localhost:8000 OUTPUT_DIR=./demo python scripts/demo_cli.py
python scripts/verify_pack.py demo/evidence_pack_demo.json
```
