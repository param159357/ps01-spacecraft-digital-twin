# Engineering decisions

Deliberate choices and deviations from the `CLAUDE.md` baseline, with reasons.
Newest first within each phase.

---

## Phase 9 — the reset button raises the commanded target, not the applied count

`computer_resets` is a target, not an event feed: the twin compares it against
`resets_applied` each tick and, if the target is higher, records exactly one timestamped
event. So "inject one reset" means "raise the target by one", and the only question is what
to add one to.

Adding one to `resets_applied` looks equivalent and is not. The vehicle applies one event
per tick, so a second click arriving inside the same second would read `resets_applied == 0`
again, command a target of 1 again, and produce one event for two clicks. Adding one to the
commanded target is correct in both cases. There is a regression test for exactly this, and
reverting the helper to the `resets_applied` version makes it fail.

The target is also re-read from `GET /simulator/state` at click time rather than taken from
the component's two-second mirror. A stale mirror would either skip an event or command
nothing, and a control that silently does nothing is worse than no control.

Three things the button deliberately does not do: it does not write an incident, it does not
touch the counter, and it does not use a new endpoint. It posts one bounded condition update
through the same path the presets use, and `RESET_RECURRENCE` is then opened — or not — by
the rule engine from observed telemetry. That is what makes the demonstration worth
watching: the incident is a verdict, not a stage direction.

## Phase 9 — frame 0 was never a frame

The run-control panel read `run.frame_seq`, which comes from the run metadata captured with
the snapshot and is not updated by telemetry events. It therefore read 0 for the whole
demonstration, next to a simulation that was visibly advancing.

It now reads the newest received frame's own `frame_seq`. When no valid frame has arrived it
shows an em dash rather than 0, because 0 is a real sequence number and using it for "nothing
yet" is the same category of error as showing a missing value as zero — which section 1.3
forbids everywhere else.

## Phase 9 — which advanced controls exist, and why mode is different

Each of the four was checked against the backend before any UI was written, because the
alternative is a decorative control that pretends to do something:

- **Starting SOC, starting spacecraft clock, starting payload temperature** are already
  parameters of `POST /simulator/run` with validated ranges, already used by the D03 and D04
  presets. Exposing them adds a UI for behaviour that was always there.
- **Operating mode** is real but is not a creation parameter — it is a known command on
  `ConditionUpdate`, and `MANEUVER` genuinely widens the configured rate and pointing
  profiles by the vehicle's `maneuver_scale`. The console therefore resets first and commands
  the mode immediately afterwards, so the new run begins in it. The panel says that plainly
  instead of implying mode is baked into the run, and mode stays changeable mid-run.

`SAFE` was left out of the list. The twin honours it, but starting a run in SAFE means
starting with the payload already shed — a scenario, not a setting, and one D03 already
covers. The section is collapsed by default because these are not the controls a
demonstration normally touches, and it resets into a paused run so the values are visibly
starting conditions rather than an edit to a run in flight.

## Phase 9 — the 28 channels stay read-only

Making a telemetry reading directly editable would be the quickest way to make any scenario
appear on screen, and it would hollow out the whole product. Every reading a console shows
has to be something the model computed from state the simulator actually holds; otherwise
detection, forecasting and investigation are all commenting on a number nobody derived.

The honest equivalent is what this change does: expose the *cause* — a reset, a starting
temperature, a mode — and let the readings follow. Where no such cause is modelled, as with
attitude disturbance, the answer stays no rather than a slider.

---

## Phase 9 — rehydration happens on a run's first frame, not at startup

The obvious place to reload active incidents is the lifespan. It is the wrong place. At
startup the restored run is PAUSED and no frame has been published, so the process does not
yet know which run will actually resume — and if the simulator resets instead of resuming,
anything loaded at startup would belong to a run that no longer matters.

`process_frame` already knows the run id of the frame it is about to evaluate, and it is the
only code path where a missing cache entry can do harm. So the load happens there, once per
run, immediately before the engine is called. A per-run set records what has been loaded, so
a reset mid-process rehydrates the new run without re-reading the old one.

The read runs through `asyncio.to_thread`, because the caller is the 1 Hz loop that also
serves SSE, and it is attempted at most three times per run. A database fault must not become
a query on every tick, and it must not stop deterministic monitoring either: the exception is
logged with its attempt number and the tick carries on, so rules still evaluate from observed
telemetry and an operator still gets warnings. The cost of giving up is stated in the log
line — a restart in that window may open a duplicate.

## Phase 9 — the row's primary key is the incident's identity

The first version of this rebuilt each incident with `Incident.model_validate(row.evidence)`
and let the payload supply the `id`. Every test passed, because `_persist_incident` writes
the payload keyed by the incident it came from, so the two always agree in normal operation.

They do not agree for a row that was copied. The real-server check inserted a duplicate by
cloning a row with its `evidence` JSON intact, and the duplicate report then named one
incident as both the duplicate and the canonical row — and a later write would have gone to
the wrong row entirely.

Identity now comes from `row.id` and `row.run_id`, and a payload that disagrees is logged.
The same reasoning applies to the rest of the overlap: severity, lifecycle, `last_seen_utc`
and acknowledgement are taken from the columns, because those are what later writes keep
current and a payload written by an older revision of this code must not resurrect a state
that has since moved on. The payload supplies only what no column carries — timeline,
evidence, source frames, suspected causes.

## Phase 9 — rule timers are deliberately not restored

Rehydration seeds `IncidentEngine.incidents` and nothing else. `_RuleState`, which holds the
persistence and healthy-dwell timers, starts empty.

That is a choice, not an omission. Restoring a dwell timer would mean resolving an incident
partly on the strength of observations a previous process saw and this one cannot check. The
first healthy frame after a restart therefore begins a new dwell and reports RECOVERING; the
incident resolves only once a full dwell of observations has arrived since the restart. It
costs one dwell of extra caution and it means recovery is always demonstrated by evidence
this process actually received.

## Phase 9 — missing data can no longer run down the dwell clock

Fixing rehydration exposed a second problem in the same area. A rule whose inputs are not
VALID appends no signal, so `_apply` never runs for it and its healthy-since timer is left
untouched. Untouched is wrong: the dwell is measured as elapsed simulated time from the
*first* healthy sample, so a long dropout followed by a single healthy frame would resolve an
incident having counted the entire gap as healthy. Section 1.3 says missing data is neither
zero nor healthy, and that includes not being healthy for the purposes of a dwell.

`evaluate` now collects the `(family, entity)` keys it actually had evidence for and breaks
the healthy streak of any active incident whose key is not among them. The dwell has to be
earned again from real observations. `DATA_STALE` is applied unconditionally on that path, so
it always counts as evaluated.

This was implemented rather than deferred because requirement 8 names it, and because the
existing test for it only passed by accident of ordering: the dropout happened while the
timer was already `None`.

## Phase 9 — a pre-existing duplicate is reported, not repaired

A run that restarted before this fix can hold two active rows for one deduplication key.
Three options were available: delete the extra row, close it, or leave it.

Deleting is out — it is stored evidence of something that really happened, and the
instruction was explicitly not to delete history. Closing it is a milder version of the same
thing: rewriting a lifecycle to make a count look tidier is still editing a record on the
strength of an inference. So the row is left exactly as it stands, with an append-only
`DUPLICATE_SUPERSEDED` timeline entry citing the canonical incident, a warning naming both
rows, and an entry in `OperationalService.superseded_duplicates`.

The consequence is stated plainly in the validation record: no console or API response shows
a duplicate, because only the canonical row is adopted, but `GET /history/summary` counts
distinct stored incidents and will still count both. The canonical choice is the earliest
`first_seen_utc`, tie-broken by id — deterministic, and the original incident rather than
whichever row the database happened to return first.

## Phase 9 — the application had no log output at all

While checking the duplicate warning on a real server it turned out that nothing the
application logs was reaching a handler. Uvicorn configures only its own loggers, so
`logging.getLogger("ps01")` and every module logger wrote into the void — including the
pre-existing `configuration loaded` and `database reachable` startup lines, which had been
invisible since Phase 1.

A requirement to report an inconsistency is not satisfied by a log line that is discarded, so
the lifespan installs a root handler when nothing else has. `basicConfig` is a no-op if the
root logger already has handlers, so a host that configures logging itself is not overridden,
and uvicorn's own loggers do not propagate to the root, so its access log is not duplicated.

---

## Phase 9 — RESET_RECURRENCE reads engineering detail, not the injection

The rule needs two things: how many resets have happened, and when. The counter is a
channel, so that half is easy. The times are the awkward half — the simulator knows them
exactly, and the temptation is to read `computer_resets` off the injected conditions.

That would be a fabricated capability. Section 1.3 is explicit that detection receives
observations and known commands and never hidden fault selectors, and a rule that reads the
injection would "detect" a recurrence the ground could not actually see.

So the rule reads `reset_events_s` out of the frame's engineering detail. That field is
already published, already displayed on the Flight console, and derived from the same
observation path as everything else. It arrives as a short comma-separated label rather than
a list of floats, which is why the parser accepts a string — a slightly awkward input is a
fair price for keeping the evidence boundary intact. The nine tests build frames directly
and never construct an `InjectedConditions`, so the boundary is pinned rather than merely
intended.

Three smaller choices inside the rule:

- **Distinct times, held in a set.** "Multiple distinct reset events" is the rule's own
  wording in section 8.1. The same event is re-published on every subsequent frame, so
  counting samples instead of events would open an incident from a single reset within two
  seconds.
- **Abstain when the counter is not VALID.** If the channel drops out, the rule neither
  opens an incident nor advances a healthy dwell. Section 1.3 again: missing data is not
  zero and not healthy. The test for this opens an incident first, then drops the counter
  for longer than the dwell, and asserts the verdict stays OPEN.
- **WARNING, not CRITICAL.** The configuration's `severity_default` is WARNING and nothing
  in the fixture justifies more. Repeated resets deserve investigation, not an invented
  severity.

## Phase 9 — the Flight channel table is built from server metadata

The table previously mapped three hardcoded arrays — attitude, wheel, link — which came to
ten of the twenty-two channels this role may read. The other twelve were reachable through
the API and shown nowhere, and the count was invisible, so nothing on the page contradicted
the impression that ten was all there was.

It now maps `Object.keys(meta)`, which is the role-filtered channel list the server already
returns in configuration order, with the count in the panel badge. The three arrays remain
for the grouped readout panels above, where the grouping is the point. This also means a
channel added to `telemetry.yaml` appears on the console without a frontend edit, and a
channel a role may *not* read cannot appear at all — the filtering stays on the server.

## Phase 9 — the investigation panel was lifted out of the incident detail

`IncidentPanel` nested `InvestigationPanel` inside the expanded incident detail, at the
bottom, below the acknowledge row. On the Flight console that put the AI investigation
several hundred pixels below the fold inside a box the operator had to open first — present
in the DOM, effectively invisible in the room.

`IncidentPanel` gained a `showInvestigation` prop, default `true` so the other specialist
consoles are unchanged, and Flight passes `false` while rendering `InvestigationPanel` as
its own top-level panel driven by the panel's `onSelect` callback. Only one of the two
mounts, so the stored analysis is still polled once.

## Phase 9 — what the Flight console deliberately does not get

- **No `FLIGHT_PROFILE_DEVIATION`.** Implementing it means deciding when a seeded
  deterministic profile counts as deviating from itself. Without a real attitude dynamics
  model there is no physical basis for the threshold, and the rule would be a random-number
  alarm. It stays `implemented: false` and `/api/config/coverage` says so.
- **No attitude disturbance injection.** Same reason. A control the judges can move that
  changes a number with no dynamics behind it is a prop, not a simulation.
- **No forecast.** Section 1.2 permits labelled deterministic profiles for attitude, wheel
  and link precisely because they are not physically modelled; a forecast line on top of one
  would be a claim the model cannot support. The channels report their prediction coverage
  as `PROFILE` or `RULE_ONLY` in the table's Coverage column.
- **No recommendation panel.** Power and Thermal have one because an implemented action
  exists in their scope. No action in `actions.yaml` addresses a computer reset, so a
  recommendation control on Flight would offer a fix that does not exist. The console
  acknowledges and investigates, and the incident panel says plainly that recovery is the
  master's.

## Phase 9 — restart duplicates an incident, and that was left alone

The restart check found that one continuing condition becomes two OPEN rows within one run,
because the lifecycle cache is in-process. Fixing it means rehydrating active incidents from
the database at startup for every rule — worth doing, but it is not a Flight-console change
and not what this task asked for. It is measured and recorded in the validation record
instead of being patched over, and specifically it is not worked around by having the rule
consult simulator truth, which would trade a visible limitation for an invisible lie.

---

## Phase 9 — recovery outcome measured before the command, not after

### A sentinel of zero made a queued command judge itself

`_command_start_load` held `(applied_tick, load_before)`, and acceptance seeded it with an
applied tick of `0` because the command had not been applied yet. The settling guard then
read `frame.tick_seq - 0 >= 3`, which is true on essentially every frame of a running
vehicle. So the first frame published after acceptance judged a command that was still
sitting in the queue: it compared the vehicle against itself, recorded `NO_CHANGE`, and
deleted the baseline. `mark_applied` then re-seeded the baseline from the frame the
command had *just* been applied on — already reflecting the change — so a real reduction
was arithmetically invisible.

It only surfaced when a frame was published between acceptance and application. One
comparison followed by an immediate approval usually applied on the very next tick, with
`mark_applied` running before the outcome check, and looked correct. Trying each duty
option first left enough time for a frame to land in between, and the demonstration's
headline number silently became a false failure.

The applied tick is now `None` until the command actually reaches the vehicle, and the
outcome check skips anything not yet applied. The baseline is whatever valid observed
load was measured at acceptance, and nothing later overwrites it.

The wider point worth keeping: `0` was standing in for "has not happened yet" in a
subtraction. A sentinel that is also a legal value in the arithmetic it feeds is not a
sentinel. `None` cannot be subtracted by accident.

## Phase 9 — deployment stack, verified locally

### Live observed-state and forecast caches are scoped to one run

`frame_seq` restarts at zero when the simulator creates a new run. The operations service
therefore clears its in-memory observed estimate, forecast and forecast-cadence marker
when an incoming frame's `run_id` differs from the preceding frame, before adopting that
frame. The first valid frame then forecasts immediately. Forecast crossings are used only
when the cached forecast belongs to the frame's run, and `/forecasts/latest` returns
unavailable rather than exposing a forecast whose run differs from the runner's active
run. Persisted forecast rows remain intact as historical evidence.

### Gemini receives minimal manual schemas only at the provider boundary

The real Gemini deployment proved that `gemini-3.5-flash`, the authorized key, manual
function calling and a small structured schema work. It also proved that merely removing
`additionalProperties` from Pydantic's generated schemas is insufficient: the remaining
`$defs`, `$ref`, metadata and constraints still caused HTTP 400 `INVALID_ARGUMENT`.

The Gemini adapter therefore supplies two small, fully inlined response schemas written
with only `type`, `properties`, `required` and `items`. They preserve every field and the
nested cited-statement shape of `InvestigationOutput`, plus every `HistoryReviewOutput`
field. These are transport hints, not replacements for the trust boundary: Gemini output
still passes the original strict Pydantic models, evidence-citation checks and enabled-
action checks. OpenAI continues to receive its original strict generated schema.

Provider failures log only a bounded, credential-redacted exception type and message.
Prompts, telemetry and tool evidence are never included in those log records.

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
