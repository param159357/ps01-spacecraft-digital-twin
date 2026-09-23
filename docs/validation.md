# Validation record

Only checks that were actually executed appear here, with their real results. Nothing is
recorded as passing because it "should" pass.

---

## Phase 4 — five mission-control dashboards

**Date:** 2026-09-23
**Environment:** Windows 11 Pro, Python 3.14.3, Node 24.19.0, PostgreSQL 17 (Docker).

### Executed checks

| # | Check | Command / method | Result |
|---|---|---|---|
| 1 | Full backend suite | `pytest -q` | **91 passed**, 0 failed, 61 s |
| 2 | Phase 4 focused suite | `pytest tests/test_phase4.py -q` | **12 passed** |
| 3 | Lint | `ruff check app tests ../scripts` | **All checks passed** |
| 4 | Frontend types | `npx tsc --noEmit` | **exit 0** |
| 5 | Static export | `npm run build` | **exit 0**, 9 routes |
| 6 | Schema drift | `alembic check` | **No new upgrade operations detected** |
| 7 | Profiles are deterministic | `test_profiles_are_deterministic_for_a_given_seed` | same seed reproduces identical values; a different seed does not |
| 8 | Profiles stay inside their own limits | `test_profile_values_stay_inside_their_configured_operating_limits` | swept a full 5400 s cycle at 37 s steps across 7 channels; all inside configured operating bounds |
| 9 | Manoeuvre widens rates by design | `test_maneuver_mode_widens_rates_by_design` | MANEUVER rates exceed NOMINAL; a known command, not a fault |
| 10 | Link unavailable outside a pass | `test_link_is_unavailable_outside_a_scheduled_pass` | contact at t=600 s, none at t=3000 s; SNR is `None` outside |
| 11 | Packet rate has a real denominator | `test_packet_error_rate_is_unavailable_with_a_zero_denominator` | 0 attempted → `None`, never 0 %; 6/120 → 5.0 % |
| 12 | Packet window is bounded | `test_packet_window_is_bounded_by_the_configured_window` | window never exceeds the configured 60 samples |
| 13 | Reset events are distinct and timestamped | `test_reset_counter_records_timestamped_events` | 2 resets produce 2 distinct event times |
| 14 | A new run restarts the counter | `test_a_new_run_restarts_the_counter_without_a_physical_reset` | fresh truth has 0 resets and no events |
| 15 | Specialists see only their own incidents | `test_specialists_only_see_incidents_in_their_own_subsystem` | each list contains only its own subsystem; `hidden_from_this_role` accounts for the rest |
| 16 | Role/subsystem matrix | `test_role_subsystem_matrix_matches_the_specification` | POWER cannot act on FLIGHT_SYSTEMS; MASTER can act on all |
| 17 | All five routes, own channel scope | `test_every_console_route_serves_its_own_channel_scope` | five accounts resolve; master 28 channels, each specialist strictly fewer |
| 18 | Live run: all 28 channels valid | live API, run `eb6f0ca7` | **0 non-VALID channels** inside a contact window |
| 19 | Browser: flight console | Chromium 1366×768 | attitude, three-axis rate chart, wheel speed/current dual axis, link panel, channel table — all populated |
| 20 | Browser: link outside a pass | reset to `sim_time_s=2400`, flight console | badge **NO CONTACT SCHEDULED**; SNR and packet rate both `—` with reasons; app stayed **CONNECTED** and run **RUNNING** |
| 21 | Browser: master diagram | Chromium 1366×768 and 1920×1080 | 2D schematic with power-flow lines, one value per block in its own unit, status as dot + text |
| 22 | Browser: power console | Chromium 1366×768 | boundary balanced 300 W = 180 served + 120 charging; curtailment note present |
| 23 | Browser: thermal console | Chromium 1366×768 | six node temperatures, commanded-versus-observed heater, cold-gas store with derived-mass caveat |
| 24 | Layout review at both target sizes | 1366×768 and 1920×1080 | no clipping or horizontal scroll; two defects found and fixed (below) |

### Acceptance criteria for Phase 4

| Criterion | Status | Evidence |
|---|---|---|
| All five routes work | **Pass** | checks 17, 19–23 |
| Multiple screens show the same incident ID | **Pass** | incident IDs come from one backend record; the master sees every subsystem and each specialist sees its own (checks 15, 16). Not yet exercised with a live incident open on two screens at once — see limitations |
| Camera-specific features have not been invented | **Pass** | the optional load is a generic payload; no camera-specific channel or control exists |
| No unrelated random telemetry | **Pass** | check 8 — every profile channel is a declared, deterministic, bounded function marked `PROFILE`, not a random filler |
| Five browser sessions remain usable together | **Partial** | each of the five consoles was driven in a browser against the same live run, one at a time. Five *concurrent* browser sessions are a Phase 9 rehearsal item |

### Defects found and fixed during this phase

1. **`computer_reset_count` rendered as "0.00".** Section 6.2 states count channels are
   not continuous floating-point gauges. `ChannelReadout` and `ChannelTableRow` now force
   zero decimals from the channel's declared type rather than the caller's preference.
   Found by reading the live flight console.
2. **Battery terminal voltage read amber during normal operation.** The "approaching a
   limit" margin used 5% of the limit's magnitude, so a 24–29 V band warned above
   27.55 V — most of the time the battery is charged. Now 5% of the band width. Fixed in
   both the readout and the subsystem diagram.
3. **The subsystem diagram filled half a 1920-wide screen.** A fixed aspect ratio at full
   width is not "compact"; the SVG is now capped and centred.

### Known limitations after Phase 4

- **Four detection rules remain unimplemented** — `ACTUATOR_MISMATCH` (Phase 5),
  `PRESSURE_INCONSISTENCY`, `FLIGHT_PROFILE_DEVIATION` and `RESET_RECURRENCE`. The new
  flight and propulsion channels are therefore displayed and rule-referenced but not yet
  monitored. `/api/config/coverage` reports this honestly.
- **Forecast coverage is unchanged and still `PARTIAL`** — battery reserve and the
  battery thermal node only. The flight-systems profiles are explicitly not forecastable.
- **The thermal console flags a heater command/observation discrepancy in the UI but
  does not open an incident.** That rule is Phase 5; the panel says so.
- **Two screens have not yet been observed holding the same live incident
  simultaneously.** The scoping is unit-tested and the ID is a single backend record, but
  the concurrent visual check belongs with the Phase 9 five-client rehearsal.
- **Deployment remains unverified** beyond the database container — unchanged since
  Phase 1.

---

## Phase 3 — first complete demonstration

**Date:** 2026-09-23
**Environment:** Windows 11 Pro, Python 3.14.3, Node 24.19.0, PostgreSQL 17.

### Executed checks

| # | Check | Command / method | Result |
|---|---|---|---|
| 1 | Full backend suite | `pytest -q` | **79 passed**, 0 failed, final run 58.15 s |
| 2 | Phase 3 focused suite | `pytest tests/test_phase3.py -q` | **5 passed** |
| 3 | Backend lint | `ruff check app tests ../scripts` | **All checks passed** |
| 4 | Frontend types | `npx tsc --noEmit` | **exit 0** |
| 5 | Static export | `npm run build` | **exit 0**, 9 routes exported |
| 6 | Estimator rejects invalid evidence | `test_estimator_refuses_invalid_required_observation` | Missing required SOC produces no estimate/forecast |
| 7 | Same-start comparison | `test_payload_action_forecast_uses_same_observed_start_and_improves_reserve` | Baseline/action share source frame and initial SOC; action ends with higher reserve; battery thermal series present |
| 8 | Incident deduplication | `test_continuing_supply_fault_is_one_incident_and_recovers_after_dwell` | Repeated bad frames remain one active incident |
| 9 | Normal eclipse context | `test_normal_eclipse_does_not_open_generation_shortfall` | 34 eclipse frames with 0 W solar open no generation fault |
| 10 | Idempotency and outcome | `test_idempotent_command_is_one_record_and_measured_drop_is_improved` | Same key returns one command; post-command load drop becomes `IMPROVED` |
| 11 | D01 through browser UI | simulator D01 preset → master | run `b35ccd34` crossed into eclipse at 01:00:00; master showed 0 W generation, 180 W load, expected discharge and **no incident** |
| 12 | D02 through browser UI and storage | simulator D02 preset → master compare/approve | run `703c743a`: reserve projection **19.7% baseline vs 23.9% action**; measured load **380.3 → 299.6 W**; command `APPLIED`, outcome **IMPROVED**, incident entered recovery |
| 13 | API restarted after backend correction | stopped old Uvicorn, started one worker without `--reload`, repeated D02 | corrected outcome visible through the running API and browser |
| 14 | Port/storage preflight | listener checks plus TCP probe | ports 3000/8000 free before initial server start; PostgreSQL answered on 5432; existing database/volume was not stopped or deleted |
| 15 | Migration/model drift | `alembic check` | **No new upgrade operations detected** |
| 16 | Final live API restart | one Uvicorn worker, no `--reload`; `GET /api/health` | **200 `{"status":"ok"}`** after the last backend change; app servers then stopped, ports 3000/8000 free, PostgreSQL still answering on 5432 |

### Acceptance criteria for Phase 3

| Criterion | Status | Evidence |
|---|---|---|
| Changed load creates supported evidence and an early consequence forecast | **Pass** | D02 opened `ENERGY_RESERVE` from observed evidence and projected a warning crossing in about 27 minutes |
| Approved action changes measured load and projected reserve | **Pass** | checks 7 and 12 |
| Duplicate clicks apply once | **Pass** | check 10; database uniqueness remains a second guard |
| One continuing fault remains one incident | **Pass** | check 8 |
| Normal eclipse is handled correctly | **Pass** | checks 9 and 11 |
| AI is not required | **Pass** | deterministic estimator, rules, forecast and action path only |

### Known limitations after Phase 3

- Forecast coverage is intentionally partial: power/battery reserve and the battery
  thermal node are projected; other nodes and flight channels remain unavailable.
- Active proposals and in-memory lifecycle state are not rehydrated after an API restart;
  stored incident/forecast/command evidence remains, and the run restarts paused.
- Browser verification used the real frontend and Uvicorn against the test database so
  fixed test credentials could be used without changing the provisioned demo passwords.
- Docker CLI was not available on this PowerShell `PATH`; PostgreSQL connectivity was
  verified directly and no compose stop/down command was run.

---

## Phase 2 — shared live simulator

**Date:** 2026-09-23
**Environment:** as Phase 1.

### Executed checks

| # | Check | Command / method | Result |
|---|---|---|---|
| 1 | Full backend suite | `pytest -q` | **74 passed**, 0 failed, 56 s |
| 2 | Model reference checks | `pytest tests/test_models.py -q` | **27 passed** |
| 3 | Lint | `ruff check app tests ../scripts` | **All checks passed** |
| 4 | Frontend types | `npx tsc --noEmit` | **exit 0** |
| 5 | Static export build | `npm run build` | **exit 0**, 9 routes |
| 6 | Energy reference through the simulation | `test_energy_reference_over_1800_seconds` | 1800 × 1 s steps at 100 W draw **52.6316 Wh**, matching section 7.2 |
| 7 | Step size independent | `test_step_size_does_not_change_the_energy_result` | one 1800 s step equals 1800 × 1 s to 1e-9 |
| 8 | Thermal integration vs analytic | `test_explicit_integration_matches_the_analytic_step_response` | matches the closed-form first-order response to 1e-3 |
| 9 | Pressure reference | `test_pressure_reference` | **17.1046 bar absolute**, matching section 7.4 |
| 10 | Full battery curtails, empty battery sheds | `test_charging_respects_capacity_and_curtails`, `test_empty_battery_sheds_optional_load_instead_of_pretending` | energy stays within [0, capacity]; payload and heater shed; 100 W essential reported unmet |
| 11 | Two clients, same sequence | two parallel `curl -N /api/stream` against live Uvicorn | both received run `a567003e`, frames **27–33 identical**, same `sim_time_s` and `battery_soc` |
| 12 | 1 Hz cadence | same capture, 7 s window | 1 snapshot + **7 telemetry events** per client |
| 13 | Role filtering on the stream | same capture | master **28** channels, power **20**; `wheel_speed` absent for power |
| 14 | Hidden truth never leaks | `test_operational_responses_never_carry_hidden_truth` + raw stream capture | none of `generation_factor`, `extra_load_w`, `heater_stuck_on`, `contact_loss` present in any operational payload |
| 15 | Simulator truth is simulator-only | `test_simulator_state_exposes_truth_only_to_the_simulator` | simulator sees `energy_wh` and hidden conditions; master gets **403** |
| 16 | Loop runs with no browser attached | `test_loop_keeps_running_with_no_client_attached` | frame counter advanced after the simulator signed out |
| 17 | Reset starts a new run, keeps the old | `test_reset_starts_a_new_run_and_keeps_the_previous_one` | new run ID, `frame_seq` 0, status PAUSED; both runs still listed |
| 18 | Pause actually stops advancement | `test_pause_stops_advancing_and_start_resumes` | counter frozen while paused, resumes on start |
| 19 | Condition bounds enforced | `test_condition_bounds_are_enforced` | out-of-range and unknown fields all **422** |
| 20 | Unmodelled channels are missing, not zero | `test_unmodelled_channels_are_missing_not_zero` | `wheel_speed` is `null` with `MISSING` and a reason |
| 21 | Browser: master console | Chromium, 1600×950 | live header, SOC 70.4 %, load 179.3 W, battery current **−4.10 A** (charging), charts rendering |
| 22 | Browser: power boundary balances | same | 300 W available = 180 W served + 120 W charging, 0 curtailed, 0 unmet |
| 23 | Browser: injection is visible downstream | simulator slider → 150 W extra load | requested load moved **179.5 → 329.6 W** and the step appeared on the chart |
| 24 | Browser: contact loss ≠ disconnection | simulator "Loss of contact" toggle | header held **RUNNING** and **CONNECTED** while **Data age** climbed to 9.4 s and readings froze |
| 25 | Checkpoint survives restart | API restarted mid-run | run recovered **paused**, and the injected `contact_loss` was still set — restored from `runs.private_checkpoint` |
| 26 | Observed vs true | simulator console | observed SOC 71.3 % against true 713.08 Wh; the difference is the declared seeded noise |

### Acceptance criteria for Phase 2

| Criterion | Status | Evidence |
|---|---|---|
| Two browser sessions display the same sequence | **Pass** | check 11 — two live SSE clients, identical frames 27–33. Verified with two concurrent HTTP clients rather than two browser windows, because one browser profile holds one session cookie per origin; the five-laptop version is a Phase 9 rehearsal item. |
| Closing the simulator browser does not stop the loop | **Pass** | check 16 |
| Energy reference test passes | **Pass** | check 6 |
| Reset starts a new run | **Pass** | check 17 |
| Private injection data stays off master responses | **Pass** | checks 14, 15 |

### Defects found and fixed during this phase

1. **Heater power read `INVALID` whenever the heater was off.** Sensor noise around a true
   0 W pushed the reading slightly negative, outside the channel's physical bounds, so
   every console showed the channel as invalid. Sensor noise cannot produce a value the
   sensor could not encode, so the noisy reading is now clamped to the encodable range
   while the *model* value is still checked against physical bounds — keeping `INVALID`
   for genuine model errors and injected sensor faults. Found by reading the live UI, not
   by a test.
2. **`start()` could reuse a run id whose row no longer existed.** If the adopted run had
   been removed from storage, every tick would have failed its insert. The runner now
   verifies the run exists and starts a fresh one if not.
3. **`Subscriber` was unhashable**, so opening the SSE stream raised `TypeError`. The
   dataclass needed `eq=False` to keep identity hashing.
4. **`npm run build` corrupted a running `npm run dev`.** Both wrote to `.next`. The
   export build now uses its own `distDir` (`.next-export`).
5. **Starlette's `TestClient` cannot stream a never-ending SSE response**, and
   `request.is_disconnected()` blocked under that transport. The disconnect poll was
   removed (StreamingResponse already cancels the generator), and the stream is now
   verified against a real Uvicorn server instead of through `TestClient`.

### Known limitations after Phase 2

- **No detection, incidents or forecasts yet.** All ten rules remain inactive and all five
  actions disabled; `/api/config/coverage` reports this and the consoles say so.
- **Ten of the 28 channels are not modelled** — the flight-systems profiles and
  `cell_voltage_spread`. They report `null` with `MISSING` and a reason. Phase 4.
- **Flight, power and thermal consoles show a live readout strip only.** Full per-subsystem
  dashboards are Phase 4.
- **SSE two-client verification used two HTTP clients, not two browsers.** Same code path
  (real Uvicorn, real HTTP, real role filtering), but the five-laptop rehearsal is Phase 9.
- **Contact windows are always "expected".** Scheduled contact gaps arrive in Phase 4;
  today only the injected `contact_loss` produces staleness.
- **Telemetry is persisted at 1 Hz with no retention policy.** Roughly 3,600 rows per hour
  of demo. Bounded fixture generation and retention are Phase 6 concerns.
- **Deployment still unverified** — only the `db` container has been run. Unchanged from
  Phase 1.

---

## Phase 1 — foundation, login and role routes

**Date:** 2026-09-23
**Environment:** Windows 11 Pro, Python 3.14.3, Node 24.19.0, PostgreSQL 17 (Docker),
Docker Desktop 29.7.2.

### Executed checks

| # | Check | Command | Result |
|---|---|---|---|
| 1 | Configuration fixtures parse and validate | `pytest tests/test_config.py -q` | **13 passed** |
| 2 | Full backend suite | `pytest -q` | **29 passed**, 0 failed, 8.04 s |
| 3 | Lint | `ruff check app tests ../scripts` | **All checks passed** |
| 4 | Frontend types | `npx tsc --noEmit` | **exit 0**, no errors |
| 5 | Static export build | `npm run build` | **exit 0**, 9 routes exported to `frontend/out` |
| 6 | Migration renders offline | `alembic upgrade head --sql` | **exit 0**, valid PostgreSQL DDL |
| 7 | Migration applies | `alembic upgrade head` | **exit 0**, 11 tables created |
| 8 | No model/schema drift | `alembic check` | **No new upgrade operations detected** |
| 9 | Account provisioning | `scripts/seed_users.py` | 5 accounts created with generated passwords |
| 10 | API starts and answers | `curl /api/health` | `200 {"status":"ok"}` |
| 11 | Missing session → 401 | `curl` on `/auth/me`, `/commands`, `/simulator/state`, `/config/channels` | **401 `NOT_AUTHENTICATED`** on all four |
| 12 | Specialist command attempt → 403 | live login as `power`, `POST /api/commands` | **403 `ROLE_NOT_PERMITTED`** |
| 13 | Specialist cannot read simulator truth | live login as `power`, `GET /api/simulator/state` | **403 `ROLE_NOT_PERMITTED`** |
| 14 | Login in a real browser | Chromium at `http://localhost:3000/login/` | Signed in as `master`, landed on `/master/` |
| 15 | Cross-console guard | `master` session navigating to `/power/` | Redirected back to `/master/` |
| 16 | Role-filtered telemetry contract | browser, `flight` account | 22 channels visible vs 28 for `master` |

Checks 1–9 and 12–13 are also covered by automated tests, so they re-run with `pytest`.

### Acceptance criteria for Phase 1

| Criterion | Status | Evidence |
|---|---|---|
| Local frontend and API start | **Pass** | checks 4, 5, 10, 14 |
| Five accounts land on their own routes | **Pass** | `test_each_account_lands_on_its_own_console`; browser checks 14 and 16 |
| Specialist command attempt returns 403 | **Pass** | check 12; `test_specialist_cannot_execute_a_recovery_command` |
| Missing session returns 401 | **Pass** | check 11; `test_missing_session_returns_401` |
| Build and types pass | **Pass** | checks 3, 4, 5 |
| No committed secrets | **Pass** | `.env` is gitignored; `.env.example` holds placeholders only; generated passwords are printed once and never written to a file |

### Defects found and fixed during this phase

1. **API hung forever on startup when PostgreSQL was unreachable.** `engine.connect()`
   had no connect timeout, so the process never reached "startup complete" and `/health`
   never answered — which also makes the section 2.3 requirement to *display* a storage
   error impossible. Fixed by adding `DB_CONNECT_TIMEOUT_SECONDS` (default 5) to the
   psycopg connect args. Verified: the API now starts with the database down, logs the
   failure explicitly, and serves `/health` and the 401 path.
2. **Migration did not match the models.** `alembic check` reported drift: `unique=True,
   index=True` on a column produces a single *unique index*, while the hand-written
   migration created a unique constraint plus a separate non-unique index. Fixed in
   `0001_initial_schema.py`; `alembic check` is now clean.
3. **Test database URL silently lost its password.** `str(SQLAlchemy URL)` masks the
   password as `***`, so every database-backed test skipped with an authentication
   failure that looked like "PostgreSQL is not reachable". Fixed by passing `URL`
   objects instead of strings.
4. **Test suite took 130 s instead of 8 s.** `DATABASE_URL` used `localhost`, which
   resolves to `::1` first; PostgreSQL is published on `127.0.0.1` only, so every
   connection paid a full IPv6 timeout. Fixed by using `127.0.0.1` explicitly.

### Known limitations after Phase 1

- **There is no simulation yet.** No run, no telemetry, no incidents, no forecasts. Every
  endpoint for those returns `503 NOT_IMPLEMENTED_YET` with its phase number, and each
  console names what is missing rather than showing placeholder values.
- **All ten detection rules are declared but inactive**, and all five recovery actions are
  disabled. `/api/config/coverage` reports this, and the consoles display "0 of 10
  active" and "0 of 5 available".
- **The client-side role guard is convenience only.** It redirects a signed-in user to
  their own console, but the server enforces the real boundary on every request. Check 12
  exercises the server path directly with curl, not through the UI.
- **Login throttling is in-process.** It resets when the API restarts and does not
  coordinate across instances. Acceptable for a single-process prototype.
- **Layout has been reviewed at 1366×768 only.** The 1920×1080 review is a Phase 4 task.
- **Deployment is unverified.** `compose.yaml`, the `Dockerfile` and the `Caddyfile` are
  written but only the `db` service has been run. The API image has not been built, Caddy
  has not served the export, and no SSE path has been exercised through the proxy. No
  deployment URL exists.
- **No AI provider is configured.** `AI_ENABLED=false`; nothing in Phase 1 depends on it.
- **Physical five-laptop rehearsal: not attempted.** Phase 9.

### Environment notes

Docker Desktop crashed at first launch on a stale runtime socket
(`%LOCALAPPDATA%\Docker\run\sailor-ingest.sock`, left from an unclean shutdown on
19 Sep) and had to be restarted manually before the database could start. Worth knowing
before demo day: if `docker compose up -d db` fails with a named-pipe error, restart
Docker Desktop first.
