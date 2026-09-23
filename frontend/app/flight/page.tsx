"use client";

import { AppShell, headerFromLive } from "@/components/AppShell";
import { ConsoleBody } from "@/components/ConsoleBody";
import { Panel } from "@/components/primitives";
import { ChannelReadout, useChannelMeta } from "@/components/telemetry";
import { useRequireRole } from "@/lib/auth";
import { useLiveStream } from "@/lib/live";

export default function FlightConsole() {
  const { user, checking } = useRequireRole("FLIGHT_SYSTEMS");
  const live = useLiveStream();
  const { meta } = useChannelMeta();
  if (checking || !user) return null;

  return (
    <AppShell role="FLIGHT_SYSTEMS" runState={headerFromLive(live)}>
      <ConsoleBody
        headline="Flight systems console"
        summary="Attitude, reaction wheel, communication link and computer health. These channels use deterministic seeded profiles rather than a validated attitude-control model, and are labelled as such throughout."
        pending={[
          {
            title: "Pointing, rates and wheel",
            phase: 4,
            what: "Pointing error, body rates and the selected wheel speed and current, against their configured mode profile.",
          },
          {
            title: "Contact and link quality",
            phase: 4,
            what: "Expected contact windows, link SNR and packet error rate with its window and denominator stated. An expected contact gap is not a radio failure.",
          },
          {
            title: "Resets and stale evidence",
            phase: 4,
            what: "Timestamped reset events beside the cumulative counter, with stale evidence marked explicitly rather than held at its last value.",
          },
        ]}
      >
        <Panel title="Live telemetry" subtitle="Shared backend run — the same frame every console sees">
          <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 xl:grid-cols-6">
            <ChannelReadout channelId="telemetry_age" frame={live.frame} meta={meta} digits={1} compact />
            <ChannelReadout channelId="bus_voltage" frame={live.frame} meta={meta} digits={1} compact />
            <ChannelReadout channelId="wheel_speed" frame={live.frame} meta={meta} digits={1} compact />
            <ChannelReadout channelId="pointing_error" frame={live.frame} meta={meta} digits={1} compact />
            <ChannelReadout channelId="link_snr" frame={live.frame} meta={meta} digits={1} compact />
            <ChannelReadout channelId="packet_error_rate" frame={live.frame} meta={meta} digits={1} compact />
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
