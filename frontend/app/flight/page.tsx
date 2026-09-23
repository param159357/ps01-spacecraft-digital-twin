"use client";

import { AppShell, headerFromLive } from "@/components/AppShell";
import { IncidentPanel } from "@/components/IncidentPanel";
import { IncidentAlerts } from "@/components/IncidentAlerts";
import { LiveChart } from "@/components/LiveChart";
import { Panel, StatusBadge } from "@/components/primitives";
import { ChannelReadout, ChannelTableRow, useChannelMeta } from "@/components/telemetry";
import { useRequireRole } from "@/lib/auth";
import { freshnessTone, useLiveStream } from "@/lib/live";

const CHART_CHANNELS = ["roll_rate", "pitch_rate", "yaw_rate", "wheel_speed", "wheel_current"];

const ATTITUDE_CHANNELS = ["pointing_error", "roll_rate", "pitch_rate", "yaw_rate"];
const WHEEL_CHANNELS = ["wheel_speed", "wheel_current"];
const LINK_CHANNELS = ["link_snr", "packet_error_rate", "telemetry_age", "computer_reset_count"];

export default function FlightConsole() {
  const { user, checking } = useRequireRole("FLIGHT_SYSTEMS");
  const live = useLiveStream(CHART_CHANNELS);
  const { meta, freshness } = useChannelMeta();

  if (checking || !user) return null;

  const { frame, run, history } = live;
  const contactExpected = frame?.context.contact_expected ?? null;
  const engineering = frame?.engineering ?? {};
  const attempted = engineering.packets_attempted_window;
  const failed = engineering.packets_failed_window;
  const windowSeconds = engineering.packet_window_seconds;
  const resetEvents = engineering.reset_events_s;

  return (
    <AppShell role="FLIGHT_SYSTEMS" runState={headerFromLive(live)}>
      <IncidentAlerts />
      <div className="space-y-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h1 className="text-lg font-semibold text-ink">Flight systems console</h1>
            <p className="mt-0.5 max-w-4xl text-sm text-ink-dim">
              Attitude, the selected reaction wheel, the communication link and computer
              health. These channels come from{" "}
              <strong className="text-ink">deterministic seeded profiles</strong>, not a
              validated attitude-control model, and are not physically forecastable.
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

        <Panel
          title="Attitude"
          subtitle="Pointing error and body rates — PROFILE coverage, not a dynamics model"
        >
          <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
            {ATTITUDE_CHANNELS.map((id) => (
              <ChannelReadout key={id} channelId={id} frame={frame} meta={meta} digits={3} />
            ))}
          </div>
        </Panel>

        <div className="grid gap-4 xl:grid-cols-3">
          <Panel
            title="Body rates"
            subtitle="All three axes in degrees per second"
            className="xl:col-span-2"
          >
            <LiveChart
              data={history}
              meta={meta}
              series={[
                { channelId: "roll_rate", axis: "left" },
                { channelId: "pitch_rate", axis: "left" },
                { channelId: "yaw_rate", axis: "left" },
              ]}
              height={200}
              showLimits={false}
            />
          </Panel>

          <Panel title="Reaction wheel" subtitle="One selected wheel, not a full assembly">
            <div className="mb-3 grid grid-cols-2 gap-4">
              {WHEEL_CHANNELS.map((id) => (
                <ChannelReadout key={id} channelId={id} frame={frame} meta={meta} digits={2} compact />
              ))}
            </div>
            <LiveChart
              data={history}
              meta={meta}
              series={[
                { channelId: "wheel_speed", axis: "left" },
                { channelId: "wheel_current", axis: "right" },
              ]}
              height={150}
              showLimits={false}
            />
            <p className="mt-1 text-[10px] text-ink-faint">
              Wheel current follows wheel acceleration, so the two are consistent by
              construction. Wheel current alone does not establish a root cause.
            </p>
          </Panel>
        </div>

        <div className="grid gap-4 xl:grid-cols-3">
          <Panel
            title="Communication link"
            subtitle="High-rate downlink during a scheduled pass"
            className="xl:col-span-2"
            actions={
              <StatusBadge tone={contactExpected ? "nominal" : "unavailable"}>
                {contactExpected === null
                  ? "No run"
                  : contactExpected
                    ? "Contact expected"
                    : "No contact scheduled"}
              </StatusBadge>
            }
          >
            <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
              {LINK_CHANNELS.map((id) => (
                <ChannelReadout key={id} channelId={id} frame={frame} meta={meta} digits={2} />
              ))}
            </div>

            <div className="mt-3 space-y-1 border-t border-edge pt-2 text-[10px] text-ink-faint">
              <p>
                Packet error rate is a rolling ratio over{" "}
                <span className="tnum">{typeof windowSeconds === "number" ? windowSeconds : "—"}</span>{" "}
                s, with{" "}
                <span className="tnum">{typeof attempted === "number" ? attempted : "—"}</span>{" "}
                attempted and{" "}
                <span className="tnum">{typeof failed === "number" ? failed : "—"}</span>{" "}
                failed packets in that window. With nothing attempted it is reported
                unavailable, never as zero.
              </p>
              <p>
                Outside a scheduled pass the link is not measured, so SNR reads as
                unavailable. That is an expected contact gap, not a radio failure.
              </p>
            </div>
          </Panel>

          <div id="incident-panel"><IncidentPanel subsystemLabel="Flight systems" /></div>
        </div>

        <div className="grid gap-4 xl:grid-cols-3">
          <Panel title="Computer health" subtitle="Cumulative counter and timestamped events">
            <div className="grid grid-cols-2 gap-4">
              <ChannelReadout
                channelId="computer_reset_count"
                frame={frame}
                meta={meta}
                digits={0}
                compact
              />
              <ChannelReadout
                channelId="computer_temperature"
                frame={frame}
                meta={meta}
                digits={1}
                compact
              />
            </div>
            <p className="mt-2 text-[10px] text-ink-faint">
              {typeof resetEvents === "string" && resetEvents
                ? `Reset events at spacecraft time: ${resetEvents} s.`
                : "No reset events recorded in this run."}{" "}
              The counter is cumulative within the run — a new run restarts it without
              implying a physical reset.
            </p>
          </Panel>

          <Panel
            title="Flight systems channels"
            subtitle="Units, limits and quality"
            className="xl:col-span-2"
          >
            <div className="max-h-[300px] overflow-auto">
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
                  {[...ATTITUDE_CHANNELS, ...WHEEL_CHANNELS, ...LINK_CHANNELS].map((id) => (
                    <ChannelTableRow key={id} channelId={id} frame={frame} meta={meta} digits={3} />
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </div>
      </div>
    </AppShell>
  );
}
