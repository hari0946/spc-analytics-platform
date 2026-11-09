# SPC Analytics Platform (historical data)

Statistical Process Control analysis of **uploaded historical** industrial data. There is no live monitoring.
Snowflake is the only database (raw SQL through the Snowflake Python Connector; no ORM).

```
React (Vite, TS, Tailwind, Recharts) ──REST──> FastAPI ──Snowflake connector──> Snowflake
                                                  │                              BRONZE → SILVER → GOLD
                                                  └── Python SPC engine ───────> SPC_RESULTS
```

## Run it

1. Copy `.env.example` to `.env` and fill in the Snowflake settings and a random `JWT_SECRET` (32+ chars). Never commit `.env`.
2. Create the schema (idempotent, tracked in `METADATA.SCHEMA_MIGRATIONS`):
   `cd backend && python -m scripts.migrate`
3. Create the first administrator (prompts for the password):
   `python -m scripts.create_admin --username <name> --email <email>`
4. Start it
   - **Docker:** `docker compose up --build` → app on http://localhost:5173, API docs on http://localhost:8000/docs
   - **Local:** `cd backend && uvicorn app.main:app --port 8000` and `cd frontend && npm install && npm run dev`

Python 3.12 is recommended. On Windows keep the virtual environment on a short path (e.g. `C:\spcvenv`);
very long paths break the `cryptography` DLL.

## Workflow
Upload CSV / Excel → preview + column mapping → load to BRONZE (raw, as received) → process (SILVER: typed, validated,
de-duplicated, rejects kept with reasons; GOLD: analysis-ready) → analyse (statistics, 10 control chart types, limits,
Western Electric / Nelson rules, capability, distribution) → dashboard, assistant, PDF / Excel reports.
Nothing is imputed or invented; rejected rows are listed with their reason.

## Modules (UI route → API)
| Module | UI | API |
|---|---|---|
| Overview dashboard | `/dashboard` | `GET /api/dashboard` |
| Process monitoring | `/monitoring` | `GET /api/data/sensor/series` |
| Control charts, statistics, limits, violations, capability, distribution | `/analysis` | `GET /api/analysis/{statistics,control-chart,limits,violations,capability,distribution}` |
| Data selection / filters | `/explorer` | `GET /api/data/{sensor,production}[/options,/summary]` |
| Upload & pipeline | `/uploads` | `/api/uploads/...` |
| AI assistant | `/assistant` | `POST /api/assistant/ask`, `GET /api/assistant/summary` |
| Reports (PDF / Excel) | `/reports` | `POST /api/reports` |
| Administration | `/admin` | `/api/users`, `/api/roles`, `/api/plants`, `/api/lines`, `/api/assets`, `/api/tags`, `/api/audit-logs` |

Full interactive documentation: `/docs` (Swagger) on the API.

## Roles
`ADMIN` (all), `ENGINEER` (upload, analyse, reports, manage plants/assets/tags), `VIEWER` (read data/results, reports).
Custom roles can be created from the permission list in the admin UI.

## Tests
- Fast, no Snowflake: `cd backend && python -m pytest -m "not integration"` (statistics, control charts, rules, capability,
  distribution, parsing, SQL builders, security hardening).
- Full, against your Snowflake account (about 25 minutes; uses clearly marked `sample_*` data and removes it afterwards):
  `python -m pytest`
- Performance on 100k generated rows: `python -m scripts.perf_check 100000`
- Frontend: `cd frontend && npx tsc -b && npm run build`

## Design notes
- SPC calculations live in `backend/app/spc_engine` and are independent of FastAPI and Snowflake.
- Heavy transformation runs inside Snowflake (`app/services/pipeline_sql.py`); only the selected page or aggregate
  is transferred to Python. User-supplied values are always bound parameters.
- Snowflake enforces only NOT NULL; uniqueness and referential integrity are enforced in the service layer.
- Sessions are server-side revocable (JWT + `USER_SESSIONS`); passwords are bcrypt hashes; failed logins are throttled.

## Try it with sample data
`sample_data/SAMPLE_sensor_history.csv` is generated sample data (not real plant data): 960 readings of temperature,
pressure, speed and flow with a planted shift and spike. Upload it as *Sensor readings* on the **Data upload** page.
