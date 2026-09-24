"use client";

/**
 * How much margin each thermal node has to its *own* configured limits.
 *
 * Why this exists rather than a limit line on the shared chart: the six nodes have
 * genuinely different limits — a panel runs to 90 °C where a battery warns at 40 °C — so
 * a single line drawn across all of them would be wrong for five of the six. Section 7.3
 * is explicit that node limits are not copied from the battery, and a chart that implied
 * otherwise would undo that care.
 *
 * Each row therefore reports the distance from the reading to whichever bound it is
 * approaching, in the node's own degrees. A channel with no valid reading says so; it is
 * never shown as zero margin, which would read as "at the limit".
 */

import { StatusBadge, type StatusTone } from "@/components/primitives";
import type { Frame } from "@/lib/live";
import type { ChannelInfo } from "@/lib/types";

interface Headroom {
  id: string;
  label: string;
  value: number | null;
  /** Degrees to the nearer bound; negative once outside it. */
  margin: number | null;
  bound: "upper" | "lower" | null;
  limitText: string;
}

function assess(channelId: string, frame: Frame | null, info: ChannelInfo | undefined): Headroom {
  const quality = frame?.quality[channelId];
  const raw = frame?.values[channelId];
  const usable = quality?.state === "VALID" && typeof raw === "number";
  const lo = info?.operating_min ?? null;
  const hi = info?.operating_max ?? null;

  const base: Headroom = {
    id: channelId,
    label: info?.label ?? channelId,
    value: usable ? (raw as number) : null,
    margin: null,
    bound: null,
    limitText: lo === null && hi === null ? "none configured" : `${lo ?? "—"} … ${hi ?? "—"} °C`,
  };
  if (!usable) return base;

  const value = raw as number;
  const toUpper = hi === null ? null : hi - value;
  const toLower = lo === null ? null : value - lo;

  // Report whichever bound is closer; that is the one an operator cares about.
  if (toUpper !== null && (toLower === null || toUpper <= toLower)) {
    return { ...base, margin: toUpper, bound: "upper" };
  }
  if (toLower !== null) return { ...base, margin: toLower, bound: "lower" };
  return base;
}

function tone(margin: number | null, info: ChannelInfo | undefined): StatusTone {
  if (margin === null) return "unavailable";
  if (margin < 0) return "critical";
  const lo = info?.operating_min ?? null;
  const hi = info?.operating_max ?? null;
  // "Close" is a fraction of the node's own band width, not of the limit's magnitude —
  // the same rule the readouts use, so the two never disagree.
  const band = lo !== null && hi !== null ? hi - lo : null;
  if (band !== null && band > 0 && margin <= band * 0.1) return "warning";
  return "nominal";
}

export function NodeHeadroom({
  channelIds,
  frame,
  meta,
}: {
  channelIds: string[];
  frame: Frame | null;
  meta: Record<string, ChannelInfo>;
}) {
  const rows = channelIds.map((id) => assess(id, frame, meta[id]));

  return (
    <div className="space-y-1.5">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="border-b border-edge text-ink-faint">
            <th className="py-1 pr-2 font-medium">Node</th>
            <th className="py-1 pr-2 text-right font-medium">Now</th>
            <th className="py-1 pr-2 text-right font-medium">Margin</th>
            <th className="py-1 pr-2 font-medium">Its own limits</th>
            <th className="py-1 font-medium">State</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const info = meta[row.id];
            return (
              <tr key={row.id} className="border-b border-edge/40">
                <td className="py-1 pr-2 text-ink">{row.label}</td>
                <td className="tnum py-1 pr-2 text-right text-ink">
                  {row.value === null ? (
                    <span className="text-unavailable">—</span>
                  ) : (
                    `${row.value.toFixed(1)} °C`
                  )}
                </td>
                <td className="tnum py-1 pr-2 text-right">
                  {row.margin === null ? (
                    <span className="text-unavailable">unavailable</span>
                  ) : (
                    <span
                      className={
                        row.margin < 0
                          ? "text-critical"
                          : tone(row.margin, info) === "warning"
                            ? "text-warning"
                            : "text-ink"
                      }
                    >
                      {row.margin < 0 ? "" : "+"}
                      {row.margin.toFixed(1)} °C
                      <span className="ml-1 text-[10px] text-ink-faint">
                        {row.bound === "upper" ? "below max" : "above min"}
                      </span>
                    </span>
                  )}
                </td>
                <td className="tnum py-1 pr-2 text-ink-faint">{row.limitText}</td>
                <td className="py-1">
                  <StatusBadge tone={tone(row.margin, info)}>
                    {row.margin === null
                      ? "NO DATA"
                      : row.margin < 0
                        ? "OUTSIDE"
                        : tone(row.margin, info) === "warning"
                          ? "NEAR LIMIT"
                          : "IN BAND"}
                  </StatusBadge>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="text-[10px] text-ink-faint">
        Margin is the distance to whichever of this node&apos;s own bounds is nearer. The
        six nodes have different limits, which is why no single limit line is drawn across
        the shared chart. A reading that is not valid shows as unavailable, never as zero
        margin.
      </p>
    </div>
  );
}
