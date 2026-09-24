"use client";

/**
 * Master operations: every active incident, the deterministic forecast, the supported
 * actions and the measured outcome.
 *
 * Boundaries this panel is built around:
 *   * Mathematics detects and projects. Gemini only explains stored evidence, in its own
 *     clearly separated block, and can never approve or execute anything.
 *   * Everything on screen belongs to the run currently on the header. A reset retires a
 *     run, so the selection, comparison, proposal, command and forecast are cleared;
 *     stored history in the database is untouched.
 *   * Nothing here decides an outcome. The duty options, trade-off text and outcome
 *     verdict all come from the server.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { Panel, StatusBadge, type StatusTone } from "@/components/primitives";
import { InvestigationPanel } from "@/components/InvestigationPanel";
import {
  ReserveForecastChart,
  type ForecastLike,
} from "@/components/ReserveForecastChart";
import type { HistoryPoint } from "@/lib/live";

interface Incident {
  id: string;
  rule_id: string;
  severity: "INFO" | "WARNING" | "CRITICAL" | "UNKNOWN";
  lifecycle: "PENDING" | "OPEN" | "RECOVERING" | "RESOLVED";
  subsystem: string;
  affected_entity: string;
  observed_evidence: { text: string; evidence_id: string }[];
  acknowledgement: { acknowledged: boolean };
}

interface Forecast extends ForecastLike {
  id: string;
  run_id: string;
  source_frame_seq: number;
  source_sim_time_s: number;
  horizon_s: number;
  assumptions: string[];
  limitations: string[];
  crossings: { kind: string; seconds_from_source: number; limit_value: number }[];
}

interface Comparison {
  baseline: Forecast;
  action: Forecast;
  proposal: {
    id: string;
    expires_at_utc: string;
    trade_off: string;
    action_id: string;
    action_arguments: Record<string, unknown>;
    outcome_paths: string[];
  };
}

interface RaisedProposal {
  id: string;
  action_id: string;
  action_arguments: Record<string, unknown>;
  expires_at_utc: string;
  trade_off: string;
  requested_by: string | null;
  requested_by_role: string | null;
}

interface Command {
  id: string;
  status: string;
  observed_outcome: string;
  outcome_detail: string | null;
}

const SEVERITY_TONE: Record<Incident["severity"], StatusTone> = {
  CRITICAL: "critical",
  WARNING: "warning",
  INFO: "accent",
  UNKNOWN: "unavailable",
};

const SEVERITY_RANK: Record<Incident["severity"], number> = {
  UNKNOWN: 0,
  INFO: 1,
  WARNING: 2,
  CRITICAL: 3,
};

// The duty the payload is left running at, not the size of the reduction. The wording
// matters: "100%" here means the payload keeps running, which is the opposite of what a
// reader assuming "100% reduction" would expect.
const DUTY_OPTIONS = [0, 50, 100] as const;

function endSoc(value: Forecast | null): string {
  const values = value?.series.find((s) => s.channel_id === "battery_soc")?.values ?? [];
  const final = values.at(-1);
  return typeof final === "number" ? `${final.toFixed(1)}%` : "unavailable";
}

export function Phase3Operations({
  runId,
  history = [],
}: {
  runId: string | null;
  history?: HistoryPoint[];
}) {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [command, setCommand] = useState<Command | null>(null);
  const [duty, setDuty] = useState<number>(0);
  const [reserve, setReserve] = useState<{ warning: number | null; critical: number | null }>({
    warning: null,
    critical: null,
  });
  const [raised, setRaised] = useState<RaisedProposal[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const commandRef = useRef<Command | null>(null);
  commandRef.current = command;

  // Configured reserve boundaries, read once. The chart draws these rather than
  // deciding for itself where a limit sits.
  useEffect(() => {
    const controller = new AbortController();
    api
      .get<{ energy_reserve?: { warning_percent: number | null; critical_percent: number | null } }>(
        "/config/coverage",
        controller.signal,
      )
      .then((body) =>
        setReserve({
          warning: body.energy_reserve?.warning_percent ?? null,
          critical: body.energy_reserve?.critical_percent ?? null,
        }),
      )
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  // A retired run leaves nothing behind on screen. Without this the previous scenario's
  // comparison, proposal, command outcome and forecast stay visible under a new run id,
  // which would present numbers from a run that no longer exists. Database history is
  // deliberately untouched.
  useEffect(() => {
    setIncidents([]);
    setSelectedId(null);
    setForecast(null);
    setComparison(null);
    setCommand(null);
    setDuty(0);
    setError(null);
  }, [runId]);

  const refresh = useCallback(async () => {
    try {
      const body = await api.get<{ incidents: Incident[] }>("/incidents");
      setIncidents(body.incidents);
      try {
        const next = await api.get<Forecast>("/forecasts/latest");
        // The endpoint already refuses a forecast from another run; this is the second
        // gate, so a late response cannot land under a new run.
        setForecast(!runId || next.run_id === runId ? next : null);
      } catch (err) {
        if (err instanceof ApiError && err.code === "FORECAST_UNAVAILABLE") setForecast(null);
        else throw err;
      }
      const current = commandRef.current;
      if (current) setCommand(await api.get<Command>(`/commands/${current.id}`));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not refresh operations.");
    }
  }, [runId]);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const active = useMemo(
    () =>
      incidents
        .filter((incident) => incident.lifecycle !== "RESOLVED")
        .sort(
          (a, b) =>
            SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity] ||
            a.rule_id.localeCompare(b.rule_id),
        ),
    [incidents],
  );

  // Keep a selection that still exists. Only auto-select when nothing valid is chosen,
  // so an operator reading one incident is never yanked to another as the list changes.
  useEffect(() => {
    setSelectedId((current) => {
      if (current && active.some((incident) => incident.id === current)) return current;
      return active[0]?.id ?? null;
    });
  }, [active]);

  // The alert popup asks for a specific incident, which may not be the newest one.
  useEffect(() => {
    function onSelect(event: Event) {
      const id = (event as CustomEvent<string>).detail;
      if (typeof id === "string") setSelectedId(id);
    }
    window.addEventListener("ps01:select-incident", onSelect);
    return () => window.removeEventListener("ps01:select-incident", onSelect);
  }, []);

  const selected = active.find((incident) => incident.id === selectedId) ?? null;

  // A comparison belongs to the incident and duty it was computed for.
  useEffect(() => {
    setComparison(null);
    setCommand(null);
    setRaised([]);
  }, [selectedId]);

  // Recommendations a specialist has raised on this incident. The master approves the
  // specialist's exact proposal rather than re-deriving one and hoping it matches.
  useEffect(() => {
    if (!selectedId) return;
    let stop = false;
    async function poll() {
      try {
        const body = await api.get<{ proposals: RaisedProposal[] }>(
          `/incidents/${selectedId}/proposals`,
        );
        if (!stop) setRaised(body.proposals.filter((p) => p.requested_by_role !== "MASTER"));
      } catch {
        // Advisory only; a dropped poll must not disturb the console.
      }
    }
    void poll();
    const id = window.setInterval(() => void poll(), 3000);
    return () => {
      stop = true;
      window.clearInterval(id);
    };
  }, [selectedId]);

  async function compare(actionId: string, dutyPercent: number) {
    if (!selected) return;
    setBusy(true);
    setError(null);
    setCommand(null);
    try {
      setComparison(
        await api.post<Comparison>(`/incidents/${selected.id}/compare`, {
          action_id: actionId,
          duty_percent: dutyPercent,
        }),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Comparison failed.");
      setComparison(null);
    } finally {
      setBusy(false);
    }
  }

  async function approveProposal(proposalId: string) {
    setBusy(true);
    setError(null);
    try {
      setCommand(
        await api.post<Command>("/commands", {
          proposal_id: proposalId,
          idempotency_key: crypto.randomUUID(),
        }),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Approval failed.");
    } finally {
      setBusy(false);
    }
  }

  async function approve() {
    if (!comparison) return;
    setBusy(true);
    setError(null);
    try {
      setCommand(
        await api.post<Command>("/commands", {
          proposal_id: comparison.proposal.id,
          idempotency_key: crypto.randomUUID(),
        }),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Approval failed.");
    } finally {
      setBusy(false);
    }
  }

  async function acknowledge() {
    if (!selected) return;
    try {
      await api.post(`/incidents/${selected.id}/acknowledge`);
      void refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Acknowledgement failed.");
    }
  }

  const isPayloadComparison = comparison?.proposal.action_id === "REDUCE_PAYLOAD_POWER";
  const comparedDuty = Number(comparison?.proposal.action_arguments?.duty_percent ?? duty);

  return (
    <div className="space-y-4">
      <div className="grid gap-4 xl:grid-cols-3">
        {/* ------------------------------------------------ active incidents */}
        <Panel
          title="Active incidents"
          subtitle="Deterministic detection — select one to work on it"
          actions={
            <StatusBadge tone={active.length ? "warning" : "nominal"}>
              {active.length} active
            </StatusBadge>
          }
        >
          {error ? <p className="mb-2 text-xs text-critical">{error}</p> : null}
          {active.length ? (
            <ul className="space-y-1.5">
              {active.map((incident) => {
                const chosen = incident.id === selectedId;
                return (
                  <li key={incident.id}>
                    <button
                      type="button"
                      aria-pressed={chosen}
                      onClick={() => setSelectedId(incident.id)}
                      className={`w-full rounded border px-2 py-1.5 text-left text-xs transition-colors ${
                        chosen
                          ? "border-accent bg-accent/10"
                          : "border-edge/70 hover:border-edge"
                      }`}
                    >
                      <span className="flex items-center justify-between gap-2">
                        <span className="tnum truncate text-ink">{incident.rule_id}</span>
                        <StatusBadge tone={SEVERITY_TONE[incident.severity]}>
                          {incident.severity}
                        </StatusBadge>
                      </span>
                      <span className="mt-0.5 block truncate text-ink-faint">
                        {incident.subsystem} · {incident.affected_entity} · {incident.lifecycle}
                        {incident.acknowledgement.acknowledged ? " · ack" : ""}
                      </span>
                      <span className="mt-1 block line-clamp-2 text-ink-dim">
                        {incident.observed_evidence.at(-1)?.text}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-xs text-ink-faint">
              No active incident. Load a scenario from the simulator console.
            </p>
          )}
        </Panel>

        {/* ------------------------------------------------ forecast numbers */}
        <Panel
          title="30-minute forecast"
          subtitle="Mathematics only — observed state, no hidden truth, no AI"
        >
          {comparison ? (
            <div className="space-y-2 text-xs">
              <p className="flex justify-between">
                <span className="text-ink-dim">No action, reserve at horizon</span>
                <span className="tnum">{endSoc(comparison.baseline)}</span>
              </p>
              <p className="flex justify-between">
                <span className="text-ink-dim">
                  {isPayloadComparison
                    ? `Payload left at ${comparedDuty}% duty`
                    : "After isolating the branch"}
                </span>
                <span className="tnum text-nominal">{endSoc(comparison.action)}</span>
              </p>
              <p className="text-warning">Trade-off: {comparison.proposal.trade_off}</p>
              <ul className="list-disc space-y-0.5 pl-4 text-ink-faint">
                {comparison.proposal.outcome_paths.map((path) => (
                  <li key={path}>{path}</li>
                ))}
              </ul>
            </div>
          ) : forecast ? (
            <div className="space-y-2 text-xs text-ink-dim">
              <p>
                Source frame <span className="tnum text-ink">{forecast.source_frame_seq}</span>
              </p>
              <p>
                Projected reserve at horizon:{" "}
                <span className="tnum text-ink">{endSoc(forecast)}</span>
              </p>
              <p>
                {forecast.crossings.length
                  ? `Crossing: ${forecast.crossings[0].kind} in about ${Math.round(
                      forecast.crossings[0].seconds_from_source / 60,
                    )} min`
                  : "No configured reserve crossing within this horizon."}
              </p>
              <p className="text-ink-faint">{forecast.assumptions[0]}</p>
            </div>
          ) : (
            <p className="text-xs text-ink-faint">
              Forecast unavailable for this run — waiting for a valid observed state.
            </p>
          )}
        </Panel>

        {/* ------------------------------------------------ approval */}
        <Panel title="Approval and outcome" subtitle="Master only — predefined actions">
          {selected ? (
            <div className="space-y-3 text-xs">
              {raised.length ? (
                <div className="space-y-1.5 rounded border border-accent/40 bg-accent/5 p-2">
                  <p className="text-[10px] tracking-wide text-accent uppercase">
                    Recommended by a specialist
                  </p>
                  {raised.map((proposal) => (
                    <div key={proposal.id} className="flex flex-wrap items-center gap-2">
                      <span className="text-ink-dim">
                        <span className="tnum">{proposal.action_id}</span>
                        {typeof proposal.action_arguments?.duty_percent === "number"
                          ? ` at ${proposal.action_arguments.duty_percent}% duty`
                          : ""}
                        {proposal.requested_by ? ` — ${proposal.requested_by}` : ""}
                        <span className="text-ink-faint">
                          {" "}(expires {proposal.expires_at_utc.slice(11, 19)} UTC)
                        </span>
                      </span>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void approveProposal(proposal.id)}
                        className="rounded border border-accent bg-accent/10 px-2 py-1 text-accent disabled:opacity-40"
                      >
                        Approve this recommendation
                      </button>
                    </div>
                  ))}
                </div>
              ) : null}

              <div className="space-y-1.5">
                <p className="text-ink-faint">
                  Compare payload duty — the level the payload keeps running at, not the
                  size of the cut.
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {DUTY_OPTIONS.map((option) => (
                    <button
                      key={option}
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        setDuty(option);
                        void compare("REDUCE_PAYLOAD_POWER", option);
                      }}
                      className={`rounded border px-2 py-1 disabled:opacity-40 ${
                        isPayloadComparison && comparedDuty === option
                          ? "border-accent bg-accent/15 text-accent"
                          : "border-edge text-ink-dim hover:text-ink"
                      }`}
                    >
                      {option}% duty
                    </button>
                  ))}
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void compare("ISOLATE_OPTIONAL_BRANCH", 0)}
                    className={`rounded border px-2 py-1 disabled:opacity-40 ${
                      comparison?.proposal.action_id === "ISOLATE_OPTIONAL_BRANCH"
                        ? "border-accent bg-accent/15 text-accent"
                        : "border-edge text-ink-dim hover:text-ink"
                    }`}
                  >
                    Isolate branch
                  </button>
                </div>
              </div>

              {comparison && !command ? (
                <button
                  type="button"
                  className="rounded border border-accent bg-accent/10 px-3 py-1.5 text-accent disabled:opacity-40"
                  disabled={busy}
                  onClick={() => void approve()}
                >
                  {isPayloadComparison
                    ? `Approve payload duty ${comparedDuty}%`
                    : "Approve branch isolation"}
                </button>
              ) : null}

              {command ? (
                <div className="space-y-2">
                  <StatusBadge
                    tone={command.observed_outcome === "IMPROVED" ? "nominal" : "warning"}
                  >
                    {command.status}
                  </StatusBadge>
                  <p className="text-ink-dim">
                    Outcome: <span className="text-ink">{command.observed_outcome}</span>
                  </p>
                  <p className="text-ink-faint">
                    {command.outcome_detail ?? "Waiting for post-command observations."}
                  </p>
                  {command.observed_outcome === "NO_CHANGE" ? (
                    <p className="rounded border border-warning/40 bg-warning/10 px-2 py-1.5 text-warning">
                      Recovery failed. The command was accepted and applied, but measured
                      telemetry did not improve — the actuator may not have responded.
                      Approval is not recovery.
                    </p>
                  ) : null}
                </div>
              ) : null}

              {!comparison && !command ? (
                <p className="text-ink-faint">Compare an option before approving.</p>
              ) : null}

              {!selected.acknowledgement.acknowledged ? (
                <button
                  type="button"
                  className="rounded border border-edge px-2 py-1 text-ink-dim hover:text-ink"
                  onClick={() => void acknowledge()}
                >
                  Acknowledge incident
                </button>
              ) : null}
            </div>
          ) : (
            <p className="text-xs text-ink-faint">Select an active incident first.</p>
          )}
        </Panel>
      </div>

      {/* ------------------------------------------------ projection chart */}
      <Panel
        title="Reserve projection"
        subtitle="Solid is observed; dashed is the mathematical projection from that observation"
      >
        <ReserveForecastChart
          history={history}
          baseline={comparison?.baseline ?? forecast}
          action={comparison?.action ?? null}
          warningPercent={reserve.warning}
          criticalPercent={reserve.critical}
        />
        <div className="mt-2 space-y-1 text-[10px] text-ink-faint">
          {(comparison?.baseline ?? forecast)?.assumptions.map((line) => (
            <p key={line}>Assumption: {line}</p>
          ))}
          {(comparison?.baseline ?? forecast)?.limitations.map((line) => (
            <p key={line}>Limitation: {line}</p>
          ))}
          {!comparison && !forecast ? (
            <p>No projection available for this run yet. Nothing is drawn rather than zero.</p>
          ) : null}
        </div>
      </Panel>

      {/* ------------------------------------------------ AI, kept separate */}
      {selected ? <InvestigationPanel incidentId={selected.id} /> : null}
    </div>
  );
}
