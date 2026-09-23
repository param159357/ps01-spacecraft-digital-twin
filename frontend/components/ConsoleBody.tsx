"use client";

// Shared Phase 1 console body.
//
// What it shows is real: the role-filtered channel dictionary and the detection/action
// coverage, both fetched live from the backend's validated fixture files. What does not
// exist yet (telemetry, incidents, forecasts) is named with its phase rather than
// mocked, so no screen implies capability the backend does not have.

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ApiError, api } from "@/lib/api";
import { formatUnit, type ChannelsResponse, type CoverageResponse } from "@/lib/types";
import { Panel, PendingPanel, StatusBadge } from "./primitives";

function useApiResource<T>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    api
      .get<T>(path, controller.signal)
      .then(setData)
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setError(err instanceof ApiError ? err.message : "Request failed.");
      });
    return () => controller.abort();
  }, [path]);

  return { data, error };
}

function ChannelTable() {
  const { data, error } = useApiResource<ChannelsResponse>("/config/channels");

  if (error) return <p className="text-sm text-critical">{error}</p>;
  if (!data) return <p className="text-sm text-ink-faint">Loading channel dictionary…</p>;

  return (
    <>
      <div className="mb-2 flex items-center gap-3 text-xs text-ink-faint">
        <span>
          {data.channels.length} channel{data.channels.length === 1 ? "" : "s"} visible to this
          role
        </span>
        <span className="tnum">{data.config_version}</span>
      </div>
      <div className="max-h-[420px] overflow-auto">
        <table className="w-full border-collapse text-left text-xs">
          <thead className="sticky top-0 bg-panel">
            <tr className="border-b border-edge text-ink-faint">
              <th className="py-1.5 pr-3 font-medium">Channel</th>
              <th className="py-1.5 pr-3 font-medium">Unit</th>
              <th className="py-1.5 pr-3 font-medium">Source</th>
              <th className="py-1.5 pr-3 font-medium">Operating limits</th>
              <th className="py-1.5 font-medium">Forecast coverage</th>
            </tr>
          </thead>
          <tbody>
            {data.channels.map((channel) => {
              const lo = channel.operating_min;
              const hi = channel.operating_max;
              const limits =
                lo === null && hi === null
                  ? "none configured"
                  : `${lo === null ? "—" : lo} … ${hi === null ? "—" : hi}`;
              return (
                <tr key={channel.id} className="border-b border-edge/50 align-top">
                  <td className="py-1.5 pr-3">
                    <span className="tnum text-ink">{channel.id}</span>
                    <span className="block text-ink-faint">{channel.label}</span>
                  </td>
                  <td className="tnum py-1.5 pr-3 text-ink-dim">{formatUnit(channel.unit)}</td>
                  <td className="py-1.5 pr-3 text-ink-faint">{channel.provenance}</td>
                  <td className="tnum py-1.5 pr-3 text-ink-dim">{limits}</td>
                  <td className="py-1.5">
                    <StatusBadge
                      tone={channel.prediction === "MODELLED" ? "accent" : "unavailable"}
                      title={
                        channel.prediction === "MODELLED"
                          ? "Physically modelled and forecastable."
                          : "Displayed value only — its future behaviour is not modelled."
                      }
                    >
                      {channel.prediction}
                    </StatusBadge>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

function CoverageTable() {
  const { data, error } = useApiResource<CoverageResponse>("/config/coverage");

  if (error) return <p className="text-sm text-critical">{error}</p>;
  if (!data) return <p className="text-sm text-ink-faint">Loading coverage…</p>;

  const liveRules = data.rules.filter((r) => r.implemented).length;
  const liveActions = data.actions.filter((a) => a.enabled).length;

  return (
    <div className="space-y-4">
      <p className="text-xs text-ink-faint">
        {data.vehicle_name} · model {data.model_version}
      </p>

      <div>
        <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-ink-dim uppercase">
          Detection rules — {liveRules} of {data.rules.length} active
        </h3>
        <ul className="space-y-1">
          {data.rules.map((rule) => (
            <li key={rule.id} className="flex items-center justify-between gap-3 text-xs">
              <span className="tnum truncate text-ink-dim">{rule.id}</span>
              <StatusBadge tone={rule.implemented ? "nominal" : "unavailable"}>
                {rule.implemented
                  ? "Active"
                  : rule.planned_phase
                    ? `Phase ${rule.planned_phase}`
                    : "Not planned"}
              </StatusBadge>
            </li>
          ))}
        </ul>
      </div>

      <div>
        <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-ink-dim uppercase">
          Recovery actions — {liveActions} of {data.actions.length} available
        </h3>
        <ul className="space-y-1">
          {data.actions.map((action) => (
            <li key={action.id} className="flex items-center justify-between gap-3 text-xs">
              <span className="tnum truncate text-ink-dim">{action.id}</span>
              <StatusBadge tone={action.enabled ? "nominal" : "unavailable"}>
                {action.enabled
                  ? "Available"
                  : action.planned_phase
                    ? `Phase ${action.planned_phase}`
                    : "Not planned"}
              </StatusBadge>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-[11px] text-ink-faint">
          A disabled action is never offered as an available fix. Only the master console
          executes recovery.
        </p>
      </div>
    </div>
  );
}

export function ConsoleBody({
  headline,
  summary,
  pending,
  children,
}: {
  headline: string;
  summary: string;
  pending: { title: string; phase: number; what: string }[];
  children?: ReactNode;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-ink">{headline}</h1>
        <p className="mt-1 max-w-4xl text-sm text-ink-dim">{summary}</p>
      </div>

      {children}

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel
          title="Telemetry contract"
          subtitle="Channels this console may read, with units and forecast coverage"
          className="xl:col-span-2"
        >
          <ChannelTable />
        </Panel>

        <Panel title="Current capability" subtitle="What the backend actually supports today">
          <CoverageTable />
        </Panel>
      </div>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {pending.map((item) => (
          <PendingPanel
            key={item.title}
            title={item.title}
            phase={item.phase}
            what={item.what}
          />
        ))}
      </div>
    </div>
  );
}
