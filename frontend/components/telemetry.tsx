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

function limitTone(info: ChannelInfo | undefined, value: number): StatusTone {
  if (!info) return "accent";
  const { operating_min: lo, operating_max: hi } = info;
  if (lo !== null && value < lo) return "critical";
  if (hi !== null && value > hi) return "critical";
  // Within 5% of a configured limit reads as a warning.
  if (hi !== null && value > hi - Math.abs(hi) * 0.05) return "warning";
  if (lo !== null && value < lo + Math.abs(lo) * 0.05) return "warning";
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
    display = (raw as number).toFixed(digits);
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
            {typeof raw === "boolean" ? (raw ? "OPEN" : "CLOSED") : (raw as number).toFixed(digits)}
          </span>
        ) : (
          <span className="text-unavailable">{"—"}</span>
        )}
      </td>
      <td className="tnum py-1 pr-3 text-ink-faint">{info ? formatUnit(info.unit) : ""}</td>
      <td className="tnum py-1 pr-3 text-ink-faint">{limits}</td>
      <td className="py-1">
        <StatusBadge tone={usable ? "nominal" : "unavailable"} title={quality?.reason ?? undefined}>
          {quality?.state ?? "NO DATA"}
        </StatusBadge>
      </td>
    </tr>
  );
}
