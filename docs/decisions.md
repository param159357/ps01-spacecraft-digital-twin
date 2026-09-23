# Engineering decisions

Deliberate choices and deviations from the `CLAUDE.md` baseline, with reasons.
Newest first within each phase.

---

## Phase 9 — deployment stack, verified locally

### Gemini response schemas are converted only at the provider boundary

The real Gemini deployment proved that `gemini-3.5-flash`, the authorized key and the
manual function-calling turn work, but both structured-output paths returned HTTP 400.
Pydantic correctly emits `additionalProperties: false` for the application's strict
models; Gemini rejects that keyword in `response_json_schema`, including when nested under
`$defs`.

The Gemini adapter now recursively removes only the `additionalProperties` keys from a
copied provider schema. The Pydantic models remain `extra="forbid"`, and the returned JSON
still passes the original model, evidence-citation and supported-action validation. The
OpenAI schema path is unchanged. This keeps provider compatibility separate from the
application's trust boundary.

### Fresh servers build the static export in place

`frontend/.next-export` remains ignored. A fresh deployment installs the verified Node 24.x line,
runs `npm ci` and `npm run build`, and checks `frontend/.next-export/index.html` before Compose is
started. This matches the current bind-mounted Caddy architecture without committing a
large generated tree or adding an artifact-transfer channel. Caddy now also fails its
container start immediately if the file is absent instead of serving an empty site.

The first Phase 9 readiness pass documented and mounted `frontend/out`, even though
`next.config.ts` has used `.next-export` as the export `distDir` since Phase 2. A leftover
local `out` directory masked the error; a fresh Google Cloud clone exposed it. Compose and
all current deployment commands now use the actual directory directly, with no symlink.

### Provisioning is part of the API image, not a host-Python procedure

The original account and history scripts lived outside the backend Docker build context,
so their documented commands could never work on a server with only the Compose stack.
Their logic now lives under `app.cli`; the old scripts are thin local wrappers. Both CLIs
therefore use the container's configured `DATABASE_URL`, and neither needs a host virtual
environment or repository files outside the API image. The history command defaults to
the most recently completed calendar month only when `--completed-month` is explicit and
retains the existing seed/config/month cache key.

### Gemini uses the official SDK but remains unvalidated without a real call

Native Gemini support uses the pinned `google-genai==2.25.0` SDK. The adapter performs one
manual function-calling turn using only the four authorized evidence tools, then one
structured JSON turn validated by the existing investigation schema and citation/action
guards. It shares the existing timeout, retry-attempt budget, daily budget, queue,
cooldown and TEMPLATE fallback. The key is a backend-only runtime environment value and
is never a Docker build argument or frontend variable.

No authorized key or tested model was placed in this local environment, so the adapter's
contract is tested but live Gemini mode is not claimed. Model selection is deferred until
the server can list what the user's key actually supports; documentation alone is not
treated as proof of availability.

### Evidence revisions now represent meaningful incident transitions

The Phase 7 implementation incremented `evidence_revision` on every telemetry sample.
That made the incident/revision cache practically ineffective and would have spent provider
budget repeatedly for an unchanged incident. Revisions now advance on severity, lifecycle,
acknowledgement or measured command-outcome transitions, while the latest stored observed
evidence continues to update. Deterministic monitoring cadence is unchanged.

### The deployment artifact was verified without a host

Phase 9 asks for a deployed persistent target, and no host has ever been chosen or
authorized for this project. Rather than stop, the phase was split: everything that does
not need a host was done and measured, and the hosted URL is recorded as blocked.

That split is deliberate, because the part that does not need a host is the part that
actually breaks. Section 14 warns against discovering hosting incompatibilities at the
end, and until now the API image, Caddy and the proxied SSE path had never been run — the
project had only ever started the `db` container. Building and running the real Compose
stack proved the artifact works: migrations apply at container start, one Uvicorn worker
owns the loop, Caddy serves the export with trailing-slash routes surviving a direct
refresh, and SSE arrives through the reverse proxy unbuffered. What remains unverified is
specifically the hosted concerns — HTTPS, `COOKIE_SECURE=true`, automatic certificates and
a public domain.

### `.env.example` shipped an origin that breaks the Compose stack

`APP_ORIGIN` was `http://localhost:3000`, the Next dev server, while `HTTP_PORT` was 8080
and Caddy served there. Anyone following the documented deployment path would have had
every sign-in rejected as a cross-site request, with a 403 and no obvious cause. The
default is now the Compose origin, because that is the one that fails: `npm run dev` on
port 3000 keeps working regardless, since `allowed_origins` already adds localhost:3000
automatically while `COOKIE_SECURE` is false.

This was confirmed twice — once by needing the override to bring the stack up, and once by
watching the LAN address return 403 on login while the static site and `/api/health`
answered normally from the same address.

### The demonstration guide records observed numbers, not intended ones

`docs/demo.md` was a Phase 1 skeleton listing scenarios "to be written". It now walks
through D01–D06 with the exact simulator preset each one uses and the values actually
observed on verified runs, including the ones that are unflattering: the accepted command
that changes nothing, and the payload operating time that never comes back. Where a value
drifts between runs the text says "about". No scenario is described as working that has
not been run.

---

## Phase 8 — essential integration checks

### Hysteresis is applied by narrowing the limits while an incident is active

`rules.yaml` had carried `hysteresis_c: 2.0` since Phase 1, with a comment spelling out the
section 8.2 behaviour — "40 C warning recovers below 38 C" — and no code had ever read it.
A temperature hovering on its operating limit therefore re-crossed on every sample and the
incident oscillated between `OPEN` and `RECOVERING`.

The band now narrows the operating limits *only while an incident is already active*, so
the threshold to open is unchanged and the threshold to recover sits inside it. That keeps
the asymmetry in one place rather than scattering two sets of limits through the rule, and
it means a channel with no active incident is still judged against exactly the limits
`telemetry.yaml` declares.

### A CRITICAL thermal band is deliberately absent

`critical_persistence_s: 2.0` was also configured and also unread. Section 7.3 illustrates
a critical band at 45 °C for 2 s, but no channel carries a critical bound, so implementing
the parameter would have meant inventing per-node thresholds for four nodes — a fixture
design decision, not an integration fix. The parameter was removed and the reason recorded
in `rules.yaml` itself. Thermal incidents are `WARNING` only: the severity a console shows
is the severity the configuration can justify. Adding critical bounds per node remains a
small, separate change.

### Configuration that describes behaviour must be behaviour that exists

Both of the above are the same lesson, and it is the one worth keeping: a parameter sitting
in a validated configuration file reads as a promise about how the system behaves. Two of
them had been quietly false since Phase 1, in a file whose whole purpose is to be the
honest description of the vehicle. Anything added to `rules.yaml` or `spacecraft.yaml`
should arrive with the code that reads it, or with a comment saying plainly that it does
not yet.

### The pending-panel pattern needs removing when the feature ships

Phase 1 deliberately shipped honest "not built yet" panels rather than placeholder data.
Phase 4 built the subsystem diagram and rendered it on the master console, but left the
placeholder that announced the diagram as unbuilt sitting at the bottom of the same page.
For four phases the master console simultaneously displayed the feature and denied having
it. Only a browser review caught it — no test asserts the absence of a panel.

---

## Phase 7 review — defects found by independent verification

Two defects were found by reviewing the Phase 6/7 work against the repository rather than
against its completion report, and driving the real HTTP API. Both are fixed.

### History is scoped by role, exactly as the live incident list is

`GET /history/summary` and `POST /history/review` guarded only "is this an operational
account". Signing in as `flight` and as `master` and requesting the same window returned
**byte-for-byte identical** responses, including a recurrence row naming
`ACTUATOR_MISMATCH` on `OPTIONAL_A` — a power entity a flight specialist cannot act on.
Section 3 requires role authorization on every endpoint "including SSE and history",
section 12.2 lists both routes as role-scoped, and Phase 4 had already established the
pattern on `GET /incidents`.

The filter is applied to the incident rows themselves, before any statistic is derived, so
severity mix, durations, recurrence and command-outcome counts cannot leak another
subsystem's activity through an aggregate. `hidden_from_this_role` reports how many
incidents in the window belong elsewhere — a count with no detail, for the same reason
Phase 4 added it: an empty list must not read as a quiet month. Coverage is deliberately
*not* scoped, because minute coverage is a property of the dataset, not of a subsystem.

The role is part of the review cache key. Two roles legitimately compute different facts
for one window, so a review generated for the master must never be served to a specialist,
and `GET /analyses/{id}` now fails closed: an analysis whose scope cannot be established
is refused rather than returned.

### An AI-path failure cannot disturb the live loop

`_enqueue_incident_change` ran on the event loop and was unguarded, while every
neighbouring database call in `process_frame` deliberately used `await
asyncio.to_thread(...)`. Two consequences: the enqueue's `SELECT` and `INSERT` blocked the
loop that also serves SSE, and because `enqueue_incident` raises (`KeyError` for a missing
incident, or any database error), an exception propagated out of `process_frame` and
abandoned the rest of that tick's incident batch — incidents after the failing one were
never published. The runner's per-tick `except` would have kept the loop alive and hidden
it, which is precisely how the Phase 5 console freeze survived three phases.

The enqueue now runs in a worker thread and swallows its own exceptions with a log line.
By the time it runs the incident is already persisted and published, so nothing
deterministic depends on it. Section 1.3 is explicit that AI failure must not delay or
disable telemetry, deterministic warnings or forecasts.

### Known, not fixed

Investigation dedup keys on `(incident_id, evidence_revision)`, but `evidence_revision`
increments on every persisted sample — a live incident reached revision 38 in about forty
seconds — so two manual investigations seconds apart produce two separate analyses and the
cache never hits for a live incident. The daily budget and the automatic cooldown still
bound provider spend, so this wastes work rather than exhausting anything. It should be
resolved before a provider is ever enabled, by keying on a revision that changes when the
evidence materially changes, or by giving manual requests a short cooldown of their own.

---

## Phase 7 — bounded live investigator

### One process owns one analysis worker

`AnalysisService` owns a priority queue capped at ten pending jobs and exactly one worker.
Critical incident work precedes warning/history work. Incident/revision keys suppress
duplicates, a newer queued revision supersedes an older one, and automatic triggers have
a 120 s cooldown. Deterministic incident publication happens before enqueueing, so the
provider path cannot delay telemetry, forecasts or command outcome handling. This matches
the existing single-API-process deployment contract; multiple API workers are unsupported.

### Provider scope is fixed by the server, never selected by the model

The provider sees only four function names. Their schemas accept no model-selected scope:
the server fixes the incident ID, source dataset and limits. Recent telemetry is capped at
five minutes and 60 frames, similar incidents at ten, and tools reject extra arguments.
There is no arbitrary SQL, command, threshold, simulator or checkpoint tool. The final
schema rejects extra fields, unknown citations and disabled action IDs.

### OpenAI Responses is the sole optional provider integration

The implementation uses the Responses REST contract directly through the standard
library: one tool-selection response and one strict JSON-schema response, at most four
tool calls, three total HTTP attempts, configured timeout/output tokens and `store:false`.
No SDK dependency and no speculative Claude/second-provider adapter were added. A provider
result is `LLM` only after schema and citation validation; all failures are `TEMPLATE`.

### Budget reservations are conservative and failed calls count

Before starting a provider job, the service requires room for the full maximum of three
HTTP attempts. Actual attempts are persisted even when validation or transport fails and
are included in the UTC-day total. This can leave a small unused remainder near the cap,
which is preferable to starting a job that cannot finish within budget.

### History requests use the same queue and exact-window cache

Daily, weekly, monthly and custom reviews submit to the same worker and budget gate as
incident investigations. The cache key includes the explicit dataset, period and exact
UTC bounds, so LIVE/SYNTHETIC/REPLAY cannot collide. With no provider, the Phase 6
deterministic renderer remains the saved `TEMPLATE` result.

### Process restart defers orphaned work

Provider calls are in memory and cannot be resumed safely after restart. Startup marks
persisted `QUEUED` or `RUNNING` rows `DEFERRED` with `PROCESS_RESTART`; it never claims
they completed, and a later matching request can enqueue fresh work.

---

## Phase 6 — historical data and recurrence

### Historical code has a storage-only dependency boundary

`analysis/history.py` and `analysis/tools.py` select stored observations, incidents,
timeline events, commands and forecasts only. They do not import the simulator package
and never select a run checkpoint. The public summary and template report were also
scanned as raw JSON for all seven hidden condition names plus checkpoint/internal-truth
labels. This is structural, not a promise that callers will remember to filter later.

### A continuing incident is counted from the incident row

All counts and recurrence groups use distinct `incidents.id` values. Timeline changes
and evidence frames may be numerous, but they cannot inflate the incident count. Duration
is the incident interval clipped to the selected window. A recurrence is a new incident
with the same rule/entity pair, not another event on one open incident.

### Command-to-incident links live in timeline evidence references

The existing command schema deliberately has no `incident_id`. Rather than add schema
just for reporting, command status and measured-outcome changes write an
`incident_events` row whose `evidence_ids` contains `command:<uuid>`. Historical action
classification follows that explicit stored link. Missing, rejected, accepted-but-
ineffective, unknown and improved remain separate categories.

### The synthetic month is minute evidence with three short 1 Hz excerpts

The generator advances the shared electrical model and the stability-substepped thermal
model once per minute, storing 43,200 June summaries rather than 2.6 million frames. It
stores three five-minute 1 Hz excerpts (900 frames total) around selected incident
openings. The labelled recurring case is an observed 150 W draw on `OPTIONAL_A` while its
stored equipment schedule calls for 0 W, every Tuesday at 10:00 UTC for 20 minutes; the
rest of the month is clean comparison time. The dataset is cached by month, seed,
spacecraft config version and schedule parameters. A partial cache is preserved and
reported instead of silently deleted.

### Phase 6 reviews are deterministic templates

`POST /history/review` renders computed facts into `analyses` with generation mode
`TEMPLATE` and a stable window cache key. It has no provider, prompt, token queue,
scheduler or generated diagnosis. The report explicitly says it does not infer hidden
truth or establish root cause. Provider-backed interpretation remains Phase 7.

---

## Phase 5 — major and delayed-intervention cases

### The optional branch is modelled, not simulated by a magic number

`power.optional_branch` is a real load with a rated power and a declared share of its heat
reaching the payload node. When it fails on, demand rises by exactly its rated power and
the payload node warms because of that same power — so D04's thermal evidence has a
modelled electrical cause rather than a hand-placed temperature offset. Isolating it
removes both at once.

### Commanding isolation is not the same as the isolator obeying

`truth.branch_isolate_commanded` records what the ground commanded and is published in
`known_commands`. Whether the branch actually de-energises depends on the hidden
`isolator_failed` injection. That separation is the whole point of the phase: with the
isolator failed, the command is genuinely `ACCEPTED` and then `APPLIED`, and measured
demand does not move, so the outcome is reported `NO_CHANGE` and the master console says
recovery failed. Nothing in the path is allowed to infer success from the fact that a
command was applied.

### Isolation removes a load; it does not repair hardware

The trade-off text says so explicitly, and the model backs it up: releasing the isolator
brings the fault straight back, because nothing repaired the branch. A test asserts this,
so the claim cannot quietly drift into "the software fixed it".

### The irreversible consequence is lost operating time, not battery damage

Section 13.1 forbids claiming permanent battery damage from low charge alone, and this
model has no damage state — a drained battery recharges normally, and a test asserts that.
The consequence D03 does record is `payload_outage_s`: simulated seconds during which
payload operation was commanded but not served, because the declared load shedding had
dropped it. Those seconds accumulate and never decrease. A recovery approved afterwards
restores the payload but cannot give the lost operating time back, which is exactly the
point the delayed-intervention case needs to make.

### ACTUATOR_MISMATCH compares a known command with a measured channel

Both sides are legitimately available to an operator: the commanded thermostat state is a
known command in frame context, and heater power is a measured channel. The rule needs no
access to simulator truth. It reports a discrepancy and does not claim to know whether the
actuator, its sensor or the command path is at fault.

### Isolation forecasts subtract a configured parameter, not hidden truth

Projecting isolation subtracts the branch's *rated* power, which is a configured vehicle
parameter the ground legitimately knows, and the projection states in its assumptions that
it presumes the isolator responds. The actual outcome is still judged afterwards from
measured telemetry — which is why the failed-isolator case reports `NO_CHANGE` despite an
optimistic projection.

### Engineering detail may carry a label; telemetry channels may not

`TelemetryFrame.values` is strictly numeric, boolean or null — that is the channel
contract. `engineering` is deliberately not a channel, and two of its entries are short
labels: which loads are currently shed, and when resets occurred. Typing it numeric-only
looked harmless because those entries are `None` almost all the time, and it went
unnoticed until the first real load-shedding event stopped frame construction outright.
`EngineeringValue` now admits a string while a separate validator keeps NaN and Infinity
out of the numeric entries, so the JSON guarantee in section 6.2 still holds.

The wider lesson recorded here: a test that exercises a model path is not the same as a
test that exercises the *observation* path. The shedding behaviour had model-level tests
from Phase 2 and they all passed, because none of them ever built a frame.

### Scenario presets use labelled starting conditions

D03 and D04 reset to a stated state of charge, spacecraft clock and payload temperature so
each scenario reaches its evidence within a demo window instead of after twenty minutes.
Section 7.1 permits beginning near a boundary provided it is visibly labelled; no timestamp
is falsified and no result is forced. The simulator console states this next to the preset
buttons.

---

## Phase 4 — five mission-control dashboards

### Flight-systems channels are profiles, and say so everywhere

`simulation/profiles.py` produces attitude, wheel, link and reset readings as pure
functions of simulated time plus a per-run phase offset derived from the seed. No
rigid-body dynamics, no random generator, so a scenario replays identically. Every one of
these channels carries `PROFILE` prediction coverage in `telemetry.yaml`, the flight
console states it in its own header, and the forecast engine does not project them. The
wheel's current follows its acceleration rather than being an independent number, so
speed and current cannot disagree arbitrarily — but wheel current alone still does not
establish a root cause.

### The contact window governs the high-rate link only

`link_snr` and `packet_error_rate` are measured during a scheduled pass and reported
`MISSING` with a reason outside one. Basic telemetry keeps flowing between passes, as a
continuous beacon would.

This is a deliberate simplification. A stricter reading of section 7.5 would stop
delivering telemetry entirely between passes, which is more faithful but would leave
every dashboard frozen for most of a 90-minute cycle — unusable as a demonstration. The
behaviour section 12.3 actually needs, a real loss of signal that leaves the app connected
while readings go stale, is modelled separately by the simulator's `contact_loss`
injection and was verified in Phase 2. Reporting `None` rather than a low SNR is the
difference between "no measurement" and "a bad link", and it is what stops an expected
contact gap from reading as a radio failure.

### A reset count of zero is a real observation

`computer_reset_count` reports `0` with quality `VALID`, not `MISSING`. Zero resets is a
true count. The simulator can raise `computer_resets` to make the vehicle record one
timestamped event per increment, and those events sit in engineering detail beside the
cumulative counter. A new run restarts the counter without implying a physical reset.

### Specialists see only their own subsystem's incidents

`GET /incidents` filters by `may_act_on`, so a power incident never appears on the flight
console. The response also carries `hidden_from_this_role`: a count, with no detail, of
active incidents elsewhere. Without it a specialist could read an empty list as "the
vehicle is quiet" while another subsystem is in trouble. An earlier draft added a
`scope=all` parameter so specialists could see everything read-only; it was dropped
because it widens visibility beyond what section 3 grants, and the master console already
holds the shared picture.

### Limit proximity is measured against the band width, not the limit's magnitude

The "approaching a limit" amber used 5% of the limit's absolute value, so a battery whose
normal operating band is 24–29 V read amber above 27.55 V — most of the time it is
charged. It now uses 5% of the band width. A console that cries warning during normal
operation teaches operators to ignore it, which is worse than no colour at all.

### The master keeps its own operations panel

The three specialist consoles share a new `IncidentPanel` with acknowledge and evidence
but no approval, because only the master executes recovery. The master keeps the richer
Phase 3 panel that carries the comparison, approval and observed-outcome workflow. Two
components rather than one prop-driven component is the deliberate choice: the Phase 3
flow is verified end to end, and rewriting it to share code would have risked a working
demonstration for tidiness.

---

## Phase 3 — first complete demonstration

### Observed state is a hard module boundary

`forecasts/estimator.py` is the only forecast initializer. It accepts a frozen
`TelemetryFrame` and nominal configuration and has no import from `simulation.truth`.
It rejects the estimate when any required channel is not `VALID`, initializes energy
from observed SOC, derives sunlit generation from observed voltage/current, and carries
only public known commands. Monitoring follows the same `value_if_valid` rule. Hidden
conditions and `runs.private_checkpoint` never enter either path.

### Forecast coverage is deliberately selected, not universal

The 30-minute engine projects battery reserve and the selected battery thermal node. It
uses the configured illumination schedule, observed persistent non-payload demand,
estimated generation, observed heater power/current, and nominal node parameters. The
result remains `PARTIAL`: it does not imply that every displayed thermal or flight
channel is forecastable.

### D01 and D02 use labelled starting-condition presets

The simulator UI can reset D01 to ten simulated seconds before the configured eclipse
and D02 to 24% SOC with a 200 W unexplained load. These are simulator-only, bounded,
explicitly labelled fictional starting conditions. Operators receive only the resulting
observations. This makes the two scenarios reproducible without waiting an hour or
falsifying received timestamps.

### Accepted, applied, and improved remain separate

Approval stores `ACCEPTED`, queues the predefined payload-duty command for the next tick,
then records `APPLIED`. Outcome is decided from later valid `load_power` observations
against the pre-application observed baseline. The first browser run found and fixed a
bug that had captured the baseline after application and incorrectly reported
`NO_CHANGE` despite an 80 W measured reduction.

### Live orchestration is in-process; evidence is persisted

Active engine state and unexpired proposals are owned by the one API process. Incidents,
forecasts and commands are persisted, but Phase 3 does not rehydrate active proposals or
the in-memory lifecycle cache after restart; a restart returns the run paused and the
operator compares again. Durable orchestration is outside the single-process hackathon
scope.

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
