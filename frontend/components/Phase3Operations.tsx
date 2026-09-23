"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { Panel, StatusBadge } from "@/components/primitives";

interface Incident {
  id: string;
  rule_id: string;
  severity: "INFO" | "WARNING" | "CRITICAL" | "UNKNOWN";
  lifecycle: "PENDING" | "OPEN" | "RECOVERING" | "RESOLVED";
  affected_entity: string;
  observed_evidence: { text: string; evidence_id: string }[];
  acknowledgement: { acknowledged: boolean };
}

interface Forecast {
  id: string;
  source_frame_seq: number;
  horizon_s: number;
  assumptions: string[];
  limitations: string[];
  crossings: { kind: string; seconds_from_source: number; limit_value: number }[];
  series: { channel_id: string; values: (number | null)[] }[];
}

interface Comparison {
  baseline: Forecast;
  action: Forecast;
  proposal: {
    id: string;
    expires_at_utc: string;
    trade_off: string;
    action_id: string;
    outcome_paths: string[];
  };
}

interface Command {
  id: string;
  status: string;
  observed_outcome: string;
  outcome_detail: string | null;
}

function endSoc(value: Forecast): string {
  const values = value.series.find((s) => s.channel_id === "battery_soc")?.values ?? [];
  const final = values.at(-1);
  return typeof final === "number" ? `${final.toFixed(1)}%` : "unavailable";
}

export function Phase3Operations() {
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [command, setCommand] = useState<Command | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const incidentBody = await api.get<{ incidents: Incident[] }>("/incidents");
      setIncidents(incidentBody.incidents);
      try {
        setForecast(await api.get<Forecast>("/forecasts/latest"));
      } catch (err) {
        if (!(err instanceof ApiError) || err.code !== "FORECAST_UNAVAILABLE") throw err;
      }
      if (command) setCommand(await api.get<Command>(`/commands/${command.id}`));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not refresh operations.");
    }
  }, [command]);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 2000);
    return () => window.clearInterval(id);
  }, [refresh]);

  async function compare(incidentId: string, actionId: string) {
    setBusy(true);
    setError(null);
    setCommand(null);
    try {
      setComparison(
        await api.post<Comparison>(`/incidents/${incidentId}/compare`, {
          action_id: actionId,
          duty_percent: 0,
        }),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Comparison failed.");
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

  const active = incidents.find((i) => i.lifecycle !== "RESOLVED") ?? incidents[0];
  return (
    <div className="grid gap-4 xl:grid-cols-3">
      <Panel title="Incidents" subtitle="Deterministic observed evidence">
        {error ? <p className="mb-2 text-xs text-critical">{error}</p> : null}
        {active ? (
          <div className="space-y-2 text-xs">
            <div className="flex justify-between gap-2">
              <span className="tnum text-ink">{active.rule_id}</span>
              <StatusBadge tone={active.severity === "CRITICAL" ? "critical" : "warning"}>
                {active.severity} · {active.lifecycle}
              </StatusBadge>
            </div>
            <p className="text-ink-dim">{active.observed_evidence.at(-1)?.text}</p>
            <p className="tnum text-ink-faint">ID {active.id.slice(0, 8)}</p>
            {/* Only actions the backend reports as enabled are offered here. */}
            <div className="flex flex-wrap gap-2 pt-1">
              <button
                className="rounded border border-accent px-2 py-1 text-accent disabled:opacity-40"
                disabled={busy}
                onClick={() => void compare(active.id, "REDUCE_PAYLOAD_POWER")}
              >
                Compare payload reduction
              </button>
              <button
                className="rounded border border-accent px-2 py-1 text-accent disabled:opacity-40"
                disabled={busy}
                onClick={() => void compare(active.id, "ISOLATE_OPTIONAL_BRANCH")}
              >
                Compare branch isolation
              </button>
            </div>
          </div>
        ) : <p className="text-xs text-ink-faint">No active incident. Load D02 from the simulator.</p>}
      </Panel>

      <Panel title="30-minute forecast" subtitle="Observed-state estimate; no hidden truth">
        {comparison ? (
          <div className="space-y-2 text-xs">
            <p className="flex justify-between"><span className="text-ink-dim">No action reserve</span><span className="tnum">{endSoc(comparison.baseline)}</span></p>
            <p className="flex justify-between">
              <span className="text-ink-dim">
                {comparison.proposal.action_id === "ISOLATE_OPTIONAL_BRANCH"
                  ? "After isolating branch"
                  : "Payload 0% reserve"}
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
            <p>Source frame <span className="tnum text-ink">{forecast.source_frame_seq}</span></p>
            <p>Projected reserve at horizon: <span className="tnum text-ink">{endSoc(forecast)}</span></p>
            <p>{forecast.crossings.length ? `Crossing: ${forecast.crossings[0].kind} in about ${Math.round(forecast.crossings[0].seconds_from_source / 60)} min` : "No configured reserve crossing within this horizon."}</p>
            <p className="text-ink-faint">{forecast.assumptions[0]}</p>
          </div>
        ) : <p className="text-xs text-ink-faint">Waiting for a valid observed state.</p>}
      </Panel>

      <Panel title="Approval and outcome" subtitle="Master-only predefined action">
        {comparison && !command ? (
          <button
            className="rounded border border-accent bg-accent/10 px-3 py-1.5 text-xs text-accent disabled:opacity-40"
            disabled={busy}
            onClick={() => void approve()}
          >
            {comparison.proposal.action_id === "ISOLATE_OPTIONAL_BRANCH"
              ? "Approve branch isolation"
              : "Approve payload duty 0%"}
          </button>
        ) : null}
        {command ? (
          <div className="space-y-2 text-xs">
            <StatusBadge tone={command.observed_outcome === "IMPROVED" ? "nominal" : "warning"}>{command.status}</StatusBadge>
            <p className="text-ink-dim">Outcome: <span className="text-ink">{command.observed_outcome}</span></p>
            <p className="text-ink-faint">
              {command.outcome_detail ?? "Waiting for post-command observations."}
            </p>
            {command.observed_outcome === "NO_CHANGE" ? (
              <p className="rounded border border-warning/40 bg-warning/10 px-2 py-1.5 text-warning">
                Recovery failed. The command was accepted and applied, but measured
                telemetry did not improve — the actuator may not have responded. Approval
                is not recovery.
              </p>
            ) : null}
          </div>
        ) : <p className="text-xs text-ink-faint">Compare an active incident before approval.</p>}
      </Panel>
    </div>
  );
}
