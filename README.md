# PS01 — Spacecraft Digital Twin

A mission-control style digital twin for a **fictional spacecraft already operating in
orbit**. One deployed site serves five consoles: a simulator, a master console and three
subsystem specialists (power, thermal/propulsion, flight systems).

The demonstration loop it is built around:

> inject a condition → shared telemetry → detect a problem → forecast its consequence →
> compare a supported action → master approves → observe whether recovery actually happens

The interface takes visual inspiration from mission-control layouts. It is **not** an
official NASA or ISRO system, carries no agency branding, and every physical constant is
a documented fixture for a fictional vehicle.

Build specification: [`CLAUDE.md`](CLAUDE.md). Current status: **Phase 9 local deployment
readiness complete; hosted verification pending**. The five-console demonstration,
stored history, bounded AI investigation path and persistent Compose stack are implemented.
See [`docs/validation.md`](docs/validation.md) for exactly which checks were executed and
which public-host checks remain blocked on access.

---

## Requirements

| Tool | Version verified here |
|---|---|
| Python | 3.14.3 (spec floor is 3.11) |
| Node.js / npm | 24.19.0 / 11.17.0 |
| Docker Desktop | 29.7.2 — used for PostgreSQL |
| git | 2.53.0 |

PostgreSQL runs in Docker; no local `psql` install is needed.

> **Windows note:** Docker Desktop requires the WSL 2 backend. Every command below was
> run from Git Bash on Windows 11. PowerShell works too, but `cross-env` in the frontend
> build script is what makes the build portable across both shells.

---

## First-time setup

```bash
git clone <your-remote> BINARY && cd BINARY
```

### 1. Environment file

```bash
cp .env.example .env
```

Then generate a real session secret and paste it into `.env` as `SESSION_SECRET`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`.env` is gitignored and must stay that way. `.env.example` contains no real secrets.

### 2. Database

```bash
docker compose up -d db
```

This publishes PostgreSQL on `127.0.0.1:5432` only. Data lives in the named volume
`ps01_pgdata` and survives `docker compose down` — but **not** `docker compose down -v`.

### 3. Backend

```bash
cd backend
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e .[dev]
```

On macOS or Linux use `./.venv/bin/python` instead of `./.venv/Scripts/python.exe`.

### 4. Database schema

```bash
cd backend && ./.venv/Scripts/python.exe -m alembic upgrade head
```

To review the SQL without touching a database:

```bash
cd backend && ./.venv/Scripts/python.exe -m alembic upgrade head --sql
```

### 5. Console accounts

```bash
cd backend && ./.venv/Scripts/python.exe -m app.cli.seed_users
```

This creates five accounts — `simulator`, `master`, `power`, `thermal`, `flight` — and
prints their generated passwords **once**. Store them then; they cannot be recovered.
There is no public signup and no role selector.

To issue new passwords later (this also revokes any live sessions):

```bash
cd backend && ./.venv/Scripts/python.exe -m app.cli.seed_users --reset-passwords
```

### 6. Frontend

```bash
cd frontend && npm ci
```

---

## Development

Two terminals:

```bash
cd backend && ./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

```bash
cd frontend && npm run dev
```

Open <http://localhost:3000> and sign in. The dev server proxies `/api` to the backend, so
the browser sees a single origin and the session cookie behaves exactly as it does behind
Caddy in deployment.

**Exactly one Uvicorn worker.** That process owns the simulation loop, so never enable
`--reload` for a demo and never run replicas. Stop the old process before starting a
replacement.

### Driving the simulation

Sign in as `simulator` and use the run controls:

- **Start / Resume** — begins a run, or resumes a paused one, at 1 Hz.
- **Pause** — stops advancing; the run stays selected and keeps its state.
- **Reset** — starts a new run ID and clears chart buffers. Previous runs stay in the
  database.

Every other console follows automatically; they do not need reloading. On a backend
restart the run comes back **paused** with its state restored, and the simulator resumes
it explicitly.

The simulator's hidden conditions (generation factor, unexplained extra load, stuck
heater, loss of contact) are visible only on that console and never reach an operational
payload. Payload duty and the heater thermostat are *commanded* state and are published
to operators as context — that is deliberate, and it is what makes a command-versus-
observation mismatch detectable later.

---

## Checks

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest -q          # backend tests
cd backend && ./.venv/Scripts/python.exe -m ruff check app tests ../scripts
cd frontend && npx tsc --noEmit                                # type check
cd frontend && npm run build                         # static export -> frontend/.next-export
```

The backend tests repoint `DATABASE_URL` at a separate `ps01_test` database before any
engine is built, so the simulation loop writes there too; the application database is
never touched. Tests that need PostgreSQL skip with a clear message when it is
unreachable.

The export build writes to `.next-export`, separate from the dev server's `.next`, so
`npm run build` and `npm run dev` can safely run at the same time.

---

## Deployed demo stack

For a fresh Linux server clone, install Node.js **24.x** and npm, then build the ignored
static export from the committed lock file. Generated `frontend/.next-export` is deliberately not
committed or transferred:

```bash
cd frontend
npm ci
npm run build
test -f .next-export/index.html
cd ..
docker compose up -d --build
```

Caddy refuses to start when `frontend/.next-export/index.html` is missing. It serves that export
and proxies `/api` to the API container, including Server-Sent Events. The API container
automatically runs `alembic upgrade head`; do not add a second manual migration step.

Set these in `.env` before bringing the stack up:

- `SITE_ADDRESS` — `:80` for a local or LAN demo, or your domain for automatic HTTPS.
- `APP_ORIGIN` — the exact browser origin, e.g. `http://localhost:8080` or
  `https://your-domain.example`. Cookie scope, CORS and the CSRF origin check all use it.
- `COOKIE_SECURE=true` on any HTTPS deployment.

For a domain deployment, `SITE_ADDRESS` is the bare domain while `APP_ORIGIN` is its exact
HTTPS browser origin without a trailing slash. Generate a fresh production
`SESSION_SECRET` on the server; never reuse the development value.

After the stack is healthy, provision it entirely inside the API container:

```bash
docker compose exec api python -m app.cli.seed_users --reset-passwords
docker compose exec api python -m app.cli.generate_history --completed-month
```

The first command prints five generated passwords once. Run it only in a private SSH
terminal and do not paste its output into chat or shared logs. The second command creates
minute summaries plus three bounded 1 Hz evidence windows for the most recently completed
UTC calendar month. Repeating the same seed/configuration/month returns the cached dataset.

Gemini is an optional backend-only provider. Configure these values privately in the
server `.env`; never use a `NEXT_PUBLIC_` variable:

```dotenv
AI_ENABLED=true
LLM_PROVIDER=gemini
LLM_MODEL=<model-confirmed-available-to-this-key>
GEMINI_API_KEY=<server-secret>
```

The exact model must be selected from the models available to the authorized key and is
not considered validated until one real investigation succeeds. OpenAI remains supported
through `LLM_PROVIDER=openai`, `LLM_MODEL` and `LLM_API_KEY`. With either provider absent,
disabled, timed out, over budget or invalid, the app labels the deterministic result
`TEMPLATE`.

Routine operations:

```bash
docker compose ps
docker compose logs -f api
docker compose restart api
docker compose down                 # stop, keeping data
```

Never run `docker compose down -v`; it deletes the PostgreSQL volume.

On restart the run comes back **paused**; the simulator account resumes or resets it
explicitly. Stopping containers does not delete stored data.

This stack has been built and run: the API image applies its migrations and serves one
Uvicorn worker, Caddy serves the export and proxies `/api`, a direct refresh of `/master/`
returns 200, and SSE arrives through the proxy unbuffered at roughly one frame per second.
`docker compose restart api` returns the run paused, and `docker compose down` preserved
every stored run. See `docs/validation.md` for the measurements and `docs/demo.md` for the
demonstration runbook and the offline fallback.

It has **not** been deployed to a public host, so HTTPS, `COOKIE_SECURE=true` and automatic
certificates remain unverified.

---

## Layout

```text
backend/app/
  config/        validated YAML fixtures (spacecraft, telemetry, rules, actions)
  contracts/     typed telemetry, incident, forecast, action and auth contracts
  storage/       SQLAlchemy models and session handling
  api/           routes, dependencies, role and CSRF guards
  simulation/    Phase 2   monitoring/ Phase 3   forecasts/ Phase 3   analysis/ Phase 6-7
frontend/
  app/           login and the five role routes plus shared history
  components/    shell, panels and shared presentation pieces
  lib/           typed API client, session context, shared types
scripts/         local wrappers for the container-native provisioning modules
docs/            demo.md, validation.md, decisions.md
```

## Accounts and routes

| Account | Role | Console |
|---|---|---|
| `simulator` | `SIMULATOR` | `/simulator/` |
| `master` | `MASTER` | `/master/` |
| `power` | `POWER` | `/power/` |
| `thermal` | `THERMAL_PROPULSION` | `/thermal/` |
| `flight` | `FLIGHT_SYSTEMS` | `/flight/` |

The server derives the role from the authenticated account and returns the destination;
the browser never assigns its own role. Only `master` executes recovery commands; only
`simulator` controls the run or sees hidden simulator truth.
