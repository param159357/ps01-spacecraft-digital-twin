"use client";

import { AppShell, headerFromLive } from "@/components/AppShell";
import { IncidentPanel } from "@/components/IncidentPanel";
import { IncidentAlerts } from "@/components/IncidentAlerts";
import { LiveChart } from "@/components/LiveChart";
import { Panel, StatusBadge } from "@/components/primitives";
import { ChannelReadout, ChannelTableRow, useChannelMeta } from "@/components/telemetry";
import { useRequireRole } from "@/lib/auth";
import { freshnessTone, useLiveStream } from "@/lib/live";

const CHART_CHANNELS = ["battery_soc", "load_power", "solar_current", "battery_current"];

const DETAIL_CHANNELS = [
  "solar_voltage",
  "solar_current",
  "bus_voltage",
  "load_power",
  "battery_soc",
  "battery_voltage",
  "battery_current",
  "battery_temperature",
  "cell_voltage_spread",
  "heater_power",
];

function PowerBoundary({ engineering }: { engineering: Record<string, unknown> }) {
  const rows: [string, string][] = [
    ["available_solar_w", "Available generation"],
    ["solar_dispatched_w", "Dispatched"],
    ["curtailed_w", "Curtailed"],
    ["served_load_w", "Served load"],
    ["unmet_load_w", "Unmet demand"],
    ["charge_bus_w", "Charging"],
    ["discharge_bus_w", "Discharging"],
  ];
  const shed = engineering.shed_loads;

  return (
    <div className="space-y-2">
      <table className="w-full text-xs">
        <tbody>
          {rows.map(([key, label]) => {
            const value = engineering[key];
            return (
              <tr key={key} className="border-b border-edge/40">
                <td className="py-1 text-ink-dim">{label}</td>
                <td className="tnum py-1 text-right text-ink">
                  {typeof value === "number" ? value.toFixed(1) : "—"}
                  <span className="ml-1 text-ink-faint">W</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      {typeof shed === "string" && shed ? (
        <p className="text-xs text-warning">
          Load shedding active: {shed}. Essential load is never shed.
        </p>
      ) : null}
      {typeof engineering.payload_outage_s === "number" && engineering.payload_outage_s > 0 ? (
        <p className="rounded border border-critical/40 bg-critical/10 px-2 py-1.5 text-xs text-critical">
          Payload operation lost: {engineering.payload_outage_s.toFixed(0)} s and counting.
          Already-elapsed time cannot be recovered by any later action.
        </p>
      ) : null}
      <p className="text-[10px] text-ink-faint">
        Curtailment at a full battery is expected behaviour, not a generation fault.
        <span className="tnum"> load_power</span> is requested demand; served and unmet
        power are derived and shown separately.
      </p>
    </div>
  );
}

export default function PowerConsole() {
  const { user, checking } = useRequireRole("POWER");
  const live = useLiveStream(CHART_CHANNELS);
  const { meta, freshness } = useChannelMeta();

  if (checking || !user) return null;

  const { frame, run, history } = live;
  const illumination = frame?.context.illumination ?? null;

  return (
    <AppShell role="POWER" runState={headerFromLive(live)}>
      <IncidentAlerts />
      <div className="space-y-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h1 className="text-lg font-semibold text-ink">Power console</h1>
            <p className="mt-0.5 max-w-4xl text-sm text-ink-dim">
              Generation, battery and electrical load. Voltage is in volts, power in watts
              and energy in watt-hours; these are never interchanged. You may acknowledge
              power incidents here — the master executes recovery.
            </p>
          </div>
          <StatusBadge tone={illumination === "ECLIPSE" ? "warning" : "nominal"}>
            {illumination ?? "NO RUN"}
          </StatusBadge>
        </div>

        {!run?.run_id ? (
          <p className="rounded border border-edge bg-panel px-4 py-3 text-sm text-ink-dim">
            No run is active. The simulator console starts one.
          </p>
        ) : null}

        <Panel
          title="Battery and demand"
          subtitle="Latest received frame"
          actions={
            <StatusBadge
              tone={freshnessTone(
                live.dataAgeS,
                freshness.warning_after_s,
                freshness.critical_after_s,
              )}
            >
              frame {frame?.frame_seq ?? "—"}
            </StatusBadge>
          }
        >
          <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 xl:grid-cols-6">
            <ChannelReadout channelId="battery_soc" frame={frame} meta={meta} digits={1} />
            <ChannelReadout channelId="load_power" frame={frame} meta={meta} digits={1} />
            <ChannelReadout channelId="battery_current" frame={frame} meta={meta} digits={2} />
            <ChannelReadout channelId="battery_voltage" frame={frame} meta={meta} digits={2} />
            <ChannelReadout channelId="bus_voltage" frame={frame} meta={meta} digits={2} />
            <ChannelReadout channelId="battery_temperature" frame={frame} meta={meta} digits={1} />
          </div>
        </Panel>

        <div className="grid gap-4 xl:grid-cols-3">
          <Panel
            title="State of charge and demand"
            subtitle="Separate axes — percent and watts are never merged"
            className="xl:col-span-2"
          >
            <LiveChart
              data={history}
              meta={meta}
              series={[
                { channelId: "battery_soc", axis: "left" },
                { channelId: "load_power", axis: "right" },
              ]}
              height={220}
            />
          </Panel>

          <Panel title="Power boundary" subtitle="Derived engineering detail">
            {frame ? (
              <PowerBoundary engineering={frame.engineering} />
            ) : (
              <p className="text-xs text-ink-faint">No frame received yet.</p>
            )}
          </Panel>
        </div>

        <div className="grid gap-4 xl:grid-cols-3">
          <Panel
            title="Generation"
            subtitle="Array current against battery current, in amperes"
            className="xl:col-span-2"
          >
            <LiveChart
              data={history}
              meta={meta}
              series={[
                { channelId: "solar_current", axis: "left" },
                { channelId: "battery_current", axis: "left" },
              ]}
              height={200}
              showLimits={false}
            />
            <p className="mt-1 text-[10px] text-ink-faint">
              Battery current is positive when discharging and negative when charging. In
              eclipse the array reports zero, which is expected, not a fault.
            </p>
          </Panel>

          <div id="incident-panel"><IncidentPanel subsystemLabel="Power" /></div>
        </div>

        <Panel title="Power channels" subtitle="Every channel this console may read">
          <div className="max-h-[360px] overflow-auto">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-panel">
                <tr className="border-b border-edge text-ink-faint">
                  <th className="py-1.5 pr-3 font-medium">Channel</th>
                  <th className="py-1.5 pr-3 text-right font-medium">Value</th>
                  <th className="py-1.5 pr-3 font-medium">Unit</th>
                  <th className="py-1.5 pr-3 font-medium">Operating limits</th>
                  <th className="py-1.5 font-medium">Quality</th>
                </tr>
              </thead>
              <tbody>
                {DETAIL_CHANNELS.map((id) => (
                  <ChannelTableRow key={id} channelId={id} frame={frame} meta={meta} />
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </div>
    </AppShell>
  );
}
