# Handoff brief — starting Phase 8

Read `CLAUDE.md` section 13.2 and the Phase 8 task list first. Verify this brief against
the current code and git history; do not assume its counts or commit hashes.

## Phase 7 state to build on

- `backend/app/analysis/service.py` owns the only analysis queue: one worker, ten pending
  jobs, priority, revision deduplication/supersession, automatic cooldown, daily budget,
  cache and deterministic fallback.
- `backend/app/analysis/provider.py` contains the single optional OpenAI Responses path.
  It is capped at two incident responses, four read-only tool calls, three total HTTP
  attempts, configured timeout/output tokens and `store:false`.
- `backend/app/analysis/tools.py` is the complete AI evidence boundary. The model cannot
  choose an incident or run and has no SQL, command, threshold, simulator or checkpoint
  tool.
- Automatic investigation triggers exist only for incident opening, meaningful severity
  escalation and failed measured recovery. A role-scoped CSRF-protected manual endpoint
  is the fourth trigger. Deterministic alerts publish before queue work.
- Daily, weekly, monthly and custom history reviews use the same queue and exact
  source/window cache. TEMPLATE remains the functional fallback.
- Master and specialist incident panels show latest/manual results, generation mode,
  citations and evidence trail. The history console polls queued reviews.

## Provider status

No authorized provider configuration was present during Phase 7:
`AI_ENABLED=false`, provider/model blank and no API key. Timeout, malformed output, bad
citations and budget exhaustion were tested with bounded fakes, but **live LLM mode is not
validated**. Do not describe it as live-complete unless Phase 8 is explicitly given an
authorized key and a real structured/tool call succeeds. A ChatGPT or Claude subscription
alone is not API authorization.

## Phase 8 priorities

1. Read section 13.2; run focused model references, incident lifecycle and recovery-action
   boundary checks before broad integration work.
2. Exercise all role restrictions, hidden-truth exclusions and duplicate command paths.
3. Use several browser/HTTP sessions against one restarted Uvicorn process; include one
   disconnect/reconnect and one reset while checking shared frame/run identity.
4. Re-run AI-disabled/outage fallback and exact history coverage/recurrence through real
   HTTP/UI. If no key is authorized, record the provider check as not run again.
5. Build only from documented dependencies, inspect actual screens and fix demonstrated
   blocking defects. Do not expand into Phase 9 deployment or optional Phase 10 ML.

## Verification baseline (rerun; do not trust blindly)

- Full backend: 112 passed; two known warnings.
- Phase 7 focused: 5 passed.
- Ruff, TypeScript and `alembic check`: clean.
- Static export: 9 application routes / 11 generated pages.
- Restarted D02 API: one `ENERGY_RESERVE WARNING`, completed TEMPLATE fallback, one cited
  observation, two evidence-tool results, zero hidden-field hits.
- Browser master console rendered the mode/fallback, citation, missing evidence, next
  checks and evidence trail.

Keep Uvicorn single-worker without `--reload`; restart after backend changes. Preserve the
healthy PostgreSQL container and its existing volume. Never run `docker compose down -v`.
