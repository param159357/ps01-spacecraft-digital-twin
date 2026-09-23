"use client";

import { AppShell, headerFromLive } from "@/components/AppShell";
import { LiveChart } from "@/components/LiveChart";
import { Panel, PendingPanel, StatusBadge } from "@/components/primitives";
import { ChannelReadout, ChannelTableRow, useChannelMeta } from "@/components/telemetry";
import { Phase3Operations } from "@/components/Phase3Operations";
import { SubsystemDiagram } from "@/components/SubsystemDiagram";
import { useRequireRole } from "@/lib/auth";
import { freshnessTone, useLiveStream } from "@/lib/live";

// Charted channels. Power and temperature are kept on separate axes with their own
// units; they are never combined on one unexplained scale.
const CHART_CHANNELS = [
  "battery_soc",
  "load_power",
  "battery_temperature",
  "bus_voltage",
  "battery_current",
];

const OVERVIEW_TABLE = [
  "bus_voltage",
  "battery_voltage",
  "battery_current",
  "solar_voltage",
  "solar_current",
  "heater_power",
  "panel_temperature",
  "computer_temperature",
  "payload_temperature",
  "tank_temperature",
  "tank_pressure",
  "propellant_mass",
];

function EngineeringDetail({ engineering }: { engineering: Record<string, unknown> }) {
  const rows: [string, string, string][] = [
    ["available_solar_w", "Available generation", "W"],
    ["solar_dispatched_w", "Dispatched", "W"],
    ["curtailed_w", "Curtailed", "W"],
    ["served_load_w", "Served load", "W"],
    ["unmet_load_w", "Unmet demand", "W"],
    ["charge_bus_w", "Charging", "W"],
    ["discharge_bus_w", "Discharging", "W"],
  ];

  return (
    <div className="space-y-2">
      <table className="w-full text-xs">
        <tbody>
          {rows.map(([key, label, unit]) => {
            const value = engineering[key];
            return (
              <tr key={key} className="border-b border-edge/40">
                <td className="py-1 text-ink-dim">{label}</td>
                <td className="tnum py-1 text-right text-ink">
                  {typeof value === "number" ? value.toFixed(1) : "—"}
                  <span className="ml-1 text-ink-faint">{unit}</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="text-[10px] text-ink-faint">
        The <span className="tnum">load_power</span> channel is requested demand. Served and
        unmet power are derived values shown here so the two are never confused.
      </p>
      {typeof engineering.shed_loads === "string" && engineering.shed_loads ? (
        <p className="text-xs text-warning">Load shedding active: {engineering.shed_loads}</p>
      ) : null}
      {typeof engineering.payload_outage_s === "number" && engineering.payload_outage_s > 0 ? (
        <p className="rounded border border-critical/40 bg-critical/10 px-2 py-1.5 text-xs text-critical">
          Payload operation lost: {engineering.payload_outage_s.toFixed(0)} s. This time is
          already gone — a recovery approved now restores the payload but cannot return it.
        </p>
      ) : null}
    </div>
  );
}

export default function MasterConsole() {
  const { user, checking } = useRequireRole("MASTER");
  const live = useLiveStream(CHART_CHANNELS);
  const { meta, freshness } = useChannelMeta();

  if (checking || !user) return null;

  const { frame, run, history } = live;
  const stale =
    live.dataAgeS !== null && live.dataAgeS >= freshness.warning_after_s && run?.status === "RUNNING";

  return (
    <AppShell role="MASTER" runState={headerFromLive(live)}>
      <div className="space-y-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h1 className="text-lg font-semibold text-ink">Master console</h1>
            <p className="mt-0.5 text-sm text-ink-dim">
            Overall health, evidence, forecast comparison and observed recovery.
            </p>
          </div>
          {run?.storage_error ? (
            <p className="rounded border border-critical/40 bg-critical/10 px-3 py-1.5 text-xs text-critical">
              Storage error — the run is paused and data is not being recorded:{" "}
              {run.storage_error}
            </p>
          ) : null}
        </div>

        {!run?.run_id ? (
          <p className="rounded border border-edge bg-panel px-4 py-3 text-sm text-ink-dim">
            No run is active. The simulator console starts one; this console will follow
            automatically.
          </p>
        ) : null}

        {stale ? (
          <p
            role="status"
            className="rounded border border-warning/40 bg-warning/10 px-4 py-2 text-xs text-warning"
          >
            Telemetry is stale — the last frame arrived {live.dataAgeS?.toFixed(1)}s ago. The
            values below are the last received readings, not current measurements.
          </p>
        ) : null}

        {/* Key values, fixed positions so nothing jumps as digits change. */}
        <Panel
          title="Key values"
          subtitle="Latest received frame"
          actions={
            <StatusBadge tone={freshnessTone(live.dataAgeS, freshness.warning_after_s, freshness.critical_after_s)}>
              frame {frame?.frame_seq ?? "—"}
            </StatusBadge>
          }
        >
          <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 xl:grid-cols-6">
            <ChannelReadout channelId="battery_soc" frame={frame} meta={meta} digits={1} />
            <ChannelReadout channelId="load_power" frame={frame} meta={meta} digits={1} />
            <ChannelReadout channelId="solar_current" frame={frame} meta={meta} digits={2} />
            <ChannelReadout channelId="battery_temperature" frame={frame} meta={meta} digits={1} />
            <ChannelReadout channelId="bus_voltage" frame={frame} meta={meta} digits={2} />
            <ChannelReadout channelId="battery_current" frame={frame} meta={meta} digits={2} />
          </div>
        </Panel>

        <div className="grid gap-4 xl:grid-cols-3">
          <Panel
            title="Subsystem overview"
            subtitle="Schematic, one key value per subsystem"
            className="xl:col-span-2"
          >
            <SubsystemDiagram frame={frame} meta={meta} />
          </Panel>

          <Panel title="Power boundary" subtitle="Derived engineering detail">
            {frame ? (
              <EngineeringDetail engineering={frame.engineering} />
            ) : (
              <p className="text-xs text-ink-faint">No frame received yet.</p>
            )}
          </Panel>
        </div>

        <div className="grid gap-4 xl:grid-cols-3">
          <Panel
            title="Energy and demand"
            subtitle="State of charge against requested load"
            className="xl:col-span-2"
          >
            <LiveChart
              data={history}
              meta={meta}
              series={[
                { channelId: "battery_soc", axis: "left" },
                { channelId: "load_power", axis: "right" },
              ]}
              height={230}
            />
          </Panel>

          <Panel title="Link and attitude" subtitle="Flight-systems summary — PROFILE coverage">
            <div className="grid grid-cols-2 gap-x-4 gap-y-3">
              <ChannelReadout channelId="link_snr" frame={frame} meta={meta} digits={1} compact />
              <ChannelReadout
                channelId="packet_error_rate"
                frame={frame}
                meta={meta}
                digits={2}
                compact
              />
              <ChannelReadout
                channelId="pointing_error"
                frame={frame}
                meta={meta}
                digits={2}
                compact
              />
              <ChannelReadout channelId="wheel_speed" frame={frame} meta={meta} digits={0} compact />
            </div>
            <p className="mt-2 text-[10px] text-ink-faint">
              {frame?.context.contact_expected === false
                ? "No scheduled contact window, so the high-rate link is not measured. That is an expected gap, not a radio failure."
                : "Measured during a scheduled contact pass."}{" "}
              These channels use deterministic profiles and are not forecastable.
            </p>
          </Panel>
        </div>

        <div className="grid gap-4 xl:grid-cols-3">
          <Panel
            title="Battery temperature"
            subtitle="Against its own configured limits"
            className="xl:col-span-2"
          >
            <LiveChart
              data={history}
              meta={meta}
              series={[{ channelId: "battery_temperature", axis: "left" }]}
              height={200}
            />
          </Panel>

          <Panel title="Commanded state" subtitle="What the ground knows it commanded">
            {frame ? (
              <ul className="space-y-1 text-xs">
                {Object.entries(frame.context.known_commands).map(([key, value]) => (
                  <li key={key} className="flex items-center justify-between gap-3">
                    <span className="tnum text-ink-dim">{key}</span>
                    <span className="tnum text-ink">{String(value)}</span>
                  </li>
                ))}
                <li className="flex items-center justify-between gap-3 pt-1">
                  <span className="tnum text-ink-dim">control_revision</span>
                  <span className="tnum text-ink">{frame.context.control_revision}</span>
                </li>
              </ul>
            ) : (
              <p className="text-xs text-ink-faint">No frame received yet.</p>
            )}
            <p className="mt-3 text-[10px] text-ink-faint">
              Injected simulator conditions are not shown here and are not available to this
              console.
            </p>
          </Panel>
        </div>

        <Panel title="Subsystem telemetry" subtitle="All permitted channels with their quality">
          <div className="max-h-[420px] overflow-auto">
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
                {OVERVIEW_TABLE.map((id) => (
                  <ChannelTableRow key={id} channelId={id} frame={frame} meta={meta} />
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        <Phase3Operations />
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          <PendingPanel
            title="Vehicle diagram"
            phase={4}
            what="Compact 2D subsystem diagram with the key value per subsystem."
          />
        </div>
      </div>
    </AppShell>
  );
}
