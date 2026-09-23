# Engineering decisions

Deliberate choices and deviations from the `CLAUDE.md` baseline, with reasons.
Newest first within each phase.

---

## Phase 2 — shared live simulator

### Hidden truth versus known commands

The injected conditions split in two deliberately. `generation_factor`, `extra_load_w`,
`heater_stuck_on` and `contact_loss` are hidden: they never appear in any operational
payload. `payload_duty_percent` and the commanded heater mode are published in the frame
context as `known_commands`, because a real ground team knows what it commanded — and
that is precisely what makes an actuator mismatch detectable from observations alone in
Phase 5. Hiding the commands too would have made the later rule impossible to write
honestly.

### Sensor noise is clamped to the encodable range, not marked invalid

Noise around a true zero was pushing `heater_power` slightly negative and tripping the
physical-bounds check, so a healthy channel displayed as `INVALID`. A real sensor cannot
encode a value outside its own range, so the noisy reading is clamped while the *model*
value is still validated. `INVALID` therefore keeps its meaning: an impossible value from
the model, or a deliberately injected sensor fault later.

### Persist, then publish

Each tick stores its frame in a short transaction and only then publishes to subscribers,
so a database write failure can never be displayed as successfully recorded data. The
write happens off the run lock; if the run was reset while the write was in flight, the
frame is discarded rather than attached to the new run.

### Contact loss advances the vehicle but delivers nothing

An injected contact loss keeps the simulation stepping while publishing no frame. That
produces the distinction section 12.3 requires: the app stays connected and the run stays
RUNNING while telemetry ages. A browser or network outage instead marks the app
disconnected. The header shows the two as separate fields for this reason.

### Data age is measured on the client's own monotonic clock

The `telemetry_age` channel carries the gap between published frames, which is the
meaningful historical quantity. The *live* age on screen is computed from
`performance.now()` since the last received frame. Simulated time and wall-clock receipt
time are never mixed into one duration.

### SSE verified against real Uvicorn, not `TestClient`

Starlette's `TestClient` does not stream a never-ending response faithfully, and
`request.is_disconnected()` blocks under that transport. The disconnect poll was removed —
`StreamingResponse` already cancels the generator, and the `finally` unsubscribes — and
the stream is verified with two concurrent `curl -N` clients against a real server, which
is also the path the reverse proxy will exercise. What remains unit-tested is the part
with real logic: bounded fan-out and role filtering.

### Bounded queues drop slow clients instead of buffering

Each subscriber gets a 200-event queue. A client that falls behind is marked overflowed
and told to resubscribe, taking a fresh snapshot, rather than accumulating unbounded
backlog. Subscription and snapshot capture happen under the same short lock so nothing
slips through the gap between them.

### The export build has its own `distDir`

`next build` and `next dev` both wrote to `.next`, and running the build while the dev
server was up corrupted it into a 500. The export build now writes to `.next-export`.

---

## Phase 1 — foundation

### Python 3.14 everywhere, not a pinned-back 3.12

The spec floor is 3.11. The development machine runs 3.14.3, so before writing any code
every pinned dependency was checked for a `cp314` wheel — including the ones that
typically lag a Python release (NumPy 2.5.3, scikit-learn 1.9.1 for the optional Phase 10,
psycopg 3.3.6, argon2-cffi-bindings). All publish 3.14 wheels, so `backend/Dockerfile`
uses `python:3.14-slim` and container and laptop stay on the same interpreter. If a
future dependency turns out to lack 3.14 support, the fallback is to pin the image to
3.13 and keep the floor at 3.11.

### Synchronous SQLAlchemy, not the asyncio extension

The 1 Hz simulation loop writes one short transaction per tick. Phase 2 will run that
through `asyncio.to_thread` rather than blocking the event loop. Sync keeps Alembic,
pytest fixtures and the loop itself straightforward, and the write volume never justifies
an async driver. Revisit only if profiling shows the thread hop actually costs something.

### A bounded database connect timeout

`DB_CONNECT_TIMEOUT_SECONDS` (default 5) is passed to psycopg. Without it the API hangs
forever in startup when PostgreSQL is unreachable — observed directly during Phase 1 — so
the process never becomes ready and `/health` never answers. Section 2.3 requires that a
storage failure be *displayed* as an explicit storage error, which is impossible if the
process never starts. With the timeout the API starts, logs the failure and keeps serving
`/health` and the 401 path.

### `app/contracts/` as its own package

The proposed layout in section 4 does not name a home for the typed telemetry, incident
and action contracts. They are shared by `simulation/`, `monitoring/`, `forecasts/` and
`api/`, so putting them inside any one of those would invert the dependency. They live in
`app/contracts/` and everything else imports from there.

### Section 12.2 endpoints registered as honest 503s

Every endpoint in the API contract is registered now, with its real authentication, role
and CSRF guards active, and answers `503 NOT_IMPLEMENTED_YET` with the phase number that
will build it. Two reasons: the authorization boundary is testable from Phase 1 rather
than Phase 3, and the route contract cannot quietly drift. No endpoint returns invented
data to look finished.

### CSRF by double-submit cookie plus origin validation

The session cookie is `HttpOnly`; a second, deliberately readable `ps01_csrf` cookie is
echoed back in an `X-CSRF-Token` header on every mutation, compared against a SHA-256
hash stored on the session row. Allowed-origin validation runs on mutations *and* on
login. `SameSite=Lax` is set as well, but is not relied on alone.

### Unique constraint names left to the database defaults

`users.username` and `sessions.token_hash` use unnamed unique constraints in the
migration so PostgreSQL derives the same names SQLAlchemy's `unique=True` would
(`users_username_key`). Composite constraints that no default would name well keep
explicit names (`uq_commands_idempotency`, `uq_frames_run_seq`). This keeps
`alembic check` from reporting phantom drift between the models and the migration.

### Two ruff rules disabled

`B008` (function call in default argument) is FastAPI's documented `Depends(...)` idiom,
not an accidental mutable default. `UP042` (prefer `StrEnum`) is declined because
`class X(str, Enum)` is explicit about the mixin and behaves identically for Pydantic
serialization. Both are recorded in `backend/pyproject.toml` with the same reasoning.

### The full section 12.1 schema in one migration

Phase 1 only needs `users` and `sessions`, but section 12.1 specifies the essential
fields for all ten tables. Creating them together avoids nine follow-up migrations and
pins the storage contract early. `runs.private_checkpoint` is annotated in both the model
and the migration as simulator-only, so no later phase serializes it by accident.

### Starlette's test client uses `httpx2`

Starlette deprecates `httpx` 0.x for `TestClient` and asks for `httpx2`, so the dev
extra pins `httpx2==2.13.0`.
