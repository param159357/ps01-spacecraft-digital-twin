"use client";

// Telemetry presentation bound to the channel dictionary.
//
// Units, limits and labels come from /api/config/channels, never hard-coded per
// component. A channel whose quality is not VALID is shown as unavailable with its
// reason — never as zero, and never by silently holding the last value as if it were
// current.

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Frame } from "@/lib/live";
import { formatUnit, type ChannelInfo, type ChannelsResponse } from "@/lib/types";
import { StatusBadge, type StatusTone } from "./primitives";

export function useChannelMeta() {
  const [meta, setMeta] = useState<Record<string, ChannelInfo>>({});
  const [freshness, setFreshness] = useState({ warning_after_s: 3, critical_after_s: 10 });

  useEffect(() => {
    const controller = new AbortController();
    api
      .get<ChannelsResponse>("/config/channels", controller.signal)
      .then((data) => {
        setMeta(Object.fromEntries(data.channels.map((c) => [c.id, c])));
        setFreshness(data.freshness);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  return { meta, freshness };
}

/**
 * Colour a value against its configured operating limits.
 *
 * The "approaching a limit" margin is a fraction of the operating *band width*, not of
 * the limit's magnitude. That distinction matters: a battery whose normal band is
 * 24–29 V would otherwise read amber above 27.55 V — which is most of the time it is
 * charged — and a console that cries warning during normal operation teaches operators
 * to ignore it.
 */
function limitTone(info: ChannelInfo | undefined, value: number): StatusTone {
  if (!info) return "accent";
  const { operating_min: lo, operating_max: hi } = info;
  if (lo !== null && value < lo) return "critical";
  if (hi !== null && value > hi) return "critical";

  const margin =
    lo !== null && hi !== null
      ? (hi - lo) * 0.05
      : 0.05 * Math.abs(hi ?? lo ?? 0);

  if (margin > 0) {
    if (hi !== null && value > hi - margin) return "warning";
    if (lo !== null && value < lo + margin) return "warning";
  }
  return "nominal";
}

export function ChannelReadout({
  channelId,
  frame,
  meta,
  digits = 1,
  compact = false,
}: {
  channelId: string;
  frame: Frame | null;
  meta: Record<string, ChannelInfo>;
  digits?: number;
  compact?: boolean;
}) {
  const info = meta[channelId];
  const quality = frame?.quality[channelId];
  const raw = frame?.values[channelId];
  const usable = quality?.state === "VALID" && raw !== null && raw !== undefined;

  const label = info?.label ?? channelId;
  const unit = info ? formatUnit(info.unit) : "";

  let display: string;
  let tone: StatusTone = "unavailable";

  if (!frame) {
    display = "—";
  } else if (!usable) {
    display = "—";
  } else if (typeof raw === "boolean") {
    display = raw ? "OPEN" : "CLOSED";
    tone = raw ? "warning" : "nominal";
  } else {
    // A count is a whole number of events, not a continuous gauge (section 6.2), so it
    // never renders with a fractional part regardless of the caller's preference.
    display = (raw as number).toFixed(info?.type === "count" ? 0 : digits);
    tone = limitTone(info, raw as number);
  }

  const toneText: Record<StatusTone, string> = {
    nominal: "text-nominal",
    warning: "text-warning",
    critical: "text-critical",
    unavailable: "text-unavailable",
    accent: "text-accent",
  };

  return (
    <div className="flex flex-col gap-0.5" title={info?.description}>
      <span className="text-[10px] tracking-wide text-ink-faint uppercase">{label}</span>
      <span
        className={`tnum ${compact ? "text-base" : "text-2xl"} ${usable ? toneText[tone] : "text-unavailable"}`}
      >
        {display}
        {usable && typeof raw === "number" && unit ? (
          <span className="ml-1 text-xs text-ink-faint">{unit}</span>
        ) : null}
      </span>
      {!usable && frame ? (
        <span className="text-[10px] text-unavailable">
          {quality?.state ?? "MISSING"}
          {quality?.reason ? ` · ${quality.reason}` : ""}
        </span>
      ) : null}
    </div>
  );
}

export function ChannelTableRow({
  channelId,
  frame,
  meta,
  digits = 2,
}: {
  channelId: string;
  frame: Frame | null;
  meta: Record<string, ChannelInfo>;
  digits?: number;
}) {
  const info = meta[channelId];
  const quality = frame?.quality[channelId];
  const raw = frame?.values[channelId];
  const usable = quality?.state === "VALID" && raw !== null && raw !== undefined;

  const limits =
    info && (info.operating_min !== null || info.operating_max !== null)
      ? `${info.operating_min ?? "—"} … ${info.operating_max ?? "—"}`
      : "none configured";

  return (
    <tr className="border-b border-edge/50">
      <td className="py-1 pr-3">
        <span className="text-ink">{info?.label ?? channelId}</span>
        <span className="tnum block text-[10px] text-ink-faint">{channelId}</span>
      </td>
      <td className="tnum py-1 pr-3 text-right">
        {usable ? (
          <span className={typeof raw === "boolean" ? "text-ink" : ""}>
            {typeof raw === "boolean"
              ? raw
                ? "OPEN"
                : "CLOSED"
              : (raw as number).toFixed(info?.type === "count" ? 0 : digits)}
          </span>
        ) : (
          <span className="text-unavailable">{"—"}</span>
        )}
      </td>
      <td className="tnum py-1 pr-3 text-ink-faint">{info ? formatUnit(info.unit) : ""}</td>
      <td className="tnum py-1 pr-3 text-ink-faint">{limits}</td>
      {/* Coverage is declared per channel in telemetry.yaml. Showing it stops a
          reading being mistaken for something the model predicts when it does not:
          PROFILE is a deterministic pattern and RULE_ONLY has no forecast at all. */}
      <td className="py-1 pr-3">
        <StatusBadge
          tone={info?.prediction === "MODELLED" ? "accent" : "unavailable"}
          title={info?.description}
        >
          {info?.prediction ?? "—"}
        </StatusBadge>
      </td>
      <td className="py-1">
        <StatusBadge tone={usable ? "nominal" : "unavailable"} title={quality?.reason ?? undefined}>
          {quality?.state ?? "NO DATA"}
        </StatusBadge>
      </td>
    </tr>
  );
}
