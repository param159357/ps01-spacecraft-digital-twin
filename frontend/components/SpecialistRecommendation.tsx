"use client";

/**
 * A specialist compares an action and puts it in front of the master.
 *
 * The boundary this component exists to keep visible: a specialist may ask the
 * mathematics "what would this do?" and record the answer as a recommendation. It may
 * not execute anything. The server enforces that — `POST /commands` refuses any role but
 * master — and this panel says so rather than leaving the reader to assume it.
 *
 * Proposals are held by the one API process and are not persisted. A restart loses them
 * and the operator compares again, which is why the expiry is shown plainly.
 */

import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { Panel, StatusBadge } from "@/components/primitives";

interface ForecastSeries {
  channel_id: string;
  values: (number | null)[];
}

interface Forecast {
  series: ForecastSeries[];
}

interface Proposal {
  id: string;
  action_id: string;
  action_arguments: Record<string, unknown>;
  expires_at_utc: string;
  trade_off: string;
  outcome_paths: string[];
  requested_by: string | null;
  requested_by_role: string | null;
}

interface Comparison {
  baseline: Forecast;
  action: Forecast;
  proposal: Proposal;
}

const DUTY_OPTIONS = [0, 50, 100] as const;

function endSoc(forecast: Forecast | undefined): string {
  const values = forecast?.series.find((s) => s.channel_id === "battery_soc")?.values ?? [];
  const final = values.at(-1);
  return typeof final === "number" ? `${final.toFixed(1)}%` : "unavailable";
}

export function SpecialistRecommendation({
  incidentId,
  subsystemLabel,
}: {
  incidentId: string | null;
  subsystemLabel: string;
}) {
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [raised, setRaised] = useState<Proposal[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // A different incident means a different recommendation. Nothing carries over.
  useEffect(() => {
    setComparison(null);
    setRaised([]);
    setError(null);
  }, [incidentId]);

  const refresh = useCallback(async () => {
    if (!incidentId) return;
    try {
      const body = await api.get<{ proposals: Proposal[] }>(
        `/incidents/${incidentId}/proposals`,
      );
      setRaised(body.proposals);
    } catch {
      // Listing is advisory. A dropped poll must not disturb the console.
    }
  }, [incidentId]);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 3000);
    return () => window.clearInterval(id);
  }, [refresh]);

  async function compare(actionId: string, duty: number) {
    if (!incidentId) return;
    setBusy(true);
    setError(null);
    try {
      setComparison(
        await api.post<Comparison>(`/incidents/${incidentId}/compare`, {
          action_id: actionId,
          duty_percent: duty,
        }),
      );
      void refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Comparison failed.");
      setComparison(null);
    } finally {
      setBusy(false);
    }
  }

  if (!incidentId) {
    return (
      <Panel title="Recommend an action" subtitle={`${subsystemLabel} scope — master approves`}>
        <p className="text-xs text-ink-faint">
          Select an active incident to compare a supported action.
        </p>
      </Panel>
    );
  }

  const duty = Number(comparison?.proposal.action_arguments?.duty_percent ?? -1);

  return (
    <Panel
      title="Recommend an action"
      subtitle={`${subsystemLabel} scope — the master console approves`}
      actions={<StatusBadge tone="unavailable">approval: MASTER</StatusBadge>}
    >
      <div className="space-y-3 text-xs">
        <p className="text-ink-faint">
          Comparing runs the mathematics from the current observed state. It changes
          nothing on the spacecraft. Payload duty is the level the payload keeps running
          at, not the size of the cut.
        </p>

        <div className="flex flex-wrap gap-1.5">
          {DUTY_OPTIONS.map((option) => (
            <button
              key={option}
              type="button"
              disabled={busy}
              onClick={() => void compare("REDUCE_PAYLOAD_POWER", option)}
              className={`rounded border px-2 py-1 disabled:opacity-40 ${
                duty === option
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

        {error ? <p className="text-critical">{error}</p> : null}

        {comparison ? (
          <div className="space-y-1.5 rounded border border-edge/70 p-2">
            <p className="flex justify-between">
              <span className="text-ink-dim">No action, reserve at horizon</span>
              <span className="tnum">{endSoc(comparison.baseline)}</span>
            </p>
            <p className="flex justify-between">
              <span className="text-ink-dim">
                {comparison.proposal.action_id === "ISOLATE_OPTIONAL_BRANCH"
                  ? "After isolating the branch"
                  : `Payload left at ${duty}% duty`}
              </span>
              <span className="tnum text-nominal">{endSoc(comparison.action)}</span>
            </p>
            <p className="text-warning">Trade-off: {comparison.proposal.trade_off}</p>
            <p className="rounded border border-accent/40 bg-accent/10 px-2 py-1.5 text-accent">
              Recorded as a recommendation. The master console can see and approve this
              exact proposal until it expires at{" "}
              <span className="tnum">{comparison.proposal.expires_at_utc.slice(11, 19)}</span> UTC.
              This console cannot execute it.
            </p>
          </div>
        ) : null}

        {raised.length ? (
          <div className="space-y-1">
            <p className="text-[10px] tracking-wide text-ink-faint uppercase">
              Live recommendations for this incident
            </p>
            {raised.map((proposal) => (
              <p key={proposal.id} className="text-ink-dim">
                <span className="tnum">{proposal.action_id}</span>
                {typeof proposal.action_arguments?.duty_percent === "number"
                  ? ` at ${proposal.action_arguments.duty_percent}% duty`
                  : ""}
                {proposal.requested_by ? ` — raised by ${proposal.requested_by}` : ""}
              </p>
            ))}
          </div>
        ) : null}
      </div>
    </Panel>
  );
}
