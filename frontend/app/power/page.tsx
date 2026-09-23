"use client";

import { AppShell, headerFromLive } from "@/components/AppShell";
import { ConsoleBody } from "@/components/ConsoleBody";
import { Panel } from "@/components/primitives";
import { ChannelReadout, useChannelMeta } from "@/components/telemetry";
import { useRequireRole } from "@/lib/auth";
import { useLiveStream } from "@/lib/live";

export default function PowerConsole() {
  const { user, checking } = useRequireRole("POWER");
  const live = useLiveStream();
  const { meta } = useChannelMeta();
  if (checking || !user) return null;

  return (
    <AppShell role="POWER" runState={headerFromLive(live)}>
      <ConsoleBody
        headline="Power console"
        summary="Generation, battery and electrical load. Voltage is in volts, power in watts and consumed energy in watt-hours; these are never interchanged. You may acknowledge power incidents and request comparisons, but recovery is executed by the master."
        pending={[
          {
            title: "Generation and demand detail",
            phase: 4,
            what: "Available solar power, dispatched output, curtailment and total requested demand, with served and unmet power shown separately.",
          },
          {
            title: "Battery state detail",
            phase: 4,
            what: "State of charge, terminal voltage, current (positive when discharging) and battery temperature.",
          },
          {
            title: "Energy reserve forecast",
            phase: 3,
            what: "Thirty simulated minutes ahead with eclipse markers and any projected reserve crossing, stated with its assumptions.",
          },
        ]}
      >
        <Panel title="Live telemetry" subtitle="Shared backend run — the same frame every console sees">
          <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 xl:grid-cols-6">
            <ChannelReadout channelId="battery_soc" frame={live.frame} meta={meta} digits={1} compact />
            <ChannelReadout channelId="load_power" frame={live.frame} meta={meta} digits={1} compact />
            <ChannelReadout channelId="bus_voltage" frame={live.frame} meta={meta} digits={1} compact />
            <ChannelReadout channelId="battery_current" frame={live.frame} meta={meta} digits={1} compact />
            <ChannelReadout channelId="battery_temperature" frame={live.frame} meta={meta} digits={1} compact />
            <ChannelReadout channelId="solar_current" frame={live.frame} meta={meta} digits={1} compact />
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
