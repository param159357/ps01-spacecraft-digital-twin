"use client";

import { AppShell, headerFromLive } from "@/components/AppShell";
import { ConsoleBody } from "@/components/ConsoleBody";
import { Panel } from "@/components/primitives";
import { ChannelReadout, useChannelMeta } from "@/components/telemetry";
import { useRequireRole } from "@/lib/auth";
import { useLiveStream } from "@/lib/live";

export default function ThermalConsole() {
  const { user, checking } = useRequireRole("THERMAL_PROPULSION");
  const live = useLiveStream();
  const { meta } = useChannelMeta();
  if (checking || !user) return null;

  return (
    <AppShell role="THERMAL_PROPULSION" runState={headerFromLive(live)}>
      <ConsoleBody
        headline="Thermal and propulsion console"
        summary="Node temperatures, heater state, propellant and tank pressure. Physically modelled nodes are marked apart from profile-only readings, so a displayed value is never mistaken for a forecastable one."
        pending={[
          {
            title: "Per-node thermal dashboard",
            phase: 4,
            what: "Per-node temperatures against their own configured limits, with commanded and observed heater state shown separately.",
          },
          {
            title: "Propellant and pressure",
            phase: 4,
            what: "Cold-gas mass estimate, tank pressure and valve state. A pressure fall caused by cooling is not reported as a leak.",
          },
          {
            title: "Thermal forecast",
            phase: 3,
            what: "Projected node temperatures with any configured-limit crossing and the assumptions behind it.",
          },
        ]}
      >
        <Panel title="Live telemetry" subtitle="Shared backend run — the same frame every console sees">
          <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 xl:grid-cols-6">
            <ChannelReadout channelId="battery_temperature" frame={live.frame} meta={meta} digits={1} compact />
            <ChannelReadout channelId="panel_temperature" frame={live.frame} meta={meta} digits={1} compact />
            <ChannelReadout channelId="payload_temperature" frame={live.frame} meta={meta} digits={1} compact />
            <ChannelReadout channelId="tank_temperature" frame={live.frame} meta={meta} digits={1} compact />
            <ChannelReadout channelId="heater_power" frame={live.frame} meta={meta} digits={1} compact />
            <ChannelReadout channelId="tank_pressure" frame={live.frame} meta={meta} digits={1} compact />
          </div>
          <p className="mt-3 text-[10px] text-ink-faint">
            Full subsystem dashboards arrive in Phase 4. Channels with no model behind them
            yet read as unavailable rather than showing a plausible number.
          </p>
        </Panel>
      </ConsoleBody>
    </AppShell>
  );
}
