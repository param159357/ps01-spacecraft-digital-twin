"use client";

/**
 * Immediate on-screen alert for a newly opened or newly escalated incident.
 *
 * The incident panel further down the page is the place an operator investigates; it is
 * not the place they find out. This surfaces the same record the moment it appears, then
 * gets out of the way.
 *
 * Deliberate behaviour:
 *   * Announces an incident once. A repeat only happens if its severity actually rises,
 *     which is a different operational event, not a refresh.
 *   * Reads `/incidents`, which the server already scopes by role. A power specialist
 *     therefore cannot be alerted about a flight incident; no filtering happens here.
 *   * Never blocks. It is a corner stack, capped, dismissible, and the live telemetry
 *     behind it stays visible and interactive.
 *   * Shows only what the incident record actually says. No invented urgency text.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { StatusBadge, type StatusTone } from "./primitives";

type Severity = "INFO" | "WARNING" | "CRITICAL" | "UNKNOWN";

interface AlertIncident {
  id: string;
  rule_id: string;
  subsystem: string;
  severity: Severity;
  lifecycle: "PENDING" | "OPEN" | "RECOVERING" | "RESOLVED";
  affected_entity: string;
  observed_evidence: { text: string }[];
  acknowledgement: { acknowledged: boolean };
}

interface IncidentListResponse {
  incidents: AlertIncident[];
}

const SEVERITY_TONE: Record<Severity, StatusTone> = {
  CRITICAL: "critical",
  WARNING: "warning",
  INFO: "accent",
  UNKNOWN: "unavailable",
};

// Ordered so an escalation can be detected. UNKNOWN is coverage, not urgency, so it sits
// at the bottom and never counts as an escalation target on its own.
const SEVERITY_RANK: Record<Severity, number> = {
  UNKNOWN: 0,
  INFO: 1,
  WARNING: 2,
  CRITICAL: 3,
};

const POLL_MS = 3000;
const MAX_VISIBLE = 3;

export function IncidentAlerts() {
  const [alerts, setAlerts] = useState<AlertIncident[]>([]);
  // Highest severity already announced per incident id. Survives re-renders.
  const announced = useRef<Map<string, number>>(new Map());

  const dismiss = useCallback((id: string) => {
    setAlerts((current) => current.filter((item) => item.id !== id));
  }, []);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    async function poll() {
      try {
        const body = await api.get<IncidentListResponse>("/incidents", controller.signal);
        if (cancelled) return;
        const fresh: AlertIncident[] = [];
        const live = new Set<string>();
        for (const incident of body.incidents) {
          live.add(incident.id);
          if (incident.lifecycle === "RESOLVED") continue;
          const rank = SEVERITY_RANK[incident.severity] ?? 0;
          const seen = announced.current.get(incident.id);
          if (seen === undefined || rank > seen) {
            announced.current.set(incident.id, rank);
            fresh.push(incident);
          }
        }
        // Forget incidents the server no longer lists, so a reset run can alert again.
        for (const id of Array.from(announced.current.keys())) {
          if (!live.has(id)) announced.current.delete(id);
        }
        if (fresh.length) {
          setAlerts((current) => {
            const byId = new Map(current.map((item) => [item.id, item]));
            for (const item of fresh) byId.set(item.id, item);
            return Array.from(byId.values()).slice(-MAX_VISIBLE);
          });
        }
      } catch {
        // A dropped poll, an expired session or a paused backend must never break the
        // console. The incident panel and the live stream report their own state.
      }
    }

    void poll();
    const id = window.setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(id);
    };
  }, []);

  const acknowledge = useCallback(
    async (incidentId: string) => {
      try {
        await api.post(`/incidents/${incidentId}/acknowledge`);
      } catch {
        // Acknowledgement is also available in the incident panel; failing here is not
        // worth interrupting the operator for.
      }
      dismiss(incidentId);
    },
    [dismiss],
  );

  const view = useCallback(
    (incidentId: string) => {
      dismiss(incidentId);
      document.getElementById("incident-panel")?.scrollIntoView({ behavior: "smooth", block: "start" });
    },
    [dismiss],
  );

  if (!alerts.length) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed inset-x-0 top-[72px] z-30 flex flex-col items-end gap-2 px-4"
    >
      {alerts.map((incident) => (
        <article
          key={incident.id}
          className={`pointer-events-auto w-full max-w-[380px] rounded border bg-panel/95 p-3 shadow-lg backdrop-blur ${
            incident.severity === "CRITICAL" ? "border-critical/60" : "border-warning/60"
          }`}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="tnum truncate text-sm font-semibold text-ink">{incident.rule_id}</p>
              <p className="truncate text-xs text-ink-faint">
                {incident.subsystem} · {incident.affected_entity}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              <StatusBadge tone={SEVERITY_TONE[incident.severity]}>{incident.severity}</StatusBadge>
              <button
                type="button"
                aria-label="Dismiss alert"
                className="rounded px-1.5 text-ink-faint hover:text-ink"
                onClick={() => dismiss(incident.id)}
              >
                ×
              </button>
            </div>
          </div>

          {incident.observed_evidence.length ? (
            <p className="mt-2 line-clamp-3 text-xs text-ink-dim">
              {incident.observed_evidence[incident.observed_evidence.length - 1].text}
            </p>
          ) : null}

          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded border border-accent/50 bg-accent/10 px-2.5 py-1 text-xs font-medium text-accent"
              onClick={() => view(incident.id)}
            >
              View incident
            </button>
            {incident.acknowledgement.acknowledged ? null : (
              <button
                type="button"
                className="rounded border border-edge px-2.5 py-1 text-xs text-ink-dim hover:text-ink"
                onClick={() => void acknowledge(incident.id)}
              >
                Acknowledge
              </button>
            )}
          </div>
        </article>
      ))}
    </div>
  );
}
