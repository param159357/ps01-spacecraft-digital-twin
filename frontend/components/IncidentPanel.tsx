"use client";

// Shared incident panel for the three specialist consoles.
//
// A specialist may view, acknowledge and request a comparison within its own assigned
// subsystem. It may not execute recovery — only the master does that (CLAUDE.md section
// 3), and this panel says so rather than showing a disabled button with no explanation.
//
// The master console keeps its own richer panel, which already carries the approval and
// observed-outcome workflow verified in Phase 3.

import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { Panel, StatusBadge, type StatusTone } from "@/components/primitives";
import { InvestigationPanel } from "@/components/InvestigationPanel";

export interface IncidentSummary {
  id: string;
  rule_id: string;
  family: string;
  subsystem: string;
  severity: "INFO" | "WARNING" | "CRITICAL" | "UNKNOWN";
  lifecycle: "PENDING" | "OPEN" | "RECOVERING" | "RESOLVED";
  affected_entity: string;
  first_seen_utc: string;
  observed_evidence: { text: string; evidence_id: string }[];
  suspected_causes?: string[];
  missing_evidence?: string[];
  acknowledgement: { acknowledged: boolean; acknowledged_by?: string | null };
}

interface IncidentListResponse {
  incidents: IncidentSummary[];
  role: string;
  hidden_from_this_role: number;
}

const SEVERITY_TONE: Record<IncidentSummary["severity"], StatusTone> = {
  CRITICAL: "critical",
  WARNING: "warning",
  INFO: "accent",
  UNKNOWN: "unavailable",
};

const LIFECYCLE_TONE: Record<IncidentSummary["lifecycle"], StatusTone> = {
  PENDING: "warning",
  OPEN: "critical",
  RECOVERING: "accent",
  RESOLVED: "nominal",
};

export function IncidentPanel({ subsystemLabel }: { subsystemLabel: string }) {
  const [incidents, setIncidents] = useState<IncidentSummary[]>([]);
  const [hiddenCount, setHiddenCount] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const body = await api.get<IncidentListResponse>("/incidents");
      setIncidents(body.incidents);
      setHiddenCount(body.hidden_from_this_role);
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.isNotImplementedYet) return;
      setError(err instanceof ApiError ? err.message : "Could not load incidents.");
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 3000);
    return () => window.clearInterval(id);
  }, [refresh]);

  async function acknowledge(incidentId: string) {
    setBusy(true);
    try {
      await api.post(`/incidents/${incidentId}/acknowledge`);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Acknowledge failed.");
    } finally {
      setBusy(false);
    }
  }

  // An alert's "View incident" names a specific incident, which may not be the one the
  // specialist currently has open.
  useEffect(() => {
    function onSelect(event: Event) {
      const id = (event as CustomEvent<string>).detail;
      if (typeof id === "string") setSelected(id);
    }
    window.addEventListener("ps01:select-incident", onSelect);
    return () => window.removeEventListener("ps01:select-incident", onSelect);
  }, []);

  const active = incidents.filter((i) => i.lifecycle !== "RESOLVED");
  const detail = incidents.find((i) => i.id === selected) ?? null;

  return (
    <Panel
      title="Incidents"
      subtitle={`${subsystemLabel} scope`}
      actions={
        <StatusBadge tone={active.length ? "critical" : "nominal"}>
          {active.length} active
        </StatusBadge>
      }
    >
      {error ? <p className="mb-2 text-xs text-critical">{error}</p> : null}

      {active.length === 0 ? (
        <p className="text-xs text-ink-faint">
          No active incidents in this subsystem.
          {hiddenCount > 0 ? (
            <>
              {" "}
              <span className="text-warning">
                {hiddenCount} active incident{hiddenCount === 1 ? "" : "s"} exist in other
                subsystems
              </span>{" "}
              — outside this console&apos;s scope. The master console has the full picture.
            </>
          ) : null}
        </p>
      ) : (
        <ul className="space-y-1.5">
          {active.map((incident) => (
            <li key={incident.id}>
              <button
                type="button"
                onClick={() => setSelected(selected === incident.id ? null : incident.id)}
                className="w-full rounded border border-edge px-2.5 py-2 text-left transition-colors hover:border-edge-strong"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="tnum text-xs text-ink">{incident.rule_id}</span>
                  <span className="flex items-center gap-1.5">
                    <StatusBadge tone={SEVERITY_TONE[incident.severity]}>
                      {incident.severity}
                    </StatusBadge>
                    <StatusBadge tone={LIFECYCLE_TONE[incident.lifecycle]}>
                      {incident.lifecycle}
                    </StatusBadge>
                  </span>
                </div>
                <div className="mt-1 flex items-center justify-between gap-2">
                  <span className="text-[10px] text-ink-faint">
                    {incident.affected_entity} · id {incident.id.slice(0, 8)}
                  </span>
                  {incident.acknowledgement.acknowledged ? (
                    <span className="text-[10px] text-nominal">
                      acknowledged{" "}
                      {incident.acknowledgement.acknowledged_by
                        ? `by ${incident.acknowledgement.acknowledged_by}`
                        : ""}
                    </span>
                  ) : null}
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}

      {detail ? (
        <div className="mt-3 space-y-2 rounded border border-edge-strong bg-panel-raised p-3">
          <p className="text-[10px] tracking-wide text-ink-faint uppercase">Observed evidence</p>
          <ul className="space-y-1 text-xs text-ink-dim">
            {detail.observed_evidence.map((item) => (
              <li key={item.evidence_id}>
                {item.text}{" "}
                <span className="tnum text-[10px] text-ink-faint">({item.evidence_id})</span>
              </li>
            ))}
          </ul>

          {detail.suspected_causes?.length ? (
            <>
              <p className="text-[10px] tracking-wide text-ink-faint uppercase">
                Suspected causes — hypotheses, not established fact
              </p>
              <ul className="list-disc space-y-0.5 pl-4 text-xs text-ink-dim">
                {detail.suspected_causes.map((cause) => (
                  <li key={cause}>{cause}</li>
                ))}
              </ul>
            </>
          ) : null}

          {detail.missing_evidence?.length ? (
            <>
              <p className="text-[10px] tracking-wide text-ink-faint uppercase">
                Missing evidence
              </p>
              <ul className="list-disc space-y-0.5 pl-4 text-xs text-ink-faint">
                {detail.missing_evidence.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          ) : null}

          <div className="flex flex-wrap items-center gap-2 pt-1">
            <button
              type="button"
              disabled={busy || detail.acknowledgement.acknowledged}
              onClick={() => void acknowledge(detail.id)}
              className="rounded border border-edge-strong px-3 py-1.5 text-xs text-ink-dim transition-colors hover:border-accent hover:text-accent disabled:opacity-40"
            >
              {detail.acknowledgement.acknowledged ? "Acknowledged" : "Acknowledge"}
            </button>
            <span className="text-[10px] text-ink-faint">
              Acknowledging records that this console has seen the incident. It is not a
              fix and does not change the vehicle.
            </span>
          </div>

          <p className="border-t border-edge pt-2 text-[10px] text-ink-faint">
            Recovery commands are executed by the master console only. This console can
            acknowledge and investigate within its own subsystem.
          </p>
          <InvestigationPanel incidentId={detail.id} />
        </div>
      ) : null}
    </Panel>
  );
}
