"use client";

// Time-series chart for live telemetry.
//
// Rules this component enforces (CLAUDE.md section 5):
//   * one physical unit per axis — volts and degrees Celsius never share an
//     unexplained axis, so a second unit gets its own labelled right-hand axis
//   * gaps stay gaps: a null value breaks the line rather than being interpolated
//   * configured limits are drawn as reference lines, in the channel's own units

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
import { formatUnit, type ChannelInfo } from "@/lib/types";

const SERIES_COLORS = ["#35d6f0", "#3ddc97", "#f5a524", "#b78cf5", "#f2555a"];

export interface ChartSeries {
  channelId: string;
  axis?: "left" | "right";
  color?: string;
}

function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

/**
 * Where illumination changed between one sample and the next.
 *
 * A battery that starts draining at a sunlight-to-eclipse boundary is behaving, not
 * failing. Without the marker the reader sees an unexplained fall and has to be told;
 * with it, the chart says so itself.
 */
function illuminationChanges(data: HistoryPoint[]): { t: number; to: string }[] {
  const marks: { t: number; to: string }[] = [];
  let previous: string | undefined;
  for (const point of data) {
    const current = point.illumination;
    if (typeof current === "string") {
      if (previous && current !== previous) marks.push({ t: point.t, to: current });
      previous = current;
    }
  }
  return marks;
}

export function LiveChart({
  data,
  series,
  meta,
  height = 240,
  showLimits = true,
  showIllumination = true,
}: {
  data: HistoryPoint[];
  series: ChartSeries[];
  meta: Record<string, ChannelInfo>;
  height?: number;
  showLimits?: boolean;
  showIllumination?: boolean;
}) {
  const leftSeries = series.filter((s) => (s.axis ?? "left") === "left");
  const rightSeries = series.filter((s) => s.axis === "right");

  const leftUnit = leftSeries[0] ? formatUnit(meta[leftSeries[0].channelId]?.unit ?? "") : "";
  const rightUnit = rightSeries[0] ? formatUnit(meta[rightSeries[0].channelId]?.unit ?? "") : "";

  if (!data.length) {
    return (
      <div
        className="flex items-center justify-center rounded border border-edge/60 text-xs text-ink-faint"
        style={{ height }}
      >
        Waiting for telemetry. No data is plotted until a frame arrives.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
        <CartesianGrid stroke="#1f2d47" strokeDasharray="2 4" />
        <XAxis
          dataKey="t"
          tickFormatter={formatClock}
          stroke="#6b7e9b"
          tick={{ fontSize: 10 }}
          minTickGap={40}
          label={{
            value: "spacecraft clock (mm:ss)",
            position: "insideBottom",
            offset: -2,
            fill: "#6b7e9b",
            fontSize: 10,
          }}
        />
        <YAxis
          yAxisId="left"
          stroke="#6b7e9b"
          tick={{ fontSize: 10 }}
          width={52}
          label={{
            value: leftUnit,
            angle: -90,
            position: "insideLeft",
            fill: "#6b7e9b",
            fontSize: 10,
          }}
        />
        {rightSeries.length ? (
          <YAxis
            yAxisId="right"
            orientation="right"
            stroke="#6b7e9b"
            tick={{ fontSize: 10 }}
            width={52}
            label={{
              value: rightUnit,
              angle: 90,
              position: "insideRight",
              fill: "#6b7e9b",
              fontSize: 10,
            }}
          />
        ) : null}

        <Tooltip
          contentStyle={{
            background: "#111a2b",
            border: "1px solid #2c3e60",
            borderRadius: 4,
            fontSize: 11,
          }}
          labelFormatter={(v) => `T+${formatClock(Number(v))}`}
          formatter={(value, name) => {
            const info = meta[String(name)];
            return [
              value === null ? "unavailable" : `${Number(value).toFixed(2)} ${formatUnit(info?.unit ?? "")}`,
              info?.label ?? String(name),
            ];
          }}
        />
        <Legend
          wrapperStyle={{ fontSize: 11, color: "#9fb0c9" }}
          formatter={(value) => meta[String(value)]?.label ?? String(value)}
        />

        {showIllumination
          ? illuminationChanges(data).map((mark) => (
              <ReferenceLine
                key={`illum-${mark.t}`}
                x={mark.t}
                yAxisId={series[0]?.axis ?? "left"}
                stroke={mark.to === "ECLIPSE" ? "#6b7e9b" : "#3ddc97"}
                strokeDasharray="2 3"
                strokeOpacity={0.8}
                label={{
                  value: mark.to === "ECLIPSE" ? "eclipse" : "sunlight",
                  fill: mark.to === "ECLIPSE" ? "#9fb0c9" : "#3ddc97",
                  fontSize: 9,
                  position: "insideTopLeft",
                }}
              />
            ))
          : null}

        {showLimits
          ? series.map((s) => {
              const info = meta[s.channelId];
              if (!info) return null;
              const axisId = s.axis ?? "left";
              return [info.operating_min, info.operating_max]
                .filter((v): v is number => v !== null)
                .map((limit) => (
                  <ReferenceLine
                    key={`${s.channelId}-${limit}`}
                    y={limit}
                    yAxisId={axisId}
                    stroke="#f5a524"
                    strokeDasharray="4 4"
                    strokeOpacity={0.5}
                    label={{
                      value: `${info.label} limit ${limit}`,
                      fill: "#f5a524",
                      fontSize: 9,
                      position: "insideTopRight",
                    }}
                  />
                ));
            })
          : null}

        {series.map((s, index) => (
          <Line
            key={s.channelId}
            yAxisId={s.axis ?? "left"}
            type="monotone"
            dataKey={s.channelId}
            name={s.channelId}
            stroke={s.color ?? SERIES_COLORS[index % SERIES_COLORS.length]}
            strokeWidth={1.75}
            dot={false}
            isAnimationActive={false}
            // A missing sample leaves a gap instead of being bridged, so an outage is
            // visible on the chart rather than smoothed away.
            connectNulls={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
