# Engineering decisions

Deliberate choices and deviations from the `CLAUDE.md` baseline, with reasons.
Newest first within each phase.

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
