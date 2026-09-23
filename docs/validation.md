# Validation record

Only checks that were actually executed appear here, with their real results. Nothing is
recorded as passing because it "should" pass.

---

## Phase 7 — bounded live investigator and periodic reviews

**Date:** 2026-09-23
**Environment:** Windows 11 Pro, Python 3.14.3, Node 24.19.0, PostgreSQL 17 using the
existing Docker volume. AI disabled; no provider, model or authorized API key configured.

### Executed checks

| # | Check | Command / method | Result |
|---|---|---|---|
| 1 | Full backend suite | `./.venv/Scripts/python.exe -m pytest -q` | **112 passed**, 0 failed, 77.27 s; two known non-failing warnings |
| 2 | Phase 7 focused coverage | `./.venv/Scripts/python.exe -m pytest -q tests/test_phase7.py` | **5 passed**: contracts, queue/dedup/concurrency, role-scoped API, all fallbacks, queue/cooldown/trigger bounds |
| 3 | Lint | `./.venv/Scripts/python.exe -m ruff check app tests ../scripts` | **All checks passed** |
| 4 | Frontend types | `npx tsc --noEmit` | **exit 0**, no errors |
| 5 | Static export | `npm run build` | **exit 0**; 9 application routes, 11 generated static pages including framework pages |
| 6 | Schema drift | `./.venv/Scripts/python.exe -m alembic check` | **No new upgrade operations detected** |
| 7 | Provider configuration audit | settings read without printing secrets | `AI_ENABLED=false`; provider absent; model absent; API key absent — real provider test correctly **not run** |
| 8 | Fallback matrix | focused tests with bounded fake providers | missing key, timeout, malformed output, unsupported evidence citation and daily budget exhaustion all finish as honestly labelled `TEMPLATE` |
| 9 | Queue controls | focused tests | pending limit 1 forced `QUEUE_FULL`; second revision inside cooldown forced `COOLDOWN`; non-allowlisted per-frame trigger rejected; maximum observed concurrent jobs **1** |
| 10 | Restarted real API | D02 simulator preset → master manual investigate → poll `/api/analyses/{id}` | one `ENERGY_RESERVE WARNING`; analysis `COMPLETED`, mode `TEMPLATE`, fallback `PROVIDER_NOT_CONFIGURED`; one observation, two evidence tools |
| 11 | Hidden-field scan | raw live analysis JSON | **0 hits** for `private_checkpoint`, `hidden_conditions`, `computer_resets`, `branch_stuck_on` or `isolator_failed` |
| 12 | Browser master console | restarted Next dev server and Uvicorn, signed in locally | incident alert remained immediate; panel visibly showed `COMPLETED`, `TEMPLATE`, cached revision, cited observation, missing evidence, next checks and `get_incident_evidence → get_forecast_comparison` trail |
| 13 | Final port/storage state | listener/TCP checks after stopping owned server PIDs | ports **3000/8000 free**; PostgreSQL remained reachable on 5432; no compose-down or volume-removal command used |

### Acceptance criteria for Phase 7

| Criterion | Status | Evidence |
|---|---|---|
| New incidents can be investigated without delaying deterministic alerts | **Pass** | opening/escalation/failure trigger after persistence/publication; one background worker; live D02 and browser check |
| Calls are bounded and not made each second | **Pass** | only four trigger values accepted; incident/revision cache, 120 s cooldown, pending cap 10, daily limit and one worker; test rejects a per-frame trigger |
| Evidence tools/citations are visible and storage-only | **Pass** | UI trail and check 11; provider has only the four fixed Phase 6 functions and cannot choose incident/run scope |
| Provider failures leave the demo functional | **Pass** | check 8; every tested failure completed through the deterministic TEMPLATE path |
| AI cannot execute recovery or change thresholds | **Pass** | no mutation/command/SQL tool exists; supported action IDs are validated against configured enabled actions; UI states read-only |
| Daily/weekly/monthly reviews share queue/cache | **Pass** | all resolved windows submit through `AnalysisService.enqueue_history`; exact source/window key deduplicates matching requests |
| Live LLM integration is not overstated | **Pass** | no authorized key was configured, so no provider call was made and LLM mode is recorded as unverified |

### Defects found and fixed during this phase

1. **Failed recovery initially reused the opening revision.** A cached opening analysis
   could therefore suppress the required failed-recovery investigation. Persisting a
   measured command outcome now advances the linked incident evidence revision before the
   `FAILED_RECOVERY` trigger is queued.
2. **In-memory QUEUED rows could survive a process restart forever.** Startup now marks
   orphaned queued/running rows `DEFERRED` with `PROCESS_RESTART`; a new request can create
   a real job instead of polling an impossible one.
3. **Failed provider attempts were initially absent from daily accounting.** TEMPLATE
   fallbacks now persist the provider and actual attempt count, and the budget sums all
   analyses rather than only successful `LLM` rows.

### Known limitations after Phase 7

- The OpenAI Responses path follows the official tool-calling and strict structured-output
  contracts, but has not made a real provider request on this machine. It must remain
  labelled unverified until an authorized API key and model are explicitly configured.
- Queue/cooldown coordination is intentionally process-local because deployment requires
  one API process. A multi-worker deployment would need a durable external queue/lock.
- Period reviews are request-driven. Daily/weekly/monthly windows use the same bounded
  path and cache, but no autonomous scheduler was requested or added.

---

## Phase 6 — historical data and recurrence

**Date:** 2026-09-23
**Environment:** Windows 11 Pro, Python 3.14.3, Node 24.19.0, PostgreSQL 17 (existing
Docker volume; Docker CLI unavailable on this shell path).

### Executed checks

| # | Check | Command / method | Result |
|---|---|---|---|
| 1 | Full backend suite | `./.venv/Scripts/python.exe -m pytest -q` | **107 passed**, 0 failed, 84.49 s; two known non-failing warnings |
| 2 | Phase 6 focused coverage | included as `tests/test_phase6.py` in check 1 | **4 passed**: window semantics, generated month/API/tools, long-incident count, live timeline persistence |
| 3 | Lint | `./.venv/Scripts/python.exe -m ruff check app tests ../scripts` | **All checks passed** |
| 4 | Frontend types | `npx tsc --noEmit` | **exit 0** |
| 5 | Static export | `npm run build` | **exit 0**; 9 application routes exported (11 static pages including framework pages) |
| 6 | Schema drift | `./.venv/Scripts/python.exe -m alembic check` | **No new upgrade operations detected** |
| 7 | Synthetic month generation | `backend/.venv/Scripts/python.exe -m scripts.generate_history --month 2026-06 --seed 6026` | run `be00d3df`; **43,200** minute buckets, **5** incidents, **900** 1 Hz evidence frames in **6.16 s** |
| 8 | Cache reuse | same command a second time | same run returned with `cached: true` in **0.073 s**; no rows regenerated |
| 9 | Real HTTP summary | restarted one-worker Uvicorn without `--reload`; authenticated `GET /api/history/summary` | dataset `SYNTHETIC`; completed June UTC; **43,200/43,200 minutes (100%)**; **5 distinct incidents**; recurrence count **5** |
| 10 | Command-versus-measured classes | same real API response | command missing **1**, rejected **1**, accepted but ineffective **1**, outcome unknown **1**, improved **1** |
| 11 | Hidden-field response guard | raw summary, template review and all four tool results | **0 hits** for seven hidden condition names, `private_checkpoint` or `hidden_conditions` |
| 12 | Deterministic review/cache | authenticated `POST /api/history/review` twice | `TEMPLATE`, `COMPLETED`; identical result, second response cached |
| 13 | Browser history console | master login → `/history/` → synthetic June → Generate review | selected dates/source visible; 5 incidents, 100% coverage, recurrence row, five action classes and cached template report all rendered |
| 14 | Final API health | `GET /api/health` after restart | **200 `{"status":"ok"}`** |
| 15 | Final port/storage state | listener and TCP checks after stopping app servers | ports **3000/8000 free**; PostgreSQL still reachable on 5432; container/volume never stopped or removed |

### Acceptance criteria for Phase 6

| Criterion | Status | Evidence |
|---|---|---|
| One long incident is counted once | **Pass** | a single eight-hour incident with 20 timeline events reports one distinct incident and 28,800 s duration |
| Synthetic and live data are never silently mixed | **Pass** | source is required by the API and stated in every response/UI panel; the same June LIVE query returned zero incidents while SYNTHETIC returned five |
| Selected dates and coverage appear | **Pass** | check 13 visibly showed the completed June UTC bounds and 43,200/43,200 minutes |
| Recurrence is derived from stored records | **Pass** | five distinct `ACTUATOR_MISMATCH`/`OPTIONAL_A` incident rows produced recurrence count five; no answer is hard-coded in statistics or UI |
| Tool responses reveal no hidden truth | **Pass** | check 11 and the expanded operational response guard |
| Month storage stays bounded | **Pass** | 43,200 minute buckets plus 900 selected 1 Hz frames, not a full month at 1 Hz |

### Defect found and fixed during this phase

The first live timeline-persistence test exposed SQLAlchemy insert ordering: because the
ORM models have scalar foreign keys but no relationship, adding a new incident and its
first event in one flush could insert the child first and violate the foreign key. The
incident row is now flushed before the opening event. The regression test then verified
opening, severity, lifecycle, acknowledgement, command-status and command-outcome rows.

### Known limitations after Phase 6

- Synthetic history is a labelled demonstration dataset, not flight evidence. Its
  recurring optional-branch schedule mismatch is configurable but remains fictional.
- Historical queries cap incidents at 2,000, timeline events at 10,000, and investigator
  tool results at their declared per-call limits; responses say when a cap is reached.
- The deterministic review interprets nothing beyond its fixed template. Live AI,
  provider configuration, event queues and periodic scheduling are Phase 7 and were not
  implemented.
- Docker CLI remained unavailable on this shell path. PostgreSQL health was verified by
  TCP and every database-backed check passed; the existing container and volume were
  never stopped or removed.

---

## Phase 5 — major and delayed-intervention cases

**Date:** 2026-09-23
**Environment:** Windows 11 Pro, Python 3.14.3, Node 24.19.0, PostgreSQL 17 (Docker).

### Executed checks

| # | Check | Command / method | Result |
|---|---|---|---|
| 1 | Full backend suite | `pytest -q` | **103 passed**, 0 failed, 58 s |
| 2 | Phase 5 focused suite | `pytest tests/test_phase5.py -q` | **12 passed** |
| 3 | Lint | `ruff check app tests ../scripts` | **All checks passed** |
| 4 | Frontend types | `npx tsc --noEmit` | **exit 0** |
| 5 | Static export | `npm run build` | **exit 0**, 9 routes |
| 6 | Branch produces power *and* thermal evidence | `test_stuck_branch_raises_demand_and_heats_the_payload_node` | demand rises by exactly the rated 150 W; payload node warms from that same power |
| 7 | Isolation removes load and heat | `test_isolation_removes_the_branch_load_and_its_heat` | demand falls and the payload temperature trend reverses |
| 8 | Essential supply is never isolated | `test_isolation_never_touches_essential_supply` | essential demand and computer power preserved |
| 9 | Isolation does not repair hardware | `test_software_isolation_does_not_repair_the_branch` | releasing the isolator brings the fault straight back |
| 10 | Failed isolator changes nothing | `test_a_failed_isolator_applies_the_command_and_changes_nothing` | commanded true, effective false, demand unchanged |
| 11 | Outage is irreversible | `test_payload_outage_accumulates_and_cannot_be_undone` | lost seconds persist after full recovery |
| 12 | No permanent battery damage claim | `test_low_battery_alone_does_not_claim_permanent_damage` | a drained battery recharges normally; no damage state exists |
| 13 | ACTUATOR_MISMATCH uses observations only | `test_actuator_mismatch_needs_only_observations_and_known_commands` | fires from a known command plus a measured channel, after persistence |
| 14 | Mismatch stays quiet when consistent | `test_actuator_mismatch_stays_quiet_when_command_and_observation_agree` | no incident when command and observation agree |
| 15 | Isolation forecast starts from the same state | `test_isolation_forecast_projects_the_branch_load_away` | identical source frame and initial SOC; isolated path ends higher; assumption stated |
| 16 | Only enabled actions are offered | `test_only_enabled_actions_may_be_offered` | exactly the two implemented actions |
| 17 | **D04 live, isolator responds** | full HTTP flow: simulator preset → master compare → approve → observe | `THERMAL_LIMIT WARNING` on `payload_temperature`; forecast **15.2 % no action vs 22.9 % isolated**; command `ACCEPTED` → `APPLIED`; outcome **IMPROVED**; measured load **330.7 → 179.7 W** (−151 W against a rated 150 W) |
| 18 | **D04 live, isolator failed** | same flow with `isolator_failed` injected | identical evidence and projection; command `ACCEPTED` → `APPLIED`; outcome **NO_CHANGE**; measured load **330.7 → 329.7 W** (unmoved) |
| 19 | Duplicate approval applies once | same driver, same idempotency key twice | second approval returned the same command record in both runs |
| 20 | **D03 live escalation** | simulator D03 preset, no operator action | `ENERGY_RESERVE` observed at both **WARNING and CRITICAL**; SOC fell to 8.5 % |
| 21 | **D03 live to the outage** | drained to empty in eclipse, then fully recovered | at SOC 0.09 %: **served 0 W, unmet 400 W, shed `payload`, computer unpowered**; payload operation lost **6 s** and still **18 s** after the load was removed and the battery recovered — the counter only ever increased |
| 22 | Load shedding still publishes a frame | `test_load_shedding_still_produces_a_valid_frame` | frame builds and serializes while loads are shed and a reset is recorded (regression guard, see defects) |
| 23 | Coverage flags match implementations | `test_coverage_endpoint_reports_what_actually_works` | 6 rules implemented, 2 actions enabled; the other 4 rules still declared false |

### Acceptance criteria for Phase 5

| Criterion | Status | Evidence |
|---|---|---|
| D04 improves after a supported action | **Pass** | check 17 — measured load fell by the branch's rated power and the outcome was `IMPROVED` |
| A genuinely unresponsive action reports failure | **Pass** | check 18 — `APPLIED` with outcome `NO_CHANGE`, and the master console states that recovery failed |
| D03 does not falsely claim permanent battery damage | **Pass** | check 12 — no damage state exists and a drained battery recharges; the recorded consequence is lost payload operating time |
| All heat and power changes have a modelled cause | **Pass** | check 6 — the payload node warms from the branch's own declared power share, not a placed offset |

### Defects found and fixed during this phase

1. **Telemetry stopped publishing the moment any load was shed.** `TelemetryFrame.engineering`
   was typed `float | bool | int | None`, but two entries carry short labels —
   `shed_loads` (the names of loads currently shed) and `reset_events_s`. Both are `None`
   until something is actually shed or a reset actually occurs, so frame construction
   raised a `ValidationError` on the very first load-shedding tick. The loop caught it
   per-tick and carried on, so the **simulation kept advancing while every console froze
   on its last frame** — observed live with truth at `sim_time` 4111 s against a frame
   stuck at 3956 s, exactly the 155 s the outage had been running.

   This is a Phase 2 defect that survived three phases because no test had ever driven
   the system into real load shedding *through the observation path*; the model-level
   shedding tests never built a frame. It would have fired during D03 in front of an
   audience, at the worst possible moment.

   Fixed by introducing `EngineeringValue`, which admits a label, while keeping the
   NaN/Infinity rejection for numeric entries via a new validator.
   `test_load_shedding_still_produces_a_valid_frame` now drives the twin into shedding
   *and* a reset, then builds and serializes a frame, so a numeric-only engineering
   payload cannot come back.

2. **The first live outage verification reported a false negative.** It read
   `payload_outage_s` from the published frame, which was frozen by defect 1, and so
   reported `0.0 s` of lost operation while the simulator's own truth showed 155 s. The
   result was discarded and the check re-run against the fixed build rather than
   recorded.

### Known limitations after Phase 5

- **Reaching the outage takes real time.** From the D03 preset the battery needs several
  minutes to drain to the point where load shedding drops the payload — which is the
  point of a *delayed*-intervention case. Check 21 drove it there from a deliberately
  low start; a demo-day walkthrough should either start low or narrate the wait.
- **`ACTUATOR_MISMATCH` covers the heater only.** The branch isolator's failure surfaces
  through the command outcome rather than a dedicated rule, because `load_power` alone
  does not let a ground rule separate the branch's contribution from total demand.
- **Three rules remain unimplemented** — `PRESSURE_INCONSISTENCY`,
  `FLIGHT_PROFILE_DEVIATION` and `RESET_RECURRENCE`. Reported honestly by
  `/api/config/coverage`.
- **Scenario presets use labelled starting conditions** near the relevant boundary so each
  case reaches its evidence inside a demo window. This is permitted by section 7.1 and is
  stated on the simulator console, but it does mean the scenarios do not begin from a
  neutral vehicle state.
- **Deployment remains unverified** beyond the database container — unchanged since
  Phase 1.

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
