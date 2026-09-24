"use client";

/**
 * Observed battery reserve with the mathematical projection drawn on the same axis.
 *
 * What this component may and may not do:
 *   * It plots only points the backend produced. It never extrapolates, smooths or
 *     invents a future value, and a `null` in a forecast series stays a gap.
 *   * Observed and projected are visually distinct: observed is solid and stops at the
 *     latest frame; projected is dashed and only ever extends forward from it.
 *   * The reserve lines come from the server's configured thresholds. A chart must not
 *     decide for itself where a limit sits.
 *   * The forecast belongs to one run. The caller is responsible for not handing it a
 *     projection from a retired run; this component draws whatever it is given.
 */

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { HistoryPoint } from "@/lib/live";

export interface ForecastSeriesPoint {
  channel_id: string;
  unit: string;
  times_s: number[];
  values: (number | null)[];
}

export interface ForecastLike {
  source_sim_time_s?: number;
  series: ForecastSeriesPoint[];
}

interface Row {
  t: number;
  observed?: number | null;
  projected?: number | null;
  projectedAction?: number | null;
}

const OBSERVED = "#35d6f0";
const PROJECTED = "#b78cf5";
const PROJECTED_ACTION = "#3ddc97";

function socSeries(forecast: ForecastLike | null): ForecastSeriesPoint | null {
  return forecast?.series.find((s) => s.channel_id === "battery_soc") ?? null;
}

function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function ReserveForecastChart({
  history,
  baseline,
  action,
  warningPercent,
  criticalPercent,
  height = 250,
}: {
  history: HistoryPoint[];
  baseline: ForecastLike | null;
  action: ForecastLike | null;
  warningPercent: number | null;
  criticalPercent: number | null;
  height?: number;
}) {
  const rows = new Map<number, Row>();

  for (const point of history) {
    const value = point.battery_soc;
    rows.set(point.t, { t: point.t, observed: typeof value === "number" ? value : null });
  }

  function merge(forecast: ForecastLike | null, key: "projected" | "projectedAction") {
    const series = socSeries(forecast);
    if (!series) return;
    series.times_s.forEach((t, index) => {
      const existing = rows.get(t) ?? { t };
      // A null stays null: an unavailable projection is not zero.
      existing[key] = series.values[index] ?? null;
      rows.set(t, existing);
    });
  }

  merge(baseline, "projected");
  merge(action, "projectedAction");

  const data = Array.from(rows.values()).sort((a, b) => a.t - b.t);

  if (!data.length) {
    return (
      <div
        className="flex items-center justify-center rounded border border-edge/60 text-xs text-ink-faint"
        style={{ height }}
      >
        Waiting for telemetry. Nothing is plotted until a frame arrives.
      </div>
    );
  }

  const hasBaseline = Boolean(socSeries(baseline));
  const hasAction = Boolean(socSeries(action));

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
        <CartesianGrid stroke="#1f2d47" strokeDasharray="2 4" />
        <XAxis
          dataKey="t"
          type="number"
          domain={["dataMin", "dataMax"]}
          tickFormatter={formatClock}
          stroke="#6b7e9b"
          tick={{ fontSize: 10 }}
          minTickGap={44}
          label={{
            value: "spacecraft clock (mm:ss) — right of the solid line is projected",
            position: "insideBottom",
            offset: -2,
            fill: "#6b7e9b",
            fontSize: 10,
          }}
        />
        <YAxis
          stroke="#6b7e9b"
          tick={{ fontSize: 10 }}
          width={52}
          domain={[0, 100]}
          label={{ value: "%", angle: -90, position: "insideLeft", fill: "#6b7e9b", fontSize: 10 }}
        />
        <Tooltip
          contentStyle={{
            background: "#111a2b",
            border: "1px solid #2c3e60",
            borderRadius: 4,
            fontSize: 11,
          }}
          labelFormatter={(v) => `T+${formatClock(Number(v))}`}
          formatter={(value, name) => [
            value === null ? "unavailable" : `${Number(value).toFixed(1)} %`,
            String(name),
          ]}
        />
        <Legend wrapperStyle={{ fontSize: 11, color: "#9fb0c9" }} />

        {typeof warningPercent === "number" ? (
          <ReferenceLine
            y={warningPercent}
            stroke="#f5a524"
            strokeDasharray="4 4"
            strokeOpacity={0.7}
            label={{
              value: `warning reserve ${warningPercent}%`,
              fill: "#f5a524",
              fontSize: 9,
              position: "insideTopRight",
            }}
          />
        ) : null}
        {typeof criticalPercent === "number" ? (
          <ReferenceLine
            y={criticalPercent}
            stroke="#f2555a"
            strokeDasharray="4 4"
            strokeOpacity={0.8}
            label={{
              value: `critical reserve ${criticalPercent}%`,
              fill: "#f2555a",
              fontSize: 9,
              position: "insideBottomRight",
            }}
          />
        ) : null}

        <Line
          type="monotone"
          dataKey="observed"
          name="Observed reserve"
          stroke={OBSERVED}
          strokeWidth={1.9}
          dot={false}
          isAnimationActive={false}
          connectNulls={false}
        />
        {hasBaseline ? (
          <Line
            type="monotone"
            dataKey="projected"
            name="Projected — no action"
            stroke={PROJECTED}
            strokeWidth={1.6}
            strokeDasharray="5 4"
            dot={false}
            isAnimationActive={false}
            connectNulls={false}
          />
        ) : null}
        {hasAction ? (
          <Line
            type="monotone"
            dataKey="projectedAction"
            name="Projected — with action"
            stroke={PROJECTED_ACTION}
            strokeWidth={1.6}
            strokeDasharray="5 4"
            dot={false}
            isAnimationActive={false}
            connectNulls={false}
          />
        ) : null}
      </LineChart>
    </ResponsiveContainer>
  );
}
