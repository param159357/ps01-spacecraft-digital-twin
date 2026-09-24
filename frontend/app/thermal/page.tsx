"use client";

import { useCallback, useState } from "react";
import { AppShell, headerFromLive } from "@/components/AppShell";
import { IncidentPanel } from "@/components/IncidentPanel";
import { IncidentAlerts } from "@/components/IncidentAlerts";
import { LiveChart } from "@/components/LiveChart";
import { Panel, StatusBadge } from "@/components/primitives";
import { ChannelReadout, ChannelTableRow, useChannelMeta } from "@/components/telemetry";
import { NodeHeadroom } from "@/components/NodeHeadroom";
import { SpecialistRecommendation } from "@/components/SpecialistRecommendation";
import { useRequireRole } from "@/lib/auth";
import { freshnessTone, useLiveStream } from "@/lib/live";

const CHART_CHANNELS = [
  "battery_temperature",
  "panel_temperature",
  "payload_temperature",
  "tank_temperature",
  "heater_power",
  "tank_pressure",
];

const NODE_CHANNELS = [
  "battery_temperature",
  "panel_temperature",
  "computer_temperature",
  "payload_temperature",
  "tank_temperature",
  "thruster_temperature",
];

const PROPULSION_CHANNELS = ["tank_pressure", "propellant_mass", "thruster_valve_open"];

export default function ThermalConsole() {
  const { user, checking } = useRequireRole("THERMAL_PROPULSION");
  const live = useLiveStream(CHART_CHANNELS);
  const { meta, freshness } = useChannelMeta();
  const [selectedIncident, setSelectedIncident] = useState<string | null>(null);
  const onIncidentSelect = useCallback((id: string | null) => setSelectedIncident(id), []);

  if (checking || !user) return null;

  const { frame, run, history } = live;
  // Built from the server's role-filtered dictionary so it cannot claim more than
  // this account may read.
  const permittedChannels = Object.keys(meta);
  const commandedHeater = frame?.context.known_commands?.heater_commanded_on;
  const observedHeater = frame?.values.heater_power;
  // Commanded state and observed activity are shown side by side on purpose: that
  // comparison is what makes a stuck actuator visible from observations alone.
  const heaterMismatch =
    typeof observedHeater === "number" &&
    typeof commandedHeater === "boolean" &&
    ((commandedHeater && observedHeater < 1) || (!commandedHeater && observedHeater > 5));

  return (
    <AppShell role="THERMAL_PROPULSION" runState={headerFromLive(live)}>
      <IncidentAlerts />
      <div className="space-y-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h1 className="text-lg font-semibold text-ink">Thermal and propulsion console</h1>
            <p className="mt-0.5 max-w-4xl text-sm text-ink-dim">
              Node temperatures, heater state, propellant and tank pressure. Each node has
              its own configured limits — they are not copied from the battery. You may
              acknowledge incidents here; the master executes recovery.
            </p>
          </div>
          <StatusBadge
            tone={freshnessTone(
              live.dataAgeS,
              freshness.warning_after_s,
              freshness.critical_after_s,
            )}
          >
            frame {frame?.frame_seq ?? "—"}
          </StatusBadge>
        </div>

        {!run?.run_id ? (
          <p className="rounded border border-edge bg-panel px-4 py-3 text-sm text-ink-dim">
            No run is active. The simulator console starts one.
          </p>
        ) : null}

        <Panel title="Thermal nodes" subtitle="Each node against its own configured limits">
          <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 xl:grid-cols-6">
            {NODE_CHANNELS.map((id) => (
              <ChannelReadout key={id} channelId={id} frame={frame} meta={meta} digits={1} />
            ))}
          </div>
        </Panel>

        <div className="grid gap-4 xl:grid-cols-3">
          <Panel
            title="Node temperatures"
            subtitle="All in degrees Celsius, one shared unit per axis"
            className="xl:col-span-2"
          >
            <LiveChart
              data={history}
              meta={meta}
              series={[
                { channelId: "battery_temperature", axis: "left" },
                { channelId: "panel_temperature", axis: "left" },
                { channelId: "payload_temperature", axis: "left" },
                { channelId: "tank_temperature", axis: "left" },
              ]}
              height={230}
              showLimits={false}
            />
            <p className="mt-1 text-[10px] text-ink-faint">
              No limit line is drawn across this chart: the nodes have different limits,
              so one line would be wrong for most of them. Per-node margins are in the
              panel below.
            </p>
          </Panel>

          <Panel
            title="Heater"
            subtitle="Commanded state beside observed activity"
            actions={
              <StatusBadge tone={heaterMismatch ? "warning" : "nominal"}>
                {heaterMismatch ? "Mismatch" : "Consistent"}
              </StatusBadge>
            }
          >
            <div className="space-y-3">
              <div className="flex items-center justify-between text-xs">
                <span className="text-ink-faint">Commanded (thermostat)</span>
                <span className="tnum text-ink">
                  {commandedHeater === undefined
                    ? "—"
                    : commandedHeater
                      ? "ON"
                      : "OFF"}
                </span>
              </div>
              <ChannelReadout
                channelId="heater_power"
                frame={frame}
                meta={meta}
                digits={1}
                compact
              />
              {heaterMismatch ? (
                <p className="text-[10px] text-warning">
                  Observed heater power disagrees with the commanded thermostat state.
                  The <span className="tnum">ACTUATOR_MISMATCH</span> rule is active and
                  will open an incident once the discrepancy persists beyond its
                  configured response allowance. Neither this panel nor that rule claims
                  to know whether the actuator, its sensor or the command path is at
                  fault.
                </p>
              ) : (
                <p className="text-[10px] text-ink-faint">
                  The thermostat holds its state between the configured on and off
                  thresholds, so a steady reading between them is expected.
                </p>
              )}
            </div>
          </Panel>
        </div>

        <Panel
          title="Headroom to each node's own limit"
          subtitle="Distance to the nearer configured bound, in that node's own degrees"
        >
          <NodeHeadroom channelIds={NODE_CHANNELS} frame={frame} meta={meta} />
        </Panel>

        <div className="grid gap-4 xl:grid-cols-3">
          <Panel
            title="Cold-gas store"
            subtitle="Pressure and the mass estimate derived from it"
            className="xl:col-span-2"
          >
            <div className="mb-3 grid grid-cols-2 gap-4 sm:grid-cols-3">
              {PROPULSION_CHANNELS.map((id) => (
                <ChannelReadout key={id} channelId={id} frame={frame} meta={meta} digits={3} compact />
              ))}
            </div>
            <LiveChart
              data={history}
              meta={meta}
              series={[
                { channelId: "tank_pressure", axis: "left" },
                { channelId: "tank_temperature", axis: "right" },
              ]}
              height={180}
              showLimits={false}
            />
            <p className="mt-1 text-[10px] text-ink-faint">
              Propellant mass is <em>derived</em> from the observed pressure and
              temperature, not weighed — so a falling mass estimate is not independent
              confirmation that gas left the tank. A pressure fall caused by cooling is
              not by itself a leak. <strong>No pressure-fault rule is implemented</strong>:
              <span className="tnum">PRESSURE_INCONSISTENCY</span> is declared in the rule
              catalogue but does not run, so nothing here will raise an incident about the
              gas store. <strong>No propulsion recovery action is enabled</strong> either,
              because its prerequisites are not modelled. This subsystem is observable and
              commandable, not yet diagnosable.
            </p>
          </Panel>

          <div id="incident-panel">
            <IncidentPanel subsystemLabel="Thermal / propulsion" onSelect={onIncidentSelect} />
          </div>
        </div>

        <SpecialistRecommendation
          incidentId={selectedIncident}
          subsystemLabel="Thermal / propulsion"
        />

        <Panel
          title="Thermal and propulsion channels"
          subtitle="Every channel this console may read, in configuration order"
          actions={<StatusBadge tone="accent">{permittedChannels.length} channels</StatusBadge>}
        >
          <div className="max-h-[360px] overflow-auto">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-panel">
                <tr className="border-b border-edge text-ink-faint">
                  <th className="py-1.5 pr-3 font-medium">Channel</th>
                  <th className="py-1.5 pr-3 text-right font-medium">Value</th>
                  <th className="py-1.5 pr-3 font-medium">Unit</th>
                  <th className="py-1.5 pr-3 font-medium">Operating limits</th>
                  <th className="py-1.5 pr-3 font-medium">Coverage</th>
                  <th className="py-1.5 font-medium">Quality</th>
                </tr>
              </thead>
              <tbody>
                {permittedChannels.map((id) => (
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
